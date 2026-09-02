"""Hermes Agent backend for BlindPilot.

Hermes Agent (https://github.com/NousResearch/hermes-agent) speaks several
protocols. This adapter uses its TUI gateway JSON-RPC, which Hermes documents
as the protocol for "custom hosts that want fine-grained control of sessions,
slash commands, approvals, and streaming events" — everything a screen-reader
front end needs, and the one Hermes protocol that runs over *both* a local
pipe and a network socket.

That last property is why this adapter uses the TUI gateway rather than
Hermes' ACP mode. ACP is stdio-only, so a remote Hermes would have needed a
second, unrelated protocol and a second worker. Here one worker drives both:

    local   -- ``python -m tui_gateway.entry`` over a pipe, nothing to configure
    remote  -- the same JSON-RPC over a WebSocket to ``hermes serve``

The wire format is identical on both paths — one JSON object per line, no
Content-Length framing — because Hermes' own TUI parses stdout lines and
WebSocket frames with the same call. So :class:`HermesWorker` is written
against a transport interface and never learns which one it got.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
Based on the original Claude Code Reader application by doubletaponair:
https://github.com/doubletaponair/claude-code-reader
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import json
import os
import platform
import queue
import shutil
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Optional, Protocol, Sequence

# Hermes' launcher is a plain executable on PATH, but the gateway itself is a
# Python module inside Hermes' own virtual environment. A GUI cannot rely on
# either being importable, so both are located on disk instead.
HERMES_SOURCE_DIRNAME = "hermes-agent"
HERMES_GATEWAY_MODULE = "tui_gateway.entry"

# Hermes writes warnings to stderr that have nothing to do with the protocol:
# SQLite build advisories, optional-tool notices, MCP server descriptions.
# Treating those as failures would break sessions that are working fine, so
# stderr is only ever used to explain an exit that already happened.
STDERR_KEEP_LINES = 20


def _no_window_kwargs() -> dict:
    """``subprocess`` keyword arguments that keep children off the screen."""
    if platform.system() == "Windows":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


def _text_output_kwargs() -> dict:
    """Decode a child's output as UTF-8, whatever the console code page is.

    Windows defaults to the legacy ANSI code page here, which raises
    UnicodeDecodeError the moment a child prints a byte outside it -- Hermes'
    own banner is enough to trigger it. Everything read from Hermes is UTF-8,
    so that is what gets asked for, and undecodable bytes are replaced rather
    than allowed to abort a check.
    """
    return {"text": True, "encoding": "utf-8", "errors": "replace"}


# --------------------------------------------------------------------------
# Locating Hermes
# --------------------------------------------------------------------------


def hermes_home() -> Path:
    """Where Hermes keeps its configuration, sessions, and installed source."""
    override = os.environ.get("HERMES_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".hermes"


def hermes_source_root() -> Optional[Path]:
    """The Hermes source tree, which is what ``-m tui_gateway.entry`` needs.

    Hermes installs itself under its home directory rather than into the
    interpreter running BlindPilot, so importing it is not an option: the
    gateway has to be launched as a subprocess out of that tree.
    """
    candidates = [hermes_home() / HERMES_SOURCE_DIRNAME]
    if platform.system() == "Windows":
        # Where the official Windows installer puts the source tree, measured:
        # %LOCALAPPDATA%\hermes\hermes-agent, with HERMES_HOME set
        # persistently for the shells that start afterwards. A process that
        # was already running when the installer finished -- this application,
        # during a wizard-driven install -- has the old environment, so the
        # default location is checked on disk rather than trusted from it.
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        candidates.append(local / "hermes" / HERMES_SOURCE_DIRNAME)
    launcher = shutil.which("hermes")
    if launcher:
        # A packaged install ships the launcher next to the venv that holds the
        # source, so walk up from it before giving up.
        here = Path(launcher).resolve().parent
        candidates.extend([here.parent, here.parent.parent])
    for candidate in candidates:
        if (candidate / HERMES_GATEWAY_MODULE.split(".")[0]).is_dir():
            return candidate
    return None


def hermes_python() -> Optional[str]:
    """The interpreter that can import Hermes, i.e. the one in its venv."""
    root = hermes_source_root()
    if root is None:
        return None
    names = ("python.exe", "python") if platform.system() == "Windows" else ("python3", "python")
    for directory in ("Scripts", "bin"):
        for name in names:
            candidate = root / "venv" / directory / name
            if candidate.is_file():
                return str(candidate)
    return None


def find_hermes_cli() -> Optional[str]:
    """Hermes' own launcher, used for version and authentication checks."""
    found = shutil.which("hermes")
    if found:
        return found
    home = Path.home()
    venv_roots = [hermes_home()]
    if platform.system() == "Windows":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        # The official Windows installer's bin directory -- measured, next to
        # the managed uv it installs -- ahead of the older guesses.
        candidates = [
            local / "hermes" / "bin" / "hermes.exe",
            local / "hermes" / "bin" / "hermes",
            home / ".local" / "bin" / "hermes.exe",
            home / ".local" / "bin" / "hermes",
            local / "Programs" / "hermes" / "hermes.exe",
        ]
        venv_roots.append(local / "hermes")
    else:
        candidates = [
            home / ".local" / "bin" / "hermes",
            Path("/usr/local/bin/hermes"),
            Path("/opt/homebrew/bin/hermes"),
        ]
    # The gateway's own venv launcher, which the older code only looked for
    # through `bin/hermes` -- a POSIX layout. On Windows the venv is
    # `Scripts\hermes.exe`, so a complete install looked incomplete.
    for base in venv_roots:
        venv = base / HERMES_SOURCE_DIRNAME / "venv"
        if platform.system() == "Windows":
            candidates.extend([venv / "Scripts" / "hermes.exe", venv / "Scripts" / "hermes"])
        else:
            candidates.append(venv / "bin" / "hermes")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def hermes_installed() -> bool:
    """Whether a local Hermes can actually be driven, not merely found.

    The launcher alone is not enough: the gateway is a module in Hermes' venv,
    so a copy with a launcher but no importable source cannot run a session.
    """
    return hermes_python() is not None or wsl_hermes_available()


# --------------------------------------------------------------------------
# Hermes inside WSL
#
# A very common arrangement on Windows: the desktop is a Windows program, but
# Hermes lives in a WSL distribution, where it was installed with the same
# one-line installer as on Linux. Nothing in Windows' PATH points at it, so
# without this it looks like Hermes is not installed at all -- which is what a
# Windows machine with a perfectly good Hermes reported before this existed.
#
# The gateway speaks over stdin and stdout, and "wsl.exe -e" connects those
# straight through, so the same protocol works with no translation. Paths are
# the one thing that does not carry across, which is why the working directory
# is converted below rather than passed as-is.
# --------------------------------------------------------------------------


# Cached because every backend check would otherwise start a WSL process, and
# the answer cannot change while the app is running.
_WSL_HERMES: Optional[str] = None
_WSL_CHECKED = False

# How long a history query inside WSL may take. The dialog runs this on the GUI
# thread, so it has to fail rather than freeze the window.
WSL_QUERY_TIMEOUT = 20.0


def wsl_exe() -> Optional[str]:
    """Windows' WSL launcher, or None when this is not Windows."""
    if platform.system() != "Windows":
        return None
    return shutil.which("wsl.exe") or shutil.which("wsl")


def wsl_hermes_path(timeout: int = 20) -> Optional[str]:
    """Hermes' path inside WSL, or None when there is no Hermes there."""
    global _WSL_HERMES, _WSL_CHECKED
    if _WSL_CHECKED:
        return _WSL_HERMES
    _WSL_CHECKED = True
    launcher = wsl_exe()
    if not launcher:
        return None
    try:
        proc = subprocess.run(
            [launcher, "-e", "sh", "-lc", "command -v hermes"],
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
    path = (proc.stdout or "").strip().splitlines()
    _WSL_HERMES = path[0].strip() if path and path[0].strip() else None
    return _WSL_HERMES


def wsl_hermes_available() -> bool:
    """Whether a Hermes in WSL can be driven from this Windows desktop."""
    return wsl_hermes_python() is not None


# Cached alongside the launcher: same reasoning, same lifetime.
_WSL_PYTHON: Optional[str] = None
_WSL_PYTHON_CHECKED = False
_WSL_STATE_DB: Optional[str] = None
_WSL_STATE_DB_CHECKED = False


def wsl_hermes_python(timeout: int = 25) -> Optional[str]:
    """The interpreter inside WSL that can import Hermes' gateway.

    Hermes' launcher has no subcommand for this protocol, so the gateway has to
    be started as a module -- which means finding the interpreter of the venv
    Hermes installed itself into. Its home is resolved inside WSL rather than
    guessed from Windows, so a distribution with HERMES_HOME set, or a home
    directory that is not /home/<user>, still works.
    """
    global _WSL_PYTHON, _WSL_PYTHON_CHECKED
    if _WSL_PYTHON_CHECKED:
        return _WSL_PYTHON
    _WSL_PYTHON_CHECKED = True
    launcher = wsl_exe()
    if not launcher:
        return None
    # Printed by WSL's own shell, so ${HERMES_HOME:-$HOME/.hermes} is expanded
    # there and the answer is a path that exists on that side.
    probe = (
        'root="${HERMES_HOME:-$HOME/.hermes}/' + HERMES_SOURCE_DIRNAME + '"; '
        'for p in "$root/venv/bin/python3" "$root/venv/bin/python"; do '
        'if [ -x "$p" ] && [ -d "$root/' + HERMES_GATEWAY_MODULE.split(".")[0] + '" ]; '
        'then printf %s "$p"; exit 0; fi; done; exit 1'
    )
    try:
        proc = subprocess.run(
            [launcher, "-e", "sh", "-lc", probe],
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
    found = (proc.stdout or "").strip()
    _WSL_PYTHON = found or None
    return _WSL_PYTHON


def wsl_state_db() -> Optional[str]:
    """Hermes' session store inside WSL, as the POSIX path WSL sees.

    Both the path and Hermes' home are resolved inside WSL rather than guessed,
    so a distribution with HERMES_HOME set still works.
    """
    global _WSL_STATE_DB, _WSL_STATE_DB_CHECKED
    if _WSL_STATE_DB_CHECKED:
        return _WSL_STATE_DB
    _WSL_STATE_DB_CHECKED = True
    launcher = wsl_exe()
    if not launcher:
        return None
    try:
        proc = subprocess.run(
            [
                launcher,
                "-e",
                "sh",
                "-lc",
                'db="${HERMES_HOME:-$HOME/.hermes}/state.db"; [ -f "$db" ] && printf %s "$db"',
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=25,
            **_text_output_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    found = (proc.stdout or "").strip()
    _WSL_STATE_DB = found or None
    return _WSL_STATE_DB


def wsl_sqlite_query(sql: str, params: "Sequence" = ()) -> "list[dict]":
    """Run one read-only query against Hermes' store from inside WSL.

    The store is also reachable from Windows under \\\\wsl.localhost, but Hermes
    keeps it in WAL mode and WAL needs shared memory that a network share
    cannot provide: SQLite answers "database is locked" and no conversation is
    ever listed. Running the query on WSL's side avoids the problem instead of
    working around it.

    The query and its parameters are passed as JSON on stdin rather than
    interpolated into a shell command, so no value is ever quoted into code.
    Rows come back as JSON, which is why the caller gets plain dicts.
    """
    launcher = wsl_exe()
    store = wsl_state_db()
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
            timeout=WSL_QUERY_TIMEOUT,
            **_text_output_kwargs(),
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


def windows_path_to_wsl(path: str) -> str:
    """Translate a Windows working directory into its WSL equivalent.

    Only the drive-letter form is translated, since that is what the folder
    picker produces. Anything already in POSIX form, or a network path with no
    WSL equivalent, is passed through for WSL itself to interpret.
    """
    text = str(path or "").strip()
    if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
        drive = text[0].lower()
        # Collapse repeated separators: "D:\\projekty\\\\x" and a trailing slash
        # both otherwise produce a path with empty components in it.
        parts = [p for p in text[2:].replace("\\", "/").split("/") if p]
        return f"/mnt/{drive}/" + "/".join(parts) if parts else f"/mnt/{drive}"
    return text.replace("\\", "/")


def wsl_path_to_windows(path: str) -> str:
    """Translate a WSL path back into the Windows form, where one exists.

    Hermes records the working directory as it saw it, so a conversation run
    through WSL carries "/mnt/d/work". Reopening it in a Windows desktop needs
    the drive-letter form back. Paths inside the distribution's own filesystem
    have no drive-letter equivalent and are returned unchanged, for the caller
    to reject.
    """
    text = str(path or "").strip().replace("\\", "/")
    parts = [p for p in text.split("/") if p]
    if len(parts) >= 2 and parts[0].lower() == "mnt" and len(parts[1]) == 1:
        drive = parts[1].upper()
        rest = "\\".join(parts[2:])
        return f"{drive}:\\{rest}" if rest else f"{drive}:\\"
    return str(path or "")


def hermes_auth_ok(timeout: int = 25) -> bool:
    """Best-effort, non-interactive check that Hermes has a model configured.

    Hermes has no ``auth status`` command, and ``hermes model`` is the
    interactive picker rather than a report, so neither can answer this. What
    makes Hermes usable is a configured provider and model, and ``hermes
    status`` prints both without prompting for anything. Its exit code is 0
    even when nothing is set up, so the output is what gets inspected.
    """
    binary = find_hermes_cli()
    if not binary:
        wsl_path = wsl_hermes_path()
        launcher = wsl_exe()
        if not wsl_path or not launcher:
            return False
        command = [launcher, "-e", wsl_path, "status"]
    else:
        command = [binary, "status"]
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
        return False
    if proc.returncode != 0:
        return False
    for line in (proc.stdout or "").splitlines():
        # "Model:" followed by anything real means a provider is selected. The
        # line is decorated for the terminal, so only its shape is relied on.
        if "Model:" in line:
            value = line.split("Model:", 1)[1].strip()
            if value and value.lower() not in ("(not set)", "not set", "none", "-"):
                return True
    return False


# --------------------------------------------------------------------------
# Transports
# --------------------------------------------------------------------------


# How long to wait for a remote handshake. Long enough for a sleepy machine on
# a private network, short enough that a wrong address is reported rather than
# leaving the user listening to silence.
REMOTE_CONNECT_TIMEOUT = 20.0

# Read timeouts that mean "nothing arrived yet", not "the connection is gone".
# The websocket library raises its own timeout type, and the socket underneath
# raises the built-in one; a client that cannot tell either from a real failure
# hangs up on a connection that is still healthy. Named as a tuple so the
# reading loop states the distinction once, where it is easy to check.
_WS_TIMEOUT_ERRORS: tuple = (TimeoutError,)
try:  # pragma: no cover - depends on the optional remote dependency
    from websocket import WebSocketTimeoutException as _WSTimeout  # type: ignore

    _WS_TIMEOUT_ERRORS = (_WSTimeout, TimeoutError)
except ImportError:
    # The local (stdio) path does not need websocket-client at all, so its
    # absence must not stop the application starting.
    pass

# Credential names Hermes' WebSocket upgrade accepts from a configured client.
# "token" is the server's session token; "ticket" is the one-shot ticket minted
# after a password login, which is what a server reachable from another machine
# requires. A third name exists for processes Hermes spawns itself and is
# deliberately not offered here.
# Credential kinds the remote settings can hold.
#
# "token" is the session token of a Hermes bound to the loopback address, where
# it needs no login of its own. "password" is for a Hermes reachable from
# another machine: any non-loopback bind makes Hermes require a real login, and
# the WebSocket upgrade then wants a one-shot ticket rather than a token.
#
# Those tickets live thirty seconds -- measured, not assumed -- so pasting one
# into a settings field is not usable. The password is stored instead and the
# ticket is minted automatically before each connection.
REMOTE_CREDENTIALS = ("token", "password")

# What Hermes' WebSocket upgrade accepts as a query parameter. Deliberately
# separate from REMOTE_CREDENTIALS: a password is stored in the settings but
# never sent this way -- it buys a ticket first.
WS_QUERY_CREDENTIALS = ("token", "ticket")

# How long a minted ticket is valid. Hermes reports 30s; the ticket is used
# immediately, and this only guards against treating a stale one as usable.
TICKET_TTL_MARGIN = 5.0


def mint_ws_ticket(url: str, username: str, password: str) -> str:
    """Log in with a password and return a one-shot WebSocket ticket.

    A Hermes reachable from another machine requires a login of its own, and
    its WebSocket upgrade then accepts only a ticket, which lives thirty
    seconds. So the desktop stores the password and does this every time it
    connects, rather than asking anyone to paste a value that expires while
    they are typing it.

    Raises ``OSError`` with a message worth showing when the login fails.
    """
    import urllib.error
    import urllib.request
    from http.cookiejar import CookieJar

    base = url.split("?", 1)[0]
    for prefix, replacement in (("wss://", "https://"), ("ws://", "http://")):
        if base.startswith(prefix):
            base = replacement + base[len(prefix) :]
            break
    base = base[: -len("/api/ws")] if base.endswith("/api/ws") else base

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def _post(path: str, body: Optional[dict]) -> dict:
        data = json.dumps(body).encode() if body is not None else b""
        request = urllib.request.Request(
            base + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(request, timeout=REMOTE_CONNECT_TIMEOUT) as response:
            return json.loads(response.read() or b"{}")

    try:
        _post(
            "/auth/password-login",
            {"provider": "basic", "username": username, "password": password},
        )
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise OSError(f"Hermes at {base} rejected that username and password.") from exc
        raise OSError(f"Could not sign in to Hermes at {base}: HTTP {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001 - network, DNS, TLS all land here
        raise OSError(_remote_failure_message(base, exc)) from exc

    try:
        payload = _post("/api/auth/ws-ticket", None)
    except Exception as exc:  # noqa: BLE001
        raise OSError(f"Signed in to Hermes at {base}, but it issued no ticket.") from exc
    ticket = str(payload.get("ticket") or "")
    if not ticket:
        raise OSError(f"Signed in to Hermes at {base}, but it issued no ticket.")
    return ticket


def remote_ws_url(host: str, port: int = 9119, secure: bool = False) -> str:
    """Build the gateway URL from the parts a user actually types.

    Asking for a whole ``ws://host:port/api/ws`` is asking to get the path
    wrong, so the settings take a host and a port and the path is added here.
    """
    host = host.strip()
    # Tolerate someone pasting a full URL anyway.
    for prefix in ("ws://", "wss://", "http://", "https://"):
        if host.lower().startswith(prefix):
            secure = prefix in ("wss://", "https://")
            host = host[len(prefix) :]
            break
    host = host.rstrip("/")
    if host.endswith("/api/ws"):
        host = host[: -len("/api/ws")].rstrip("/")
    if ":" in host and not host.startswith("["):
        host, _, given_port = host.rpartition(":")
        if given_port.isdigit():
            port = int(given_port)
    scheme = "wss" if secure else "ws"
    return f"{scheme}://{host}:{port}/api/ws"


def _authenticated_ws_url(url: str, token: str, credential: str) -> str:
    """Put the credential in the query string, where the upgrade looks for it.

    The name has to be one Hermes' upgrade recognises -- ``token`` for a
    loopback server, ``ticket`` for a gated one. That is a different list from
    :data:`REMOTE_CREDENTIALS`, which is what the *settings* can hold: a
    password is stored, but never sent as a query parameter. Validating against
    the wrong list is exactly how a minted ticket went out as ``?token=`` and
    was rejected.
    """
    if not token:
        return url
    name = credential if credential in WS_QUERY_CREDENTIALS else "token"
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{name}={urllib.parse.quote(token, safe='')}"


def _redacted_ws_url(url: str) -> str:
    """The address without any credential, safe to show or speak."""
    return url.split("?", 1)[0]


def _remote_failure_message(url: str, exc: Exception) -> str:
    """Say what to do about a failed connection, not just that it failed.

    A blind user cannot glance at a log, so the message has to carry the
    diagnosis: refused means nothing is listening, a rejected handshake means
    the key is wrong, a timeout means the machine is not answering.
    """
    detail = str(exc).strip() or exc.__class__.__name__
    lowered = detail.lower()
    if "403" in detail or "401" in detail or "handshake" in lowered:
        return (
            f"Hermes at {url} refused the connection key. Check the key in "
            "Remote Hermes settings, then try again."
        )
    if "refused" in lowered:
        return (
            f"Nothing is listening at {url}. Start Hermes there with "
            "'hermes serve', then try again."
        )
    if "name or service not known" in lowered or "getaddrinfo" in lowered:
        return f"The address {url} could not be found. Check the host name."
    if "timed out" in lowered or "timeout" in lowered:
        # A closed port on a reachable machine also reports a timeout rather
        # than a refusal, so name both causes instead of guessing one.
        return (
            f"{url} did not answer. Check that Hermes is running there with "
            "'hermes serve', and that the machine is awake and reachable."
        )
    return f"Could not reach Hermes at {url}: {detail}"


class Transport(Protocol):
    """One JSON-RPC connection to Hermes, local or remote.

    Both transports carry the same protocol, so the worker is written against
    this interface and never branches on which one it was handed.
    """

    def send(self, message: dict) -> bool:
        """Write one frame. ``False`` means the peer is gone."""
        ...

    def receive(self, timeout: float) -> Optional[dict]:
        """Read one frame, or ``None`` when nothing arrived in ``timeout``."""
        ...

    def close(self) -> None:
        """Release the connection."""
        ...

    def connected(self) -> bool:
        """Whether this connection can still carry another turn."""
        ...

    def failure_detail(self) -> str:
        """Why the connection ended, for a message a user can act on."""
        ...


class StdioTransport:
    """Hermes' gateway as a child process, spoken to over its pipes.

    This is the zero-configuration path: no address, no port, no key. The
    frames are newline-delimited JSON, which is why stdout is read by line.
    """

    def __init__(self, cwd: str) -> None:
        self._cwd = cwd
        self._proc: Optional[subprocess.Popen] = None
        self._frames: "list[dict]" = []
        self._frames_lock = threading.Lock()
        self._frames_ready = threading.Condition(self._frames_lock)
        self._stderr: list[str] = []
        self._closed = False

    def start(self) -> None:
        """Launch the gateway. Raises ``OSError`` if it cannot be started."""
        python = hermes_python()
        root = hermes_source_root()
        env = os.environ.copy()
        if python and root is not None:
            # Hermes on this side of the machine: run its own interpreter.
            #
            # The gateway imports itself from the source tree, and Hermes' own
            # launcher clears these two before exec'ing, so mirror that: a
            # stale PYTHONHOME from the host application would break the child.
            env.pop("PYTHONHOME", None)
            existing = env.get("PYTHONPATH", "").strip()
            env["PYTHONPATH"] = f"{root}{os.pathsep}{existing}" if existing else str(root)
            env["HERMES_PYTHON_SRC_ROOT"] = str(root)
            command = [python, "-m", HERMES_GATEWAY_MODULE]
            cwd = self._cwd
        else:
            # Hermes inside WSL, driven from a Windows desktop. Its interpreter
            # is invisible to Windows, so the gateway is started with WSL's own
            # launcher instead. The module is asked for rather than a
            # subcommand, because Hermes has no CLI subcommand for this
            # protocol -- checked, rather than assumed.
            #
            # The working directory is translated and handed to WSL, not to
            # Popen, which only understands Windows paths.
            wsl_python = wsl_hermes_python()
            launcher = wsl_exe()
            if not wsl_python or not launcher:
                raise OSError("Hermes Agent is installed but its Python environment is missing")
            command = [
                launcher,
                "--cd",
                windows_path_to_wsl(self._cwd),
                "-e",
                wsl_python,
                "-m",
                HERMES_GATEWAY_MODULE,
            ]
            # Popen's cwd is a Windows path; WSL was already told where to run.
            cwd = None
        self._proc = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            env=env,
            **_no_window_kwargs(),
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except ValueError:
                # Not protocol. Hermes keeps stdout clean, so this is either a
                # library printing over it or a truncated line; either way the
                # remedy is the same as for stderr - remember it for the
                # failure message and carry on.
                self._stderr.append(line[:400])
                continue
            with self._frames_ready:
                self._frames.append(frame)
                self._frames_ready.notify_all()
        with self._frames_ready:
            self._closed = True
            self._frames_ready.notify_all()

    def _read_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            line = line.strip()
            if line:
                self._stderr.append(line)
                del self._stderr[:-STDERR_KEEP_LINES]

    def send(self, message: dict) -> bool:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return False
        try:
            proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError):
            return False
        return True

    def receive(self, timeout: float) -> Optional[dict]:
        with self._frames_ready:
            if not self._frames and not self._closed:
                self._frames_ready.wait(timeout)
            if self._frames:
                return self._frames.pop(0)
        return None

    def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except (OSError, ValueError):
            pass
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass
        # Close the read pipes too, and only after the child is gone so the
        # reader threads are not left reading a closed handle. Leaving them to
        # the garbage collector raises ResourceWarning: unclosed file, which
        # CI's `pytest -W error` turns into a failed build - and, being
        # unraisable, it is reported against whichever test happened to be
        # running when the collector ran, not the one that opened the pipe.
        # (The same class of defect upstream fixed for its one-shot sound
        # players by keeping them referenced until reaped.)
        for stream in (proc.stdout, proc.stderr):
            if stream is None:
                continue
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        # The child is finished with; dropping the reference lets Popen's own
        # finalizer see an already-waited process rather than a live one.
        self._proc = None
        with self._frames_ready:
            self._closed = True
            self._frames_ready.notify_all()

    def connected(self) -> bool:
        """Whether the local Hermes is still running and usable.

        The counterpart of the remote check, so a caller holding a connection
        between turns asks the same question of either transport.
        """
        proc = self._proc
        return proc is not None and proc.poll() is None and not self._closed

    def failure_detail(self) -> str:
        detail = "\n".join(self._stderr[-6:]).strip()
        return detail or "Hermes closed the connection before the turn completed"


class WebSocketTransport:
    """A Hermes running elsewhere, reached over ``hermes serve``.

    The point of this path is a Hermes that lives on another machine — a
    home server, or a WSL instance reached over a private network — so the
    desktop can drive it without a terminal. The protocol is the same as the
    local path; only the pipe changes.

    Authentication is a query parameter, not a header. Hermes' HTTP routes do
    accept ``X-Hermes-Session-Token``, but the WebSocket upgrade reads its
    credential from the URL and refuses the handshake without one - a header
    alone is answered with 403. Two credential names are meant for a client a
    person configures: ``token`` for the server's session token, and
    ``ticket`` for the one-shot ticket minted after a password login on a
    server reachable from outside this machine.

    The connection is read by a thread of its own, which matters for a
    connection held between turns. A Hermes bound to a public address (the
    case whenever it is reached over a private network) pings every 20 seconds
    and closes a connection that does not answer within 20 more; the client
    library only answers those pings from inside a receive call. So a
    connection nobody is reading is dropped by the server within about half a
    minute, and the reading thread is what keeps it alive while it waits.
    Frames arriving with no one waiting for them queue up rather than being
    lost, so an event that lands between turns is still there for the next one.
    """

    def __init__(
        self,
        url: str,
        token: str = "",
        credential: str = "token",
        username: str = "",
    ) -> None:
        self._base_url = url
        self._token = token
        self._credential = credential if credential in REMOTE_CREDENTIALS else "token"
        self._username = username or "hermes"
        # Kept credential-free for anything shown to the user: a token spoken
        # aloud by a screen reader is a token read out to the room.
        self._display_url = _redacted_ws_url(url)
        # Annotated rather than left to inference: `websocket` is imported inside
        # `start()` so a user on another backend need not have it installed, and
        # with no type here mypy reads the attribute as `None` and rejects the
        # assignment in `start()` on a machine where the package ships its own
        # types. Deliberately loose: naming `WebSocket` would fail wherever it
        # is untyped instead, which is the same problem the other way round.
        self._ws: Optional[Any] = None
        self._error = ""
        self._frames: "queue.Queue[dict]" = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        self._closing = threading.Event()

    def start(self) -> None:
        """Connect. Raises ``OSError`` with a message worth showing a user."""
        try:
            from websocket import create_connection  # type: ignore
        except ImportError as exc:  # pragma: no cover - dependency probe
            raise OSError(
                "Remote Hermes needs the websocket-client package: pip install websocket-client"
            ) from exc
        if self._credential == "password":
            # A Hermes reachable from another machine requires its own login,
            # and the ticket it then issues lives thirty seconds -- so it is
            # minted here, immediately before use, rather than stored.
            ticket = mint_ws_ticket(self._base_url, self._username, self._token)
            url = _authenticated_ws_url(self._base_url, ticket, "ticket")
        else:
            url = _authenticated_ws_url(self._base_url, self._token, "token")
        try:
            self._ws = create_connection(
                url,
                timeout=REMOTE_CONNECT_TIMEOUT,
                # The reading thread and the sending turn touch the connection
                # from different threads, which the library only supports when
                # asked to.
                enable_multithread=True,
            )
        except Exception as exc:  # noqa: BLE001 - any failure is "cannot reach it"
            self._error = str(exc)
            raise OSError(_remote_failure_message(self._display_url, exc)) from exc
        self._closing.clear()
        self._reader = threading.Thread(target=self._read_forever, daemon=True)
        self._reader.start()

    def _read_forever(self) -> None:
        """Read frames into the queue for as long as the connection lives.

        Reading continuously is what answers the server's keepalive pings, so
        this is not merely a convenience: without it a connection held between
        turns is closed by the server after about half a minute.

        A read that times out is NOT the end of the connection, and telling the
        two apart is what keeps a long turn alive. The socket carries the
        connect timeout into every later operation, so a quiet stretch longer
        than that -- a build, a test run, a model thinking, anything that
        produces no frame for twenty seconds -- raised a timeout here. Treating
        it as a dead peer ended this thread, and from then on nobody answered
        the server's pings, so the server closed a connection that was fine.
        Measured: the thread died after 21 seconds of quiet even on a loopback
        server, which does not ping at all, and ``send`` went on reporting
        success into a socket with no reader behind it.
        """
        ws = self._ws
        if ws is None:
            return
        while not self._closing.is_set():
            try:
                raw = ws.recv()
            except _WS_TIMEOUT_ERRORS:
                # Nothing arrived in time. The connection is untouched, and the
                # library has already answered any ping from inside recv, so the
                # only correct thing to do is read again.
                continue
            except Exception as exc:  # noqa: BLE001
                if not self._closing.is_set():
                    self._error = str(exc)
                return
            if not raw:
                continue
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            try:
                self._frames.put(json.loads(raw))
            except ValueError:
                # Hermes speaks JSON per frame; anything else is not ours to
                # interpret, and dropping it is better than stopping the read.
                continue

    def send(self, message: dict) -> bool:
        """Write one frame. ``False`` means the peer is gone.

        A dead reading thread counts as gone, even when the socket still accepts
        bytes. Measured: with the reader stopped, ``send`` reported success and
        the answer never came, because there was nobody left to read the reply
        -- a silent loss, which is the worst outcome for a listener who has no
        way to see that nothing is happening. Refusing here turns it into a
        message the turn can report.
        """
        if self._ws is None:
            return False
        reader = self._reader
        if reader is not None and not reader.is_alive() and not self._closing.is_set():
            if not self._error:
                self._error = "the connection to Hermes stopped being read"
            return False
        try:
            self._ws.send(json.dumps(message, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            self._error = str(exc)
            return False
        return True

    def receive(self, timeout: float) -> Optional[dict]:
        """The next frame the reader has, or ``None`` if none arrived in time.

        ``None`` is not a failure: the caller polls, and a turn spends most of
        its time waiting. A dead connection is reported through
        ``failure_detail`` instead, which the caller already consults.
        """
        try:
            return self._frames.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None

    def close(self) -> None:
        self._closing.set()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:  # noqa: BLE001
                pass

    def connected(self) -> bool:
        """Whether this connection is still usable for another turn.

        Consulted before a held connection is reused: a server restart or a
        network drop is only discovered by the reading thread, which records it
        and stops.
        """
        if self._ws is None or self._closing.is_set():
            return False
        reader = self._reader
        return reader is not None and reader.is_alive()

    def failure_detail(self) -> str:
        if self._error:
            return f"Lost the connection to Hermes at {self._display_url}: {self._error}"
        return f"Hermes at {self._display_url} closed the connection before the turn completed"


# --------------------------------------------------------------------------
# The model picker
# --------------------------------------------------------------------------

# Asking the gateway costs a Python start-up, so the picker is given a bounded
# wait rather than being allowed to hang the dialog open.
MODEL_QUERY_TIMEOUT = 60.0

# What joins a provider to a model in one picker row.
#
# NOT a colon, which is what this used at first and is the form Hermes' own
# ``/model`` command takes. Hermes reads a colon prefix as a provider only when
# the left side is a provider name it ships with; a user-defined one -- every
# `providers:` / `custom_providers:` entry, which is what a Foundry/Palantir
# endpoint or any self-hosted gateway is -- is not on that list, so the whole
# row came back as a model name with the provider left untouched. Measured:
# ``parse_model_input("palantir-gpt:<model>")`` returns the previous provider
# and ``"palantir-gpt:<model>"`` as the model. That matters because each entry
# has its own endpoint and API mode (…/openai/v1 with chat_completions vs
# …/anthropic with anthropic_messages), so a wrong provider is not a label
# problem -- it is a turn sent to the wrong place.
#
# The two halves therefore travel as separate protocol fields, and this
# separator only ever has to survive a round trip through BlindPilot's own UI
# and settings. " · " is not a character any provider slug or model id uses,
# and a screen reader reads the row as two parts rather than running them
# together.
MODEL_ROW_SEPARATOR = " · "

# The reasoning levels Hermes accepts on ``session.create``.
#
# Taken from its own validator (``hermes_constants.VALID_REASONING_EFFORTS``)
# plus "none", which that validator reads as "think as little as possible"
# rather than as an unknown word. Hermes ignores a level it does not
# recognise, so a saved value from an older list can never break a turn -- it
# just leaves the profile's own setting in place.
#
# This is a fixed list rather than something read from the catalog because the
# levels are not per-provider: the same set is offered for every model.
HERMES_EFFORTS = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
)


def split_model_row(row: str) -> tuple[str, str]:
    """Split a picker row into ``(provider, model)``.

    A row typed by hand, or restored from a version that saved the old
    colon-joined form, may have no separator at all: then there is no provider
    to name and the whole string is the model, which is exactly what Hermes
    does with a bare model name -- resolve it against the current provider.
    """
    text = (row or "").strip()
    if not text:
        return "", ""
    if MODEL_ROW_SEPARATOR in text:
        provider, _, model = text.partition(MODEL_ROW_SEPARATOR)
        return provider.strip(), model.strip()
    return "", text


def _model_rows(payload: dict) -> tuple[list[str], str]:
    """Flatten Hermes' provider catalog into picker rows.

    Hermes groups models under providers and can have the same model name in
    more than one of them, so each row is qualified with its provider - which
    is also the form ``/model`` accepts, meaning a picked row can be sent back
    unchanged.
    """
    models: list[str] = []
    current = ""
    providers = payload.get("providers")
    if not isinstance(providers, list):
        return models, current
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        slug = str(provider.get("slug") or "").strip()
        if not slug:
            continue
        # An unauthenticated provider is listed so the user can see it exists,
        # but picking one would fail at the first turn.
        if provider.get("authenticated") is False:
            continue
        entries = provider.get("models")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            name = str(entry).strip()
            if not name:
                continue
            qualified = f"{slug}{MODEL_ROW_SEPARATOR}{name}"
            models.append(qualified)
            if provider.get("is_current") and not current:
                current = qualified
    return models, current


SESSION_DENY_SOURCES = ("kanban", "tool")


def _ready_or_fail(transport: Transport, deadline_seconds: float) -> str:
    """Wait for the gateway announcement; return "" when ready, else the error.

    A dropped connection ends the wait immediately instead of sitting out the
    whole deadline. `receive` returning None means "nothing yet", which is
    ordinary while a gateway starts up -- but on a transport that is no longer
    connected there is nobody left to announce anything, and spinning for the
    full sixty seconds only delays telling the user what happened. Worth being
    explicit about: this loop does not sleep, so on a dead transport it was a
    busy wait, and the failure it reported was a timeout rather than the real
    reason the connection ended.
    """
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        frame = transport.receive(0.5)
        if frame is None:
            if not transport.connected():
                return transport.failure_detail() or "Hermes closed the connection."
            continue
        params = frame.get("params")
        if isinstance(params, dict) and params.get("type") == "gateway.ready":
            return ""
    return "Hermes did not respond in time."


def hermes_session_catalog(
    cwd: Optional[str] = None,
    *,
    remote_url: str = "",
    remote_token: str = "",
    remote_credential: str = "token",
    remote_username: str = "",
) -> tuple[list[dict], set[str], str]:
    """List Hermes conversations, and say which of them are live right now.

    Returns ``(sessions, live_keys, error)``. ``sessions`` rows carry ``id``,
    ``title``, ``preview``, ``message_count``, ``source`` and ``started_at``;
    ``live_keys`` holds the session keys that currently run inside the target
    gateway process, so the caller can mark them attachable.

    Two questions, two answers. ``session.list`` reads the profile database and
    shows every human conversation on every surface (CLI, TUI, messaging) --
    that is the "go back to any conversation" list. ``session.active_list``
    reports only sessions with an agent in the gateway process -- a session on
    that list can be attached to live, with its running turn.

    A row whose source is internal bookkeeping (kanban workers, tool runs) is
    dropped, the same deny-list Hermes' own resume picker uses.
    """
    if remote_url:
        transport: Transport = WebSocketTransport(
            remote_url, remote_token, remote_credential, remote_username
        )
    else:
        if not hermes_installed():
            return [], set(), "Hermes Agent was not found on this computer."
        transport = StdioTransport(cwd or str(Path.home()))
    try:
        transport.start()  # type: ignore[attr-defined]
    except OSError as exc:
        return [], set(), str(exc)
    try:
        problem = _ready_or_fail(transport, MODEL_QUERY_TIMEOUT)
        if problem:
            return [], set(), problem

        transport.send(
            {"jsonrpc": "2.0", "id": 1, "method": "session.list", "params": {"limit": 200}}
        )
        sessions: list[dict] = []
        error = ""
        deadline = time.monotonic() + MODEL_QUERY_TIMEOUT
        while time.monotonic() < deadline:
            frame = transport.receive(0.5)
            if frame is None:
                continue
            if frame.get("id") != 1:
                continue
            if isinstance(frame.get("error"), dict):
                error = str(frame["error"].get("message") or "Hermes could not list its sessions.")
                break
            result = frame.get("result")
            if not isinstance(result, dict):
                error = "Hermes returned no session list."
                break
            for row in result.get("sessions") or []:
                if not isinstance(row, dict):
                    continue
                source = str(row.get("source") or "").strip().lower()
                if source in SESSION_DENY_SOURCES:
                    continue
                sessions.append(
                    {
                        "id": str(row.get("id") or ""),
                        "title": str(row.get("title") or ""),
                        "preview": str(row.get("preview") or ""),
                        "message_count": int(row.get("message_count") or 0),
                        "source": source,
                        "started_at": float(row.get("started_at") or 0),
                    }
                )
            break
        if error:
            return [], set(), error

        transport.send({"jsonrpc": "2.0", "id": 2, "method": "session.active_list", "params": {}})
        live: set[str] = set()
        deadline = time.monotonic() + MODEL_QUERY_TIMEOUT
        while time.monotonic() < deadline:
            frame = transport.receive(0.5)
            if frame is None:
                continue
            if frame.get("id") != 2:
                continue
            if isinstance(frame.get("error"), dict):
                # Attaching is a bonus on top of the list; a refusal to
                # enumerate live sessions must not lose the list itself.
                break
            result = frame.get("result")
            if isinstance(result, dict):
                for row in result.get("sessions") or []:
                    if isinstance(row, dict):
                        key = str(row.get("session_key") or "")
                        if key:
                            live.add(key)
            break
        return sessions, live, ""
    finally:
        transport.close()


def hermes_model_options(
    cwd: Optional[str] = None,
    *,
    remote_url: str = "",
    remote_token: str = "",
    remote_credential: str = "token",
    remote_username: str = "",
) -> tuple[list[str], list[str], str, str, str]:
    """Ask Hermes which models and reasoning levels it can run.

    Returns the same five-part shape the other backends' catalogs use: models,
    effort levels, current model, current effort, error.

    Works for a Hermes on this machine and for one reached over the network.
    That second case used to be refused outright, on the reasoning that a
    remote Hermes "chooses its model on the machine it runs on" -- which was
    wrong: ``hermes serve`` carries the SAME JSON-RPC surface over its
    WebSocket as the local pipe does (its own ws module dispatches through the
    identical handler table), so ``model.options`` answers either way. The only
    real difference is that the local path may have to start a gateway first.
    """
    if remote_url:
        transport: Transport = WebSocketTransport(
            remote_url, remote_token, remote_credential, remote_username
        )
    else:
        if not hermes_installed():
            return [], [], "", "", "Hermes Agent was not found on this computer."
        transport = StdioTransport(cwd or str(Path.home()))
    try:
        transport.start()  # type: ignore[attr-defined]
    except OSError as exc:
        return [], [], "", "", str(exc)

    deadline = time.monotonic() + MODEL_QUERY_TIMEOUT
    try:
        ready = False
        while time.monotonic() < deadline and not ready:
            frame = transport.receive(0.5)
            if frame is None:
                continue
            params = frame.get("params")
            if isinstance(params, dict) and params.get("type") == "gateway.ready":
                ready = True
        if not ready:
            return [], [], "", "", "Hermes did not respond in time."

        transport.send({"jsonrpc": "2.0", "id": 1, "method": "model.options", "params": {}})
        while time.monotonic() < deadline:
            frame = transport.receive(0.5)
            if frame is None:
                continue
            if frame.get("id") != 1:
                continue
            error = frame.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                return [], [], "", "", str(message or "Hermes could not list its models.")
            result = frame.get("result")
            if not isinstance(result, dict):
                return [], [], "", "", "Hermes returned no model list."
            models, current = _model_rows(result)
            if not models:
                return [], [], "", "", "Hermes reported no usable models."
            # The catalog does not carry the reasoning levels, because they are
            # not per-provider: Hermes accepts the same fixed set for every
            # model and simply ignores one a model cannot use.
            return models, list(HERMES_EFFORTS), current, "", ""
        return [], [], "", "", "Hermes did not answer the model request in time."
    finally:
        transport.close()
