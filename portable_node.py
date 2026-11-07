"""The portable Node.js and npm BlindPilot installs for itself.

BlindPilot installs its npm-backed backends without asking for elevation, so it
needs a Node and an npm it owns: a per-user npm prefix under the data folder,
and a Node LTS unpacked into ``runtimes/node`` from nodejs.org, checksum
verified. This module is that machinery — the discovery, the download, the
archive extraction, npm's environment and the npm install/update command lines,
and the logged process runner those commands go through.

Split out of ``blindpilot_app.py`` (issue #34) so the window calls the installer
instead of defining it. The window reaches these as
``portable_node.install_portable_node`` and the like, so it keeps no re-export:
the names are gone from blindpilot_app rather than aliased. The URLs, the flags,
the messages and the folders are unchanged by the move.

``APP_VERSION`` is the one thing read from the window module, and it is read
through ``agent_backends._app_version`` rather than imported, because the window
module imports this one. That is the same route Codex's clientInfo takes.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, List, Optional

import install_path
from agent_backends import (
    BACKENDS,
    _app_version,
    blindpilot_data_dir,
    normalize_backend,
    subprocess_env,
)
from certificates import open_url
from process_utils import no_window_kwargs


def _backend_installs_with_npm(backend: str) -> bool:
    """Whether this backend is one BlindPilot can install for the user.

    Not every backend ships on npm: Hermes installs itself from its own
    installer, so telling the user that npm is required would send them after
    the wrong thing entirely.
    """
    return bool(BACKENDS[normalize_backend(backend)].npm_package)


NODE_RELEASE_INDEX_URL = "https://nodejs.org/dist/index.json"
NODE_RELEASE_BASE_URL = "https://nodejs.org/dist"
_NODE_MINIMUM_MAJOR = 18


def _managed_npm_prefix() -> Path:
    """A writable per-user prefix owned by BlindPilot, never a system folder."""
    return blindpilot_data_dir() / "npm"


def _managed_npm_bin_dir() -> Path:
    prefix = _managed_npm_prefix()
    return prefix if platform.system() == "Windows" else prefix / "bin"


def _node_runtime_root() -> Path:
    return blindpilot_data_dir() / "runtimes" / "node"


def _node_archive_spec(
    version: str, system: Optional[str] = None, machine: Optional[str] = None
) -> Optional[tuple[str, str]]:
    """Return the official Node archive name and its extracted folder."""
    system = system or platform.system()
    machine = (machine or platform.machine()).casefold()
    os_name = {"Windows": "win", "Darwin": "darwin", "Linux": "linux"}.get(system)
    arch = {
        "amd64": "x64",
        "x86_64": "x64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(machine)
    if not os_name or not arch:
        return None
    stem = f"node-{version}-{os_name}-{arch}"
    extension = ".zip" if system == "Windows" else ".tar.gz"
    return stem + extension, stem


def _managed_node_dir() -> Optional[Path]:
    """Newest complete portable Node runtime previously installed by BlindPilot."""
    spec = _node_archive_spec("v0.0.0")
    if spec is None:
        return None
    marker = "-".join(spec[1].split("-")[-2:])
    executable = "node.exe" if platform.system() == "Windows" else "bin/node"
    npm = "npm.cmd" if platform.system() == "Windows" else "bin/npm"
    candidates: list[tuple[tuple[int, ...], Path]] = []
    try:
        folders = _node_runtime_root().glob(f"node-v*-{marker}")
        for folder in folders:
            match = re.match(r"node-v(\d+(?:\.\d+)+)-", folder.name)
            if match and (folder / executable).is_file() and (folder / npm).is_file():
                candidates.append((tuple(int(part) for part in match.group(1).split(".")), folder))
    except OSError:
        return None
    return max(candidates, default=((), None), key=lambda item: item[0])[1]


def _managed_npm() -> Optional[str]:
    runtime = _managed_node_dir()
    if runtime is None:
        return None
    relative = "npm.cmd" if platform.system() == "Windows" else "bin/npm"
    return str(runtime / relative)


def activate_managed_cli_paths() -> None:
    """Make BlindPilot-managed Node and backend launchers usable this run."""
    runtime = _managed_node_dir()
    if runtime is not None:
        install_path._add_to_process_path(
            runtime if platform.system() == "Windows" else runtime / "bin"
        )
    managed_bin = _managed_npm_bin_dir()
    if managed_bin.is_dir():
        install_path._add_to_process_path(managed_bin)


def _find_npm() -> Optional[str]:
    return shutil.which("npm") or _managed_npm()


def _automatic_npm_install_available() -> bool:
    return _find_npm() is not None or _node_archive_spec("v0.0.0") is not None


def _fetch_url_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": f"BlindPilot/{_app_version()}"})
    with open_url(request, timeout=30) as response:
        return response.read()


def _safe_extract_node_archive(archive_path: Path, destination: Path) -> None:
    """Extract one verified Node archive without permitting path traversal."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    if archive_path.suffix.casefold() == ".zip":
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                target = (destination / member.filename).resolve()
                if os.path.commonpath((str(root), str(target))) != str(root):
                    raise OSError("The Node.js archive contains an unsafe path.")
            archive.extractall(destination)
        return
    with tarfile.open(archive_path, mode="r:gz") as archive:
        archive.extractall(destination, filter="data")


def install_portable_node(log: Callable[[str], None]) -> Optional[str]:
    """Install the latest Node LTS and npm for this user, with no elevation."""
    existing = _managed_npm()
    if existing:
        node_bin = Path(existing).parent
        try:
            changed = install_path.ensure_on_path(node_bin)
            if changed:
                log(f"Added {node_bin} to {changed}.")
        except OSError as exc:
            log(f"Node.js is installed, but adding it to PATH failed: {exc}")
        return existing
    if _node_archive_spec("v0.0.0") is None:
        log(
            f"Automatic Node.js installation is not available for "
            f"{platform.system()} {platform.machine()}."
        )
        return None

    log("Node.js and npm were not found. Installing the latest Node.js LTS for this user.")
    try:
        releases = json.loads(_fetch_url_bytes(NODE_RELEASE_INDEX_URL).decode("utf-8"))
        release = next(
            item
            for item in releases
            if isinstance(item, dict)
            and item.get("lts")
            and isinstance(item.get("version"), str)
            and int(item["version"].lstrip("v").split(".", 1)[0]) >= _NODE_MINIMUM_MAJOR
        )
        version = release["version"]
        archive_name, extracted_name = _node_archive_spec(version) or ("", "")
        if not archive_name:
            raise OSError("No official Node.js archive is available for this computer.")
        release_url = f"{NODE_RELEASE_BASE_URL}/{version}"
        checksums = _fetch_url_bytes(f"{release_url}/SHASUMS256.txt").decode("utf-8")
        checksum = next(
            line.split()[0] for line in checksums.splitlines() if line.split()[1:] == [archive_name]
        )
    except (OSError, ValueError, StopIteration, urllib.error.URLError) as exc:
        log(f"Could not discover the current Node.js LTS release: {exc}")
        return None

    runtime_root = _node_runtime_root()
    destination = runtime_root / extracted_name
    if destination.is_dir():
        npm = destination / ("npm.cmd" if platform.system() == "Windows" else "bin/npm")
        return str(npm) if npm.is_file() else None

    try:
        runtime_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="install-", dir=runtime_root) as temporary:
            temporary_path = Path(temporary)
            archive_path = temporary_path / archive_name
            log(f"Downloading Node.js {version} from nodejs.org...")
            request = urllib.request.Request(
                f"{release_url}/{archive_name}",
                headers={"User-Agent": f"BlindPilot/{_app_version()}"},
            )
            digest = hashlib.sha256()
            with (
                open_url(request, timeout=60) as response,
                open(archive_path, "wb") as output,
            ):
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
            if digest.hexdigest().casefold() != checksum.casefold():
                raise OSError("The downloaded Node.js archive failed SHA-256 verification.")
            extract_root = temporary_path / "extracted"
            _safe_extract_node_archive(archive_path, extract_root)
            extracted = extract_root / extracted_name
            if not extracted.is_dir():
                raise OSError("The Node.js archive did not contain its expected folder.")
            try:
                os.replace(extracted, destination)
            except FileExistsError:
                pass  # Another install thread completed the same release first.
    except (OSError, tarfile.TarError, zipfile.BadZipFile, urllib.error.URLError) as exc:
        log(f"Node.js installation failed: {exc}")
        return None

    npm_path = destination / ("npm.cmd" if platform.system() == "Windows" else "bin/npm")
    node_bin = destination if platform.system() == "Windows" else destination / "bin"
    if not npm_path.is_file():
        log("Node.js was extracted, but npm was missing from the installed runtime.")
        return None
    try:
        changed = install_path.ensure_on_path(node_bin)
        if changed:
            log(f"Added {node_bin} to {changed}.")
    except OSError as exc:
        log(f"Node.js was installed, but adding it to PATH failed: {exc}")
    log(f"Installed Node.js and npm: {npm_path}")
    return str(npm_path)


def _npm_environment(npm: str) -> dict[str, str]:
    """npm's own directory first, then everything a terminal would have.

    npm is itself a shim that has to find `node`, so it fails from the macOS
    Dock for exactly the reason the provider CLIs do.
    """
    env = subprocess_env(npm)
    env.update(
        {
            "CI": "1",
            "npm_config_yes": "true",
            "npm_config_progress": "false",
            "npm_config_fund": "false",
            "npm_config_audit": "false",
        }
    )
    return env


def _npm_install_argv(backend: str, latest: bool = False) -> Optional[List[str]]:
    """The npm command that installs a backend, or None if npm is unavailable.

    `latest` pins the package's latest tag, which is what an update wants.
    """
    package = BACKENDS[normalize_backend(backend)].npm_package
    npm = _find_npm()
    if not package or not npm:
        return None
    return [
        npm,
        "install",
        "--global",
        "--prefix",
        str(_managed_npm_prefix()),
        f"{package}@latest" if latest else package,
    ]


def _npm_update_argv(backend: str) -> Optional[List[str]]:
    return _npm_install_argv(backend, latest=True)


def _managed_backend_binary(backend: str) -> Optional[str]:
    executable = BACKENDS[normalize_backend(backend)].executable
    suffixes = (".exe", ".cmd", ".ps1", "") if platform.system() == "Windows" else ("",)
    return next(
        (
            str(candidate)
            for suffix in suffixes
            if (candidate := _managed_npm_bin_dir() / f"{executable}{suffix}").is_file()
        ),
        None,
    )


def _run_logged_process(
    argv: List[str], log: Callable[[str], None], env: Optional[dict[str, str]] = None
) -> Optional[int]:
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env if env is not None else subprocess_env(argv[0]),
            **no_window_kwargs(),
        )
    except OSError as exc:
        log(f"The installer could not be started: {exc}")
        return None
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            log(line)
    return proc.wait()
