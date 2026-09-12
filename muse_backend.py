"""Muse Code backend for BlindPilot.

Muse Code is Meta's terminal coding agent, installed by the one-line
``curl -fsSL https://dev.meta.ai/install.sh | bash``. Its installer and its
binaries ship for macOS and Linux only, so on Windows the CLI is reached
inside WSL with the same bridge Hermes uses: ``wsl.exe -e`` connects this
process's stdin and stdout straight through to the child, and only the
working directory needs translating on the way over.

The adapter drives Muse's own host protocol, MSP ("Muse Session Protocol"):
JSON-RPC 2.0, one object per line, over the stdio of ``muse serve``. The wire
surface used here -- initialize, session/start + session/resume,
turn/start + turn/steer + turn/cancel, model/list, approval/decide,
userInput/answer, session/compact -- is the stable surface of MSP v1
(schema fingerprint sha256:03312c21... at Muse 1.0.3).

Discovery, authentication status, and the model catalog are asked of the
CLI where a subcommand exists and read off disk where it does not; the
shapes of those answers are measured against Muse 1.0.3 and are allowed to
get better rather than required to stay.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
Based on the original Claude Code Reader application by doubletaponair:
https://github.com/doubletaponair/claude-code-reader
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import certificates  # noqa: F401 - kept for symmetry with hermes_backend; Muse is local-only.
from hermes_backend import (
    _no_window_kwargs,
    _text_output_kwargs,
    wsl_exe,
    windows_path_to_wsl,
)

# The launcher is a plain bash script installed by the official installer.
# Windows cannot run it (its platform check is Darwin/Linux), which is why a
# Windows desktop reaches Muse inside WSL; macOS and Linux run it directly.
MUSE_LAUNCHER = "muse"

# Muse stores its account credentials here after `muse login` completes,
# measured on 1.0.3. ~/.config/muse/auth.json, honouring XDG_CONFIG_HOME.
MUSE_CREDENTIAL_RELPATH = ("muse", "auth.json")

# `model/list` needs a live MSP host to ask, and a host costs a WSL process
# on Windows; the answer is cached per machine for a while instead. This is
# a preference cache rather than the truth: the catalog changes with Muse
# releases, and the picker always re-reads it when the cache has expired.
MODEL_CACHE_SECONDS = 900.0

# How long any single CLI probe may take. `muse --version` is instant once
# the launcher has resolved itself, but its first run under a cold WSL
# distribution has to boot the distribution first, which is seconds.
CLI_PROBE_TIMEOUT = 45

# MSP's ReasoningEffort enum, the closed vocabulary `turn/start` accepts
# (MSP schema at Muse 1.0.3). The CLI's own list is filtered to this.
_MSP_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "ultra"})


def _no_window() -> dict:
    """Kept name for callers that read this module's own helpers."""
    return _no_window_kwargs()


# --------------------------------------------------------------------------
# Locating Muse
# --------------------------------------------------------------------------


def _wsl_available() -> bool:
    """Whether this is Windows with a WSL launcher present."""
    return platform.system() == "Windows" and wsl_exe() is not None


def wsl_muse_path(timeout: int = CLI_PROBE_TIMEOUT) -> Optional[str]:
    """Muse's launcher path inside WSL, or None when there is no Muse there.

    Cached for the life of the process, like the other WSL answers: every
    backend check would otherwise boot a WSL distribution to ask the same
    question, and the answer cannot change while the application is running.
    """
    global _WSL_MUSE, _WSL_MUSE_CHECKED
    if _WSL_MUSE_CHECKED:
        return _WSL_MUSE
    _WSL_MUSE_CHECKED = True
    launcher = wsl_exe()
    if not launcher:
        return None
    try:
        proc = subprocess.run(
            [launcher, "-e", "sh", "-lc", 'command -v muse || ls "$HOME/.local/bin/muse"'],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            **_text_output_kwargs(),
            **_no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    found = (proc.stdout or "").strip().splitlines()
    _WSL_MUSE = found[0].strip() if found and found[0].strip() else None
    return _WSL_MUSE


# Cached answers; see wsl_muse_path for why they are cached.
_WSL_MUSE: Optional[str] = None
_WSL_MUSE_CHECKED = False


def muse_cli_path() -> Optional[str]:
    """The launcher's path as a report can show it, or None when not installed.

    On Windows this is a path inside the WSL distribution, not a Windows one;
    the report says so by being a path a Windows process could not run, and
    the callers that must run it go through ``muse_command`` instead.
    """
    if platform.system() == "Windows":
        return wsl_muse_path()
    from shutil import which

    return which(MUSE_LAUNCHER)


def muse_installed() -> bool:
    """Whether Muse can actually be driven from this machine.

    On macOS and Linux that is the launcher on PATH. On Windows the launcher
    cannot run, so the question becomes whether WSL has one -- which is the
    shape the machine this was written against actually has.
    """
    return muse_cli_path() is not None


def muse_version(timeout: int = CLI_PROBE_TIMEOUT) -> str:
    """What `muse --version` prints, asked through Muse's own bridge.

    `backend_status` cannot hand this "binary" to Popen on Windows (it is a
    bash script inside WSL), so the version is read the same way every other
    Muse command reaches the CLI.
    """
    command = muse_command()
    if not command:
        return ""
    try:
        proc = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            **_text_output_kwargs(),
            **_no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or "").strip() or (proc.stderr or "").strip()


def reset_discovery() -> None:
    """Forget every cached answer about where Muse is.

    The install and update flows run the official installer and then check
    whether a launcher appeared; without this the first probe's answer --
    taken before the installer ran -- would be kept for the life of the
    process and a successful install would read as a failed one.
    """
    global _WSL_MUSE, _WSL_MUSE_CHECKED
    _WSL_MUSE = None
    _WSL_MUSE_CHECKED = False


def muse_popen_wrapper() -> Optional["Callable[..., subprocess.Popen]"]:
    """A Popen that runs ``[muse, *args]`` where Muse can actually run.

    The login runner builds ``[binary, *login_args]`` and hands it to Popen.
    On Windows that binary is a bash script inside a WSL distribution, which
    Popen cannot execute, so the argv is rebuilt here as ``[wsl.exe, -e,
    <launcher>, *args]`` -- the same bridge every other Muse path uses.
    None where no rebuild is needed, so the caller keeps plain Popen.
    """
    if platform.system() != "Windows":
        return None
    launcher = wsl_exe()
    path = wsl_muse_path()
    if not launcher or not path:
        return None

    def popen(args: list[str], **kwargs: Any) -> subprocess.Popen:
        return subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [launcher, "-e", path, *args[1:]], **kwargs
        )

    return popen


def muse_command(cwd: Optional[str] = None) -> Optional[list[str]]:
    """The argv that runs `muse serve` (or any muse subcommand) from here.

    Returns None when Muse is not installed where this process can reach it.
    The working directory is translated and handed to WSL rather than given
    to Popen, because Popen only understands Windows paths -- and WSL's own
    ``--cd`` rejects a relative argument (measured: ``--cd .`` fails with
    ``Wsl/E_INVALIDARG``, printing its error on stdout, which would poison
    the protocol stream), so the directory is always made absolute.
    """
    if platform.system() == "Windows":
        path = wsl_muse_path()
        launcher = wsl_exe()
        if not path or not launcher:
            return None
        command = [launcher]
        if cwd:
            absolute = str(Path(cwd).resolve()) if cwd else ""
            command += ["--cd", windows_path_to_wsl(absolute)]
        command += ["-e", path]
        return command
    from shutil import which

    found = which(MUSE_LAUNCHER)
    if not found:
        return None
    return [found]


# --------------------------------------------------------------------------
# Credential reading
# --------------------------------------------------------------------------


def _wsl_credential_path() -> Optional[str]:
    """The credential file's path as WSL sees it, or None without WSL.

    Resolved inside WSL rather than guessed, so a distribution whose home is
    not /home/<user>, or one with XDG_CONFIG_HOME set, still answers.
    """
    launcher = wsl_exe()
    if not launcher:
        return None
    probe = (
        'd="${XDG_CONFIG_HOME:-$HOME/.config}"; p="$d/muse/auth.json"; '
        '[ -f "$p" ] && printf %s "$p"'
    )
    try:
        proc = subprocess.run(
            [launcher, "-e", "sh", "-lc", probe],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=25,
            **_text_output_kwargs(),
            **_no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    found = (proc.stdout or "").strip()
    return found or None


def _credential_path() -> Optional[str]:
    """Where Muse's credentials live, in this process's own terms."""
    if platform.system() == "Windows":
        return _wsl_credential_path()
    config = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    path = Path(config) / "muse" / "auth.json"
    return str(path) if path.is_file() else None


def muse_credentials() -> Optional[dict]:
    """The account Muse stored at sign-in, or None when there is none.

    Read off disk rather than asked of the CLI: the checks that need this run
    while the setup wizard is on screen, and starting an MSP host to ask would
    cost a process -- inside a WSL boot on Windows -- to read one file.
    """
    path = _credential_path()
    if not path:
        return None
    try:
        if platform.system() == "Windows":
            launcher = wsl_exe()
            if not launcher:
                return None
            # The file's bytes are read inside WSL, where the path means what
            # it meant when Muse wrote it.
            proc = subprocess.run(
                [launcher, "-e", "sh", "-lc", f'cat "{path}" 2>/dev/null'],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=25,
                **_text_output_kwargs(),
                **_no_window_kwargs(),
            )
            payload_text = proc.stdout if proc.returncode == 0 else ""
        else:
            payload_text = Path(path).read_text(encoding="utf-8")
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        payload = json.loads(payload_text or "{}")
    except ValueError:
        return None
    return payload if isinstance(payload, dict) and payload else None


def muse_signed_in() -> bool:
    """Whether a Meta account is signed in, read from what Muse stored.

    The credential file's shape is measured at 1.0.3 ({"providers": {...},
    "schema_version": 1}); any payload with real content under `providers`
    counts, so a future release that adds fields still answers "yes" and an
    empty or failed read answers "no" rather than guessing.
    """
    payload = muse_credentials()
    if not payload:
        return False
    providers = payload.get("providers")
    if isinstance(providers, dict) and providers:
        return True
    # Older or future shapes: anything with non-empty content counts, except
    # a schema_version alone, which says the file exists but nothing else.
    return any(key != "schema_version" for key in payload)


def _muse_session_log_tail(path: str, lines: int = 80) -> str:
    """Read the recent part of one Muse session log, wherever Muse runs."""
    if not path:
        return ""
    try:
        if platform.system() == "Windows":
            launcher = wsl_exe()
            if not launcher:
                return ""
            proc = subprocess.run(
                [launcher, "-e", "tail", "-n", str(lines), "--", path],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=10,
                **_text_output_kwargs(),
                **_no_window_kwargs(),
            )
            return proc.stdout or ""
        return "\n".join(
            Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _contains_http_402(value: object) -> bool:
    if isinstance(value, dict):
        return value.get("http_status") == 402 or any(_contains_http_402(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_http_402(v) for v in value)
    return False


def muse_session_access_error(path: str) -> str:
    """Explain Meta refusing a Spark inference request, if its log records it.

    Muse accepts a turn before it opens the provider stream. A signed-in account
    without Spark inference access receives HTTP 402 there, then Muse retries
    internally with increasing delays and emits nothing to MSP meanwhile.
    Reading its own durable session log is the only prompt signal that turns
    that otherwise looks like a stalled BlindPilot turn into an actionable
    result.
    """
    for line in reversed(_muse_session_log_tail(path).splitlines()):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if _contains_http_402(entry):
            return (
                "Muse Spark cannot answer because Meta refused this account (HTTP 402). "
                "Sign in with an account that has Muse Spark inference access, then try again."
            )
    return ""


# --------------------------------------------------------------------------
# Account lines for /status
# --------------------------------------------------------------------------


def muse_account_lines() -> list[str]:
    """The sign-in report Muse can give, as plain lines.

    Muse has no `auth status` subcommand; what it stored at sign-in is the
    answer, the same way FreeBuff's stored credentials are its answer.
    """
    payload = muse_credentials()
    if not payload:
        return ["Signed in: no"]
    lines = [f"Signed in: {'yes' if muse_signed_in() else 'no'}"]
    providers = payload.get("providers")
    if isinstance(providers, dict) and providers:
        names = sorted(str(name) for name in providers)
        lines.append(f"Connected providers: {', '.join(names)}")
    return lines


# --------------------------------------------------------------------------
# Model catalog
# --------------------------------------------------------------------------

_catalog_lock = threading.Lock()
_catalog_cache: tuple[float, tuple[list[str], list[str], str, str]] | None = None


def muse_model_catalog(
    cwd: Optional[str] = None, timeout: float = 90.0
) -> tuple[list[str], list[str], str, str]:
    """(models, efforts, current model, current effort) from a live MSP host.

    The catalog comes from `model/list` on a real `muse serve`, which is the
    same request the picker's model switch is served by. Effort levels are
    the protocol's ReasoningEffort enum, read from `muse --help`, which
    documents them rather than asking a model.

    Returns empty lists rather than raising: a Muse that will not start --
    a cold WSL distribution, a broken install -- is reported by the caller
    as "no catalog", not by this function crashing.
    """
    global _catalog_cache
    import time

    with _catalog_lock:
        cached = _catalog_cache
        if cached is not None and time.monotonic() - cached[0] < MODEL_CACHE_SECONDS:
            return cached[1]

    command = muse_command(cwd)
    if not command:
        return [], [], "", ""
    models: list[str] = []
    current = ""
    try:
        proc = subprocess.Popen(
            [*command, "serve"],
            # On Windows the working directory was handed to WSL in the argv;
            # Popen's own cwd would only accept a Windows path.
            cwd=None if platform.system() == "Windows" else cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            **_no_window_kwargs(),
        )
    except OSError:
        return [], [], "", ""
    import threading as _threading

    frames: list[dict] = []
    lock = _threading.Lock()
    got_catalog = _threading.Event()

    def _read() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            with lock:
                frames.append(frame)
                # Anything addressed or announced unblocks the writer; the
                # exact frame is picked out of `frames` by its id afterwards.
                got_catalog.set()

    reader = _threading.Thread(target=_read, daemon=True)
    reader.start()
    try:
        assert proc.stdin is not None
        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    # MSP requires clientInfo.name to match ^[a-z0-9_]+$: a
                    # mixed-case display name is invalid params.
                    "params": {"clientInfo": {"name": "blindpilot", "version": "1"}},
                }
            )
            + "\n"
        )
        proc.stdin.flush()
        if not _wait_event(got_catalog, 20.0):
            raise OSError("muse serve did not answer initialize")
        with lock:
            frames.clear()
        got_catalog.clear()
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "initialized"}) + "\n")
        proc.stdin.flush()
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "model/list"}) + "\n")
        proc.stdin.flush()
        _wait_event(got_catalog, 20.0)
        with lock:
            reply = next((f for f in frames if f.get("id") == 2), None)
    finally:
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

    if isinstance(reply, dict) and isinstance(reply.get("result"), dict):
        rows = reply["result"].get("models")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                label = str(row.get("displayLabel") or row.get("modelId") or "").strip()
                if label and label not in models:
                    models.append(label)
                if row.get("isDefault") and not current:
                    current = label
    efforts = _muse_effort_levels(command)
    with _catalog_lock:
        _catalog_cache = (time.monotonic(), (models, efforts, current, ""))
    return models, efforts, current, ""


def _wait_event(event: threading.Event, timeout: float) -> bool:
    return event.wait(timeout)


def _muse_effort_levels(command: Sequence[str]) -> list[str]:
    """Reasoning effort levels out of `muse --help`, kept to the protocol's.

    The CLI documents its own enum (measured 1.0.3:
    "none|minimal|low|medium|high|xhigh|max|ultra"), and reading the CLI rather
    than hardcoding means a release that widens it is picked up without a
    change here. But `turn/start` sends the value over MSP, whose
    ReasoningEffort is closed and has no "max": a tier the protocol would
    reject is offered to nobody, so the CLI's answer is filtered to what the
    protocol accepts. A CLI tier dropped here can still be picked inside Muse
    itself. (Measured order at 1.0.3: minimal, low, medium, high, xhigh --
    "none" is dropped by the whitespace test, which is fine: asking for no
    reasoning is a tier a BlindPilot user is unlikely to want.)
    """
    try:
        proc = subprocess.run(
            [*command, "--help"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=CLI_PROBE_TIMEOUT,
            **_text_output_kwargs(),
            **_no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    import re

    match = re.search(r"--reasoning-effort <EFFORT>\s*\n?\s*([^)]+)", proc.stdout or "")
    if not match:
        return []
    levels = [p.strip() for p in match.group(1).strip().rstrip(")").split("|")]
    return [level for level in levels if level and " " not in level and level in _MSP_EFFORTS]


def muse_model_options(
    cwd: Optional[str] = None,
) -> tuple[list[str], list[str], str, str, str]:
    """The picker's answer: (models, efforts, current model, current effort, error)."""
    try:
        models, efforts, current, current_effort = muse_model_catalog(cwd)
    except (OSError, ValueError):
        models, efforts, current, current_effort = [], [], "", ""
    error = "" if models else "Muse did not answer the model catalog request."
    return models, efforts, current, current_effort, error


# Keep the module importable without certificates on a tree where it moves;
# see the noqa above.
_ = certificates
