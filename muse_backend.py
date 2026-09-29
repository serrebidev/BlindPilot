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

import base64
import json
import os
import platform
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional, Sequence

from hermes_backend import (
    StdioTransport,
    _no_window_kwargs,
    _text_output_kwargs,
    wsl_exe,
    windows_path_to_wsl,
)

# Where the session index lives when Muse runs on this machine. $XDG_DATA_HOME
# wins when set, else ~/.local/share -- which third-party indexers confirm is
# also where macOS keeps its sessions; Application Support holds only
# ancillary state there, but is still guessed before a live host is asked.
# On Windows Muse runs inside WSL, so none of these exist and the history
# reader goes through the WSL bridge below instead.
_MUSE_MAC_DATA_DIR = ("Library", "Application Support", "muse")

# The launcher is a plain bash script installed by the official installer.
# Windows cannot run it (its platform check is Darwin/Linux), which is why a
# Windows desktop reaches Muse inside WSL; macOS and Linux run it directly.
MUSE_LAUNCHER = "muse"

# `model/list` needs a live MSP host to ask, and a host costs a WSL process
# on Windows; the answer is cached per machine for a while instead. This is
# a preference cache rather than the truth: the catalog changes with Muse
# releases, and the picker always re-reads it when the cache has expired.
MODEL_CACHE_SECONDS = 900.0

# How long a live MSP host gets to answer one request of the catalog's. Two
# of them are asked -- initialize, then model/list -- and a host that has
# started at all answers both in well under a second; the budget is for a
# cold distribution, not for thinking.
MODEL_QUERY_TIMEOUT = 20.0

# How long any single CLI probe may take. `muse --version` is instant once
# the launcher has resolved itself, but its first run under a cold WSL
# distribution has to boot the distribution first, which is seconds.
CLI_PROBE_TIMEOUT = 45

# MSP's ReasoningEffort enum, the closed vocabulary `turn/start` accepts
# (MSP schema; 1.3.0 added "max"). The CLI's own list is filtered to this.
_MSP_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"})

# --------------------------------------------------------------------------
# Locating Muse
# --------------------------------------------------------------------------


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
            [*command, "--version"],
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
    global _WSL_MUSE, _WSL_MUSE_CHECKED, _HOME_CACHE, _HOME_CHECKED
    _WSL_MUSE = None
    _WSL_MUSE_CHECKED = False
    _HOME_CACHE = None
    _HOME_CHECKED = False


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


def _await_reply(transport: StdioTransport, request_id: int, timeout: float) -> Optional[dict]:
    """The answer to one request off a freshly opened host, or None."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = transport.receive(0.5)
        if frame is None:
            if not transport.connected():
                return None
            continue
        if frame.get("id") == request_id:
            return frame
    return None


def _open_host(
    command: Sequence[str], cwd: Optional[str], timeout: float = MODEL_QUERY_TIMEOUT
) -> tuple[Optional[StdioTransport], Optional[dict]]:
    """A `muse serve` that answered initialize, with what initialize said.

    Either both -- an open host and its initialize result -- or neither; a
    host that never answers is closed here rather than handed back half-open.
    """
    transport = StdioTransport(cwd or str(Path.home()), argv=[*command, "serve"], peer="Muse Code")
    try:
        transport.start()
    except OSError:
        return None, None
    transport.send(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            # MSP requires clientInfo.name to match ^[a-z0-9_]+$: a
            # mixed-case display name is invalid params.
            "params": {"clientInfo": {"name": "blindpilot", "version": "1"}},
        }
    )
    reply = _await_reply(transport, 1, timeout)
    if reply is None:
        transport.close()
        return None, None
    transport.send({"jsonrpc": "2.0", "method": "initialized"})
    result = reply.get("result")
    return transport, result if isinstance(result, dict) else None


_catalog_lock = threading.Lock()
_catalog_cache: tuple[float, tuple[list[str], list[str], str, str]] | None = None


def muse_model_catalog(cwd: Optional[str] = None) -> tuple[list[str], list[str], str, str]:
    """(models, efforts, current model, current effort) from a live MSP host.

    The catalog comes from `model/list` on a real `muse serve`, reached over
    the same stdio transport a turn uses, which is the same request the
    picker's model switch is served by. Effort levels are the protocol's
    ReasoningEffort enum, read from `muse --help`, which documents them
    rather than asking a model.

    Returns empty lists rather than raising: a Muse that will not start --
    a cold WSL distribution, a broken install -- is reported by the caller
    as "no catalog", not by this function crashing.
    """
    global _catalog_cache
    with _catalog_lock:
        cached = _catalog_cache
        if cached is not None and time.monotonic() - cached[0] < MODEL_CACHE_SECONDS:
            return cached[1]

    command = muse_command(cwd)
    if not command:
        return [], [], "", ""
    transport, _init = _open_host(command, cwd)
    if transport is None:
        return [], [], "", ""
    try:
        transport.send({"jsonrpc": "2.0", "id": 2, "method": "model/list"})
        reply = _await_reply(transport, 2, MODEL_QUERY_TIMEOUT)
    finally:
        transport.close()

    models: list[str] = []
    current = ""
    if isinstance(reply, dict) and isinstance(reply.get("result"), dict):
        rows = reply["result"].get("models")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                # The id, not displayLabel: the pick goes back to Muse as
                # modelId, and the two are free to differ.
                label = str(row.get("modelId") or "").strip()
                if label and label not in models:
                    models.append(label)
                if row.get("isDefault") and not current:
                    current = label
    efforts = _muse_effort_levels(command)
    with _catalog_lock:
        _catalog_cache = (time.monotonic(), (models, efforts, current, ""))
    return models, efforts, current, ""


def _muse_effort_levels(command: Sequence[str]) -> list[str]:
    """Reasoning effort levels out of `muse --help`, kept to the protocol's.

    The CLI documents its own enum (measured 1.0.3:
    "none|minimal|low|medium|high|xhigh|max|ultra"), and reading the CLI rather
    than hardcoding means a release that widens it is picked up without a
    change here. But `turn/start` sends the value over MSP, whose
    ReasoningEffort is closed (it lacked "max" until 1.3.0): a tier the protocol would
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


# --------------------------------------------------------------------------
# Session history store
# --------------------------------------------------------------------------


def _muse_data_dirs() -> list[Path]:
    """The directories that can hold Muse's session store on this machine."""
    roots: list[Path] = []
    override = os.environ.get("XDG_DATA_HOME", "").strip()
    if override:
        roots.append(Path(override).expanduser())
    roots.append(Path.home() / ".local" / "share")
    if platform.system() == "Darwin":
        roots.append(Path.home().joinpath(*_MUSE_MAC_DATA_DIR).parent)
    seen: list[Path] = []
    for root in roots:
        candidate = root / "muse"
        if candidate not in seen:
            seen.append(candidate)
    return seen


def muse_index_db() -> Optional[Path]:
    """Muse's session index on this machine, or None when it is not here.

    The guessed directories first; when none of them holds a store, a live
    host is asked where its data lives, which survives a layout nobody has
    seen. On Windows the store lives inside the WSL distribution Muse runs
    in, so this answers None and the history reader queries through the
    bridge.
    """
    for data in _muse_data_dirs():
        path = data / "session-index.db"
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    home = _muse_home_live()
    if home:
        path = Path(home) / "session-index.db"
        try:
            if path.is_file():
                return path
        except OSError:
            pass
    return None


_HOME_CACHE: Optional[str] = None
_HOME_CHECKED = False


def _muse_home_live() -> Optional[str]:
    """Where a live host keeps its data (initialize.museHome), asked once.

    The offline guesses cover every layout seen so far; this is the fallback
    that survives one nobody has seen, on any platform Muse runs on. Cached
    like the other discovery answers. Windows is excluded: the host there
    would answer a WSL path this process cannot open, and the history reader
    goes through the WSL bridge instead.
    """
    global _HOME_CACHE, _HOME_CHECKED
    if _HOME_CHECKED:
        return _HOME_CACHE
    _HOME_CHECKED = True
    if platform.system() == "Windows":
        return None
    command = muse_command()
    if not command:
        return None
    transport, init = _open_host(command, None)
    if transport is None:
        return None
    try:
        home = (init or {}).get("museHome")
        _HOME_CACHE = str(home) if home else None
    finally:
        transport.close()
    return _HOME_CACHE


def wsl_muse_index_db() -> Optional[str]:
    """The session index path as WSL sees it, or None without WSL."""
    launcher = wsl_exe()
    if not launcher:
        return None
    probe = (
        'd="${XDG_DATA_HOME:-$HOME/.local/share}/muse"; p="$d/session-index.db"; '
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


def wsl_muse_sqlite_query(sql: str, params: Sequence = ()) -> list[dict]:
    """Run one read-only query against Muse's WSL store, from inside WSL.

    The same JSON-over-stdin technique as Hermes' ``wsl_sqlite_query``: the
    store may be in WAL mode, which SQLite cannot read over the
    \\\\wsl.localhost share, and no value is ever quoted into shell code.
    """
    launcher = wsl_exe()
    store = wsl_muse_index_db()
    if not launcher or not store:
        return []
    reader = (
        "import json,sqlite3,sys\n"
        "req=json.load(sys.stdin)\n"
        "db=sqlite3.connect('file:'+req['db']+'?mode=ro',uri=True,timeout=5.0)\n"
        "db.row_factory=sqlite3.Row\n"
        "try:\n"
        "    rows=[dict(r) for r in db.execute(req['sql'],tuple(req['params'])).fetchall()]\n"
        "finally:\n"
        "    db.close()\n"
        "json.dump(rows,sys.stdout,default=str)\n"
    )
    payload = json.dumps({"db": store, "sql": sql, "params": list(params)})
    try:
        proc = subprocess.run(
            [launcher, "-e", "python3", "-c", reader],
            input=payload,
            capture_output=True,
            timeout=25,
            **_text_output_kwargs(),
            **_no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    try:
        rows = json.loads(proc.stdout or "[]")
    except ValueError:
        return []
    return rows if isinstance(rows, list) else []


# How much of a WSL-side session log is fetched. A transcript past this is a
# runaway one, and the parser tolerates the cut: every line stands alone, so a
# truncated tail only drops the last partial line.
_WSL_LOG_CAP_BYTES = 32 * 1024 * 1024


def muse_session_log_text(path: str) -> str:
    """The whole of one session.jsonl, from here or through WSL.

    Empty when the log cannot be read. A WSL-side path is fetched with ``cat``
    rather than opened: on Windows the path names a file inside the
    distribution, which this process cannot open itself.
    """
    if not path:
        return ""
    try:
        if Path(path).is_file():
            return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    launcher = wsl_exe()
    if not launcher:
        return ""
    try:
        proc = subprocess.run(
            [launcher, "-e", "sh", "-lc", f'head -c {_WSL_LOG_CAP_BYTES} -- "{path}"'],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=25,
            **_text_output_kwargs(),
            **_no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout or ""


# Bounds for the transcript parser. A conversation past either is not replayed
# whole: the picker only needs enough to read back, not a runaway log.
_MUSE_MAX_TURNS = 2000
_MUSE_MAX_RESPONSE_CHARS = 200_000


def parse_muse_transcript(text: str) -> list[tuple[str, str]]:
    """(prompt, response) turns from one session.jsonl dump.

    The log is an event envelope per line; only the run events carry the
    conversation. A ``started`` event opens a turn with the submitted prompt, an
    ``assistant_message_committed`` appends to its answer (commentary and final
    alike), and a steered prompt arrives as an ``inbox_item_queued`` whose
    source is ``user_steer``. Everything else -- tool calls, reasoning,
    reminders, retained frames -- is Muse's bookkeeping, not the conversation.
    """
    turns: list[list[str]] = []
    for line in (text or "").splitlines():
        if '"run"' not in line and "user_steer" not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("kind") != "run":
            # A steer arrives outside a run event; anything else mentioning
            # user_steer is a quoted string inside another payload.
            event = payload.get("event")
            if isinstance(event, dict) and event.get("kind") == "inbox_item_queued":
                prompt = _muse_steer_prompt(event)
                if prompt:
                    turns.append([prompt, ""])
            continue
        event = payload.get("event")
        if not isinstance(event, dict):
            continue
        kind = event.get("kind")
        if kind == "started":
            started_prompt = event.get("prompt")
            if isinstance(started_prompt, str) and started_prompt.strip():
                turns.append([started_prompt, ""])
        elif kind == "assistant_message_committed":
            text_part = event.get("text")
            if not isinstance(text_part, str) or not text_part.strip():
                continue
            if not turns:
                turns.append(["", ""])
            previous = turns[-1][1]
            joined = text_part if not previous else previous + "\n\n" + text_part
            turns[-1][1] = joined[:_MUSE_MAX_RESPONSE_CHARS]
        if len(turns) >= _MUSE_MAX_TURNS:
            break
    return [(prompt, response) for prompt, response in turns]


def _muse_steer_prompt(event: dict) -> str:
    """The user text of a steered prompt, or "" when this is not one.

    Background and scheduled runtime deliveries share the inbox kind, so the
    source is checked rather than assumed: only ``user_steer`` is quoted.
    """
    source = event.get("source")
    if isinstance(source, dict):
        if source.get("source") != "user_steer":
            return ""
    elif source != "user_steer":
        return ""
    inner = event.get("payload")
    if isinstance(inner, dict):
        prompt = inner.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            return prompt
    body = event.get("body")
    return body if isinstance(body, str) and body.strip() else ""


# --------------------------------------------------------------------------
# Image attachments
# --------------------------------------------------------------------------

# Magic bytes to media types. MSP takes the bytes as base64 with the media
# type alongside; dimensions are optional and are not sent.
_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

# Anything past this is attached by path rather than by bytes: the model gets
# a file it can open instead of a payload that dwarfs the prompt.
_MAX_IMAGE_BYTES = 8 * 1024 * 1024


def muse_image_part(path: str) -> Optional[dict]:
    """One file as an MSP image input part, or None when it is not one.

    Only image bytes qualify; anything else -- a document, an unreadable file,
    a picture past the cap -- is named in the prompt text instead.
    """
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    if not raw or len(raw) > _MAX_IMAGE_BYTES:
        return None
    media = ""
    for magic, kind in _IMAGE_MAGIC:
        if raw.startswith(magic):
            media = kind
            break
    if not media and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        media = "image/webp"
    if not media:
        return None
    return {
        "type": "image",
        "mediaType": media,
        "base64Data": base64.b64encode(raw).decode("ascii"),
    }


# --------------------------------------------------------------------------
# Skills for the slash picker
# --------------------------------------------------------------------------

_skills_lock = threading.Lock()
_skills_cache: dict[str, tuple[float, list[tuple[str, str]]]] = {}

# Skill lists change when skills are installed, not while a picker is open.
SKILLS_CACHE_SECONDS = 300.0


def muse_skills(cwd: Optional[str] = None) -> list[tuple[str, str]]:
    """(selector, description) skills Muse offers in one directory.

    Asked of the CLI, which answers offline from its bundled, user, and
    project skills. Empty rather than raising when Muse is not there.
    """
    key = str(Path(cwd).resolve()) if cwd else ""
    now = time.monotonic()
    with _skills_lock:
        cached = _skills_cache.get(key)
        if cached is not None and now - cached[0] < SKILLS_CACHE_SECONDS:
            return list(cached[1])
    rows = _muse_skills_live(cwd)
    with _skills_lock:
        if len(_skills_cache) > 32:
            _skills_cache.clear()
        _skills_cache[key] = (now, rows)
    return list(rows)


def _muse_skills_live(cwd: Optional[str]) -> list[tuple[str, str]]:
    command = muse_command(cwd)
    if not command:
        return []
    try:
        proc = subprocess.run(
            [*command, "skills", "list", "--json"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=CLI_PROBE_TIMEOUT,
            **_text_output_kwargs(),
            **_no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    try:
        payload = json.loads(proc.stdout or "{}")
    except ValueError:
        return []
    skills = payload.get("skills")
    if not isinstance(skills, list):
        return []
    rows: list[tuple[str, str]] = []
    for entry in skills:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name or "/" in name or " " in name:
            continue
        detail = str(entry.get("short_description") or "").strip()
        if not detail:
            first = str(entry.get("description") or "").strip().splitlines()
            detail = first[0] if first else ""
        if len(detail) > 120:
            detail = detail[:119].rstrip() + "…"
        rows.append((name, detail or f"Run Muse's {name} skill"))
    rows.sort(key=lambda row: row[0].lower())
    return rows


# --------------------------------------------------------------------------
# Last-observed subscription usage
# --------------------------------------------------------------------------

_usage_lock = threading.Lock()
_usage_payload: Optional[dict] = None


def note_muse_usage(payload: object) -> None:
    """Remember the subscription usage one of this app's turns observed.

    ``usage/read`` answers what its own host process has seen, so a fresh host
    asked out of nowhere observes nothing; the worker stashes what its live
    host saw at the end of every turn instead, and /status reads it back.
    """
    if not isinstance(payload, dict) or not payload:
        return
    if not isinstance(payload.get("window"), dict) and not isinstance(payload.get("weekly"), dict):
        return
    with _usage_lock:
        global _usage_payload
        _usage_payload = dict(payload)


def muse_usage_payload() -> Optional[dict]:
    """The stashed usage, or None once both of its windows have reset."""
    global _usage_payload
    with _usage_lock:
        payload = dict(_usage_payload) if _usage_payload else None
    if payload is None:
        return None
    now_ms = time.time() * 1000
    resets = [
        block.get("resetsAtMs")
        for key in ("window", "weekly")
        if isinstance(payload.get(key), dict)
        for block in (payload[key],)
    ]
    stamps = [float(stamp) for stamp in resets if isinstance(stamp, (int, float))]
    if stamps and all(stamp <= now_ms for stamp in stamps):
        with _usage_lock:
            _usage_payload = None
        return None
    return payload
