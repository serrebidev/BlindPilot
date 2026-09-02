"""Backend adapters for BlindPilot.

BlindPilot began as Claude Code Reader. Claude's adapter remains in
``blindpilot_app.py``; this module contains the
provider-neutral discovery helpers plus the Codex, FreeBuff, and opencode
workers.
Hermes lives in ``hermes_backend.py`` and ``hermes_worker.py``, imported
on demand so a machine without Hermes pays nothing for it.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
Based on the original Claude Code Reader application by doubletaponair:
https://github.com/doubletaponair/claude-code-reader
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import atexit
import json
import os
import platform
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol, Sequence, cast

from markdown_rows import _SENTENCE_END_RE as _markdown_sentence_end_re
from markdown_rows import complete_sentences


# A windowed app owns no console, so every child process Windows considers a
# console program is given a brand new one - a terminal that pops up on screen,
# takes focus away from the screen reader, and in the case of a long-running
# agent CLI stays there for the whole turn. CREATE_NO_WINDOW suppresses it.
CREATE_NO_WINDOW = 0x08000000 if platform.system() == "Windows" else 0


def no_window_kwargs() -> dict:
    """``subprocess`` keyword arguments that keep children off the screen."""
    return {"creationflags": CREATE_NO_WINDOW} if CREATE_NO_WINDOW else {}


def own_group_kwargs() -> dict:
    """``subprocess`` keyword arguments that give a child its own process group.

    Half the provider CLIs are launchers rather than programs: npm's
    ``@openai/codex`` is a Node script that runs the real Codex as a child of
    its own, and FreeBuff's is the same shape. Killing the launcher leaves that
    child running — still holding its lock, still waiting on a sign-in nobody
    is completing. A child in a group of its own can be stopped as a group,
    which is the only way to stop what it started too.
    """
    return {} if platform.system() == "Windows" else {"start_new_session": True}


def end_process_group(proc: object, timeout: float = 0.0) -> None:
    """Stop a child started with :func:`own_group_kwargs`, and its own children.

    Returns as soon as the signals are away unless *timeout* is given: Stop
    Task and a closing wizard both call this from the window's own thread, and
    a wait there is a frozen application rather than a stopped task.

    The group is only signalled when the child is demonstrably the leader of
    one, which is exactly what ``start_new_session`` made it. Anything else —
    a child started without its own group, a stand-in in a test, Windows — is
    stopped on its own. That check is not a formality: a process still sitting
    in BlindPilot's group would otherwise have BlindPilot signal itself, and
    take every other backend down with it.
    """
    poll = getattr(proc, "poll", None)
    if poll is not None and poll() is not None:
        return
    pid = getattr(proc, "pid", None)
    if platform.system() != "Windows" and isinstance(pid, int) and pid > 0:
        try:
            if os.getpgid(pid) == pid:
                os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass
    kill = getattr(proc, "kill", None)
    if kill is not None:
        try:
            kill()
        except OSError:
            pass
    wait = getattr(proc, "wait", None)
    if timeout and wait is not None:
        try:
            wait(timeout=timeout)
        except Exception:
            pass


_CONSOLE_WINDOW_CLASSES = ("ConsoleWindowClass", "PseudoConsoleWindow")


def reserve_hidden_console() -> bool:
    """Take a console for this process now, hidden, and keep it.

    Creating a pseudo-terminal gives a windowed application a console whether
    it wants one or not, and that console arrives visible. Claiming one up
    front, before any terminal is created, means there is nothing left to
    create later: the console already exists and is already hidden.
    """
    if platform.system() != "Windows":
        return False
    import ctypes

    kernel32 = ctypes.windll.kernel32
    try:
        handle = kernel32.GetConsoleWindow()
        if not handle:
            if not kernel32.AllocConsole():
                return False
            handle = kernel32.GetConsoleWindow()
        if handle:
            _banish_window(handle)
        return bool(handle)
    except OSError:
        return False


def _banish_window(handle: int) -> None:
    """Hide a window, and park it off-screen in case it is shown again.

    Hiding alone leaves a window that something else can show, and the console
    is shown again while the terminal is torn down. Off-screen and sized to
    nothing, being shown costs nothing that can be seen.
    """
    import ctypes

    user32 = ctypes.windll.user32
    try:
        user32.ShowWindow(handle, 0)  # SW_HIDE
        # SWP_NOACTIVATE | SWP_NOZORDER | SWP_NOOWNERZORDER
        user32.SetWindowPos(handle, 0, -32000, -32000, 0, 0, 0x0010 | 0x0004 | 0x0200)
    except OSError:
        pass


def _descendant_pids(roots: set[int]) -> set[int]:
    """Every process descended from ``roots``, so only our own are touched."""
    import ctypes
    import ctypes.wintypes as wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == -1:
        return set(roots)
    parents: dict[int, int] = {}
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        ok = kernel32.Process32First(snapshot, ctypes.byref(entry))
        while ok:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            ok = kernel32.Process32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)

    family = set(roots)
    for pid in parents:
        seen: set[int] = set()
        walker = pid
        while walker and walker not in seen:
            seen.add(walker)
            if walker in family:
                family.update(seen)
                break
            walker = parents.get(walker, 0)
    return family


def hide_console_windows(roots: Optional[set[int]] = None) -> int:
    """Hide the console this process was given, and any its children raise.

    Creating the pseudo-terminal attaches a console to BlindPilot itself, which
    is why the window belongs to us rather than to the program being run. That
    one is found directly; the enumeration is the safety net for a child that
    raises its own, and never touches a console outside this process tree.
    """
    if platform.system() != "Windows":
        return 0
    import ctypes
    import ctypes.wintypes as wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    hidden = 0
    try:
        own = kernel32.GetConsoleWindow()
        if own and user32.IsWindowVisible(own):
            _banish_window(own)
            hidden += 1
    except OSError:
        pass

    candidates: list[tuple[int, int]] = []

    def visit(handle, _param):
        try:
            if not user32.IsWindowVisible(handle):
                return True
            name = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(handle, name, 64)
            if name.value in _CONSOLE_WINDOW_CLASSES:
                owner = wintypes.DWORD()
                user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
                candidates.append((handle, int(owner.value)))
        except Exception:
            pass
        return True

    try:
        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
        user32.EnumWindows(callback, 0)
    except Exception:
        return hidden
    # Walking the process table costs milliseconds, and this runs in a tight
    # loop to catch the window in the frame it appears. Pay for it only when
    # there is actually a console window on screen to account for.
    if not candidates:
        return hidden
    try:
        family = _descendant_pids(set(roots or ()) | {os.getpid()})
    except Exception:
        family = set(roots or ()) | {os.getpid()}
    for handle, owner in candidates:
        if owner in family:
            try:
                _banish_window(handle)
                hidden += 1
            except Exception:
                pass
    return hidden


BACKEND_CLAUDE = "claude"
BACKEND_CODEX = "codex"
BACKEND_FREEBUFF = "freebuff"
BACKEND_OPENCODE = "opencode"
BACKEND_HERMES = "hermes"
BACKEND_IDS = (
    BACKEND_CLAUDE,
    BACKEND_CODEX,
    BACKEND_FREEBUFF,
    BACKEND_OPENCODE,
    BACKEND_HERMES,
)
BACKEND_LABELS = {
    BACKEND_CLAUDE: "Claude Code",
    BACKEND_CODEX: "Codex",
    BACKEND_FREEBUFF: "FreeBuff",
    BACKEND_OPENCODE: "opencode",
    BACKEND_HERMES: "Hermes",
}

# FreeBuff has no model-list or model-selection CLI flags. Its installed
# package and downloaded executable do contain the live picker catalog, so the
# adapter discovers that catalog at runtime and writes the same setting the
# picker uses. GLM 5.3 is the preferred default while FreeBuff still offers it;
# FreeBuff drops and renames models between releases, so this is a preference
# rather than a requirement, and a release without it falls back to FreeBuff's
# own choice rather than failing.
FREEBUFF_PREFERRED_MODEL = "z-ai/glm-5.3-flash"
_FREEBUFF_SETTINGS_LOCK = threading.Lock()
_freebuff_catalog_cache: tuple[tuple[str, float, int], list[str]] | None = None


# ----- Mid-run questions -----
#
# Every backend BlindPilot drives can stop mid-turn to ask the person a
# multiple-choice question, and each one describes that question differently:
# Claude Code sends the AskUserQuestion tool's input through a `can_use_tool`
# control request, Codex sends an `item/tool/requestUserInput` JSON-RPC
# request, opencode publishes a `question.asked` event, and FreeBuff draws its
# `ask_user` tool straight onto its terminal. The window should not have to
# know any of that, so each adapter translates its provider's shape into the
# two types below, and translates the answers back on the way out.


@dataclass(frozen=True)
class QuestionOption:
    """One answer a backend offers for a question."""

    label: str
    description: str = ""


@dataclass(frozen=True)
class Question:
    """One question a backend has paused its turn to ask.

    ``id`` is whatever the backend needs to match the answer back to the
    question — Codex's question id, or an empty string where the provider keys
    answers by position or by the question's own text instead.
    """

    question: str
    header: str = ""
    options: tuple[QuestionOption, ...] = ()
    multi_select: bool = False
    id: str = ""
    # Whether the provider accepts an answer that is not one of its options.
    # Every backend does today, and all four say so in their tool descriptions
    # ("the client will add a free-form Other option automatically"), so this
    # is the default rather than the exception.
    allow_custom: bool = True
    # Codex alone marks a question whose answer should not be echoed back.
    secret: bool = False


# Given the questions, return one list of chosen answers per question — the
# option labels the person picked, or the text they typed instead — or None if
# they closed the dialog without answering. Called on the worker thread, and
# blocks it: the backend's turn is waiting on this answer.
AskQuestions = Callable[[Sequence[Question]], Optional[list[list[str]]]]


def question_summary(questions: Sequence[Question], answers: Optional[list[list[str]]]) -> str:
    """One row for the transcript saying what was asked and what was said.

    Both halves are kept: read back later, a bare "Spaces" says nothing, and
    the answer is the reason the rest of the turn went the way it did.
    """
    if answers is None:
        asked = " ".join(question.question for question in questions)
        return f"Question left unanswered: {asked}"
    parts = []
    for index, question in enumerate(questions):
        chosen = answers[index] if index < len(answers) else []
        if question.secret:
            # Codex marks a question whose answer is a secret. The transcript
            # is read back, copied, and saved, so it gets the fact of an answer
            # and not the answer itself.
            parts.append(
                f'You answered "{question.question}".'
                if chosen
                else f'You left "{question.question}" unanswered.'
            )
            continue
        said = ", ".join(chosen) if chosen else "nothing"
        parts.append(f'You answered "{question.question}" with "{said}".')
    return " ".join(parts)


@dataclass(frozen=True)
class BackendInfo:
    id: str
    label: str
    executable: str
    install_command: str
    login_args: tuple[str, ...]
    supports_model: bool
    supports_effort: bool
    supports_permissions: bool
    supports_steering: bool
    # Whether the provider can summarise a long conversation in place to free
    # up its context window. FreeBuff's CLI has no such command — its only
    # context control is starting a new conversation.
    supports_compaction: bool = False
    # Whether the CLI opens the sign-in page itself. Claude Code and Codex do,
    # even when BlindPilot starts them with no console; FreeBuff deliberately
    # prints the address and tells you to open it yourself. Opening a page the
    # CLI has already opened leaves two tabs on the same authorization, so
    # BlindPilot only opens the ones nobody else will.
    login_opens_browser: bool = False
    # What the CLI's "paste the code from the browser" prompt looks like. It is
    # written without a newline after it, so it is matched against the output
    # as it arrives rather than line by line. Empty when the CLI never asks.
    login_code_prompt: str = ""
    # Whether signing in has to happen in a terminal the user can type into.
    # Claude and Codex authenticate through a browser and report the result on
    # exit, so BlindPilot can run them hidden and watch. Hermes' equivalent is
    # an interactive picker: run hidden with no stdin it simply fails, so the
    # wizard opens a real console instead of pretending to have signed in.
    login_needs_terminal: bool = False
    # Whether the backend takes an attachment's BYTES rather than its path.
    # The CLI backends run on this machine, so naming the file is enough for
    # them. Hermes may be running somewhere else entirely -- in WSL, or on
    # another machine over the network -- where a path from here means nothing
    # or, worse, means a different file that happens to share the name. Those
    # backends are handed the file itself.
    uploads_attachments: bool = False


BACKENDS = {
    BACKEND_CLAUDE: BackendInfo(
        BACKEND_CLAUDE,
        "Claude Code",
        "claude",
        "See https://claude.com/claude-code",
        # "claude /login" is a slash command typed inside the interactive
        # session, not a command line. Run that way it opens a terminal UI that
        # BlindPilot has no console for, so it sat there until it timed out and
        # no browser ever opened. "claude auth login" is the command line.
        ("auth", "login"),
        True,
        True,
        True,
        True,
        supports_compaction=True,
        login_opens_browser=True,
        login_code_prompt=r"[Pp]aste code here[^>]*>",
    ),
    BACKEND_CODEX: BackendInfo(
        BACKEND_CODEX,
        "Codex",
        "codex",
        "npm install -g @openai/codex",
        ("login",),
        True,
        True,
        True,
        True,
        supports_compaction=True,
        login_opens_browser=True,
    ),
    BACKEND_FREEBUFF: BackendInfo(
        BACKEND_FREEBUFF,
        "FreeBuff",
        "freebuff",
        "npm install -g freebuff",
        ("login",),
        True,
        False,
        False,
        True,
        supports_compaction=False,
    ),
    BACKEND_OPENCODE: BackendInfo(
        BACKEND_OPENCODE,
        "opencode",
        "opencode",
        "npm install -g opencode-ai",
        ("providers", "login"),
        True,
        True,
        True,
        True,
        supports_compaction=True,
    ),
    BACKEND_HERMES: BackendInfo(
        BACKEND_HERMES,
        "Hermes",
        "hermes",
        "See https://hermes-agent.nousresearch.com/docs",
        ("model",),
        True,
        # Hermes takes a reasoning level as a per-session override on
        # session.create. This said False at first, with a comment claiming the
        # protocol had no such control; it has one, and the effect is
        # measurable -- a session created with "low" reads back "low" while one
        # created without it reads the profile's own level. Saying False here
        # hid the control in the picker AND told the setup wizard to announce
        # that Hermes "does not expose a reasoning effort level".
        True,
        True,
        True,
        supports_compaction=True,
        login_needs_terminal=True,
        # Hermes' gateway takes an upload of the file, so an attachment works
        # the same whether Hermes is here, in WSL, or on another machine.
        uploads_attachments=True,
    ),
}

# What a "compact this conversation" turn looks like per provider: the text to
# send, and any extra keyword arguments its worker needs.
#
# Claude Code takes ``/compact`` as an ordinary message even in headless
# streaming mode, and acts on it. Codex and opencode have no such message —
# for both it is a request of its own — so their workers are told to compact
# instead, and ignore the text. The text is still shown to the user either
# way, so the row in the list says what was asked for.
_COMPACTION_REQUESTS: dict[str, tuple[str, dict]] = {
    BACKEND_CLAUDE: ("/compact", {}),
    BACKEND_CODEX: ("/compact", {"compact": True}),
    BACKEND_OPENCODE: ("/compact", {"compact": True}),
    # Hermes compacts through a request of its own, like Codex. Its own name
    # for the command is /compress; the text shown to the user says what was
    # asked for in BlindPilot's words, and the worker acts on the flag.
    BACKEND_HERMES: ("/compact", {"compact": True}),
}


def compaction_request(backend: object) -> Optional[tuple[str, dict]]:
    """(message text, extra worker arguments) to compact, or None if it can't."""
    return _COMPACTION_REQUESTS.get(normalize_backend(backend))


def normalize_backend(value: object) -> str:
    """Return a supported backend id, defaulting to Claude Code."""
    if not isinstance(value, str):
        return BACKEND_CLAUDE
    compact = re.sub(r"[\s_-]+", "", value).casefold()
    aliases = {
        "claude": BACKEND_CLAUDE,
        "claudecode": BACKEND_CLAUDE,
        "codex": BACKEND_CODEX,
        "freebuff": BACKEND_FREEBUFF,
        "opencode": BACKEND_OPENCODE,
        "opencodeai": BACKEND_OPENCODE,
        "hermes": BACKEND_HERMES,
        "hermesagent": BACKEND_HERMES,
        "nous": BACKEND_HERMES,
    }
    return aliases.get(compact, BACKEND_CLAUDE)


def backend_label(backend: str) -> str:
    return BACKEND_LABELS[normalize_backend(backend)]


def _fallback_cli_paths(name: str) -> tuple[Path, ...]:
    home = Path.home()
    managed = blindpilot_data_dir() / "npm"
    managed_bin = managed if platform.system() == "Windows" else managed / "bin"
    if platform.system() == "Windows":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        candidates: list[Path] = []
        for suffix in (".exe", ".cmd", ".ps1", ""):
            filename = name + suffix
            candidates.extend(
                [
                    managed_bin / filename,
                    appdata / "npm" / filename,
                    home / ".local" / "bin" / filename,
                    home / ".volta" / "bin" / filename,
                    local / "Microsoft" / "WinGet" / "Links" / filename,
                    local / "Programs" / name / filename,
                ]
            )
        return tuple(candidates)
    return (
        managed_bin / name,
        home / ".local" / "bin" / name,
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
        home / ".npm-global" / "bin" / name,
        home / ".volta" / "bin" / name,
    )


def find_backend_cli(backend: str) -> Optional[str]:
    """Find a provider CLI even in the restricted PATH inherited by a GUI."""
    info = BACKENDS[normalize_backend(backend)]
    if info.id == BACKEND_HERMES:
        # Hermes installs itself under its own home directory rather than into
        # a shared bin, so it has a search of its own that also knows about the
        # virtual environment the gateway is launched from. It is not an npm
        # package, so it returns before the managed-prefix search below.
        from hermes_backend import find_hermes_cli

        return find_hermes_cli()
    # A backend BlindPilot installed itself is complete, writable by this user,
    # and updated through the same prefix. Prefer it over an older system/npm
    # copy that happens to occur earlier on PATH.
    managed = blindpilot_data_dir() / "npm"
    managed_bin = managed if platform.system() == "Windows" else managed / "bin"
    suffixes = (".exe", ".cmd", ".ps1", "") if platform.system() == "Windows" else ("",)
    for suffix in suffixes:
        candidate = managed_bin / f"{info.executable}{suffix}"
        if candidate.is_file():
            return str(candidate)
    found = shutil.which(info.executable)
    if found:
        return found
    for candidate in _fallback_cli_paths(info.executable):
        if candidate.is_file():
            return str(candidate)
    if platform.system() != "Windows":
        shell = os.environ.get("SHELL")
        if shell and os.path.isfile(shell):
            try:
                proc = subprocess.run(
                    [shell, "-l", "-c", f"command -v {info.executable}"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                result = proc.stdout.strip().splitlines()
                if proc.returncode == 0 and result and os.path.isfile(result[0]):
                    return result[0]
            except (OSError, subprocess.TimeoutExpired):
                pass
    return None


def backend_auth_ok(backend: str, timeout: int = 12) -> bool:
    """Best-effort non-interactive authentication check."""
    backend = normalize_backend(backend)
    if backend == BACKEND_HERMES:
        from hermes_backend import hermes_auth_ok

        return hermes_auth_ok(timeout=max(timeout, 25))
    binary = find_backend_cli(backend)
    if not binary:
        return False
    try:
        if backend == BACKEND_CLAUDE:
            proc = subprocess.run(
                [binary, "auth", "status"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                env=subprocess_env(binary),
                **no_window_kwargs(),
            )
            return proc.returncode == 0
        if backend == BACKEND_CODEX:
            proc = subprocess.run(
                [binary, "login", "status"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                env=subprocess_env(binary),
                **no_window_kwargs(),
            )
            return proc.returncode == 0
        if backend == BACKEND_FREEBUFF:
            credential = Path.home() / ".config" / "manicode" / "credentials.json"
            try:
                payload = json.loads(credential.read_text(encoding="utf-8"))
                account = payload.get("default") if isinstance(payload, dict) else None
                return isinstance(account, dict) and all(
                    isinstance(account.get(field), str) and account[field].strip()
                    for field in ("authToken", "fingerprintId", "fingerprintHash")
                )
            except (OSError, ValueError):
                return False
        if backend == BACKEND_OPENCODE:
            return _opencode_auth_ok()
    except (OSError, subprocess.TimeoutExpired):
        return False
    return True


def _opencode_auth_ok() -> bool:
    """Whether opencode has a provider it could actually run a model on.

    Answered from the credentials opencode stored when a provider was
    connected, which is where /connect puts them, plus its own key in the
    environment. This has to answer without starting a server, because it is
    asked while the setup wizard is on screen.

    Deliberately not "is any ``*_API_KEY`` set": opencode does read a provider's
    key straight out of the environment, but so do plenty of programs that have
    nothing to do with it, and a confident yes that turns into a wall at the
    first message is worse than an "unconfirmed" the wizard lets you walk past.
    """
    try:
        payload = json.loads((_opencode_data_dir() / "auth.json").read_text(encoding="utf-8"))
        if isinstance(payload, dict) and payload:
            return True
    except (OSError, ValueError):
        pass
    return bool(os.environ.get("OPENCODE_API_KEY", "").strip())


# A macOS application launched from Finder or the Dock inherits launchd's PATH
# — /usr/bin:/bin:/usr/sbin:/sbin — and nothing else. Every provider CLI
# installed by npm or Homebrew is a `#!/usr/bin/env node` shim, so a child
# started with that PATH dies on "env: node: No such file or directory" before
# it prints anything a person could act on. Asking the login shell what PATH it
# would have given is the only way to recover the directories the user actually
# installed into. It costs a shell start-up, so it is asked once.
_login_shell_path: Optional[list[str]] = None
_LOGIN_SHELL_PATH_LOCK = threading.Lock()


def login_shell_path_dirs() -> list[str]:
    """The PATH a terminal would have, for handing to children (POSIX only)."""
    global _login_shell_path
    with _LOGIN_SHELL_PATH_LOCK:
        if _login_shell_path is not None:
            return list(_login_shell_path)
        dirs: list[str] = []
        shell = os.environ.get("SHELL")
        if platform.system() != "Windows" and shell and os.path.isfile(shell):
            try:
                proc = subprocess.run(
                    [shell, "-l", "-c", "printf '%s\\n' \"$PATH\""],
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                if proc.returncode == 0:
                    dirs = [
                        entry for entry in proc.stdout.strip().split(os.pathsep) if entry.strip()
                    ]
            # A login shell runs the user's own startup files and can fail in
            # ways nothing here can predict. This is a best-effort addition to
            # a PATH that already works for most installs, so a shell that
            # misbehaves costs the addition and nothing else.
            except Exception:
                dirs = []
        _login_shell_path = dirs
        return list(dirs)


def subprocess_env(binary: str) -> dict[str, str]:
    """The environment every provider CLI must be started with.

    The CLI's own directory goes first — an npm or Homebrew shim finds its
    sibling `node` that way — and the login shell's PATH is appended behind
    whatever this process already has, so a Node installed somewhere else
    entirely (nvm, Volta, asdf) is still reachable without displacing a
    runtime BlindPilot manages itself.
    """
    env = os.environ.copy()
    entries = [entry for entry in env.get("PATH", "").split(os.pathsep) if entry.strip()]
    known = set(entries)
    directory = os.path.dirname(binary)
    if directory and directory not in known:
        entries.insert(0, directory)
        known.add(directory)
    for entry in login_shell_path_dirs():
        if entry not in known:
            entries.append(entry)
            known.add(entry)
    env["PATH"] = os.pathsep.join(entries)
    return env


def _codex_app_server_binary(binary: str) -> str:
    """Prefer Codex's native executable over an npm wrapper on Windows.

    Terminating an npm ``.cmd`` launcher does not terminate its native child,
    which leaves the app-server writer lock attached to the thread. Launching
    the packaged executable directly gives BlindPilot reliable ownership and
    makes immediate session continuation possible.
    """
    if platform.system() != "Windows" or Path(binary).suffix.casefold() == ".exe":
        return binary
    package_root = Path(binary).parent / "node_modules" / "@openai" / "codex"
    try:
        candidates = sorted(
            package_root.glob("node_modules/@openai/codex-win32-*/vendor/**/bin/codex.exe")
        )
    except OSError:
        candidates = []
    return str(candidates[0]) if candidates else binary


def codex_model_options(
    cwd: Optional[str] = None,
) -> tuple[list[str], list[str], str, str, str]:
    """Read Codex's installed model catalog and configured defaults."""
    binary = find_backend_cli(BACKEND_CODEX)
    efforts: list[str] = []
    if not binary:
        return [], efforts, "", "", "Codex was not found."
    try:
        result = subprocess.run(
            [binary, "debug", "models", "--bundled"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=subprocess_env(binary),
            **no_window_kwargs(),
        )
        payload = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return [], efforts, "", "", "Could not read the model list from Codex."

    entries = payload if isinstance(payload, list) else payload.get("models", [])
    models: list[str] = []
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            model = entry.get("slug") or entry.get("id") or entry.get("model")
            if isinstance(model, str) and model and model not in models:
                models.append(model)
            supported = entry.get("supported_reasoning_levels") or []
            if isinstance(supported, list):
                for level in supported:
                    effort = level.get("effort") if isinstance(level, dict) else level
                    if isinstance(effort, str) and effort and effort not in efforts:
                        efforts.append(effort)

    if not efforts:
        efforts = ["low", "medium", "high", "xhigh"]

    current_model = ""
    current_effort = ""
    config_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    try:
        import tomllib

        with open(config_home / "config.toml", "rb") as handle:
            config = tomllib.load(handle)
        if isinstance(config.get("model"), str):
            current_model = config["model"]
        if isinstance(config.get("model_reasoning_effort"), str):
            current_effort = config["model_reasoning_effort"]
    except (OSError, ValueError):
        pass
    error = "" if models else "Codex returned an empty model catalog."
    return models, efforts, current_model, current_effort, error


def blindpilot_config_dir() -> Path:
    """Where BlindPilot keeps its own settings, separate from any backend's."""
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "BlindPilot"
    return Path.home() / ".config" / "blindpilot"


def blindpilot_data_dir() -> Path:
    """Per-user, non-roaming storage for managed runtimes and CLI packages."""
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "BlindPilot"
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / "blindpilot"


def _freebuff_choice_path() -> Path:
    return blindpilot_config_dir() / "freebuff-model.json"


def _read_freebuff_choice() -> str:
    """Return the FreeBuff model BlindPilot last selected, if any."""
    try:
        payload = json.loads(_freebuff_choice_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    model = payload.get("model") if isinstance(payload, dict) else None
    return model.strip() if isinstance(model, str) else ""


def _write_freebuff_choice(model: str) -> None:
    """Record the selection somewhere only BlindPilot writes.

    FreeBuff resets ``freebuffModel`` in its own settings to whichever model it
    recommends once a turn has run, so that file cannot be read back as the
    user's choice.  This record is what survives.
    """
    path = _freebuff_choice_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": model}, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def _freebuff_catalog_cache_path() -> Path:
    return blindpilot_config_dir() / "freebuff-catalog.json"


def _read_freebuff_catalog_cache(stamp: tuple[str, float, int]) -> list[str]:
    """The catalog last read out of this exact FreeBuff release, if any.

    Discovering the catalog means scanning a hundred megabytes of compiled
    FreeBuff, which takes seconds.  Doing that once per installed release rather
    than once per run of BlindPilot is the difference between a message that
    sends immediately and one that waits on a file scan first.
    """
    try:
        payload = json.loads(_freebuff_catalog_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    if payload.get("path") != stamp[0] or payload.get("size") != stamp[2]:
        return []
    if abs(float(payload.get("mtime") or 0.0) - stamp[1]) > 0.001:
        return []
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    return [model for model in models if isinstance(model, str) and model]


def _write_freebuff_catalog_cache(stamp: tuple[str, float, int], models: list[str]) -> None:
    path = _freebuff_catalog_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"path": stamp[0], "mtime": stamp[1], "size": stamp[2], "models": models},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _freebuff_package_readme(binary: Optional[str]) -> Optional[Path]:
    if not binary:
        return None
    wrapper = Path(binary)
    candidates = [
        wrapper.parent / "node_modules" / "freebuff" / "README.md",
        wrapper.parent.parent / "lib" / "node_modules" / "freebuff" / "README.md",
    ]
    try:
        resolved = wrapper.resolve()
        candidates.extend([resolved.parent / "README.md", resolved.parent.parent / "README.md"])
    except OSError:
        pass
    return next((path for path in candidates if path.is_file()), None)


def _resolve_minified_string(
    source: str, expression: str, direct: Optional[dict[str, str]] = None
) -> str:
    expression = expression.strip()
    if len(expression) >= 2 and expression[0] == expression[-1] == '"':
        return expression[1:-1]
    if direct is None:
        direct = dict(re.findall(r'(?<![\w$])([A-Za-z_$][\w$]*)="([^"]+)"', source))
    if expression in direct:
        return direct[expression]
    if "." in expression:
        owner, member = expression.split(".", 1)
        marker = f"{owner}={{"
        owner_start = source.find(marker)
        if owner_start >= 0:
            body_start = owner_start + len(marker)
            body = source[body_start : body_start + 3000].split("}", 1)[0]
            value = re.search(rf'(?:^|,){re.escape(member)}:"([^"]+)"', body)
            if value:
                return value.group(1)
    alias = re.search(
        rf"(?<![\w$]){re.escape(expression)}=([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)",
        source,
    )
    if alias and alias.group(1) != expression:
        return _resolve_minified_string(source, alias.group(1), direct)
    return ""


# FreeBuff stamps a release date onto some display names, and revises it in
# place: "DeepSeek V4 Pro" became "DeepSeek V4 Pro 08/13" without the packaged
# README following. Reading the two as different models drops the dated one.
_MODEL_DATE_SUFFIX_RE = re.compile(r"\s+\d{1,2}/\d{1,2}(?:/\d{2,4})?$")


def _readme_offset(readme_text: str, display_name: str) -> int:
    """Where the README documents this model, or -1 if it does not at all."""
    offset = readme_text.find(display_name)
    if offset >= 0:
        return offset
    undated = _MODEL_DATE_SUFFIX_RE.sub("", display_name)
    return readme_text.find(undated) if undated != display_name else -1


def _freebuff_models_from_install(binary: Optional[str]) -> list[str]:
    """Read the current picker catalog from the installed FreeBuff release."""
    executable = (
        Path.home()
        / ".config"
        / "manicode"
        / ("freebuff.exe" if platform.system() == "Windows" else "freebuff")
    )
    readme = _freebuff_package_readme(binary)
    try:
        stat = executable.stat()
        stamp = (str(executable), stat.st_mtime, stat.st_size)
    except OSError:
        return []

    global _freebuff_catalog_cache
    if _freebuff_catalog_cache and _freebuff_catalog_cache[0] == stamp:
        return list(_freebuff_catalog_cache[1])
    remembered = _read_freebuff_catalog_cache(stamp)
    if remembered:
        _freebuff_catalog_cache = (stamp, list(remembered))
        return list(remembered)

    try:
        source = executable.read_bytes().decode("latin-1", errors="ignore")
        readme_text = readme.read_text(encoding="utf-8") if readme else ""
    except OSError:
        return []

    pattern = re.compile(
        r'([A-Za-z_$][\w$]*)=\{id:([^,}]+),displayName:"([^"]+)"'
        r'[^{}]{0,1000}?availability:"(?:always|off_peak_only)"'
    )
    matches = list(pattern.finditer(source))
    if not matches:
        return []
    start = max(0, min(match.start() for match in matches) - 50_000)
    end = min(len(source), max(match.end() for match in matches) + 10_000)
    catalog_source = source[start:end]
    direct = dict(re.findall(r'(?<![\w$])([A-Za-z_$][\w$]*)="([^"]+)"', catalog_source))
    discovered: list[tuple[int, str]] = []
    for match in matches:
        display_name = match.group(3)
        # The packaged README documents the user-facing regular/earned models.
        # If it is absent, the catalog-shaped object is still better than a
        # stale list compiled into BlindPilot.
        order = _readme_offset(readme_text, display_name) if readme_text else -1
        if readme_text and order < 0:
            continue
        model_id = _resolve_minified_string(source, match.group(2), direct)
        if not model_id or "/" not in model_id:
            continue
        discovered.append((order if order >= 0 else match.start(), model_id))

    models: list[str] = []
    for _order, model_id in sorted(discovered):
        if model_id not in models:
            models.append(model_id)
    if FREEBUFF_PREFERRED_MODEL in models:
        models.remove(FREEBUFF_PREFERRED_MODEL)
        models.insert(0, FREEBUFF_PREFERRED_MODEL)
    _freebuff_catalog_cache = (stamp, list(models))
    _write_freebuff_catalog_cache(stamp, list(models))
    return models


def freebuff_model_options() -> tuple[list[str], list[str], str, str, str]:
    """Return the model choices exposed by the installed FreeBuff release."""
    binary = find_backend_cli(BACKEND_FREEBUFF)
    models = _freebuff_models_from_install(binary)
    chosen = _read_freebuff_choice()
    saved = ""
    try:
        settings = Path.home() / ".config" / "manicode" / "settings.json"
        payload = json.loads(settings.read_text(encoding="utf-8"))
        selected = payload.get("freebuffModel") if isinstance(payload, dict) else None
        if isinstance(selected, str) and selected.strip():
            saved = selected.strip()
    except (OSError, ValueError):
        pass
    if not models:
        models = [FREEBUFF_PREFERRED_MODEL]
        error = "Could not refresh FreeBuff's model catalog; showing the preferred default."
    else:
        error = ""
    # A remembered choice is only worth offering while the catalog is unknown.
    # When it was read successfully, a model missing from it is one this
    # release has dropped, and listing it means picking it, waiting on a picker
    # that will never show it, and losing the message.
    if error:
        for candidate in (chosen, saved):
            if candidate and candidate not in models:
                models.append(candidate)
    # BlindPilot's own record wins over FreeBuff's settings file. FreeBuff
    # rewrites that file to its recommended model after a turn, so honouring it
    # would quietly downgrade every following turn to the recommendation.
    current = (
        chosen
        if chosen in models
        else FREEBUFF_PREFERRED_MODEL
        if FREEBUFF_PREFERRED_MODEL in models
        else saved
        if saved in models
        else models[0]
    )
    return models, [], current, "", error


def _freebuff_display_key(text: str) -> str:
    """Letters and digits only, with a version letter before a number dropped.

    The picker paints a display name, never the model id, and the two disagree
    in ways too small to be worth a table and too varied to keep up with:
    ``mimo/mimo-v2.5`` is drawn "MiMo 2.5" and ``deepseek/deepseek-v4-flash``
    is drawn "DeepSeek V4 Flash", so the ``v`` is dropped by one side or the
    other depending on the model. Reducing both sides the same way makes the
    id a substring of the row it belongs to whichever of them carries it.
    """
    return re.sub(r"v(?=\d)", "", re.sub(r"[^a-z0-9]+", "", text.casefold()))


def _freebuff_picker_options(visible: str, models: list[str]) -> tuple[list[str], int]:
    """Return the model IDs painted by FreeBuff's picker and its focused index.

    The index matters as much as the list: the caller moves to a model by
    pressing Down the difference between two positions in it, so a row that is
    on screen and not in this list does not cost that model, it costs every
    model below it. Matching therefore has to recognise every row the picker
    can move to, or recognise none of them.
    """
    # Longest first, so a model whose id is a prefix of another's cannot claim
    # the longer one's row.
    keyed = sorted(
        ((model, _freebuff_display_key(model.rsplit("/", 1)[-1])) for model in models),
        key=lambda pair: len(pair[1]),
        reverse=True,
    )
    options: list[str] = []
    focused = -1
    for raw in visible.splitlines():
        if "│" not in raw:
            continue
        row = _freebuff_display_key(raw)
        matched = next((model for model, key in keyed if key and key in row), "")
        if not matched or matched in options:
            continue
        options.append(matched)
        if "›" in raw or re.search(r"(?:^|│)\s*>\s*", raw):
            focused = len(options) - 1
    return options, focused


def invalidate_backend_cache(backend: str | None = None) -> None:
    """Drop version-derived provider data before an explicit runtime refresh."""
    global _freebuff_catalog_cache, _login_shell_path
    # The wizard may have just put a backend on PATH. The answer cached before
    # that happened is precisely the one that made putting it there necessary.
    _login_shell_path = None
    if backend is None or normalize_backend(backend) == BACKEND_OPENCODE:
        with _OPENCODE_CATALOG_LOCK:
            _opencode_catalog_cache.clear()
    if backend is None or normalize_backend(backend) == BACKEND_FREEBUFF:
        _freebuff_catalog_cache = None
        try:
            _freebuff_catalog_cache_path().unlink()
        except OSError:
            pass


def set_freebuff_model(model: str) -> None:
    """Select the model in FreeBuff, and record the choice as BlindPilot's own."""
    selected = model.strip()
    if not selected:
        models, _efforts, current, _effort, _error = freebuff_model_options()
        selected = current or (models[0] if models else FREEBUFF_PREFERRED_MODEL)
    settings = Path.home() / ".config" / "manicode" / "settings.json"
    with _FREEBUFF_SETTINGS_LOCK:
        _write_freebuff_choice(selected)
        try:
            payload = json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if payload.get("freebuffModel") == selected:
            return
        payload["freebuffModel"] = selected
        settings.parent.mkdir(parents=True, exist_ok=True)
        temporary = settings.with_suffix(".json.blindpilot.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, settings)


# Codex ships `request_user_input` — the tool it stops a turn to ask a
# multiple-choice question with — switched off, and available only in plan mode
# even when switched on. Both are settings rather than protocol, so they are
# passed for this app server alone and nothing is written to the user's
# ~/.codex/config.toml.
_CODEX_QUESTION_FEATURE = "default_mode_request_user_input"
_CODEX_QUESTION_ARGS = (
    "-c",
    "tools.experimental_request_user_input={enabled=true}",
    "--enable",
    _CODEX_QUESTION_FEATURE,
)


def _codex_questions(raw: object) -> tuple[Question, ...]:
    """Read request_user_input's params into BlindPilot's own question shape.

    A question with no options is a free-text one; `isOther` is Codex asking
    for the "Other" answer its tool description tells the model not to write
    itself, so the two together decide whether typing is offered.
    """
    if not isinstance(raw, list):
        return ()
    questions: list[Question] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("question") or "").strip()
        if not text:
            continue
        options: list[QuestionOption] = []
        for option in entry.get("options") or []:
            if isinstance(option, dict) and option.get("label"):
                options.append(
                    QuestionOption(str(option["label"]), str(option.get("description") or ""))
                )
        questions.append(
            Question(
                question=text,
                header=str(entry.get("header") or ""),
                options=tuple(options),
                multi_select=False,
                allow_custom=bool(entry.get("isOther")) or not options,
                secret=bool(entry.get("isSecret")),
                id=str(entry.get("id") or ""),
            )
        )
    return tuple(questions)


class CodexWorker(threading.Thread):
    """Run one Codex turn through the official app-server JSONL protocol."""

    def __init__(
        self,
        prompt: str,
        session_id: Optional[str],
        cwd: str,
        permission_mode: str,
        *,
        model: str = "",
        effort: str = "",
        compact: bool = False,
        on_session: Callable[[str], None],
        on_started: Callable[[], None],
        on_activity: Callable[[str, str], None],
        on_complete: Callable[[str], None],
        on_failed: Callable[[str], None],
        on_done: Callable[[], None],
        on_question: Optional[AskQuestions] = None,
    ) -> None:
        super().__init__(daemon=True)
        self._prompt = prompt
        self._session_id = session_id
        self._cwd = cwd
        self._permission_mode = permission_mode
        self._model = model
        self._effort = effort
        # Compaction is a request of its own rather than a message, so this
        # turn summarises the conversation instead of adding to it.
        self._compact = compact
        self._on_session = on_session
        self._on_started = on_started
        self._on_activity = on_activity
        self._on_complete = on_complete
        self._on_failed = on_failed
        self._on_done = on_done
        self._on_question = on_question
        self._proc: Optional[subprocess.Popen[str]] = None
        self._cancelled = False
        self._write_lock = threading.Lock()
        self._accepting_input = threading.Event()
        self._thread_id = session_id or ""
        self._turn_id = ""
        self._request_id = 10
        self._assistant_parts: list[str] = []
        self._assistant_delta_seen: set[str] = set()
        self._assistant_streams: dict[str, list[str]] = {}
        self._reasoning_streams: dict[str, list[str]] = {}
        self._tool_outputs: dict[str, list[str]] = {}
        self._stderr: list[str] = []
        self._failed = False

    def accepting_input(self) -> bool:
        return self._accepting_input.is_set() and not self._cancelled

    def _send(self, message: dict) -> bool:
        proc = self._proc
        if not proc or not proc.stdin:
            return False
        try:
            data = json.dumps(message, ensure_ascii=False) + "\n"
            with self._write_lock:
                proc.stdin.write(data)
                proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def steer(self, text: str) -> bool:
        if not self.accepting_input() or not self._thread_id or not self._turn_id:
            return False
        return self._send(
            {
                "method": "turn/steer",
                "id": self._next_id(),
                "params": {
                    "threadId": self._thread_id,
                    "input": [{"type": "text", "text": text}],
                    "expectedTurnId": self._turn_id,
                },
            }
        )

    def cancel(self) -> None:
        self._cancelled = True
        self._accepting_input.clear()
        if self._thread_id and self._turn_id:
            self._send(
                {
                    "method": "turn/interrupt",
                    "id": self._next_id(),
                    "params": {"threadId": self._thread_id, "turnId": self._turn_id},
                }
            )
        proc = self._proc
        if proc and proc.poll() is None:
            end_process_group(proc)

    def _fail(self, message: str) -> None:
        """Report why the turn ended, once.

        The first account of a failure is the one that can be acted on. A crash
        while cleaning up after it is not worth speaking over the top of it.
        """
        if self._failed:
            return
        self._failed = True
        self._on_failed(message)

    def run(self) -> None:
        try:
            self._do_run()
        except Exception as exc:
            # `finally` re-enables Send and stops the progress earcon either
            # way, so anything thrown here used to end the turn exactly as a
            # finished one ends - with no answer and nothing said. The
            # traceback went to a stderr the windowed build does not have.
            self._fail(f"BlindPilot stopped reading Codex: {exc}")
        finally:
            self._accepting_input.clear()
            proc = self._proc
            if proc and proc.poll() is None:
                end_process_group(proc, timeout=2)
            self._on_done()

    @staticmethod
    def _policy(mode: str) -> tuple[str, dict]:
        if mode == "bypassPermissions":
            return "never", {"type": "dangerFullAccess"}
        if mode == "plan":
            return "never", {"type": "readOnly", "networkAccess": False}
        if mode in ("auto", "dontAsk"):
            return "never", {"type": "workspaceWrite", "networkAccess": True}
        return "on-request", {"type": "workspaceWrite", "networkAccess": True}

    def _do_run(self) -> None:
        binary = find_backend_cli(BACKEND_CODEX)
        if not binary:
            self._fail("Codex is not installed. Run: npm install -g @openai/codex")
            return
        if self._compact and not self._session_id:
            self._fail("There is no Codex conversation to compact yet")
            return
        try:
            server_binary = _codex_app_server_binary(binary)
            self._proc = subprocess.Popen(
                [server_binary, *_CODEX_QUESTION_ARGS, "app-server", "--stdio"],
                cwd=self._cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                env=subprocess_env(server_binary),
                # npm's `codex` is a Node launcher that runs the real Codex as
                # a child of its own; stopping only the launcher leaves that
                # child holding the app-server session.
                **own_group_kwargs(),
                **no_window_kwargs(),
            )
        except OSError as exc:
            self._fail(f"Failed to launch Codex: {exc}")
            return

        if self._proc.stderr:
            threading.Thread(target=self._read_stderr, daemon=True).start()

        init_id = self._next_id()
        self._send(
            {
                "method": "initialize",
                "id": init_id,
                "params": {
                    "clientInfo": {
                        "name": "blindpilot",
                        "title": "BlindPilot",
                        "version": "0.3.0",
                    },
                    # request_user_input is still marked experimental in the
                    # app-server protocol, and Codex only sends experimental
                    # requests to a client that asked for them.
                    "capabilities": {"experimentalApi": True},
                },
            }
        )
        self._send({"method": "initialized", "params": {}})

        thread_request = self._next_id()
        if self._session_id:
            request = {
                "method": "thread/resume",
                "id": thread_request,
                "params": {"threadId": self._session_id},
            }
        else:
            approval, sandbox = self._policy(self._permission_mode)
            sandbox_type = str(sandbox.get("type", ""))
            params: dict = {
                "cwd": self._cwd,
                "approvalPolicy": approval,
                "sandbox": {
                    "readOnly": "read-only",
                    "workspaceWrite": "workspace-write",
                    "dangerFullAccess": "danger-full-access",
                }.get(sandbox_type, "workspace-write"),
                "serviceName": "blindpilot",
            }
            if self._model:
                params["model"] = self._model
            request = {"method": "thread/start", "id": thread_request, "params": params}
        self._send(request)

        turn_request = 0
        compact_request = 0
        started_notified = False
        assert self._proc.stdout is not None
        for raw in self._proc.stdout:
            if self._cancelled:
                return
            try:
                message = json.loads(raw)
            except ValueError:
                continue

            if message.get("id") == thread_request:
                error = message.get("error")
                if error:
                    self._fail(self._error_text(error, "Could not start a Codex session"))
                    return
                thread = (message.get("result") or {}).get("thread") or {}
                self._thread_id = str(thread.get("id") or self._session_id or "")
                if not self._thread_id:
                    self._fail("Codex did not return a session id")
                    return
                self._on_session(self._thread_id)
                if self._compact:
                    # Compaction is not a message: it answers immediately with
                    # an empty result and then runs a turn of its own, whose
                    # notifications the loop below already understands.
                    compact_request = self._next_id()
                    self._send(
                        {
                            "method": "thread/compact/start",
                            "id": compact_request,
                            "params": {"threadId": self._thread_id},
                        }
                    )
                    continue
                approval, sandbox = self._policy(self._permission_mode)
                params = {
                    "threadId": self._thread_id,
                    "input": [{"type": "text", "text": self._prompt}],
                    "cwd": self._cwd,
                    "approvalPolicy": approval,
                    "sandboxPolicy": sandbox,
                }
                if self._model:
                    params["model"] = self._model
                if self._effort:
                    params["effort"] = self._effort
                turn_request = self._next_id()
                self._send({"method": "turn/start", "id": turn_request, "params": params})
                continue

            if compact_request and message.get("id") == compact_request:
                error = message.get("error")
                if error:
                    self._fail(self._error_text(error, "Codex could not compact"))
                    return
                # The result is empty; the compaction turn announces itself.
                continue

            if turn_request and message.get("id") == turn_request:
                error = message.get("error")
                if error:
                    self._fail(self._error_text(error, "Codex could not start the turn"))
                    return
                turn = (message.get("result") or {}).get("turn") or {}
                self._turn_id = str(turn.get("id") or "")
                self._accepting_input.set()
                if not started_notified:
                    self._on_started()
                    started_notified = True
                continue

            if "method" in message and "id" in message:
                self._handle_server_request(message)
                continue

            method = message.get("method")
            params = message.get("params") or {}
            if method == "turn/started":
                turn = params.get("turn") or {}
                self._turn_id = str(turn.get("id") or self._turn_id)
                self._accepting_input.set()
                if not started_notified:
                    self._on_started()
                    started_notified = True
            elif method == "item/started":
                self._item_started(params.get("item") or {})
            elif method == "item/completed":
                self._item_completed(params.get("item") or {})
            elif method == "item/agentMessage/delta":
                item_id = str(params.get("itemId") or "")
                delta = str(params.get("delta") or "")
                if delta:
                    self._assistant_delta_seen.add(item_id)
                    self._assistant_streams.setdefault(item_id, []).append(delta)
                    self._assistant_parts.append(delta)
            elif method in (
                "item/reasoning/summaryTextDelta",
                "item/reasoning/textDelta",
                "item/plan/delta",
            ):
                delta = str(params.get("delta") or "")
                if delta:
                    item_id = str(params.get("itemId") or "")
                    self._reasoning_streams.setdefault(item_id, []).append(delta)
            elif method == "item/commandExecution/outputDelta":
                item_id = str(params.get("itemId") or "")
                delta = str(params.get("delta") or "")
                if delta:
                    self._tool_outputs.setdefault(item_id, []).append(delta)
            elif method in ("warning", "configWarning"):
                warning = str(params.get("message") or params.get("summary") or "")
                if warning and _CODEX_QUESTION_FEATURE not in warning:
                    # The one warning that is skipped is Codex's notice that
                    # BlindPilot switched a still-developing feature on. It is
                    # true, it is deliberate, and repeating it at the top of
                    # every single turn is noise nobody can act on.
                    self._on_activity("tool", f"Codex warning: {warning}")
            elif method == "turn/completed":
                self._accepting_input.clear()
                turn = params.get("turn") or {}
                status = turn.get("status")
                if status == "failed":
                    self._fail(self._error_text(turn.get("error"), "Codex turn failed"))
                elif status == "interrupted":
                    if not self._cancelled:
                        self._fail("Codex turn was interrupted")
                elif self._compact:
                    # A compaction turn produces no answer text of its own, so
                    # say what happened rather than finishing in silence.
                    self._on_complete("Conversation compacted.")
                else:
                    self._on_complete("".join(self._assistant_parts).strip())
                return

        if not self._cancelled:
            detail = "\n".join(self._stderr[-10:]).strip()
            self._fail(detail or "Codex app server closed before the turn completed")

    def _read_stderr(self) -> None:
        if not self._proc or not self._proc.stderr:
            return
        for line in self._proc.stderr:
            if line.strip():
                self._stderr.append(line.strip())

    @staticmethod
    def _error_text(error: object, fallback: str) -> str:
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
        return fallback

    def _handle_server_request(self, message: dict) -> None:
        method = str(message.get("method") or "")
        request_id = message.get("id")
        mode = self._permission_mode
        if method == "item/commandExecution/requestApproval":
            decision = "accept" if mode in ("auto", "bypassPermissions") else "decline"
            self._send({"id": request_id, "result": {"decision": decision}})
        elif method == "item/fileChange/requestApproval":
            decision = (
                "accept" if mode in ("acceptEdits", "auto", "bypassPermissions") else "decline"
            )
            self._send({"id": request_id, "result": {"decision": decision}})
        elif method == "item/tool/requestUserInput":
            self._answer_user_input(request_id, message.get("params") or {})
        else:
            self._send(
                {
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": "BlindPilot cannot handle this request yet",
                    },
                }
            )

    def _answer_user_input(self, request_id: object, params: dict) -> None:
        """Put request_user_input in front of the person and answer it.

        Codex keys the answers by each question's own id, and takes a list per
        question even where only one answer was asked for. A question nobody
        answered is sent back with an empty list, which is how Codex reads
        "the person had nothing to say to this" — the turn then carries on
        rather than waiting for an answer that is not coming.
        """
        questions = _codex_questions(params.get("questions"))
        answers = self._on_question(questions) if (questions and self._on_question) else None
        self._on_activity("tool", question_summary(questions, answers))
        payload = {
            question.id or question.question: {
                "answers": answers[index] if answers is not None and index < len(answers) else []
            }
            for index, question in enumerate(questions)
        }
        self._send({"id": request_id, "result": {"answers": payload}})

    def _item_started(self, item: dict) -> None:
        kind = item.get("type")
        if kind == "commandExecution":
            command = item.get("command")
            if isinstance(command, list):
                command = " ".join(map(str, command))
            self._on_activity("tool", f"Running: {command or 'command'}")
        elif kind == "fileChange":
            changes = item.get("changes") or []
            paths = [str(c.get("path")) for c in changes if isinstance(c, dict) and c.get("path")]
            self._on_activity("tool", "Editing " + (", ".join(paths) or "files"))
        elif kind == "mcpToolCall":
            self._on_activity(
                "tool",
                f"Using {item.get('server') or 'MCP'}: {item.get('tool') or 'tool'}",
            )
        elif kind == "webSearch":
            self._on_activity("tool", f"Searching the web: {item.get('query') or ''}".strip())
        elif kind == "imageView":
            self._on_activity("tool", f"Viewing {item.get('path') or 'image'}")

    def _item_completed(self, item: dict) -> None:
        kind = item.get("type")
        item_id = str(item.get("id") or "")
        if kind == "agentMessage":
            text = str(item.get("text") or "")
            if item_id in self._assistant_delta_seen:
                streamed = "".join(self._assistant_streams.pop(item_id, []))
                if streamed:
                    self._on_activity("assistant", streamed)
            elif text:
                self._assistant_parts.append(text)
                self._on_activity("assistant", text)
        elif kind in ("reasoning", "plan"):
            reasoning = "".join(self._reasoning_streams.pop(item_id, []))
            if reasoning:
                self._on_activity("thinking", reasoning)
        elif kind == "commandExecution":
            chunks = self._tool_outputs.pop(item_id, [])
            output = "".join(chunks) or str(item.get("aggregatedOutput") or "")
            if output.strip():
                self._on_activity("result", output.strip())
        elif kind == "fileChange":
            changes = item.get("changes") or []
            summary = []
            for change in changes:
                if isinstance(change, dict) and change.get("path"):
                    summary.append(f"{change.get('kind') or 'changed'}: {change['path']}")
            if summary:
                self._on_activity("result", "\n".join(summary))


_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[()][A-Z0-9])")


def _strip_terminal_noise(text: str) -> str:
    text = _ANSI_RE.sub("", text).replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _freebuff_chat_dirs(cwd: str) -> dict[str, float]:
    """Return known FreeBuff chat ids and modification times.

    FreeBuff derives its storage bucket from the Git root, which is not always
    the workspace basename supplied with ``--cwd``.  Search every chat bucket
    so a newly-created id can still be captured and resumed.
    """
    root = Path.home() / ".config" / "manicode" / "projects"
    candidates = [root / Path(cwd).name / "chats", root / "chats"]
    try:
        candidates.extend(p for p in root.glob("*/chats") if p not in candidates)
    except OSError:
        pass
    found: dict[str, float] = {}
    for folder in candidates:
        try:
            for path in folder.iterdir():
                if path.is_dir():
                    found[path.name] = max(found.get(path.name, 0.0), path.stat().st_mtime)
        except OSError:
            pass
    return found


def _freebuff_chat_path(cwd: str, session_id: str) -> Optional[Path]:
    """Locate one FreeBuff chat regardless of its Git-derived project bucket."""
    if not session_id:
        return None
    root = Path.home() / ".config" / "manicode" / "projects"
    candidates = [root / Path(cwd).name / "chats", root / "chats"]
    try:
        candidates.extend(path for path in root.glob("*/chats") if path not in candidates)
    except OSError:
        pass
    for folder in candidates:
        candidate = folder / session_id
        if candidate.is_dir():
            return candidate
    return None


_FREEBUFF_INTERRUPTED = "[response interrupted]"

# The title FreeBuff draws on the box its `ask_user` tool opens. It is the one
# thing on that screen that is always there and never part of an answer, so it
# is what says a turn has stopped to ask something.
# What FreeBuff's start screen says while it is waiting for a model to be
# chosen. The wording has moved between releases - the card was once labelled
# RECOMMENDED and is now introduced by a heading - so all of it is recognised:
# a chooser nobody answers is a terminal that never reaches its composer, and a
# message that is never sent.
_FREEBUFF_PICKER_RE = re.compile(r"(?i)RECOMMENDED|Start coding for free|See all \d+ models?")

_FREEBUFF_QUESTION_MARKER = "Some questions for you"

# A question in that box: collapsed (right-pointing) or open (down-pointing),
# numbered only when there is more than one.
_FREEBUFF_QUESTION_RE = re.compile(r"^([▼▶])\s*(?:\d+\.\s*)?(\S.*?)\s*$")

# One answer in the open question: a radio circle, or a checkbox where the
# question takes more than one answer.
_FREEBUFF_OPTION_RE = re.compile(r"^\s+([○●☐☑])\s+(\S.*?)\s*$")

# FreeBuff adds this entry itself, below the model's own options, and opens a
# text field when it is chosen. BlindPilot's dialog offers the same thing in
# its own words, so the entry is not repeated there.
_FREEBUFF_CUSTOM_LABEL = "Custom"

_FREEBUFF_MULTI_MARKER = "(Select multiple options)"

# The box's bottom-left corner, and the side it draws down both edges.
_FREEBUFF_BOX_BOTTOM = "╰"
_FREEBUFF_BOX_SIDE = "│"
_FREEBUFF_OPEN_MARK = "▼"
_FREEBUFF_CHECKBOXES = ("☐", "☑")

# Keys the box understands. Down moves through the answers, wrapping into the
# next question once it runs off the end of one; Enter chooses; Tab jumps to
# Submit; Escape closes the box, which FreeBuff reports to the model as the
# questions having been skipped.
_KEY_DOWN = "\x1b[B"
_KEY_ENTER = "\r"
_KEY_TAB = "\t"
_KEY_ESCAPE = "\x1b"

# Long enough for OpenTUI to repaint between keystrokes.
_FREEBUFF_KEY_SETTLE = 0.15

# FreeBuff's own tool takes at least one question and puts no ceiling on
# them; this is the point past which a box has stopped being a question
# and started being a loop.
_FREEBUFF_MAX_QUESTIONS = 8


def _freebuff_question_box(visible: str) -> list[str]:
    """The lines of FreeBuff's question box, with its border taken off."""
    lines = visible.split("\n")
    start = next(
        (index for index, line in enumerate(lines) if _FREEBUFF_QUESTION_MARKER in line),
        -1,
    )
    if start < 0:
        return []
    body: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith(_FREEBUFF_BOX_BOTTOM):
            break
        if stripped.startswith(_FREEBUFF_BOX_SIDE):
            stripped = stripped[1:]
        if stripped.endswith(_FREEBUFF_BOX_SIDE):
            stripped = stripped[:-1]
        body.append(stripped.rstrip())
    return body


def _freebuff_open_question(visible: str) -> tuple[int, int, Optional[Question]]:
    """(how many questions there are, which one is open, and that question).

    FreeBuff shows one question's answers at a time and keeps the rest folded
    away, so this reads whichever one is open. The count is what says how many
    more are still to come.
    """
    body = _freebuff_question_box(visible)
    if not body:
        return 0, -1, None
    total = 0
    open_index = -1
    text = ""
    options: list[QuestionOption] = []
    multi = False
    for line in body:
        header = _FREEBUFF_QUESTION_RE.match(line)
        if header:
            if header.group(1) == _FREEBUFF_OPEN_MARK:
                if open_index >= 0:
                    # Two open questions at once is a half-drawn frame; keep
                    # the first, which is the one already read.
                    break
                open_index = total
                text = header.group(2)
                options = []
                multi = False
            total += 1
            continue
        if open_index < 0 or total != open_index + 1:
            continue
        if _FREEBUFF_MULTI_MARKER in line:
            multi = True
            continue
        option = _FREEBUFF_OPTION_RE.match(line)
        if option:
            if option.group(1) in _FREEBUFF_CHECKBOXES:
                multi = True
            options.append(QuestionOption(option.group(2)))
    if open_index < 0 or not text:
        return total, -1, None
    if options and options[-1].label == _FREEBUFF_CUSTOM_LABEL:
        options = options[:-1]
    return total, open_index, Question(question=text, options=tuple(options), multi_select=multi)


# How long a frame must hold still before it is read out. The terminal repaints
# in bursts, so this is only long enough to let one burst land whole.
_FREEBUFF_FRAME_SECONDS = 0.1

# Never wait longer than this to read out a finished sentence, however busy the
# repainting is. Text that arrives faster than the frames settle would otherwise
# never reach the listener until the turn ended.
_FREEBUFF_MAX_LAG_SECONDS = 0.4

# How long a starting FreeBuff may paint nothing at all before the message is
# given up on. This bounds the wait by silence rather than by the clock: any
# repaint at all -- a download progress bar, a splash, the model picker -- is
# progress and starts it again, so a slow first launch that is visibly doing
# something is never cut off. What it does catch is a FreeBuff that starts,
# connects, and then paints nothing ever again, which is what 0.0.163 does
# here: the turn's own deadline is an hour, so waiting that out cost the
# message and an hour of silence with it. Two minutes because a first launch
# downloads a 125MB FreeBuff and then unpacks it, and only the download half
# draws a progress bar.
_FREEBUFF_STARTUP_SILENCE_SECONDS = 120.0

# Sentence-ending punctuation, or the end of a paragraph, either of which is a
# place a listener expects the reading to stop. The definition lives in
# ``markdown_rows`` (which depends on nothing of ours) so the Hermes worker can
# share it without importing this module and closing an import cycle.
_SENTENCE_END_RE = _markdown_sentence_end_re


def _complete_sentences(text: str) -> str:
    """The part of ``text`` that reads as finished, or nothing yet."""
    return complete_sentences(text)


def _unwrap_screen_text(text: str) -> str:
    """Rejoin the lines the terminal broke, keeping the ones the answer meant.

    A captured frame is text laid out to the width of FreeBuff's box, so most of
    its newlines belong to the terminal rather than the answer.  Left in, every
    one of them reads as the end of a sentence, and the reading stops in the
    middle of a clause.  A line that runs the full width was broken because it
    ran out of room and continues below; a short one ended because the text did.
    """
    lines = [line.rstrip() for line in text.splitlines()]
    if len(lines) < 2:
        return text.strip()
    width = max(len(line) for line in lines)
    if width < 24:
        return text.strip()
    pieces: list[str] = []
    for index, line in enumerate(lines[:-1]):
        pieces.append(line)
        following = lines[index + 1]
        wrapped = bool(line) and bool(following) and len(line) >= width - 12
        pieces.append(" " if wrapped else "\n")
    pieces.append(lines[-1])
    return "".join(pieces).strip()


def _keyed(text: str, letters_only: bool = False) -> tuple[str, list[int]]:
    """A comparable form of ``text``, and where each kept character came from.

    Whitespace always goes, because the terminal decides where the answer
    breaks its lines and revises that as the text grows: the same words can be
    one line in one frame and two in the next. Dropping everything except
    letters and digits goes further, and makes the answer as the terminal drew
    it comparable with the Markdown it was drawn from.
    """
    kept: list[str] = []
    positions: list[int] = []
    for index, character in enumerate(text):
        if character.isspace() or (letters_only and not character.isalnum()):
            continue
        kept.append(character.casefold() if letters_only else character)
        positions.append(index)
    positions.append(len(text))
    return "".join(kept), positions


def _unspoken_tail(narrated: str, answer: str) -> str:
    """The part of the finished answer that was never read out.

    What was read came off the screen, laid out for a terminal; the finished
    answer comes from the saved chat, written as Markdown. Comparing only the
    letters and digits is what makes the two comparable, so that an answer
    which scrolled out of view while it was being written is still finished
    aloud, and one that was read in full is not read twice.
    """
    spoken_key, _spoken_positions = _keyed(narrated, letters_only=True)
    answer_key, positions = _keyed(answer, letters_only=True)
    if not answer_key:
        return ""
    if not spoken_key:
        return answer.strip()
    if answer_key.startswith(spoken_key):
        return answer[positions[len(spoken_key)] :].strip()
    if answer_key in spoken_key:
        return ""
    # The two do not line up at all, which means the reading was of something
    # else. Reading the answer again is better than never reading it.
    return answer.strip()


def _append_delta(spoken: str, current: str) -> str:
    """The part of ``current`` that has not been read out yet.

    The screen is a window onto the answer, not the whole of it: once the answer
    is taller than the terminal, the top scrolls away and a frame becomes a
    continuation rather than an extension.  Matching the longest overlap between
    the end of what was read and the start of what is on screen keeps the
    reading in one piece across that scroll, instead of repeating a paragraph or
    silently dropping one.
    """
    if not current:
        return ""
    if not spoken:
        return current
    spoken_key, _spoken_positions = _keyed(spoken)
    current_key, positions = _keyed(current)
    if not current_key:
        return ""
    if current_key.startswith(spoken_key):
        return current[positions[len(spoken_key)] :]
    # The tail of what was read is normally still on screen, so find it there
    # first; that is one string search rather than a scan over every overlap.
    anchor = spoken_key[-160:]
    if anchor:
        position = current_key.rfind(anchor)
        if position >= 0:
            return current[positions[position + len(anchor)] :]
    for size in range(min(len(spoken_key), len(current_key), 400), 0, -1):
        if spoken_key.endswith(current_key[:size]):
            return current[positions[size] :]
    return current


def _freebuff_answer_message(payload: object) -> Optional[dict]:
    """Return the newest AI message that actually carries a reply.

    FreeBuff writes a ``mode-divider`` message ahead of every turn, and it also
    has ``variant: "ai"``.  Skipping messages with no reply blocks keeps the
    identity of the turn's real answer stable from the moment it appears.
    """
    if not isinstance(payload, list):
        return None
    for item in reversed(payload):
        if not isinstance(item, dict) or item.get("variant") != "ai":
            continue
        blocks = item.get("blocks")
        if isinstance(blocks, list) and any(
            isinstance(block, dict) and block.get("type") in ("text", "agent") for block in blocks
        ):
            return item
    return None


def _freebuff_answer_id(chat: Optional[Path]) -> str:
    """Identify the newest answer already in a chat, before a new turn starts."""
    if chat is None:
        return ""
    try:
        payload = json.loads((chat / "chat-messages.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    message = _freebuff_answer_message(payload)
    return str(message.get("id") or "") if message else ""


def _freebuff_chat_snapshot(
    chat: Optional[Path],
) -> tuple[str, str, str, list[tuple[str, str, str]]]:
    """Read the newest answer's id, reasoning, text, and agent states.

    The id is what tells one turn's answer from the one before it: FreeBuff
    rewrites the whole file on every save, so text alone cannot distinguish new
    output from a resumed conversation's history.
    """
    if chat is None:
        return "", "", "", []
    try:
        payload = json.loads((chat / "chat-messages.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", "", "", []
    message = _freebuff_answer_message(payload)
    if message is None:
        return "", "", "", []
    message_id = str(message.get("id") or "")

    thinking: list[str] = []
    answer: list[str] = []
    agents: list[tuple[str, str, str]] = []
    blocks = message.get("blocks")
    if not isinstance(blocks, list):
        blocks = []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            content = str(block.get("content") or "").strip()
            # Closing the hidden terminal stamps this marker onto whatever the
            # turn had produced, so it has to come off the end as well as being
            # rejected on its own.
            if content.endswith(_FREEBUFF_INTERRUPTED):
                content = content[: -len(_FREEBUFF_INTERRUPTED)].strip()
            if not content:
                continue
            if block.get("textType") == "reasoning":
                thinking.append(content)
            else:
                answer.append(content)
        elif kind == "agent":
            name = str(block.get("agentName") or block.get("agentType") or "agent").strip()
            status = str(block.get("status") or "running").strip().casefold()
            agent_id = str(block.get("agentId") or f"{index}:{name}")
            agents.append((agent_id, name, status))
    return message_id, "\n\n".join(thinking), "\n\n".join(answer), agents


def _freebuff_chat_stamp(chat: Optional[Path]) -> tuple:
    """A cheap fingerprint of a chat's files, to skip re-reading them.

    The loop looks at the chat many times a second so that the end of a turn is
    noticed the moment it happens; asking the filesystem what changed is orders
    of magnitude cheaper than parsing the whole conversation to find out.
    """
    if chat is None:
        return ()
    marks: list[tuple[int, int]] = []
    for name in ("chat-messages.json", "log.jsonl"):
        try:
            stat = (chat / name).stat()
            marks.append((stat.st_mtime_ns, stat.st_size))
        except OSError:
            marks.append((0, 0))
    return tuple(marks)


def _freebuff_run_status(chat: Optional[Path], offset: int = 0) -> str:
    """Return complete/cancelled from FreeBuff's authoritative per-chat log."""
    if chat is None:
        return ""
    try:
        with (chat / "log.jsonl").open("rb") as handle:
            handle.seek(max(0, offset))
            tail = handle.read().decode("utf-8", "replace")
    except OSError:
        return ""
    if "Agent run cancelled by user" in tail:
        return "cancelled"
    if "Main prompt finished" in tail:
        return "complete"
    return ""


def _freebuff_launch_failure(visible: str) -> str:
    """Say why FreeBuff's terminal closed, in its own words where it gave any.

    A terminal that dies during start-up has usually printed one line saying
    why. ``env: node: No such file or directory`` is the common one on macOS,
    where an application started from the Dock inherits a PATH holding no Node
    at all, and it is far more use than a guess at reinstalling.
    """
    lines = [line.strip() for line in _strip_terminal_noise(visible).splitlines()]
    reason = next((line for line in reversed(lines) if line), "")
    if len(reason) > 200:
        reason = reason[:199] + "\u2026"
    if not reason:
        return (
            "FreeBuff's terminal closed before it was ready for a prompt, "
            "without saying why. Check that FreeBuff runs in a terminal, then "
            "try again."
        )
    if "node" in reason.casefold() and "not found" in reason.casefold().replace(
        "no such file or directory", "not found"
    ):
        return (
            f"FreeBuff could not start: {reason}. FreeBuff runs on Node.js, and "
            "BlindPilot could not find it. Install Node.js, or use File \u2192 "
            "Manage Backends to let BlindPilot install one for you."
        )
    return f"FreeBuff's terminal closed before it was ready for a prompt: {reason}"


def _freebuff_startup_silence(visible: str, seconds: float) -> str:
    """Say that FreeBuff started, went quiet, and never asked for a prompt.

    A terminal that dies prints why on its way out, and
    :func:`_freebuff_launch_failure` reports that sentence. This is the other
    shape of the same lost message: FreeBuff is still running, still connected,
    and simply never paints a composer to type into.
    """
    waited = int(seconds)
    lines = [line.strip() for line in _strip_terminal_noise(visible).splitlines()]
    reason = next((line for line in reversed(lines) if line), "")
    if len(reason) > 200:
        reason = reason[:199] + "\u2026"
    if reason:
        return (
            f"FreeBuff stopped short of a prompt and printed nothing for {waited} "
            f"seconds. The last thing it showed was: {reason}"
        )
    return (
        f"FreeBuff started but printed nothing at all for {waited} seconds, so "
        "there was never a prompt to send the message to. Run freebuff in a "
        "terminal to see what it does there: if it hangs there too, the "
        "installed FreeBuff release is at fault rather than BlindPilot."
    )


class FreebuffTerminal(Protocol):
    """The pseudo-terminal handle, whichever library produced it.

    winpty on Windows and pexpect everywhere else hand back objects with
    nothing in common but the three calls made on them here. What those calls
    give back differs too, and none of it is used, so none of it is named.
    """

    def write(self, text: str, /) -> object: ...

    def terminate(self, force: bool = False) -> object: ...

    def close(self, force: bool = False) -> object: ...


def _spawn_freebuff_pty(
    args: list[str], cwd: str, stream_ended: threading.Event
) -> tuple[FreebuffTerminal, Callable[[float], str]]:
    """Start FreeBuff under a pseudo-terminal, off screen, and read it.

    Returns the terminal and a call that yields whatever it has produced.
    Output is pumped into a queue by a thread of its own, so a terminal that
    started before anyone asked for its output keeps every byte of it.
    """
    if platform.system() == "Windows":
        from winpty import PtyProcess

        # Own the console before the terminal asks for one, so none is
        # created on screen. The watcher below is the safety net for the
        # consoles FreeBuff's own tools can still raise.
        reserve_hidden_console()
        roots = {os.getpid()}
        holder: dict[str, object] = {}

        def hide_terminal() -> None:
            # Poll hard while the terminal is starting, then slowly: the
            # tools FreeBuff itself starts can each raise a console of
            # their own, and any of them would take the reader's focus.
            # Tearing the terminal down raises one last console, after the
            # output has stopped, so this outlives the stream it guards.
            started = time.monotonic()
            grace_until: Optional[float] = None
            while True:
                now = time.monotonic()
                if stream_ended.is_set():
                    if grace_until is None:
                        grace_until = now + 5
                    elif now > grace_until:
                        return
                try:
                    hide_console_windows(roots)
                except Exception:
                    # Never let this thread die: it is the only thing
                    # keeping the terminal off the screen for the whole run.
                    pass
                quick = now - started < 8 or grace_until is not None
                time.sleep(0.005 if quick else 0.25)

        threading.Thread(target=hide_terminal, daemon=True).start()

        # pywinpty accepts an argv list and supplies a real ConPTY console.
        pty = PtyProcess.spawn(args, dimensions=(60, 180), cwd=cwd)
        holder["pty"] = pty
        pty_pid = getattr(pty, "pid", 0)
        if pty_pid:
            roots.add(int(pty_pid))
        chunks: queue.Queue[str] = queue.Queue()

        def pump() -> None:
            try:
                while pty.isalive():
                    try:
                        data = pty.read(4096)
                    except Exception:
                        break
                    if data:
                        chunks.put(data)
            finally:
                # Nothing more will ever arrive. Saying so turns a terminal
                # that died at startup into a reported failure instead of an
                # hour of silence.
                stream_ended.set()

        threading.Thread(target=pump, daemon=True).start()

        def read(timeout: float) -> str:
            try:
                return chunks.get(timeout=timeout)
            except queue.Empty:
                return ""

        return pty, read

    import pexpect

    child = pexpect.spawn(
        args[0],
        args[1:],
        cwd=cwd,
        encoding="utf-8",
        # Without this the terminal inherits whatever PATH the window was
        # started with. Launched from the macOS Dock that is launchd's, which
        # holds no Node, and FreeBuff's `#!/usr/bin/env node` shim dies before
        # it can paint anything.
        env=subprocess_env(args[0]),
        dimensions=(60, 180),
        timeout=0.25,
    )

    def read_posix(timeout: float) -> str:
        try:
            return child.read_nonblocking(4096, timeout=timeout)
        except pexpect.TIMEOUT:
            return ""
        except pexpect.EOF:
            stream_ended.set()
            return ""

    return child, read_posix


# A FreeBuff terminal takes seconds to reach its composer, and that wait is the
# largest part of how long a message takes to start answering. One is therefore
# started ahead of time, for the conversation the next message will most likely
# continue, and handed to the run that claims it.
_FREEBUFF_PREWARM_LOCK = threading.Lock()
_freebuff_prewarm: Optional[dict] = None
# Long enough to cover thinking time between messages, short enough that an
# abandoned terminal does not sit there all day.
_FREEBUFF_PREWARM_TTL = 15 * 60


def _kill_pty(pty: object) -> None:
    """End the terminal and give the pseudo-terminal itself back.

    Both calls are wanted, not the first one that works: `terminate` stops
    FreeBuff, `close` releases the handle the terminal was reached through.
    Returning after a successful terminate left that handle open every time.
    """
    for method, arguments in (("terminate", (True,)), ("close", (True,))):
        call = getattr(pty, method, None)
        if call is None:
            continue
        try:
            call(*arguments)
        except Exception:
            continue


def discard_freebuff_prewarm() -> None:
    """Throw away any terminal held for the next message."""
    global _freebuff_prewarm
    with _FREEBUFF_PREWARM_LOCK:
        holding, _freebuff_prewarm = _freebuff_prewarm, None
    if holding is not None:
        holding["ended"].set()
        _kill_pty(holding["pty"])


def prewarm_freebuff(cwd: str, session_id: Optional[str], model: str, delay: float = 0.0) -> None:
    """Start the terminal the next FreeBuff message will use.

    Doing nothing here is always safe: a message that finds no terminal waiting,
    or one started for a different conversation, simply starts its own.
    """
    binary = find_backend_cli(BACKEND_FREEBUFF)
    if not binary:
        return
    key = (os.path.abspath(cwd), session_id or "", model or _read_freebuff_choice())
    with _FREEBUFF_PREWARM_LOCK:
        holding = _freebuff_prewarm
        if holding is not None and holding["key"] == key and not holding["ended"].is_set():
            # One is already waiting for exactly this. Starting another would
            # throw away a terminal that has finished starting for one that has
            # not, which is the opposite of the point.
            return

    def work() -> None:
        if delay:
            time.sleep(delay)
        # FreeBuff reads the selected model once, at launch, and rewrites the
        # setting to its own recommendation after a turn. Applying the choice
        # before the terminal starts is what makes a waiting one usable.
        try:
            set_freebuff_model(key[2])
        except OSError:
            return
        args = [binary, "--cwd", cwd]
        if session_id:
            args.extend(["--continue", session_id])
        # A new conversation's chat is created when the terminal starts, not
        # when it is given a message, so the record of which chats existed
        # beforehand has to be taken here. Taken later it would already include
        # this one, and the message would finish without ever learning the id of
        # the conversation it had just had.
        before = _freebuff_chat_dirs(cwd)
        ended = threading.Event()
        try:
            pty, read = _spawn_freebuff_pty(args, cwd, ended)
        except Exception:
            return
        holding = {
            "key": key,
            "pty": pty,
            "read": read,
            "ended": ended,
            "before": before,
            "expires": time.monotonic() + _FREEBUFF_PREWARM_TTL,
        }
        stale = None
        with _FREEBUFF_PREWARM_LOCK:
            global _freebuff_prewarm
            stale, _freebuff_prewarm = _freebuff_prewarm, holding
        if stale is not None:
            stale["ended"].set()
            _kill_pty(stale["pty"])

    threading.Thread(target=work, daemon=True).start()


def _take_freebuff_prewarm(cwd: str, session_id: Optional[str], model: str) -> Optional[dict]:
    """Claim the waiting terminal, if it is the one this message needs."""
    key = (os.path.abspath(cwd), session_id or "", model)
    global _freebuff_prewarm
    with _FREEBUFF_PREWARM_LOCK:
        holding = _freebuff_prewarm
        if holding is None:
            return None
        expired = time.monotonic() > holding["expires"]
        if holding["key"] != key or expired or holding["ended"].is_set():
            # Whatever is waiting cannot serve this message. Drop it rather
            # than leave a terminal running for a conversation nobody resumed.
            _freebuff_prewarm = None
        else:
            _freebuff_prewarm = None
            return holding
    holding["ended"].set()
    _kill_pty(holding["pty"])
    return None


atexit.register(discard_freebuff_prewarm)


class FreebuffWorker(threading.Thread):
    """Drive FreeBuff's interactive TUI through a pseudo-terminal.

    FreeBuff currently has no JSON or headless interface.  A PTY is therefore
    required so its OpenTUI client behaves exactly as it does in a terminal.
    The adapter keeps live narration useful by turning visible TUI updates into
    activity rows and resumes the chat id FreeBuff creates on the next turn.
    """

    _PROMPT_RE = re.compile(r"(?mi)(?:^\s*[›>]\s*$|Enter a coding task or / for commands)")
    _BUSY_RE = re.compile(
        r"(?mi)(?:thinking(?:\.\.\.|…)|working(?:\.\.\.|…)|■\s*Esc|Esc\s+to\s+(?:stop|interrupt))"
    )

    def __init__(
        self,
        prompt: str,
        session_id: Optional[str],
        cwd: str,
        permission_mode: str,
        *,
        model: str = "",
        effort: str = "",
        on_session: Callable[[str], None],
        on_started: Callable[[], None],
        on_activity: Callable[[str, str], None],
        on_complete: Callable[[str], None],
        on_failed: Callable[[str], None],
        on_done: Callable[[], None],
        on_question: Optional[AskQuestions] = None,
    ) -> None:
        super().__init__(daemon=True)
        self._prompt = prompt
        self._session_id = session_id
        self._cwd = cwd
        self._model = model.strip()
        self._on_session = on_session
        self._on_started = on_started
        self._on_activity = on_activity
        self._on_complete = on_complete
        self._on_failed = on_failed
        self._on_done = on_done
        self._on_question = on_question
        self._cancelled = False
        self._accepting_input = threading.Event()
        self._write_lock = threading.Lock()
        self._pty: Optional[FreebuffTerminal] = None
        # Set once the terminal can produce no further output.
        self._stream_ended = threading.Event()
        # Everything read out this turn, and the frame each kind is waiting to
        # see a second time before believing it.
        self._narrated: dict[str, str] = {}
        self._pending_frame: dict[str, str] = {}
        self._failed = False

    def accepting_input(self) -> bool:
        return self._accepting_input.is_set() and not self._cancelled

    def steer(self, text: str) -> bool:
        if not self.accepting_input():
            return False
        return self._submit_text(text)

    def _submit_text(self, text: str) -> bool:
        # OpenTUI handles paste and Enter as separate input events. Sending
        # both in one ConPTY write fills the composer but does not submit it.
        if not self._write(text):
            return False
        time.sleep(0.05)
        return self._write("\r")

    def _write(self, text: str) -> bool:
        if self._pty is None:
            return False
        try:
            with self._write_lock:
                self._pty.write(text)
            return True
        except Exception:
            return False

    def cancel(self) -> None:
        self._cancelled = True
        self._accepting_input.clear()
        pty = self._pty
        if pty is not None:
            # Every turn ends here, through `run`'s `finally`, so this is where
            # a terminal that was stopped but never closed piles up.
            _kill_pty(pty)

    def _fail(self, message: str) -> None:
        """Report why the turn ended, once."""
        if self._failed:
            return
        self._failed = True
        self._on_failed(message)

    def run(self) -> None:
        try:
            self._do_run()
        except Exception as exc:
            # See CodexWorker.run: without this the terminal is torn down, Send
            # comes back, and the turn is over with nothing said about why.
            self._fail(f"BlindPilot stopped reading FreeBuff: {exc}")
        finally:
            self._accepting_input.clear()
            self.cancel()
            self._on_done()

    def _do_run(self) -> None:
        binary = find_backend_cli(BACKEND_FREEBUFF)
        if not binary:
            self._fail("FreeBuff is not installed. Run: npm install -g freebuff")
            return
        # Reading the model catalog means scanning the whole of FreeBuff, which
        # is far too slow to do before sending a message. The recorded choice is
        # all that is needed here; the catalog is only fetched if the model
        # picker actually appears, which happens on a first launch.
        try:
            self._model = self._model or _read_freebuff_choice() or FREEBUFF_PREFERRED_MODEL
            set_freebuff_model(self._model)
        except OSError as exc:
            self._fail(f"Could not select the FreeBuff model: {exc}")
            return
        models: list[str] = []
        before = _freebuff_chat_dirs(self._cwd)
        chat_path = _freebuff_chat_path(self._cwd, self._session_id or "")
        log_offset = 0
        if chat_path is not None:
            try:
                log_offset = (chat_path / "log.jsonl").stat().st_size
            except OSError:
                pass
        args = [binary, "--cwd", self._cwd]
        if self._session_id:
            args.extend(["--continue", self._session_id])
        waiting = _take_freebuff_prewarm(self._cwd, self._session_id, self._model)
        adopted = waiting is not None
        if waiting is not None:
            # A terminal was started for this conversation while the message was
            # being typed, and has been buffering its output ever since. Taking
            # it saves the whole of FreeBuff's start-up. Its own record of the
            # chats that existed before it started is what identifies the new
            # conversation, since it created that chat when it started.
            # The prewarm record holds a terminal, a reader, an event and a
            # listing side by side, so what comes back out of it is opaque
            # until it is named.
            self._pty = cast(FreebuffTerminal, waiting["pty"])
            read = waiting["read"]
            self._stream_ended = waiting["ended"]
            before = waiting["before"]
        else:
            try:
                read = self._spawn_pty(args)
            except ImportError:
                package = "pywinpty" if platform.system() == "Windows" else "pexpect"
                self._fail(
                    f"FreeBuff support needs {package} and pyte. Reinstall BlindPilot dependencies."
                )
                return
            except Exception as exc:
                self._fail(f"Failed to launch FreeBuff: {exc}")
                return
        adopted_at = time.monotonic()

        try:
            import pyte
        except ImportError:
            self._fail("FreeBuff support needs pyte. Reinstall BlindPilot dependencies.")
            return
        screen = pyte.HistoryScreen(180, 60, history=4000)
        stream = pyte.Stream(screen)
        sent = False
        started = False
        ready_since: Optional[float] = None
        last_visible = ""
        # What has been read out already, in the shape the screen paints it, so
        # the next frame can be compared against it.
        spoken_thinking = ""
        spoken_answer = ""
        last_emit_at = 0.0
        structured_answer = ""
        # A resumed chat already holds the previous turn's answer. Remember its
        # id so it is never mistaken for this turn's, and so the real answer is
        # recognised as new the moment FreeBuff writes it.
        baseline_answer_id = _freebuff_answer_id(chat_path)
        structured_answer_id = ""
        chat_stamp: tuple = ()
        run_status = ""
        agent_states: dict[str, str] = {}
        screen_dirty = False
        screen_changed_at = time.monotonic()
        accepted_recommended_model = False
        picker_expanded = False
        picker_expanded_at = 0.0
        saw_busy = False
        session_reported = bool(self._session_id)
        next_session_check = time.monotonic()
        turn_started_at: Optional[float] = None
        next_heartbeat = float("inf")
        completion_seen_at: Optional[float] = None
        deadline = time.monotonic() + 60 * 60

        def refresh(wait: float) -> str:
            """Feed whatever the terminal has produced and re-read the screen.

            The question box is driven a keystroke at a time, and every one of
            them has to be seen to land before the next is sent. This is the
            main loop's own reading, pulled out so both can use it.
            """
            nonlocal last_visible, screen_changed_at, screen_dirty
            until = time.monotonic() + max(0.0, wait)
            while True:
                waiting = read(0.03)
                if waiting:
                    stream.feed(waiting)
                if time.monotonic() >= until:
                    break
            current = "\n".join(line.rstrip() for line in screen.display).strip()
            if current != last_visible:
                last_visible = current
                screen_changed_at = time.monotonic()
                screen_dirty = True
            return current

        def restart() -> Optional[Callable[[float], str]]:
            """Replace a terminal that was waiting and is no longer usable.

            One started ahead of time can have been sitting for a quarter of an
            hour, and can have died or moved on in that time. Starting again
            costs the usual wait; refusing to would cost the message.
            """
            nonlocal before
            _kill_pty(self._pty)
            # The terminal being replaced had a whole FreeBuff in it, and one
            # rewrites the model setting to its own recommendation as it goes.
            # The replacement reads that setting at launch, so the choice has to
            # be put back before it starts rather than only before the first.
            try:
                set_freebuff_model(self._model)
            except OSError:
                pass
            before = _freebuff_chat_dirs(self._cwd)
            self._stream_ended = threading.Event()
            try:
                return self._spawn_pty(args)
            except Exception as exc:
                self._fail(f"Failed to launch FreeBuff: {exc}")
                return None

        while not self._cancelled and time.monotonic() < deadline:
            # Short enough that a frame is picked up as soon as it lands. The
            # answer is read off the screen, so this interval is the floor on
            # how quickly a finished sentence can reach the listener.
            chunk = read(0.03)
            if chunk:
                # Take everything already waiting as one frame. A repaint
                # arrives as a burst of small writes, and feeding them one at a
                # time would mean laying out the screen over and over.
                for _ in range(256):
                    more = read(0)
                    if not more:
                        break
                    chunk += more
            stale = adopted and not sent and time.monotonic() - adopted_at >= 12
            if stale or (not chunk and self._stream_ended.is_set() and not sent and adopted):
                adopted = False
                replacement = restart()
                if replacement is None:
                    return
                read = replacement
                screen = pyte.HistoryScreen(180, 60, history=4000)
                stream = pyte.Stream(screen)
                last_visible = ""
                screen_dirty = False
                # The replacement has painted nothing yet, and nothing is what
                # the startup wait is measured in, so start it again here.
                screen_changed_at = time.monotonic()
                continue
            if not chunk and self._stream_ended.is_set():
                if not sent:
                    # Whatever killed it said so on the terminal before dying —
                    # a missing Node, a failed download, a refused login. That
                    # sentence is the whole of what the person can act on, so
                    # it is reported instead of a guess.
                    self._fail(_freebuff_launch_failure(last_visible))
                    return
                # It ended mid-turn, so whatever was captured is the whole
                # answer; fall through to report it rather than wait it out.
                break
            if chunk:
                stream.feed(chunk)
                visible = "\n".join(line.rstrip() for line in screen.display).strip()
                if visible != last_visible:
                    last_visible = visible
                    ready_since = None
                    screen_changed_at = time.monotonic()
                    screen_dirty = True

            # FreeBuff opens on a model chooser rather than the composer.
            # Accept its highlighted model, or navigate to the chosen one, so
            # the hidden terminal reaches a prompt it can be given a message at.
            if (
                not accepted_recommended_model
                and not sent
                and _FREEBUFF_PICKER_RE.search(last_visible)
            ):
                if not models:
                    models = freebuff_model_options()[0]
                picker_models, focused = _freebuff_picker_options(last_visible, models)
                if (
                    self._model in picker_models
                    and focused >= 0
                    and picker_models[focused] == self._model
                ):
                    self._write("\r")
                    accepted_recommended_model = True
                    continue
                if not picker_expanded:
                    # Move from the recommended card to "See all models" and
                    # open it. The expanded picker is then navigated from its
                    # actual runtime ordering, so model catalog changes do not
                    # require a BlindPilot update.
                    self._write("\x1b[B")
                    time.sleep(0.05)
                    self._write("\r")
                    picker_expanded = True
                    picker_expanded_at = time.monotonic()
                    continue
                if self._model in picker_models and focused >= 0:
                    target = picker_models.index(self._model)
                    arrow = "\x1b[B" if target > focused else "\x1b[A"
                    for _step in range(abs(target - focused)):
                        self._write(arrow)
                        time.sleep(0.05)
                    self._write("\r")
                    accepted_recommended_model = True
                    continue
                if time.monotonic() - picker_expanded_at >= 5:
                    # FreeBuff drops models between releases. Throwing the
                    # message away over that is a worse answer than running it
                    # on what FreeBuff is offering instead, provided the swap
                    # is said out loud rather than made quietly.
                    self._on_activity(
                        "tool",
                        f"FreeBuff no longer offers {self._model}; "
                        "using the model it recommends instead",
                    )
                    self._write(_KEY_ENTER)
                    accepted_recommended_model = True
                    continue

            if (
                sent
                and _FREEBUFF_QUESTION_MARKER in last_visible
                # An answered box stays on screen with every question folded
                # away, so the title alone is not enough: it is a question
                # actually being asked that stops the turn.
                and _freebuff_open_question(last_visible)[2] is not None
            ):
                # The turn has stopped to ask something and will not move again
                # until it is answered, so this takes over the loop until the
                # box is gone.
                if self._answer_questions(refresh):
                    continue

            at_prompt = bool(self._PROMPT_RE.search(last_visible))
            busy = bool(self._BUSY_RE.search(last_visible))
            if sent and busy:
                saw_busy = True
            if not sent and at_prompt:
                if not self._submit_text(self._prompt):
                    self._fail("Could not send the prompt to FreeBuff")
                    return
                sent = True
                started = True
                turn_started_at = time.monotonic()
                next_heartbeat = turn_started_at + 30
                # A resumed TUI initially contains the previous turn. Treat it
                # as a baseline so it is not announced again while the new
                # prompt is being painted onto the screen.
                previous_thinking, previous_answer = self._freebuff_sections(last_visible)
                spoken_thinking = _complete_sentences(_unwrap_screen_text(previous_thinking))
                spoken_answer = _complete_sentences(_unwrap_screen_text(previous_answer))
                self._accepting_input.set()
                self._on_started()
                continue

            now = time.monotonic()
            if not sent and now - screen_changed_at >= _FREEBUFF_STARTUP_SILENCE_SECONDS:
                self._fail(
                    _freebuff_startup_silence(last_visible, _FREEBUFF_STARTUP_SILENCE_SECONDS)
                )
                return
            if sent and now >= next_session_check:
                next_session_check = now + 1.0
                if chat_path is None:
                    after = _freebuff_chat_dirs(self._cwd)
                    new_ids = set(after) - set(before)
                    discovered = (
                        max(new_ids, key=lambda chat_id: after[chat_id])
                        if new_ids
                        else self._session_id
                    )
                    if discovered:
                        self._session_id = discovered
                        chat_path = _freebuff_chat_path(self._cwd, discovered)
                        log_offset = 0
                if self._session_id and not session_reported:
                    self._on_session(self._session_id)
                    session_reported = True

            if sent and chat_path is not None:
                # Parsing the chat costs far more than looking at it, and it is
                # looked at many times a second, so it is only read when
                # FreeBuff has actually written to it.
                stamp = _freebuff_chat_stamp(chat_path)
                if stamp != chat_stamp:
                    chat_stamp = stamp
                    answer_id, _thinking, answer, agents = _freebuff_chat_snapshot(chat_path)
                    if answer_id and answer_id == baseline_answer_id:
                        # Still the answer this turn was resumed from; nothing
                        # of this turn has been written yet.
                        answer_id, answer, agents = "", "", []
                    elif answer_id and answer_id != structured_answer_id:
                        # FreeBuff opened this turn's answer. Everything tracked
                        # so far belonged to an earlier message.
                        structured_answer_id = answer_id
                        baseline_answer_id = ""
                        agent_states.clear()
                    # The file is written whole, once the reply is finished, so
                    # it is the authoritative text of the answer rather than a
                    # source to read from: the reading happens off the screen.
                    if answer:
                        structured_answer = answer
                    for agent_id, name, status in agents:
                        previous_status = agent_states.get(agent_id)
                        if previous_status is None:
                            self._on_activity("tool", f"FreeBuff started {name}")
                        elif previous_status != status and status in ("complete", "completed"):
                            self._on_activity("result", f"FreeBuff finished {name}")
                        agent_states[agent_id] = status
                    run_status = _freebuff_run_status(chat_path, log_offset)
                if run_status == "cancelled":
                    self._fail("FreeBuff reported that the response was interrupted")
                    return
                if run_status == "complete":
                    if completion_seen_at is None:
                        completion_seen_at = now
                    elif now - completion_seen_at >= 0.2:
                        break

            if sent and now >= next_heartbeat:
                elapsed = max(1, int(now - (turn_started_at or now)))
                self._on_activity("tool", f"FreeBuff is still working; {elapsed} seconds elapsed")
                next_heartbeat = now + 30
            # The screen is the only place the answer appears as it is written:
            # the chat file is not saved until the reply is finished. So the
            # reading follows the terminal, one finished sentence at a time.
            # A frame is taken once it has held still for a moment, or, if the
            # text is arriving faster than that, as soon as the wait would start
            # to be audible.
            if started and screen_dirty:
                settled = now - screen_changed_at >= _FREEBUFF_FRAME_SECONDS
                overdue = now - last_emit_at >= _FREEBUFF_MAX_LAG_SECONDS
                if settled or overdue:
                    thinking, answer = self._freebuff_sections(last_visible)
                    spoken_thinking = self._emit_screen_delta("thinking", spoken_thinking, thinking)
                    spoken_answer = self._emit_screen_delta("assistant", spoken_answer, answer)
                    screen_dirty = False
                    last_emit_at = now
            if sent and saw_busy and at_prompt and not busy and chat_path is None:
                if ready_since is None:
                    ready_since = time.monotonic()
                elif time.monotonic() - ready_since >= 1.0:
                    break

        if self._cancelled:
            return
        if not sent:
            self._fail("FreeBuff did not become ready for input")
            return

        self._accepting_input.clear()
        # The turn ends holding the last sentence, which never became "finished
        # text" while the answer was still growing. Nothing arrives after this,
        # so release the rest of the frame whole.
        screen_thinking, screen_response = self._freebuff_sections(last_visible)
        self._emit_screen_delta("thinking", spoken_thinking, screen_thinking, whole=True)
        self._emit_screen_delta("assistant", spoken_answer, screen_response, whole=True)
        # The saved chat is the answer as FreeBuff wrote it, rather than as the
        # terminal laid it out, so it is what the transcript keeps.
        response = structured_answer or _unwrap_screen_text(screen_response)
        if response:
            # An answer taller than the terminal scrolls its own beginning off
            # the screen, and the reading stops there. The saved chat is the
            # whole of it, so whatever was never read is read now.
            tail = _unspoken_tail(self._narrated.get("assistant", ""), response)
            if tail:
                self._on_activity("assistant", tail)
            self._on_complete(response)
        else:
            self._fail("No response received from FreeBuff")
            return

        after = _freebuff_chat_dirs(self._cwd)
        new_ids = set(after) - set(before)
        session = max(new_ids, key=lambda chat_id: after[chat_id]) if new_ids else self._session_id
        if session and not session_reported:
            self._on_session(session)
        # The next message in this conversation should not have to wait for a
        # terminal to start, so start one now, while nobody is waiting on it.
        if session:
            prewarm_freebuff(self._cwd, session, self._model, delay=1.0)

    def _press(self, key: str, times: int = 1) -> None:
        """Send one of the box's keys, giving OpenTUI time to repaint."""
        for _ in range(max(0, times)):
            self._write(key)
            time.sleep(_FREEBUFF_KEY_SETTLE)

    def _answer_questions(self, refresh: Callable[[float], str]) -> bool:
        """Work through FreeBuff's question box and submit the answers.

        FreeBuff has no way to be told an answer other than the box it drew, so
        this is the box being used: each question is put to the person in a
        dialog, and what they chose is walked to with the arrow keys and chosen
        with Enter. Only one question's answers are on screen at a time, which
        is why they are asked one at a time rather than all at once.

        Returns whether the box was dealt with, so a frame that turned out to
        be half-drawn can be left for the next pass.
        """
        answered: set[str] = set()
        submit = False
        # Bounded so a box that never changes cannot spin: two passes per
        # question is already more than any answer needs.
        for _pass in range(2 * _FREEBUFF_MAX_QUESTIONS):
            if self._cancelled:
                return True
            visible = refresh(_FREEBUFF_KEY_SETTLE)
            if _FREEBUFF_QUESTION_MARKER not in visible:
                # FreeBuff closed the box itself; nothing left to answer.
                return bool(answered)
            total, index, question = _freebuff_open_question(visible)
            if question is None:
                visible = refresh(0.5)
                total, index, question = _freebuff_open_question(visible)
                if question is None:
                    return False
            if question.question in answered:
                # The same question is still open, so the keystrokes did not
                # land where they were meant to. Send what has been answered
                # rather than ask again in a loop.
                submit = True
                break
            chosen = self._ask_one(question)
            if chosen is None:
                # Escape closes the box, and FreeBuff tells the model the
                # questions were skipped — which is the truth.
                self._press(_KEY_ESCAPE)
                refresh(0.5)
                return True
            answered.add(question.question)
            self._choose(question, chosen)
            if index >= total - 1:
                submit = True
                break
        if submit:
            # Tab moves to Submit from an answer and is ignored once Submit
            # already has the focus, so it is safe either way.
            self._press(_KEY_TAB)
            self._press(_KEY_ENTER)
        refresh(0.5)
        return True

    def _ask_one(self, question: Question) -> Optional[list[str]]:
        """Put one question to the person. None if they closed the dialog."""
        if self._on_question is None:
            return None
        answers = self._on_question([question])
        chosen = answers[0] if answers else None
        self._on_activity("tool", question_summary([question], answers))
        if not chosen:
            return None
        return chosen

    def _choose(self, question: Question, chosen: list[str]) -> None:
        """Walk the box to the chosen answers and take them."""
        # The focus opens on the first answer, and "Custom" sits one past the
        # last of the model's own, which is where anything typed goes.
        labels = [option.label for option in question.options]
        custom = len(labels)
        position = 0
        if not question.multi_select:
            answer = chosen[0]
            target = labels.index(answer) if answer in labels else custom
            self._press(_KEY_DOWN, target)
            self._press(_KEY_ENTER)
            if target == custom:
                # Choosing "Custom" opens a text field with the cursor in it;
                # Enter closes it and moves on to the next question.
                self._write(answer)
                time.sleep(_FREEBUFF_KEY_SETTLE)
                self._press(_KEY_ENTER)
            # A chosen answer moves the box on by itself.
            return
        typed = [text for text in chosen if text not in labels]
        for target in sorted(labels.index(text) for text in chosen if text in labels):
            self._press(_KEY_DOWN, target - position)
            self._press(_KEY_ENTER)
            position = target
        if typed:
            self._press(_KEY_DOWN, custom - position)
            self._press(_KEY_ENTER)
            self._write(", ".join(typed))
            time.sleep(_FREEBUFF_KEY_SETTLE)
            self._press(_KEY_ENTER)
            position = custom
        # Ticking a box leaves the focus where it was, so the next question has
        # to be walked to: one step past the last answer wraps onto it.
        self._press(_KEY_DOWN, custom - position + 1)

    def _emit_screen_delta(self, kind: str, spoken: str, current: str, whole: bool = False) -> str:
        """Read out whatever the screen has gained since the last frame.

        Only finished sentences are released while the answer is still being
        written: the live edge of a frame is a half-written word, and half a
        sentence read aloud is what makes a run sound broken. At the end of the
        turn nothing more is coming, so the rest is released as it stands.
        """
        text = _unwrap_screen_text(current)
        ready = text if whole else _complete_sentences(text)
        if not ready:
            return spoken
        addition = _append_delta(spoken, ready)
        unrelated = bool(spoken) and addition == ready and len(ready) < len(spoken)
        if unrelated and not whole:
            # This frame shares nothing with what was read and holds less of it,
            # which is what a half-drawn repaint looks like. Wait to see it
            # again before reading it, rather than read a torn frame aloud —
            # but do not wait forever, because it is also what the start of a
            # genuinely shorter answer looks like.
            if self._pending_frame.get(kind) != ready:
                self._pending_frame[kind] = ready
                return spoken
        self._pending_frame.pop(kind, None)
        addition = addition.strip()
        if addition:
            self._on_activity(kind, addition)
            narrated = self._narrated.get(kind, "")
            self._narrated[kind] = f"{narrated}\n{addition}" if narrated else addition
        return ready

    def _spawn_pty(self, args: list[str]) -> Callable[[float], str]:
        self._pty, read = _spawn_freebuff_pty(args, self._cwd, self._stream_ended)
        return read

    def _freebuff_sections(self, visible: str) -> tuple[str, str]:
        """Extract reasoning and answer text from FreeBuff's rendered screen."""
        raw_lines = _strip_terminal_noise(visible).splitlines()
        # This turn's output is whatever follows the echo of this turn's prompt.
        # A resumed conversation paints the turn before it above that, reasoning
        # and all, and none of that is an answer to what was just asked.
        needle = next((line.strip() for line in self._prompt.splitlines() if line.strip()), "")[:60]
        prompt_index = -1
        if needle:
            for index, raw in enumerate(raw_lines):
                if needle in raw:
                    prompt_index = index
        start = prompt_index + 1 if prompt_index >= 0 else 0

        thinking_index = -1
        thinking_indent = 0
        for index in range(start, len(raw_lines)):
            raw = raw_lines[index]
            if re.fullmatch(r"\s*[•*]\s*Thinking\s*", raw, re.IGNORECASE):
                thinking_index = index
                thinking_indent = len(raw) - len(raw.lstrip())

        if thinking_index >= 0:
            candidates = raw_lines[thinking_index + 1 :]
            in_thinking = True
        elif prompt_index >= 0:
            candidates = raw_lines[start:]
            in_thinking = False
        else:
            # The prompt has scrolled off the top, so there is no way to tell
            # this turn's output from the conversation above it. What is missed
            # here is read out from the saved chat once the turn ends.
            candidates = []
            in_thinking = False

        thinking: list[str] = []
        answer: list[str] = []
        for raw in candidates:
            stripped = raw.strip()
            if not stripped:
                continue
            lower = stripped.lower()
            if self._PROMPT_RE.search(stripped):
                break
            # The status bar sits between the answer and the composer, and
            # carries the model, what is left of the session, and the control
            # that ends it. None of that is the answer.
            if lower.endswith("end session") or re.search(
                r"\bunlimited\b.*(?:end session|[✕x])", lower
            ):
                break
            if lower.startswith(
                (
                    "freebuff ",
                    "esc ",
                    "ctrl+",
                    "shift+",
                    "sponsored",
                    "ad ",
                    "learn more",
                    "directory:",
                    "model:",
                    "start monetizing",
                    "get api access",
                )
            ):
                continue
            if lower.startswith(("thinking...", "thinking…", "working...", "working…")):
                continue
            if (
                lower.endswith(" ad │")
                or "learn more" in lower
                or "get api access" in lower
                or "start monetizing" in lower
                or "sponsored" in lower
                or (
                    stripped.startswith("│")
                    and stripped.endswith("│")
                    and re.search(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)+\b", lower)
                )
            ):
                continue
            if re.fullmatch(r"[╭╮╰╯│─━═┄┈\s]+", raw):
                continue
            if stripped.startswith("⎘") or re.fullmatch(r"[⎘•△▽✕xs\d.\s]+", stripped):
                continue
            if self._BUSY_RE.search(stripped):
                continue
            if stripped.startswith("│") and stripped.endswith("│"):
                continue
            if "▸" in stripped and re.search(r"\b(?:running|completed)\b", lower):
                continue

            indent = len(raw) - len(raw.lstrip())
            if in_thinking and indent > thinking_indent:
                thinking.append(stripped)
                continue
            in_thinking = False
            answer.append(stripped)

        return "\n".join(thinking).strip(), "\n".join(answer).strip()

    def _clean_freebuff_screen(self, visible: str) -> str:
        """Compatibility helper returning only user-facing answer text."""
        return self._freebuff_sections(visible)[1]


# ------------------------------------------------------------------------
# opencode
#
# opencode is driven through its own headless HTTP server rather than through
# one process per turn, because the server is the same surface its terminal
# interface talks to and is the only one that exposes everything BlindPilot
# needs from a provider: a streaming answer, steering a turn that is already
# running, answering a permission request, compaction, the catalog behind
# ``/model``, and the provider catalog behind ``/connect``. One server is
# started on first use and shared by every tab. It listens on the loopback
# interface behind a password generated for this run, so nothing else on the
# machine can drive it.

_OPENCODE_SERVER_LOCK = threading.Lock()
_opencode_server: Optional["OpencodeServer"] = None
_OPENCODE_CATALOG_LOCK = threading.Lock()
# One catalog per working directory: a project can pin its own model list.
_opencode_catalog_cache: dict[str, dict] = {}

# Effort levels are per-model in opencode — a model's "variants". The picker
# wants one list, so the levels every model offers are pooled and shown in the
# order a person would expect rather than the order they were discovered in.
_OPENCODE_EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "max", "thinking")

# What BlindPilot's provider-neutral permission modes mean to opencode. A rule
# set is applied in order, so the wildcard comes first and the exceptions
# follow, which is the shape opencode's own agents use. "default" is absent on
# purpose: it means "whatever opencode itself is configured to do", so no rule
# set is sent at all.
_OPENCODE_PERMISSIONS: dict[str, list[dict[str, str]]] = {
    "acceptEdits": [
        {"permission": "*", "pattern": "*", "action": "allow"},
        {"permission": "bash", "pattern": "*", "action": "ask"},
        {"permission": "external_directory", "pattern": "*", "action": "ask"},
        {"permission": "doom_loop", "pattern": "*", "action": "ask"},
    ],
    "plan": [
        {"permission": "*", "pattern": "*", "action": "allow"},
        {"permission": "edit", "pattern": "*", "action": "deny"},
        {"permission": "bash", "pattern": "*", "action": "ask"},
        {"permission": "external_directory", "pattern": "*", "action": "deny"},
    ],
    "auto": [
        {"permission": "*", "pattern": "*", "action": "allow"},
        {"permission": "external_directory", "pattern": "*", "action": "ask"},
    ],
    "bypassPermissions": [
        {"permission": "*", "pattern": "*", "action": "allow"},
    ],
}

# opencode ships a "plan" agent whose whole job is the mode BlindPilot calls
# plan, so plan mode selects it rather than trying to describe it in rules.
_OPENCODE_AGENTS = {"plan": "plan"}


def _opencode_data_dir() -> Path:
    """Where opencode keeps its database and credentials, on every platform."""
    override = os.environ.get("OPENCODE_DATA")
    if override:
        return Path(override)
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "opencode"


def _free_port() -> int:
    """A port the operating system says is free right now."""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _opencode_server_binary(binary: str) -> str:
    """Prefer opencode's own executable over an npm wrapper on Windows.

    Same reason Codex gets the same treatment: terminating a ``.cmd`` launcher
    does not terminate the native child it started, and a server nobody owns
    keeps both its port and its database open for the rest of the session.
    """
    if platform.system() != "Windows" or Path(binary).suffix.casefold() == ".exe":
        return binary
    candidate = Path(binary).parent / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
    return str(candidate) if candidate.is_file() else binary


def opencode_error_text(error: object, fallback: str) -> str:
    """A sentence worth speaking out of whatever the server or urllib raised."""
    import urllib.error

    if isinstance(error, urllib.error.HTTPError):
        try:
            payload = json.loads(error.read().decode("utf-8", "replace"))
        except (OSError, ValueError):
            payload = None
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict) and isinstance(data.get("message"), str):
                return data["message"]
            for key in ("message", "error"):
                if isinstance(payload.get(key), str) and payload[key]:
                    return str(payload[key])
        return f"{fallback} (HTTP {error.code})"
    if isinstance(error, dict):
        data = error.get("data")
        if isinstance(data, dict) and isinstance(data.get("message"), str):
            return data["message"]
        for key in ("message", "name"):
            if isinstance(error.get(key), str) and error[key]:
                return str(error[key])
    text = str(error).strip()
    return text or fallback


class OpencodeServer:
    """The one ``opencode serve`` process BlindPilot talks to."""

    def __init__(self, binary: str) -> None:
        import base64
        import secrets
        from collections import deque

        password = secrets.token_urlsafe(24)
        env = subprocess_env(binary)
        env["OPENCODE_SERVER_PASSWORD"] = password
        self._log: "deque[str]" = deque(maxlen=50)
        self._url = ""
        self._listening = threading.Event()
        self._auth = "Basic " + base64.b64encode(f"opencode:{password}".encode("utf-8")).decode(
            "ascii"
        )
        # Started from the home directory rather than any one project: one
        # server serves them all, and the ``directory`` query parameter on each
        # request is what says which project a call is about.
        self._proc = subprocess.Popen(
            [binary, "serve", "--port", str(_free_port()), "--hostname", "127.0.0.1"],
            cwd=str(Path.home()),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            env=env,
            **own_group_kwargs(),
            **no_window_kwargs(),
        )
        threading.Thread(target=self._pump, daemon=True).start()
        self._listening.wait(60)
        if not self._url:
            self.stop()
            detail = "\n".join(list(self._log)[-5:]).strip()
            raise OSError(detail or "opencode's server did not start.")

    # The reader doubles as the thing that keeps the pipe from filling up, so
    # it runs for the life of the process rather than only until start-up.
    def _pump(self) -> None:
        stdout = self._proc.stdout
        if stdout is not None:
            for line in stdout:
                text = line.rstrip()
                if text:
                    self._log.append(text)
                if not self._url:
                    found = re.search(r"listening on (http://\S+)", text)
                    if found:
                        self._url = found.group(1).rstrip("/")
                        self._listening.set()
        # The process ended. Release anyone still waiting to be told the URL.
        self._listening.set()

    @property
    def base_url(self) -> str:
        return self._url

    def alive(self) -> bool:
        return self._proc.poll() is None

    def open(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        body: object = None,
        timeout: Optional[float] = 120,
    ):
        """The raw response, for callers that want to read it as it arrives."""
        import urllib.parse
        import urllib.request

        url = self._url + path
        query = {key: value for key, value in (params or {}).items() if value}
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", self._auth)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        return urllib.request.urlopen(request, timeout=timeout)

    def request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        body: object = None,
        timeout: Optional[float] = 120,
    ) -> object:
        with self.open(method, path, params, body, timeout) as response:
            raw = response.read().decode("utf-8", "replace")
        return json.loads(raw) if raw.strip() else None

    def stop(self) -> None:
        proc = self._proc
        if proc.poll() is None:
            # It owns a SQLite database, so it is asked to close before it is
            # made to. The group sweep afterwards is for anything it started
            # that did not go with it.
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
            end_process_group(proc, timeout=5)


def opencode_server() -> OpencodeServer:
    """The shared server, started on first use and reused from then on."""
    global _opencode_server
    with _OPENCODE_SERVER_LOCK:
        running = _opencode_server
        if running is not None and running.alive():
            return running
        binary = find_backend_cli(BACKEND_OPENCODE)
        if not binary:
            raise OSError("opencode is not installed. Run: npm install -g opencode-ai")
        _opencode_server = OpencodeServer(_opencode_server_binary(binary))
        return _opencode_server


def stop_opencode_server() -> None:
    """Shut the shared server down — on exit, and before an update replaces it."""
    global _opencode_server
    with _OPENCODE_SERVER_LOCK:
        running, _opencode_server = _opencode_server, None
    if running is not None:
        running.stop()


atexit.register(stop_opencode_server)


def _opencode_catalog(cwd: Optional[str] = None, refresh: bool = False) -> dict:
    """opencode's providers, their models, and the defaults it would pick.

    Asked per directory, because a project's own ``opencode.json`` can pin a
    model or turn providers off, and cached per directory for the same reason.
    Caching matters: the catalog is hundreds of models across nearly two
    hundred providers, and the picker is opened far more often than a
    project's set of providers changes.
    """
    key = cwd or ""
    with _OPENCODE_CATALOG_LOCK:
        cached = _opencode_catalog_cache.get(key)
    if cached is not None and not refresh:
        return cached
    server = opencode_server()
    params = {"directory": cwd} if cwd else None
    providers = server.request("GET", "/config/providers", params=params, timeout=60)
    config = server.request("GET", "/config", params=params, timeout=60)
    commands = server.request("GET", "/command", params=params, timeout=60)
    providers = providers if isinstance(providers, dict) else {}
    config = config if isinstance(config, dict) else {}
    catalog = {
        "providers": providers.get("providers") or [],
        "default": providers.get("default") or {},
        "model": config.get("model") or "",
        "commands": _opencode_command_list(commands),
    }
    with _OPENCODE_CATALOG_LOCK:
        _opencode_catalog_cache[key] = catalog
    return catalog


def _opencode_command_list(payload: object) -> list[tuple[str, str]]:
    """opencode's slash commands, as (name, description).

    Only the commands proper: opencode lists installed skills here as well, and
    a picker a person arrows through is worth keeping to the length of what
    they typed a slash to find.
    """
    commands: list[tuple[str, str]] = []
    for entry in payload if isinstance(payload, list) else []:
        if not isinstance(entry, dict) or entry.get("source") != "command":
            continue
        name = str(entry.get("name") or "")
        if name:
            commands.append((name, str(entry.get("description") or "")))
    commands.sort()
    return commands


def opencode_commands(cwd: Optional[str] = None) -> list[tuple[str, str]]:
    """The commands read for this directory, or none if it has not been read.

    Deliberately never asks the server: the slash picker opens on a key press,
    and the catalog is already read in the background whenever opencode is the
    chosen backend.
    """
    with _OPENCODE_CATALOG_LOCK:
        catalog = _opencode_catalog_cache.get(cwd or "")
    commands = catalog.get("commands") if catalog else None
    return list(commands) if isinstance(commands, list) else []


def _opencode_models_from_cli() -> list[str]:
    """The catalog as the CLI prints it, for when the server will not start."""
    binary = find_backend_cli(BACKEND_OPENCODE)
    if not binary:
        return []
    try:
        result = subprocess.run(
            [binary, "models"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=subprocess_env(binary),
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    models: list[str] = []
    for line in (result.stdout or "").splitlines():
        name = line.strip()
        if name and "/" in name and " " not in name and name not in models:
            models.append(name)
    return models


def opencode_model_options(
    cwd: Optional[str] = None,
) -> tuple[list[str], list[str], str, str, str]:
    """Every model opencode can reach, the effort levels they offer, and the
    model it would use if BlindPilot named none."""
    try:
        catalog = _opencode_catalog(cwd)
    except (OSError, ValueError) as exc:
        models = _opencode_models_from_cli()
        if not models:
            return [], [], "", "", opencode_error_text(exc, "opencode's model list is unavailable.")
        return (
            models,
            list(_OPENCODE_EFFORT_ORDER),
            "",
            "",
            "Could not reach opencode's server; showing the list its CLI prints.",
        )

    models = []
    efforts: list[str] = []
    for provider in catalog["providers"]:
        if not isinstance(provider, dict):
            continue
        provider_id = str(provider.get("id") or "")
        entries = provider.get("models")
        if not provider_id or not isinstance(entries, dict):
            continue
        for model_id, model in sorted(entries.items()):
            models.append(f"{provider_id}/{model_id}")
            variants = model.get("variants") if isinstance(model, dict) else None
            if isinstance(variants, dict):
                for variant in variants:
                    if variant not in efforts:
                        efforts.append(variant)

    known = [effort for effort in _OPENCODE_EFFORT_ORDER if effort in efforts]
    efforts = known + sorted(effort for effort in efforts if effort not in known)
    if not models:
        return [], efforts, "", "", "opencode has no connected providers yet. Type /connect."
    return models, efforts, opencode_default_model(catalog=catalog), "", ""


def opencode_default_model(cwd: Optional[str] = None, catalog: Optional[dict] = None) -> str:
    """The ``provider/model`` opencode itself would run, or "" if it has none."""
    try:
        catalog = catalog if catalog is not None else _opencode_catalog(cwd)
    except (OSError, ValueError):
        return ""
    configured = catalog.get("model")
    if isinstance(configured, str) and "/" in configured:
        return configured
    defaults = catalog.get("default") or {}
    for provider in catalog.get("providers") or []:
        if not isinstance(provider, dict):
            continue
        provider_id = str(provider.get("id") or "")
        model_id = defaults.get(provider_id)
        if provider_id and isinstance(model_id, str) and model_id:
            return f"{provider_id}/{model_id}"
    return ""


def opencode_split_model(model: str) -> tuple[str, str]:
    """``provider/model`` as the two halves the server asks for separately."""
    provider, _, name = (model or "").partition("/")
    return (provider.strip(), name.strip()) if name.strip() else ("", "")


def opencode_model_efforts(model: str, cwd: Optional[str] = None) -> list[str]:
    """The effort levels this one model offers, so an unusable one is dropped.

    opencode rejects a variant a model does not define, and the picker offers
    the levels pooled across every model, so the choice has to be checked
    against the model it is about to be sent with.
    """
    provider_id, model_id = opencode_split_model(model)
    if not provider_id:
        return []
    try:
        catalog = _opencode_catalog(cwd)
    except (OSError, ValueError):
        return []
    for provider in catalog["providers"]:
        if isinstance(provider, dict) and provider.get("id") == provider_id:
            entry = (provider.get("models") or {}).get(model_id)
            variants = entry.get("variants") if isinstance(entry, dict) else None
            return list(variants) if isinstance(variants, dict) else []
    return []


# ---- /connect: the providers, and the credentials that reach them ----


def opencode_providers() -> tuple[list[tuple[str, str]], set[str], str]:
    """Every provider opencode knows, and which of them are already connected.

    This is what its ``/connect`` command offers. The ones already reachable
    come first, so the list opens on what is actually in use.
    """
    try:
        server = opencode_server()
        payload = server.request("GET", "/provider", timeout=60)
    except (OSError, ValueError) as exc:
        return [], set(), opencode_error_text(exc, "Could not read opencode's provider list.")
    if not isinstance(payload, dict):
        return [], set(), "opencode returned no provider list."
    connected = {str(name) for name in (payload.get("connected") or [])}
    everything: list[tuple[str, str]] = []
    for provider in payload.get("all") or []:
        if not isinstance(provider, dict):
            continue
        provider_id = str(provider.get("id") or "")
        if provider_id:
            everything.append((provider_id, str(provider.get("name") or provider_id)))
    everything.sort(key=lambda item: (item[0] not in connected, item[1].casefold()))
    return everything, connected, ""


def opencode_auth_methods(provider_id: str) -> list[dict]:
    """How this provider can be signed in to.

    Providers with nothing special to say are absent from opencode's own list;
    for them the only way in is an API key, which is what the fallback says.
    """
    try:
        server = opencode_server()
        payload = server.request("GET", "/provider/auth", timeout=60)
    except (OSError, ValueError):
        payload = None
    methods = payload.get(provider_id) if isinstance(payload, dict) else None
    if isinstance(methods, list) and methods:
        return [method for method in methods if isinstance(method, dict)]
    return [{"type": "api", "label": "Manually enter API key"}]


def opencode_connect_api_key(provider_id: str, key: str, metadata: Optional[dict] = None) -> str:
    """Store an API key for a provider. Returns "" on success, else the error."""
    body: dict = {"type": "api", "key": key}
    if metadata:
        body["metadata"] = {str(k): str(v) for k, v in metadata.items() if v}
    try:
        opencode_server().request("PUT", f"/auth/{provider_id}", body=body, timeout=60)
    except (OSError, ValueError) as exc:
        return opencode_error_text(exc, f"Could not connect {provider_id}.")
    invalidate_backend_cache(BACKEND_OPENCODE)
    return ""


def opencode_disconnect(provider_id: str) -> str:
    """Forget a provider's credentials. Returns "" on success, else the error."""
    try:
        opencode_server().request("DELETE", f"/auth/{provider_id}", timeout=60)
    except (OSError, ValueError) as exc:
        return opencode_error_text(exc, f"Could not disconnect {provider_id}.")
    invalidate_backend_cache(BACKEND_OPENCODE)
    return ""


def opencode_oauth_start(
    provider_id: str, method: int, inputs: Optional[dict] = None
) -> tuple[dict, str]:
    """Begin a browser sign-in. Returns (authorization, error).

    The authorization carries the URL to open, instructions worth reading out,
    and whether the provider finishes on its own ("auto") or hands back a code
    the user has to paste ("code").
    """
    body: dict = {"method": method}
    if inputs:
        body["inputs"] = {str(k): str(v) for k, v in inputs.items() if v}
    try:
        payload = opencode_server().request(
            "POST", f"/provider/{provider_id}/oauth/authorize", body=body, timeout=120
        )
    except (OSError, ValueError) as exc:
        return {}, opencode_error_text(exc, f"Could not start sign-in for {provider_id}.")
    return (payload if isinstance(payload, dict) else {}), ""


def opencode_oauth_finish(provider_id: str, method: int, code: str = "") -> str:
    """Complete a browser sign-in. Returns "" on success, else the error."""
    body: dict = {"method": method}
    if code:
        body["code"] = code
    try:
        opencode_server().request(
            "POST", f"/provider/{provider_id}/oauth/callback", body=body, timeout=300
        )
    except (OSError, ValueError) as exc:
        return opencode_error_text(exc, f"Could not finish signing in to {provider_id}.")
    invalidate_backend_cache(BACKEND_OPENCODE)
    return ""


def _opencode_tool_label(name: str, arguments: object) -> str:
    """One spoken line for a tool that has just started."""
    values = arguments if isinstance(arguments, dict) else {}
    detail = ""
    for key in ("command", "filePath", "path", "pattern", "query", "url", "description"):
        value = values.get(key)
        if isinstance(value, str) and value.strip():
            detail = " ".join(value.split())
            break
    verbs = {
        "bash": "Running",
        "edit": "Editing",
        "write": "Writing",
        "read": "Reading",
        "glob": "Finding files",
        "grep": "Searching",
        "list": "Listing",
        "webfetch": "Fetching",
        "websearch": "Searching the web",
        "task": "Delegating",
    }
    verb = verbs.get(name, f"Using {name}")
    return f"{verb}: {detail}" if detail else verb


# An assistant message the provider refuses on replay poisons the whole
# conversation: opencode rebuilds the request from its stored history every
# step, so once one malformed message is in, every later turn fails with the
# same 400 until the message is gone. DeepSeek-class providers report it as
# "content or tool_calls must be set"; the paired complaint about tool results
# left dangling is the same break in a different coat.
_POISON_HISTORY_RE = re.compile(
    r"Invalid assistant message|tool_calls['?]? must be followed by tool messages"
    r"|missing in assistant tool call message|reasoning_content.*must be passed back",
    re.IGNORECASE,
)


def _poison_history_error(text: str) -> bool:
    """Whether a backend error says the stored history itself was refused."""
    return bool(_POISON_HISTORY_RE.search(text or ""))


def _opencode_questions(raw: object) -> tuple[Question, ...]:
    """Read a question.asked event into BlindPilot's own question shape.

    opencode's own flags are `multiple` for more than one answer and `custom`
    for a typed one. Only `multiple` is trusted as written: its tool tells the
    model not to offer an "Other" of its own because the client adds one, so a
    typed answer is offered whether or not `custom` was set.
    """
    if not isinstance(raw, list):
        return ()
    questions: list[Question] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("question") or "").strip()
        if not text:
            continue
        options: list[QuestionOption] = []
        for option in entry.get("options") or []:
            if isinstance(option, dict) and option.get("label"):
                options.append(
                    QuestionOption(str(option["label"]), str(option.get("description") or ""))
                )
        questions.append(
            Question(
                question=text,
                header=str(entry.get("header") or ""),
                options=tuple(options),
                multi_select=bool(entry.get("multiple")),
            )
        )
    return tuple(questions)


class OpencodeWorker(threading.Thread):
    """Run one opencode turn against the shared headless server."""

    def __init__(
        self,
        prompt: str,
        session_id: Optional[str],
        cwd: str,
        permission_mode: str,
        *,
        model: str = "",
        effort: str = "",
        compact: bool = False,
        on_session: Callable[[str], None],
        on_started: Callable[[], None],
        on_activity: Callable[[str, str], None],
        on_complete: Callable[[str], None],
        on_failed: Callable[[str], None],
        on_done: Callable[[], None],
        on_question: Optional[AskQuestions] = None,
    ) -> None:
        super().__init__(daemon=True)
        self._prompt = prompt
        self._session_id = session_id or ""
        self._cwd = cwd
        self._permission_mode = permission_mode
        self._model = model
        self._effort = effort
        # Compaction is a request of its own rather than a message, so this
        # turn summarises the conversation instead of adding to it.
        self._compact = compact
        self._on_session = on_session
        self._on_started = on_started
        self._on_activity = on_activity
        self._on_complete = on_complete
        self._on_failed = on_failed
        self._on_done = on_done
        self._on_question = on_question
        self._server: Optional[OpencodeServer] = None
        self._stream: object = None
        # Resolved once the turn starts. Working out whether a model offers the
        # chosen effort can mean reading opencode's catalog, and a message can
        # be steered into a running turn from the window's own thread.
        self._variant = ""
        self._cancelled = False
        self._accepting_input = threading.Event()
        self._roles: dict[str, str] = {}
        self._emitted: set[str] = set()
        # A command runs on a request that only answers once the turn is over,
        # so a failure can be noticed from either thread. This is what keeps
        # the turn from being reported as failed twice.
        self._settled = threading.Event()
        self._answer: list[str] = []
        self._tools_running: set[str] = set()
        # Set when a question was answered this turn. The provider poison that
        # a broken question replay leaves behind is only worth the surgery
        # below where a question was actually part of the turn.
        self._question_answered = False
        # One repair per turn: if the retry is refused too, report it rather
        # than looping.
        self._history_repaired = False

    # ----- what the window drives -----

    def accepting_input(self) -> bool:
        return self._accepting_input.is_set() and not self._cancelled

    def steer(self, text: str) -> bool:
        """Add a message to the turn that is already running.

        opencode admits a prompt sent mid-turn and hands it to the model at the
        next step, which is exactly what steering means here.

        Answers from what this worker already knows and sends on a thread of
        its own, because this is called from the window's thread: a request
        waited on there is a window that stops answering the screen reader.
        Whether the turn is still accepting input is the question being asked,
        and that is known here; a message opencode then refuses is rare enough
        to belong in the transcript rather than in a frozen window.
        """
        if not self.accepting_input() or not self._session_id or self._server is None:
            return False
        server, session, body = self._server, self._session_id, self._prompt_body(text)

        def deliver() -> None:
            try:
                server.request(
                    "POST",
                    f"/session/{session}/prompt_async",
                    params={"directory": self._cwd},
                    body=body,
                    timeout=60,
                )
            except (OSError, ValueError) as exc:
                if not self._cancelled:
                    detail = opencode_error_text(exc, "the request failed")
                    self._on_activity(
                        "tool", f"opencode did not take the steering message: {detail}"
                    )

        threading.Thread(target=deliver, daemon=True).start()
        return True

    def cancel(self) -> None:
        self._cancelled = True
        self._accepting_input.clear()
        server, session = self._server, self._session_id
        if server is not None and session:
            try:
                server.request(
                    "POST",
                    f"/session/{session}/abort",
                    params={"directory": self._cwd},
                    timeout=30,
                )
            except (OSError, ValueError):
                pass
        # Closing the event stream is what unblocks the run loop. The server
        # itself is shared, and stays up for the next turn.
        self._close_stream()

    def run(self) -> None:
        try:
            self._do_run()
        except Exception as exc:
            # See CodexWorker.run: without this the event stream is closed, Send
            # comes back, and the turn is over with nothing said about why.
            self._fail(f"BlindPilot stopped reading opencode: {exc}")
        finally:
            self._accepting_input.clear()
            self._close_stream()
            self._on_done()

    def _close_stream(self) -> None:
        stream = self._stream
        if stream is not None:
            try:
                stream.close()  # type: ignore[attr-defined]
            except Exception:
                pass

    # ----- the turn -----

    def _fail(self, message: str) -> None:
        if not self._settled.is_set():
            self._settled.set()
            self._on_failed(message)

    def _on_session_error(self, properties: dict) -> bool:
        """A session.error ends the turn — unless one repair attempt fits.

        A provider that refuses a stored message refuses every following
        request too, so an ordinary failure here would leave the conversation
        permanently stuck behind a turn that can never be replayed. The one
        break BlindPilot itself can walk into is a question round-trip, so
        when the error reads like refused history and a question was answered
        this turn, the question's step is deleted and the prompt sent again.

        Returns False when the turn is over for good (failure reported, or the
        reader should keep going on the repaired conversation).
        """
        text = opencode_error_text(properties.get("error"), "opencode reported an error")
        if not (_poison_history_error(text) and self._question_answered):
            self._fail(text)
            return False
        if self._history_repaired or self._cancelled:
            self._fail(text)
            return False
        self._history_repaired = True
        if self._repair_history():
            self._on_activity(
                "tool",
                "opencode refused the conversation after the question; "
                "removing the broken step and trying again.",
            )
            self._resend()
            return True
        self._fail(text)
        return False

    def _repair_history(self) -> bool:
        """Delete the poisoned question step and everything after it.

        The message the provider cannot replay is two-fold: the question
        step whose tool call comes back unpaired, and the empty assistant
        step it died on — neither content nor tool calls, which is the very
        text of the 400. Both go, and so does anything written after them,
        since every later step is refused for the same reason.

        Returns True when something was actually removed. The listing's order
        is not counted on: the question step to cut at is the latest one by
        its own timestamp.
        """
        server = self._server
        session = self._session_id
        if server is None or not session:
            return False
        try:
            messages = server.request(
                "GET", f"/session/{session}/message", params={"directory": self._cwd}, timeout=60
            )
        except (OSError, ValueError):
            return False
        entries = [
            entry
            for entry in (messages if isinstance(messages, list) else [])
            if isinstance(entry, dict)
        ]

        def stamp(entry: dict) -> float:
            info = entry.get("info") if isinstance(entry.get("info"), dict) else entry
            time = info.get("time") if isinstance(info.get("time"), dict) else {}
            try:
                return float(time.get("created") or info.get("time_created") or 0)
            except (TypeError, ValueError):
                return 0.0

        cutoff_id = ""
        cutoff_stamp = -1.0
        for entry in entries:
            info = entry.get("info") if isinstance(entry.get("info"), dict) else entry
            if str(info.get("role") or "") != "assistant":
                continue
            message_id = str(info.get("id") or "")
            if not message_id:
                continue
            parts = entry.get("parts") if isinstance(entry.get("parts"), list) else []
            for part in parts:
                if not isinstance(part, dict) or str(part.get("tool") or "") != "question":
                    continue
                state = part.get("state") if isinstance(part.get("state"), dict) else {}
                if str(state.get("status") or "") != "completed":
                    continue
                when = stamp(entry)
                if when >= cutoff_stamp:
                    cutoff_id, cutoff_stamp = message_id, when
                break
        if not cutoff_id:
            return False
        # The step the question died on has no timestamp of its own worth
        # trusting, so the cut is by position in the listing relative to the
        # question step, and by time for anything ordered oddly.
        try:
            index = next(
                position
                for position, entry in enumerate(entries)
                if (entry.get("info") if isinstance(entry.get("info"), dict) else entry).get("id")
                == cutoff_id
            )
        except StopIteration:
            return False
        doomed = [
            entry
            for position, entry in enumerate(entries)
            if position >= index and stamp(entry) >= cutoff_stamp
        ]
        removed = False
        for entry in doomed:
            info = entry.get("info") if isinstance(entry.get("info"), dict) else entry
            message_id = str(info.get("id") or "")
            if not message_id:
                continue
            try:
                server.request(
                    "DELETE",
                    f"/session/{session}/message/{message_id}",
                    params={"directory": self._cwd},
                    timeout=60,
                )
            except (OSError, ValueError):
                return removed
            removed = True
        return removed

    def _resend(self) -> None:
        """Send this turn's prompt again on the repaired conversation."""
        server = self._server
        if server is None or not self._session_id:
            self._fail("opencode's server went away while repairing the conversation")
            return
        command = self._as_command()
        try:
            if command is not None:
                self._start_command(*command)
            else:
                server.request(
                    "POST",
                    f"/session/{self._session_id}/prompt_async",
                    params={"directory": self._cwd},
                    body=self._prompt_body(self._prompt),
                    timeout=120,
                )
        except (OSError, ValueError) as exc:
            self._fail(opencode_error_text(exc, "opencode would not take the message again."))
            return
        self._answer.clear()
        self._emitted.clear()
        self._accepting_input.set()

    def _prompt_body(self, text: str) -> dict:
        body: dict = {"parts": [{"type": "text", "text": text}]}
        provider_id, model_id = opencode_split_model(self._model)
        if provider_id:
            body["model"] = {"providerID": provider_id, "modelID": model_id}
        agent = _OPENCODE_AGENTS.get(self._permission_mode)
        if agent:
            body["agent"] = agent
        if self._variant:
            body["variant"] = self._variant
        return body

    def _do_run(self) -> None:
        try:
            self._server = opencode_server()
        except OSError as exc:
            self._fail(opencode_error_text(exc, "opencode's server could not be started."))
            return
        if self._compact and not self._session_id:
            self._fail("There is no opencode conversation to compact yet")
            return
        # Effort levels are per model in opencode, and it rejects one the model
        # does not define, so the pooled choice from the picker is checked
        # against the model this turn will use.
        if self._effort:
            model = self._model or opencode_default_model(self._cwd)
            if self._effort in opencode_model_efforts(model, self._cwd):
                self._variant = self._effort

        # Subscribing before anything is asked for is what makes the first
        # words of the answer part of this turn rather than of the next one.
        try:
            self._stream = self._server.open(
                "GET", "/event", params={"directory": self._cwd}, timeout=None
            )
        except (OSError, ValueError) as exc:
            self._fail(opencode_error_text(exc, "Could not follow opencode's progress."))
            return

        try:
            self._open_session()
        except (OSError, ValueError) as exc:
            self._fail(opencode_error_text(exc, "Could not start an opencode conversation."))
            return
        self._on_session(self._session_id)

        command = self._as_command()
        try:
            if self._compact:
                self._start_compaction()
            elif command is not None:
                self._start_command(*command)
            else:
                self._server.request(
                    "POST",
                    f"/session/{self._session_id}/prompt_async",
                    params={"directory": self._cwd},
                    body=self._prompt_body(self._prompt),
                    timeout=120,
                )
        except (OSError, ValueError) as exc:
            self._fail(opencode_error_text(exc, "opencode would not accept the message."))
            return

        self._accepting_input.set()
        self._on_started()
        self._read_events()

    def _open_session(self) -> None:
        assert self._server is not None
        rules = _OPENCODE_PERMISSIONS.get(self._permission_mode)
        if self._session_id:
            # A resumed conversation keeps its id, but the permission mode may
            # have been changed since, so its rules are re-stated every turn.
            if rules is not None:
                self._server.request(
                    "PATCH",
                    f"/session/{self._session_id}",
                    params={"directory": self._cwd},
                    body={"permission": rules},
                    timeout=60,
                )
            return
        body: dict = {}
        if rules is not None:
            body["permission"] = rules
        agent = _OPENCODE_AGENTS.get(self._permission_mode)
        if agent:
            body["agent"] = agent
        provider_id, model_id = opencode_split_model(self._model)
        if provider_id:
            body["model"] = {"providerID": provider_id, "id": model_id}
        created = self._server.request(
            "POST", "/session", params={"directory": self._cwd}, body=body, timeout=120
        )
        session_id = created.get("id") if isinstance(created, dict) else None
        if not session_id:
            raise OSError("opencode did not return a conversation id")
        self._session_id = str(session_id)

    def _as_command(self) -> Optional[tuple[str, str]]:
        """(command, arguments) if the prompt names one of opencode's commands.

        opencode's commands are prompt templates its server expands — the text
        "/init" sent as a message is just those five characters, and would be
        answered rather than run. Anything it does not recognise stays an
        ordinary message, so a sentence that happens to start with a slash is
        not swallowed.
        """
        text = self._prompt.strip()
        if not text.startswith("/"):
            return None
        # Split on any whitespace, not a space: a command sent with attached
        # files has their paths on the lines after it.
        parts = text[1:].split(None, 1)
        if not parts:
            return None
        name, arguments = parts[0], parts[1] if len(parts) > 1 else ""
        known = {command for command, _description in opencode_commands(self._cwd)}
        return (name, arguments.strip()) if name in known else None

    def _start_command(self, command: str, arguments: str) -> None:
        """Run one of opencode's commands, and narrate it like any other turn.

        Its command request only answers once the whole turn is over, so it is
        made from a thread of its own and the event stream is what the window
        hears from — the same way it hears an ordinary message.
        """
        body: dict = {"command": command, "arguments": arguments}
        provider_id, model_id = opencode_split_model(self._model)
        if provider_id:
            body["model"] = f"{provider_id}/{model_id}"
        agent = _OPENCODE_AGENTS.get(self._permission_mode)
        if agent:
            body["agent"] = agent

        def work() -> None:
            try:
                assert self._server is not None
                self._server.request(
                    "POST",
                    f"/session/{self._session_id}/command",
                    params={"directory": self._cwd},
                    body=body,
                    timeout=None,
                )
            except (OSError, ValueError) as exc:
                if self._cancelled:
                    return
                detail = opencode_error_text(exc, "the request failed")
                self._fail(f"opencode could not run /{command}: {detail}")
                # Nothing else will end the turn now, so release the reader.
                self._close_stream()

        threading.Thread(target=work, daemon=True).start()

    def _start_compaction(self) -> None:
        assert self._server is not None
        provider_id, model_id = opencode_split_model(
            self._model or opencode_default_model(self._cwd)
        )
        if not provider_id:
            raise OSError("Choose an opencode model before compacting")
        self._server.request(
            "POST",
            f"/session/{self._session_id}/summarize",
            params={"directory": self._cwd},
            body={"providerID": provider_id, "modelID": model_id},
            timeout=120,
        )

    def _read_events(self) -> None:
        stream = self._stream
        assert stream is not None
        try:
            for raw in stream:  # type: ignore[attr-defined]
                if self._cancelled:
                    return
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except ValueError:
                    continue
                kind = str(event.get("type") or "")
                properties = event.get("properties") or {}
                # Every session on the server shares one stream — the title
                # writer and any subagent included — so anything that names a
                # different conversation belongs to somebody else's turn.
                if properties.get("sessionID") not in (None, self._session_id):
                    continue
                if kind == "session.error":
                    # The repair path re-sends and keeps reading; only an
                    # unrepaired failure returns here for good.
                    if not self._on_session_error(properties):
                        return
                if kind in ("session.idle", "session.compacted"):
                    self._finish()
                    return
                self._handle_event(kind, properties)
        except Exception as exc:
            # Deliberately broad: Stop closes this connection from another
            # thread, mid-read, and what a socket torn out from under the HTTP
            # reader raises is not something to enumerate. A stop is expected
            # and silent; anything else is still reported as the failure it is.
            if not self._cancelled:
                self._fail(opencode_error_text(exc, "opencode's event stream ended"))
            return
        finally:
            self._accepting_input.clear()
        if not self._cancelled:
            self._fail("opencode closed the connection before the turn finished")

    def _finish(self) -> None:
        if self._settled.is_set():
            return
        self._settled.set()
        if self._compact:
            # A compaction produces no answer of its own, so say what happened
            # rather than finishing in silence.
            self._on_complete("Conversation compacted.")
        else:
            # opencode writes an answer as one text part per step, and a step
            # boundary is a paragraph boundary — run together they read as one
            # sentence that was never written.
            self._on_complete("\n\n".join(self._answer).strip())

    def _handle_event(self, kind: str, properties: dict) -> None:
        if kind == "message.updated":
            info = properties.get("info") or {}
            if isinstance(info, dict) and info.get("id"):
                self._roles[str(info["id"])] = str(info.get("role") or "")
        elif kind == "message.part.updated":
            self._part(properties.get("part") or {})
        elif kind in ("permission.asked", "permission.v2.asked"):
            self._answer_permission(properties)
        elif kind in ("question.asked", "question.v2.asked"):
            self._answer_question(properties)
        elif kind == "session.status":
            status = properties.get("status") or {}
            if isinstance(status, dict) and status.get("type") == "retry":
                self._on_activity(
                    "tool", f"opencode is retrying (attempt {status.get('attempt') or 1})"
                )

    def _part(self, part: object) -> None:
        if not isinstance(part, dict):
            return
        part_id = str(part.get("id") or "")
        kind = str(part.get("type") or "")
        if kind in ("text", "reasoning"):
            # The user's own message arrives as a part too; only the model's
            # side of the conversation belongs in the response rows.
            if self._roles.get(str(part.get("messageID") or "")) != "assistant":
                return
            # opencode opens a part empty, streams it a token at a time, then
            # repeats it in full: that last repeat is what says it is finished
            # and can be read out as one row rather than a hundred.
            text = str(part.get("text") or "").strip()
            if not text or part_id in self._emitted:
                return
            self._emitted.add(part_id)
            if kind == "reasoning":
                self._on_activity("thinking", text)
            else:
                self._answer.append(text)
                self._on_activity("assistant", text)
        elif kind == "tool":
            self._tool(part_id, part)

    def _tool(self, part_id: str, part: dict) -> None:
        state = part.get("state")
        if not isinstance(state, dict):
            return
        status = str(state.get("status") or "")
        name = str(part.get("tool") or "tool")
        if status == "running" and part_id not in self._tools_running:
            self._tools_running.add(part_id)
            self._on_activity("tool", _opencode_tool_label(name, state.get("input")))
        elif status == "completed":
            output = str(state.get("output") or "").strip()
            if output:
                self._on_activity("result", output)
        elif status == "error":
            message = str(state.get("error") or "").strip()
            self._on_activity("result", message or f"{name} failed")

    def _post(self, routes: list[tuple[str, Optional[dict]]], what: str) -> bool:
        """POST to the first of these routes opencode accepts.

        A permission request and a question both hold the turn open until they
        are answered, and opencode is in the middle of moving both onto new
        endpoints. Trying the old one and then the new one costs one failed
        request in the worst case; getting it wrong costs a turn that never
        ends, so the routes are tried rather than guessed at.
        """
        if self._server is None:
            return False
        problem = ""
        for path, body in routes:
            try:
                self._server.request(
                    "POST", path, params={"directory": self._cwd}, body=body, timeout=30
                )
                return True
            except (OSError, ValueError) as exc:
                problem = opencode_error_text(exc, f"opencode would not accept {what}")
        # Saying so matters: unanswered, the turn waits for an answer that is
        # never coming, and silence would look like the model thinking.
        self._on_activity("tool", f"Could not answer {what}: {problem}")
        return False

    def _answer_permission(self, properties: dict) -> None:
        """Answer a permission request the way the chosen mode says to.

        BlindPilot decides from the permission mode rather than interrupting
        with a dialog, the same way its Codex adapter does, so a run never
        stops waiting on an answer nobody was asked for.
        """
        request_id = str(properties.get("id") or "")
        if not request_id or self._server is None:
            return
        permission = str(properties.get("permission") or "")
        mode = self._permission_mode
        if mode == "bypassPermissions":
            reply = "always"
        elif mode == "auto":
            reply = "once"
        elif mode == "acceptEdits" and permission in ("edit", "write", "patch"):
            reply = "once"
        else:
            reply = "reject"
        answered = self._post(
            [
                (
                    f"/session/{self._session_id}/permissions/{request_id}",
                    {"response": reply},
                ),
                (
                    f"/api/session/{self._session_id}/permission/{request_id}/reply",
                    {"reply": reply},
                ),
            ],
            "a permission request",
        )
        if answered and reply == "reject":
            self._on_activity(
                "tool",
                f"Declined {permission or 'a request'} — the permission mode does not allow it",
            )

    def _answer_question(self, properties: dict) -> None:
        """Put a mid-run question to the person and send opencode their answers.

        opencode takes one list of chosen labels per question, in the order it
        asked them, and a question left unanswered has to be rejected rather
        than replied to — a turn waiting on an answer that is never coming
        never ends. It is in the middle of moving both onto new endpoints, so
        each is tried in turn the same way a permission reply is.
        """
        request_id = str(properties.get("id") or "")
        if not request_id or self._server is None:
            return
        questions = _opencode_questions(properties.get("questions"))
        answers = self._on_question(questions) if (questions and self._on_question) else None
        self._on_activity("tool", question_summary(questions, answers))
        if answers is None:
            self._post(
                [
                    (f"/question/{request_id}/reject", None),
                    (f"/api/session/{self._session_id}/question/{request_id}/reject", None),
                ],
                "a question",
            )
            return
        self._question_answered = True
        body = {"answers": [list(answer) for answer in answers]}
        self._post(
            [
                (f"/question/{request_id}/reply", body),
                (f"/api/session/{self._session_id}/question/{request_id}/reply", body),
            ],
            "a question",
        )


class AgentWorker(Protocol):
    """The part of a backend's worker that the window actually drives.

    All three workers are threads, but a thread is not what the window wants
    from them: it wants to start a turn, ask whether it is still running, stop
    it, and wait for it to let go. Saying that here is what lets the window
    hold whichever worker the backend chose without knowing which one it is —
    and lets `cancel` be a method the code is allowed to call, rather than one
    a reader has to take on trust.
    """

    def start(self) -> None: ...

    def is_alive(self) -> bool: ...

    def join(self, timeout: Optional[float] = None) -> None: ...

    def cancel(self) -> None: ...


# Callable rather than type[...]: each backend's worker takes the keywords its
# own turn needs — Codex alone understands `compact` — and the caller passes
# the extras for the backend it picked.
AgentWorkerFactory = Callable[..., AgentWorker]


def worker_class(backend: str, claude_worker: AgentWorkerFactory) -> AgentWorkerFactory:
    backend = normalize_backend(backend)
    if backend == BACKEND_CODEX:
        return CodexWorker
    if backend == BACKEND_FREEBUFF:
        return FreebuffWorker
    if backend == BACKEND_OPENCODE:
        return OpencodeWorker
    if backend == BACKEND_HERMES:
        # Imported here rather than at module scope so a machine without Hermes
        # pays nothing for it, and an import error in the adapter cannot stop
        # the other backends from working.
        from hermes_worker import HermesWorker

        return HermesWorker
    return claude_worker
