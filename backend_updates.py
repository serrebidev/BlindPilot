"""Read-only backend update discovery for the setup wizard.

Update execution stays in ``blindpilot_app``. This module only answers what is
installed and whether a documented source reports a newer stable version.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Protocol

import agent_backends


class BackendUpdateState(str, Enum):
    UNAVAILABLE = "unavailable"
    CURRENT = "current"
    UPDATE_AVAILABLE = "update_available"
    FAILED = "failed"


@dataclass(frozen=True)
class BackendUpdateResult:
    backend_id: str
    executable_path: Optional[str]
    installed_version: Optional[str]
    available_version: Optional[str]
    state: BackendUpdateState
    source: str
    checked_at: float
    failure_reason: Optional[str] = None


_VERSION_RE = re.compile(
    r"(?<![0-9])v?(?P<main>[0-9]+(?:\.[0-9]+){0,3})(?:-(?P<pre>[0-9A-Za-z.-]+))?(?![0-9])"
)
_NPM_PACKAGES = {
    agent_backends.BACKEND_CODEX: "@openai/codex",
    agent_backends.BACKEND_FREEBUFF: "freebuff",
    agent_backends.BACKEND_OPENCODE: "opencode-ai",
    agent_backends.BACKEND_COMMANDCODE: "command-code",
    agent_backends.BACKEND_GEMINI: "@google/gemini-cli",
}


def parse_version(text: str) -> Optional[tuple[int, int, int, tuple[str, ...]]]:
    match = _VERSION_RE.search(text or "")
    if not match:
        return None
    numbers = tuple(int(part) for part in match.group("main").split("."))
    numbers += (0,) * (3 - len(numbers))
    pre = tuple(match.group("pre").split(".")) if match.group("pre") else ()
    return (numbers[0], numbers[1], numbers[2], pre)


def compare_versions(left: str, right: str) -> int:
    """Compare stable versions, returning 0 for an unsupported prerelease."""
    a = parse_version(left)
    b = parse_version(right)
    if a is None or b is None or a[3] or b[3]:
        return 0
    return (a[:3] > b[:3]) - (a[:3] < b[:3])


class BackendAdapter(Protocol):
    source: str
    manual_update_supported: bool

    def package_name(self, backend: str) -> Optional[str]: ...

    def latest_version(self, backend: str) -> Optional[str]: ...


def _run_version_probe(binary: str) -> str:
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            stdin=subprocess.DEVNULL,
            env=agent_backends.subprocess_env(binary),
            **agent_backends.no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return ((result.stdout or "") + "\n" + (result.stderr or "")).strip()


def _run_npm_metadata(npm: str, package: str) -> Optional[str]:
    try:
        result = subprocess.run(
            [npm, "view", package, "version", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            stdin=subprocess.DEVNULL,
            env=agent_backends.subprocess_env(npm),
            **agent_backends.no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    text = (result.stdout or "").strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = text.strip('"')
    return value if isinstance(value, str) and parse_version(value) else None


class _NpmAdapter:
    source = "npm"
    manual_update_supported = True

    def package_name(self, backend: str) -> Optional[str]:
        return _NPM_PACKAGES.get(agent_backends.normalize_backend(backend))

    def latest_version(self, backend: str) -> Optional[str]:
        package = self.package_name(backend)
        npm = _find_npm()
        return _run_npm_metadata(npm, package) if package and npm else None


class _InstalledOnlyAdapter:
    source = "official_installer"
    manual_update_supported = True

    def package_name(self, _backend: str) -> Optional[str]:
        return None

    def latest_version(self, _backend: str) -> Optional[str]:
        return None


def _find_npm() -> Optional[str]:
    for name in ("npm.cmd", "npm"):
        path = agent_backends.shutil.which(name)
        if path:
            return path
    return None


class BackendUpdateService:
    def __init__(self, cache_path: Optional[Path] = None, ttl: float = 300.0) -> None:
        self.cache_path = cache_path
        self.ttl = ttl
        self._cache: dict[str, BackendUpdateResult] = {}
        self._inflight: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._adapters: dict[str, BackendAdapter] = {
            backend: _NpmAdapter() for backend in _NPM_PACKAGES
        }
        self._adapters.update(
            {
                backend: _InstalledOnlyAdapter()
                for backend in agent_backends.BACKENDS
                if backend not in self._adapters
            }
        )
        self._load_cache()

    def check(self, backend: str, force: bool = False) -> BackendUpdateResult:
        backend = agent_backends.normalize_backend(backend)
        now = time.time()
        with self._lock:
            cached = self._cache.get(backend)
            if cached and not force and now - cached.checked_at < self.ttl:
                return cached
            event = self._inflight.get(backend)
            if event is None:
                event = threading.Event()
                self._inflight[backend] = event
                owner = True
            else:
                owner = False
        if not owner:
            event.wait(35)
            with self._lock:
                return self._cache.get(backend) or self._failed(backend, "check did not finish")
        try:
            result = self._check_uncached(backend)
            with self._lock:
                self._cache[backend] = result
                self._save_cache()
            return result
        finally:
            with self._lock:
                event = self._inflight.pop(backend, None)
                if event:
                    event.set()

    def invalidate(self, backend: str) -> None:
        with self._lock:
            self._cache.pop(agent_backends.normalize_backend(backend), None)
            self._save_cache()

    def _check_uncached(self, backend: str) -> BackendUpdateResult:
        checked_at = time.time()
        adapter = self._adapters.get(backend)
        if adapter is None:
            return self._failed(backend, "backend is not registered", checked_at)
        binary = agent_backends.find_backend_cli(backend)
        if not binary:
            return BackendUpdateResult(
                backend,
                None,
                None,
                None,
                BackendUpdateState.UNAVAILABLE,
                adapter.source,
                checked_at,
            )
        installed_text = _run_version_probe(binary)
        installed = parse_version(installed_text)
        if installed is None:
            return self._failed(backend, "installed version could not be read", checked_at, binary)
        installed_text = ".".join(str(part) for part in installed[:3])
        available = adapter.latest_version(backend)
        if available is None:
            if adapter.source == "npm":
                return self._failed(
                    backend,
                    "could not read the npm latest-version metadata",
                    checked_at,
                    binary,
                    installed_text,
                )
            return BackendUpdateResult(
                backend,
                binary,
                installed_text,
                None,
                BackendUpdateState.CURRENT,
                adapter.source,
                checked_at,
            )
        if parse_version(available) is None:
            return self._failed(
                backend, "latest version was invalid", checked_at, binary, installed_text
            )
        state = (
            BackendUpdateState.UPDATE_AVAILABLE
            if compare_versions(available, installed_text) > 0
            else BackendUpdateState.CURRENT
        )
        return BackendUpdateResult(
            backend, binary, installed_text, available, state, adapter.source, checked_at
        )

    @staticmethod
    def _failed(
        backend: str,
        reason: str,
        checked_at: Optional[float] = None,
        binary: Optional[str] = None,
        installed: Optional[str] = None,
    ) -> BackendUpdateResult:
        return BackendUpdateResult(
            backend,
            binary,
            installed,
            None,
            BackendUpdateState.FAILED,
            "unavailable",
            checked_at or time.time(),
            reason,
        )

    def _load_cache(self) -> None:
        if self.cache_path is None:
            return
        try:
            rows = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(rows, dict):
            return
        for backend, row in rows.items():
            try:
                self._cache[backend] = BackendUpdateResult(
                    backend_id=row["backend_id"],
                    executable_path=row.get("executable_path"),
                    installed_version=row.get("installed_version"),
                    available_version=row.get("available_version"),
                    state=BackendUpdateState(row["state"]),
                    source=row["source"],
                    checked_at=float(row["checked_at"]),
                    failure_reason=row.get("failure_reason"),
                )
            except (KeyError, TypeError, ValueError):
                continue

    def _save_cache(self) -> None:
        if self.cache_path is None:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                backend: {**asdict(result), "state": result.state.value}
                for backend, result in self._cache.items()
            }
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.cache_path.parent, delete=False
            ) as handle:
                json.dump(payload, handle)
                temporary = Path(handle.name)
            temporary.replace(self.cache_path)
        except OSError:
            return
