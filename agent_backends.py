"""Backend adapters for BlindPilot.

BlindPilot began as Claude Code Reader. Claude's adapter remains in
``blindpilot_app.py``; this module contains the
provider-neutral discovery helpers plus the Codex, FreeBuff, and opencode
workers.
Hermes lives in ``hermes_backend.py`` and ``hermes_worker.py``, imported
on demand so a machine without Hermes pays nothing for it.

Codex's app-server used to be started fresh for every turn and killed at the
end of it. It is now owned by ``backend_pool``: one process shared by every
tab, held across turns instead of torn down between them, so a conversation
does not pay the app-server's start-up -- and, where MCP servers are
configured, all of their child processes' start-up too -- on every message.
``backend_pool.py`` decides how long that process lives; what is here says
only how to start, check, and stop it.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
Based on the original Claude Code Reader application by doubletaponair:
https://github.com/doubletaponair/claude-code-reader
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import atexit
import collections
import json
import logging
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
from typing import Callable, NamedTuple, Optional, Protocol, Sequence, cast

import backend_pool
import diagnostics

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
            # POSIX only, and the platform test above is a runtime condition
            # the checker cannot read, so against a Windows target it reports
            # all three of these as missing.
            if os.getpgid(pid) == pid:  # type: ignore[attr-defined]
                os.killpg(pid, signal.SIGKILL)  # type: ignore[attr-defined]
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


def _probe_backend(binary: str, args: list[str], timeout: int) -> tuple[Optional[int], str]:
    """Run a short provider command. Returns (exit code, what it printed).

    The exit code is ``None`` when the command could not be run at all, which
    is a different answer from "it ran and said no" and is reported as such.
    """
    try:
        proc = subprocess.run(
            [binary, *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=subprocess_env(binary),
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, ""
    # These CLIs draw their own output, so what comes back is wrapped in the
    # escape sequences that would otherwise be spelled out letter by letter.
    return proc.returncode, _strip_terminal_noise(proc.stdout) or _strip_terminal_noise(proc.stderr)


def _claude_account_lines(code: Optional[int], text: str) -> list[str]:
    """Read `claude auth status`, which answers in JSON."""
    if code is None:
        return ["Signed in: could not ask Claude Code"]
    payload: object = None
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            payload = json.loads(text[start : end + 1])
        except ValueError:
            payload = None
    if not isinstance(payload, dict):
        # A future release that stops answering in JSON still has something to
        # say, and its own words are better than a shrug.
        return [f"Signed in: {'yes' if code == 0 else 'no'}"] + ([text] if text else [])
    lines = [f"Signed in: {'yes' if payload.get('loggedIn') else 'no'}"]
    for field, caption in (
        ("email", "Account"),
        ("subscriptionType", "Subscription"),
        ("authMethod", "Signed in with"),
        ("orgName", "Organisation"),
    ):
        value = str(payload.get(field) or "").strip()
        if value:
            lines.append(f"{caption}: {value}")
    return lines


def _codex_account_lines(code: Optional[int], text: str) -> list[str]:
    """Read `codex login status`, which answers in one sentence."""
    if code is None:
        return ["Signed in: could not ask Codex"]
    lines = [f"Signed in: {'yes' if code == 0 else 'no'}"]
    if text:
        lines.append(f"Account: {' '.join(text.split())}")
    return lines


def _freebuff_account_lines() -> list[str]:
    """FreeBuff has no status command; its stored credentials are the answer."""
    credential = Path.home() / ".config" / "manicode" / "credentials.json"
    try:
        payload = json.loads(credential.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["Signed in: no"]
    account = payload.get("default") if isinstance(payload, dict) else None
    if not isinstance(account, dict):
        return ["Signed in: no"]
    signed_in = all(
        isinstance(account.get(field), str) and account[field].strip()
        for field in ("authToken", "fingerprintId", "fingerprintHash")
    )
    lines = [f"Signed in: {'yes' if signed_in else 'no'}"]
    for field, caption in (("name", "Account"), ("email", "Email")):
        value = str(account.get(field) or "").strip()
        if value:
            lines.append(f"{caption}: {value}")
    return lines


def _opencode_account_lines() -> list[str]:
    """opencode has no status command either; its stored credentials answer.

    Read off disk rather than asked of its server, because /status should not
    be the thing that starts one — a report on what is already set up has no
    business spending ten seconds bringing a server to life to say so.
    """
    providers: list[str] = []
    try:
        payload = json.loads((_opencode_data_dir() / "auth.json").read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            providers = sorted(str(name) for name in payload)
    except (OSError, ValueError):
        providers = []
    if os.environ.get("OPENCODE_API_KEY", "").strip():
        providers.append("OPENCODE_API_KEY in the environment")
    if not providers:
        return ["Signed in: no", "Connected providers: none"]
    return ["Signed in: yes", f"Connected providers: {', '.join(providers)}"]


def backend_status(backend: str, timeout: int = 20) -> str:
    """What the chosen backend can say about itself, as lines of plain text.

    This is what ``/status`` reports, and every backend answers it. None of
    them answers it themselves in the headless mode BlindPilot drives them in:
    Claude Code's own ``/status`` is interactive-only and replies "/status
    isn't available in this environment" when it is sent as a message, and
    Codex, FreeBuff and opencode have no status command at all. So each one is
    asked in the way it can actually answer — a CLI subcommand where there is
    one, the credentials it stored where there is not — and the answers are
    written the same way, so the report reads the same whichever backend is
    selected.
    """
    backend = normalize_backend(backend)
    lines = [f"Backend: {backend_label(backend)}"]
    binary = find_backend_cli(backend)
    if not binary:
        lines.append("Command line: not installed")
        return "\n".join(lines)
    lines.append(f"Command line: {binary}")
    _code, version = _probe_backend(binary, ["--version"], min(timeout, 20))
    if version:
        lines.append(f"Version: {version.splitlines()[0].strip()}")
    if backend == BACKEND_CLAUDE:
        lines.extend(_claude_account_lines(*_probe_backend(binary, ["auth", "status"], timeout)))
    elif backend == BACKEND_CODEX:
        lines.extend(_codex_account_lines(*_probe_backend(binary, ["login", "status"], timeout)))
    elif backend == BACKEND_FREEBUFF:
        lines.extend(_freebuff_account_lines())
    else:
        lines.extend(_opencode_account_lines())
    return "\n".join(lines)


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


class SettingsFile(NamedTuple):
    """One file that configures one backend, and how to describe it.

    `scope` and `note` are both spoken. They are not decoration: the two
    project-level Claude Code files differ only in whether the repository
    carries them, and opening the wrong one silently is how somebody's personal
    settings end up committed to a repository that is not theirs.
    """

    backend: str
    scope: str
    path: Path
    note: str

    @property
    def exists(self) -> bool:
        try:
            return self.path.is_file()
        except OSError:
            return False


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def _hermes_home() -> Path:
    """Where Hermes keeps its configuration, honouring its own override.

    `HERMES_HOME` is how one machine runs several Hermes profiles side by side,
    so reading it is the difference between naming the file somebody is actually
    using and naming a default they abandoned.
    """
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def settings_files(cwd: Optional[str] = None) -> list[SettingsFile]:
    """Every settings file BlindPilot knows how to point somebody at.

    Listing them creates nothing. These belong to the CLIs, which write their
    own on first run, and a file invented at a path BlindPilot guessed would
    be worse than no file: it would do nothing while looking like it did.

    Locations are the ones each CLI documents. opencode also reads
    `.opencode/opencode.json` inside a project, and Claude Code reads an
    enterprise policy file above all of these that is not an individual's to
    edit, so neither is offered here.
    """
    home = Path.home()
    project = Path(cwd).expanduser() if cwd else None
    entries = [
        SettingsFile(
            BACKEND_CLAUDE,
            "global",
            home / ".claude" / "settings.json",
            "Applies to every project on this machine.",
        ),
        SettingsFile(
            BACKEND_CODEX,
            "global",
            _codex_home() / "config.toml",
            "Applies to every project. TOML rather than JSON.",
        ),
        SettingsFile(
            BACKEND_FREEBUFF,
            "global",
            home / ".config" / "manicode" / "settings.json",
            "Applies to every project. BlindPilot keeps your model choice here.",
        ),
        SettingsFile(
            BACKEND_OPENCODE,
            "global",
            home / ".config" / "opencode" / "opencode.json",
            "Applies to every project.",
        ),
        SettingsFile(
            BACKEND_HERMES,
            "global",
            _hermes_home() / "config.yaml",
            "Applies to every project. YAML rather than JSON, and it belongs to "
            "the Hermes this backend talks to: a Hermes reached over the network "
            "reads the file on that machine, not this one.",
        ),
    ]
    if project is not None:
        entries += [
            SettingsFile(
                BACKEND_CLAUDE,
                "this folder",
                project / ".claude" / "settings.json",
                "Shared: committed to this repository, so anyone who has it gets these.",
            ),
            SettingsFile(
                BACKEND_CLAUDE,
                "this folder, personal",
                project / ".claude" / "settings.local.json",
                "Yours alone: normally ignored by git, so it stays on this machine.",
            ),
            SettingsFile(
                BACKEND_OPENCODE,
                "this folder",
                project / "opencode.json",
                "Applies in this folder, and can pin a model or turn providers off.",
            ),
        ]
    return entries


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
    if platform.system() == "Windows":
        # Every CLI is started with `cwd` set to the user's project folder, and
        # CLIs shell out constantly - git, node, npm, sh. Windows has
        # historically searched the current directory for those, and the
        # project folder is usually a repository somebody cloned in order to
        # ask an agent about it, not code they wrote. A `git.exe` committed to
        # it should not be what runs when the agent asks for git.
        #
        # This is the documented way off that search path, read by
        # NeedCurrentDirectoryForExePathW, and it is inherited - so it covers
        # the CLI and everything the CLI goes on to start.
        env["NoDefaultCurrentDirectoryInExePath"] = "1"
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
    if platform.system() == "Darwin":
        # The one folder a Mac user looks in. The chat database already lives
        # here (see accessible_ai.storage.paths), so settings belong beside it
        # rather than in a Linux-style dot folder.
        return Path.home() / "Library" / "Application Support" / "BlindPilot"
    return Path.home() / ".config" / "blindpilot"


def blindpilot_data_dir() -> Path:
    """Per-user, non-roaming storage for managed runtimes and CLI packages."""
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "BlindPilot"
    if platform.system() == "Darwin":
        # One subtree of Application Support, so the whole app owns one folder
        # on a Mac -- nothing hidden in ~/.local and ~/.config.
        return Path.home() / "Library" / "Application Support" / "BlindPilot" / "data"
    data = os.environ.get("XDG_DATA_HOME")
    return (Path(data) if data else Path.home() / ".local" / "share") / "blindpilot"


def migrate_macos_legacy_dirs() -> None:
    """Move an older macOS install's folders into the Application Support home.

    Before BlindPilot knew the platform's conventions it kept config in
    ``~/.config/blindpilot`` and managed runtimes in ``~/.local/share/
    blindpilot``, the Linux layout. A Mac update must not strand a user's
    settings or their installed CLIs out there, so the first launch after the
    move relocates both -- never overwriting anything already in the new
    home, and never failing the launch when a move does not go through (the
    old code keeps reading the old folder then).

    Called at import time, before any settings are read, so the config file
    is found in its new home from this launch on. Idempotent and a no-op on
    every other platform.
    """
    if platform.system() != "Darwin":
        return
    home = Path.home()
    legacy_config = home / ".config" / "blindpilot"
    legacy_data = home / ".local" / "share" / "blindpilot"
    for source, destination in (
        (legacy_config, blindpilot_config_dir()),
        (legacy_data, blindpilot_data_dir()),
    ):
        _move_dir_contents(source, destination)


def _move_dir_contents(source: Path, destination: Path) -> None:
    """Move the entries of *source* into *destination*, never overwriting.

    Each entry is moved on its own so one failure leaves the rest behind;
    entries that would collide with something already in the new home are
    skipped rather than replacing it -- the new copy wins, and the old one
    stays where it is, readable by any rollback.
    """
    try:
        if not source.is_dir():
            return
        destination.mkdir(parents=True, exist_ok=True)
        for entry in list(source.iterdir()):
            target = destination / entry.name
            if target.exists() or target.is_symlink():
                continue
            try:
                shutil.move(str(entry), str(target))
            except OSError:
                continue
        # Only a source left with nothing in it is a source that finished.
        if not any(source.iterdir()):
            try:
                source.rmdir()
            except OSError:
                pass
    except OSError:
        pass


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


# How long a failing Codex turn waits for the last line of stderr, which is
# the one that says why. Long enough for a line already written to arrive,
# short enough that a pipe which never closes cannot hold the turn open.
_CODEX_LAST_WORDS_SECONDS = 1.0

# How long an interrupt is given to be confirmed before the turn's thread is
# treated as wedged. Half of the window's whole teardown budget
# (_CANCEL_JOIN_SECONDS, 3.0), leaving the rest for the join that follows.
_CODEX_INTERRUPT_VERIFY_SECONDS = 1.5

# How long a cancel waits to be told the id of a turn it has already asked for.
# Stop can land in the gap between `turn/start` and the reply that names the
# turn, and a turn with no name cannot be interrupted -- so the last thing a
# stopped turn does is look for that reply. Small: the reply is either already
# on its way or it is not coming, and this is spent before the verify wait, so
# the two together still fit inside the window's teardown budget.
_CODEX_TURN_ID_GRACE_SECONDS = 0.25

# How long a turn waits on its inbox before looking at its own cancelled flag.
# Short enough that Stop is answered promptly, long enough that a quiet turn
# is not a spin.
_CODEX_POLL_SECONDS = 0.1

# How much of the app-server's stderr is kept. The process now outlives
# thousands of turns, and only the tail of it is ever read; an uncapped list
# is a leak on the branch whose whole point is not leaving things behind.
_CODEX_STDERR_LINES = 200

# How many conversations may hold a note about a turn given up on them. A
# thread nobody ever resumes never collects its note, so the notes are capped
# rather than trusted to be read.
_CODEX_ABANDONED_THREADS = 64


class _Closed:
    """What the reader puts in every inbox when the app-server's stdout ends.

    A turn blocked on its queue has to be woken by something; `None` is a
    value the protocol could plausibly carry, so this is a type of its own.
    """


_CODEX_CLOSED = _Closed()


class _Asked:
    """The mark a turn puts in its own inbox when it asks for a turn.

    Everything already queued behind it was said before this turn asked for
    anything, so none of it can be about this turn. Everything after it may
    be. The inbox is first in, first out and both threads write to it in
    order, so the mark separates the two exactly -- which a flag set at send
    time does not, because by then the earlier messages are already queued and
    are read after it.
    """


_CODEX_ASKED = _Asked()


class _TurnWatch:
    """One turn's completion, and how many callers are waiting to hear it."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.watchers = 0


# Only one Codex app-server is ever wanted, so only one turn at a time may go
# looking for one. Without this, two tabs sending their first Codex message
# together both find the pool empty, both launch a server, and the second one
# handed back displaces -- and stops -- the first, killing a live turn.
_CODEX_START_LOCK = threading.Lock()


def _offer(listener: "queue.Queue[object]", message: object) -> None:
    """Put a message in a queue without ever blocking the reader.

    A turn's inbox is unbounded, so this is a safety net rather than a routine
    path: the one reader serves every tab, and blocking it on one conversation
    would stop all of them. A bounded queue loses its oldest message instead.
    """
    try:
        listener.put_nowait(message)
        return
    except queue.Full:
        pass
    try:
        listener.get_nowait()
    except queue.Empty:
        pass
    try:
        listener.put_nowait(message)
    except queue.Full:
        pass


class CodexServer:
    """One ``codex app-server`` process, shared by every tab using Codex.

    The app-server multiplexes: several threads live in one process, keyed by
    threadId, which is what lets one server serve every tab instead of one per
    tab. Replies are routed back to whichever turn asked, by request id.
    """

    def __init__(self, proc: object) -> None:
        self.proc = proc
        # Two locks, never held together. `_write_lock` guards only the
        # stdin write+flush pair, so a wedged app-server blocking on flush
        # cannot also block every other tab's `next_id()`. `_state_lock`
        # guards only in-process bookkeeping (the id counter, and later the
        # reply-routing table) -- Task 7 registers a reply queue before
        # sending, and taking the same lock `send` uses for I/O there would
        # self-deadlock.
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._next_id = 10
        # request id -> the queue the waiting turn is reading
        self._waiting: dict[int, "queue.Queue[object]"] = {}
        # Of those, the requests whose reply names a thread to subscribe.
        self._thread_replies: set[int] = set()
        # threadId -> the queue that conversation's turn is reading. Only ever
        # holds conversations somebody is reading right now: a message for a
        # thread with no entry belongs to a turn that has ended, and creating
        # an entry for it would both leak and replay it into the next turn.
        self._threads: dict[str, "queue.Queue[object]"] = {}
        # turnId -> the completion the reader reports, and its watchers.
        self._turns: dict[str, _TurnWatch] = {}
        # threadId -> the turns given up on it that may still be generating.
        # Written when a cancel could not be confirmed, read by the next turn
        # to resume that conversation, and cleared as it is read.
        self._abandoned: dict[str, set[str]] = {}
        # Bounded, because this list now belongs to a process that outlives
        # thousands of turns rather than dying with one of them.
        self._stderr: "collections.deque[str]" = collections.deque(maxlen=_CODEX_STDERR_LINES)
        # How many lines have ever been written, which is what lets a turn
        # read only its own: the deque forgets, so a position in it is not a
        # position in the stream.
        self._stderr_written = 0
        self._readers: list[threading.Thread] = []
        self._stderr_reader: Optional[threading.Thread] = None
        self._closed = False
        self._read_error = ""
        # How many turns are speaking through this process right now.
        self._borrowers = 0

    # ----- lifetime -----

    def start_readers(self) -> None:
        """Read this process's stdout and stderr for as long as it lives.

        One thread per pipe per *process*, not per turn: the app-server
        multiplexes, so a turn that read stdout itself would swallow every
        other tab's notifications.
        """
        if getattr(self.proc, "stdout", None) is not None:
            reader = threading.Thread(target=self._read_stdout, name="codex-stdout", daemon=True)
            self._readers.append(reader)
            reader.start()
        else:
            self._close("")
        if getattr(self.proc, "stderr", None) is not None:
            stderr_reader = threading.Thread(
                target=self._read_stderr, name="codex-stderr", daemon=True
            )
            self._stderr_reader = stderr_reader
            self._readers.append(stderr_reader)
            stderr_reader.start()

    def alive(self) -> bool:
        poll = getattr(self.proc, "poll", None)
        return poll is not None and poll() is None

    def next_id(self) -> int:
        with self._state_lock:
            self._next_id += 1
            return self._next_id

    def send(self, message: dict) -> bool:
        stdin = getattr(self.proc, "stdin", None)
        if stdin is None:
            return False
        try:
            data = json.dumps(message, ensure_ascii=False) + "\n"
            with self._write_lock:
                stdin.write(data)
                stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    # ----- routing -----

    def inbox(self) -> "queue.Queue[object]":
        """A queue for one turn to read.

        Unbounded, because a turn's own answer text must never be dropped to
        make room for more of it. Empty until `expect` or `attach` says what
        should arrive in it; one of those has to be called before it is read,
        because they are also what wakes a reader of a server already closed.
        """
        return queue.Queue()

    def expect(self, request_id: int, listener: "queue.Queue[object]") -> None:
        """Send this request's reply to that queue. Register before sending."""
        self._expect(request_id, listener, binds_thread=False)

    def expect_thread(self, request_id: int, listener: "queue.Queue[object]") -> None:
        """As `expect`, and subscribe the thread the reply names.

        `thread/start` and `thread/resume` both answer with the conversation's
        id (both `required: ["thread", ...]` in the app server's own schema),
        and the reader binds that conversation to this queue *before* it hands
        the reply on. Everything arrives on one stdout read by one thread, so
        any message routed after that reply finds the binding already in place,
        and nothing has to be buffered for a thread nobody is reading yet.

        Messages routed *before* the reply are a different matter: a threadId
        nothing is bound to yet is dropped. In practice the only notification
        Codex sends between creating a thread and answering for it is
        `mcpServer/startupStatus/updated`, which this worker ignores.
        """
        self._expect(request_id, listener, binds_thread=True)

    def _expect(self, request_id: int, listener: "queue.Queue[object]", binds_thread: bool) -> None:
        with self._state_lock:
            closed = self._closed
            if not closed:
                self._waiting[request_id] = listener
                if binds_thread:
                    self._thread_replies.add(request_id)
        if closed:
            _offer(listener, _CODEX_CLOSED)

    def unexpect(self, request_ids: Sequence[int]) -> None:
        with self._state_lock:
            for request_id in request_ids:
                self._waiting.pop(request_id, None)
                self._thread_replies.discard(request_id)

    def attach(self, thread_id: str, listener: "queue.Queue[object]") -> None:
        """Read this conversation's notifications from that queue.

        Ordinarily `expect_thread` has already done this from the reply. This
        is the belt and braces for a turn that had to fall back to the session
        id it was given, and it is idempotent.
        """
        if not thread_id:
            return
        with self._state_lock:
            closed = self._closed
            if not closed:
                self._threads[thread_id] = listener
        if closed:
            _offer(listener, _CODEX_CLOSED)

    def detach(self, thread_id: str, listener: "queue.Queue[object]") -> None:
        """Stop reading a conversation, without disturbing a newer reader.

        No production caller -- `_release` uses `detach_listener` below,
        which does not need the id. Only a test calls this one directly.
        """
        with self._state_lock:
            if self._threads.get(thread_id) is listener:
                del self._threads[thread_id]

    def detach_listener(self, listener: "queue.Queue[object]") -> None:
        """Stop reading every conversation bound to this queue.

        By identity rather than by id, because the id is exactly what a turn
        may not have. The reader binds the conversation the moment the reply
        names it; a turn cancelled between that routing and its own reading of
        the reply never learns the id, and detaching by an id it does not have
        would leave the binding there for the life of the process.
        """
        with self._state_lock:
            for thread_id in [k for k, v in self._threads.items() if v is listener]:
                del self._threads[thread_id]

    def borrow(self) -> None:
        """One more turn is speaking through this process."""
        with self._state_lock:
            self._borrowers += 1

    def give_back(self) -> None:
        with self._state_lock:
            self._borrowers = max(0, self._borrowers - 1)

    def borrower_count(self) -> int:
        """How many turns hold this process right now.

        Not the same as how many conversations are bound: a turn that has sent
        `thread/start` and is waiting for the reply holds the process without
        being bound to anything, and is exactly the turn that must not have it
        stopped underneath it.
        """
        with self._state_lock:
            return self._borrowers

    def watch_turn(self, turn_id: str) -> threading.Event:
        """The event the reader sets when this turn completes.

        Counted, because two watchers are ordinary: the turn's own thread
        registers when the turn starts, and a `cancel` on the window's thread
        registers again when it interrupts. The event goes when the last of
        them lets go, so a process that outlives thousands of turns does not
        accumulate one of these for each.
        """
        with self._state_lock:
            watch = self._turns.get(turn_id)
            if watch is None:
                watch = _TurnWatch()
                self._turns[turn_id] = watch
            watch.watchers += 1
            if self._closed:
                watch.done.set()
            return watch.done

    def forget_turn(self, turn_id: str) -> None:
        with self._state_lock:
            watch = self._turns.get(turn_id)
            if watch is None:
                return
            watch.watchers -= 1
            if watch.watchers <= 0:
                del self._turns[turn_id]

    def read_error(self) -> str:
        """Why the reader stopped, if it stopped on an error rather than EOF."""
        with self._state_lock:
            return self._read_error

    def stderr_lines(self) -> list[str]:
        """Everything still kept, oldest first. The whole process's, not a turn's."""
        with self._state_lock:
            return list(self._stderr)

    def stderr_mark(self) -> int:
        """Where a turn's own stderr begins, to be read back with `stderr_since`."""
        with self._state_lock:
            return self._stderr_written

    def stderr_since(self, mark: int) -> list[str]:
        """Only the lines written after that mark.

        A held process explains itself for every turn it ever runs. Reporting
        a death at turn fifty with a warning from turn three is a wrong reason
        spoken aloud to somebody who cannot scroll back and check.
        """
        with self._state_lock:
            fresh = self._stderr_written - mark
            if fresh <= 0:
                return []
            lines = list(self._stderr)
        return lines[-fresh:] if fresh < len(lines) else lines

    def abandon_turn(self, thread_id: str, turn_id: str) -> None:
        """Remember that this turn was given up while it may still be running.

        Kept on the process rather than the worker, because the worker is one
        turn and the thing that has to act on it is the next one. An
        interrupt Codex never confirmed leaves a turn generating, and its
        words arriving mid-way through the next prompt's turn used to be
        appended to that prompt's answer.
        """
        if not thread_id or not turn_id:
            return
        with self._state_lock:
            self._abandoned.setdefault(thread_id, set()).add(turn_id)
            while len(self._abandoned) > _CODEX_ABANDONED_THREADS:
                # A conversation nobody ever resumes would otherwise keep its
                # note for the life of the process. Oldest first: dicts keep
                # insertion order, and the newest note is the one still live.
                self._abandoned.pop(next(iter(self._abandoned)))

    def take_abandoned_turns(self, thread_id: str) -> set[str]:
        """The turns given up on this conversation, cleared as they are read."""
        with self._state_lock:
            return self._abandoned.pop(thread_id, set())

    def await_last_words(self, timeout: float) -> None:
        """Let the final line of stderr land before a turn reports the death."""
        reader = self._stderr_reader
        if reader is not None:
            reader.join(timeout=timeout)

    def _read_stdout(self) -> None:
        stdout = getattr(self.proc, "stdout", None)
        if stdout is None:
            self._close("")
            return
        try:
            for raw in stdout:
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                try:
                    self._route(message)
                except Exception as exc:
                    # One message nobody anticipated must not be read as the
                    # stream breaking: this reader serves every tab, and
                    # closing here would end all of their turns at once. It is
                    # written down, though: the turn that message was for now
                    # waits on a reply that will not come, and without a record
                    # the only sign of it is a turn that ends at the reaper a
                    # quarter of an hour later. Method and id only -- what the
                    # message was about is not this log's business.
                    try:
                        logging.getLogger("blindpilot.codex").warning(
                            "dropped an app-server message that could not be routed: "
                            "method=%r id=%r (%s)",
                            message.get("method"),
                            message.get("id"),
                            type(exc).__name__,
                        )
                    except Exception:
                        pass
                    continue
        except Exception as exc:
            # Whatever broke the pipe is the reason every turn on this server
            # is about to end, so it is carried rather than lost to a thread
            # nobody joins.
            self._close(f"BlindPilot stopped reading Codex: {exc}")
            return
        self._close("")

    def _read_stderr(self) -> None:
        stderr = getattr(self.proc, "stderr", None)
        if stderr is None:
            return
        try:
            for line in stderr:
                if line.strip():
                    with self._state_lock:
                        self._stderr.append(line.strip())
                        self._stderr_written += 1
        except Exception:
            # stderr is only ever read for the reason a turn failed; failing
            # to read it must not become a second failure of its own.
            pass

    def _route(self, message: dict) -> None:
        method = message.get("method")
        if method is None:
            # A reply. Only the turn that asked wants it.
            request_id = message.get("id")
            waiting = None
            if isinstance(request_id, int):
                with self._state_lock:
                    waiting = self._waiting.pop(request_id, None)
                    binds = request_id in self._thread_replies
                    self._thread_replies.discard(request_id)
                    if waiting is not None and binds and not self._closed:
                        result = message.get("result")
                        thread = result.get("thread") if isinstance(result, dict) else None
                        # Shape-checked at every step. This runs on the one
                        # reader thread, and anything raised here would be
                        # caught as a broken stream and end every tab's turn,
                        # where the same malformed reply parsed in a worker
                        # only ever ended that worker's own.
                        thread_id = str(thread.get("id") or "") if isinstance(thread, dict) else ""
                        if thread_id:
                            self._threads[thread_id] = waiting
            if waiting is not None:
                _offer(waiting, message)
            return
        params = message.get("params")
        if not isinstance(params, dict):
            params = {}
        if method == "turn/completed":
            turn = params.get("turn")
            turn_id = str((turn or {}).get("id") or "") if isinstance(turn, dict) else ""
            with self._state_lock:
                watch = self._turns.get(turn_id)
            if watch is not None:
                watch.done.set()
        thread_id = str(params.get("threadId") or "")
        with self._state_lock:
            if self._closed:
                return
            if thread_id:
                # Nobody reading means the turn it belongs to has ended -- a
                # trailing delta or a `turn/completed` for a turn that was
                # interrupted. Keeping it would hand the *next* turn on this
                # conversation somebody else's ending to act on.
                listener = self._threads.get(thread_id)
                if listener is None:
                    return
            elif len(self._threads) == 1:
                # `threadId` is required on every notification and every server
                # request this worker acts on (checked against the app server's
                # own `generate-json-schema` for 0.149.1). What is left without
                # one is process-wide -- `configWarning`, `thread/started` --
                # and with a single conversation open there is no ambiguity
                # about who should hear it. With more than one there is, so an
                # unnamed message is dropped rather than told to the wrong tab.
                listener = next(iter(self._threads.values()))
            else:
                return
            _offer(listener, message)

    def _close(self, error: str) -> None:
        """Wake everyone waiting on this server and say why, once."""
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._read_error = error
            listeners = list(self._waiting.values()) + list(self._threads.values())
            # Replies can no longer arrive, and no further notification can be
            # routed, so nothing is left to deliver to either.
            self._waiting.clear()
            self._thread_replies.clear()
            self._threads.clear()
            # stdout has ended, so this process is finished with every turn it
            # was running. Saying so lets a cancel racing the death answer at
            # once instead of spending its whole verify budget on a pipe that
            # will never speak again.
            turns = list(self._turns.values())
        for listener in listeners:
            _offer(listener, _CODEX_CLOSED)
        for watch in turns:
            watch.done.set()

    def confirm_interrupt(self, thread_id: str, turn_id: str, timeout: float) -> bool:
        """Wait for Codex to say the turn stopped. Overridden in tests.

        This runs on the thread that called `cancel`, not the turn's own, so
        it must not read the message loop: it waits on the event the shared
        reader sets when it sees `turn/completed` for this turn id. Returning
        False means nothing confirmed, which is what makes "interrupt, verify,
        abandon the thread if unsure" a decision rather than a guess.
        """
        done = self.watch_turn(turn_id)
        try:
            return done.wait(timeout)
        finally:
            self.forget_turn(turn_id)

    def interrupt(self, thread_id: str, turn_id: str, timeout: float) -> bool:
        """Ask Codex to stop this turn and say whether it confirmed."""
        if not thread_id or not turn_id:
            return False
        # Held across the send, so a completion arriving the instant the
        # interrupt lands is not missed, and given back either way: the caller
        # may be `cancel`, which has no turn of its own to clean up after.
        self.watch_turn(turn_id)
        try:
            if not self.send(
                {
                    "method": "turn/interrupt",
                    "id": self.next_id(),
                    "params": {"threadId": thread_id, "turnId": turn_id},
                }
            ):
                return False
            return self.confirm_interrupt(thread_id, turn_id, timeout)
        finally:
            self.forget_turn(turn_id)

    def stop(self) -> None:
        end_process_group(self.proc, timeout=2)
        self._close("")


def _start_codex_server() -> CodexServer:
    """Launch the shared app-server. Raises OSError if it cannot be started."""
    binary = find_backend_cli(BACKEND_CODEX)
    if not binary:
        raise OSError("Codex is not installed. Run: npm install -g @openai/codex")
    server_binary = _codex_app_server_binary(binary)
    proc = subprocess.Popen(
        [server_binary, *_CODEX_QUESTION_ARGS, "app-server", "--stdio"],
        cwd=str(Path.home()),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
        env=subprocess_env(server_binary),
        # npm's `codex` is a Node launcher that runs the real Codex as a child
        # of its own; stopping only the launcher leaves that child holding the
        # app-server session.
        **own_group_kwargs(),
        **no_window_kwargs(),
    )
    server = CodexServer(proc)
    server.start_readers()
    return server


def codex_adapter() -> backend_pool.Adapter:
    """How the pool starts, checks, interrupts and stops Codex."""

    def alive(server: object) -> bool:
        return bool(cast(CodexServer, server).alive())

    def stop(server: object) -> None:
        cast(CodexServer, server).stop()

    def busy(server: object) -> bool:
        # Borrowing, not reading: a turn that has sent `thread/start` and is
        # waiting for the reply holds the process while bound to no
        # conversation at all, and is exactly the turn the reaper must not
        # stop the process underneath.
        return cast(CodexServer, server).borrower_count() > 0

    return backend_pool.Adapter(
        start=_start_codex_server,
        alive=alive,
        # The pool's generic interrupt has no turn to name. Codex's cancel path
        # goes through CodexServer.interrupt with the ids it holds; reaching
        # here means nothing could be confirmed.
        interrupt=lambda _server, _timeout: False,
        stop=stop,
        busy=busy,
    )


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
        # The turn borrows a process it does not own: the pool starts it, the
        # pool stops it, and several tabs may be reading the same one.
        self._server: Optional[CodexServer] = None
        self._held: Optional[backend_pool.HeldProcess] = None
        # Where this turn's stderr begins in a process that has been running
        # since long before it. Zero until a server is borrowed, which is
        # right for one started for this turn: everything it has said is this
        # turn's.
        self._stderr_mark = 0
        self._inbox: Optional["queue.Queue[object]"] = None
        self._expected: list[int] = []
        self._borrowed = False
        self._cancelled = False
        self._accepting_input = threading.Event()
        self._thread_id = session_id or ""
        self._turn_id = ""
        # Set when an interrupt went unconfirmed, or when a turn that was asked
        # for could never be named: this conversation is not trusted to be
        # carried on in the process, and the next turn resumes it from its
        # rollout on disk instead. The thread is what is given up, never the
        # server -- several other tabs are talking through that.
        self.abandoned_thread = ""
        # Whether Codex has been asked to run a turn on this conversation. True
        # from the moment the request goes out, which is before there is any id
        # to name it by: between the two, a turn is running that a cancel
        # cannot address.
        self._turn_requested = False
        # Set once `_turn_id` will not change again -- either because the turn
        # has been named or because this turn has stopped looking. A cancel on
        # another thread waits on it rather than polling.
        self._turn_id_known = threading.Event()
        # Set when this turn has ended of its own accord. A cancel that lost
        # the race to a finishing turn has nothing to interrupt, and must not
        # read the silence that follows as Codex ignoring it.
        self._finished = threading.Event()
        # The turn id currently registered with the server, so the registration
        # is given back exactly once.
        self._watched = ""
        # Whether this turn has asked for a turn of its own yet. Until it has,
        # anything naming a turn names an older one.
        self._turn_asked = False
        # The turns those earlier messages named. Once a turn has been seen to
        # be somebody else's, it stays somebody else's however late the rest of
        # it arrives.
        self._stale_turns: set[str] = set()
        self._request_id = 10
        self._assistant_parts: list[str] = []
        self._assistant_delta_seen: set[str] = set()
        self._assistant_streams: dict[str, list[str]] = {}
        self._reasoning_streams: dict[str, list[str]] = {}
        self._tool_outputs: dict[str, list[str]] = {}
        self._failed = False

    @property
    def _stderr_lines(self) -> list[str]:
        """What the shared server has said on stderr, newest last.

        Only what has been said since this turn borrowed the process. The pipe
        belongs to the process now rather than the turn, because one process
        outlives many turns and is read by one thread for all of them -- so
        without the mark, a death at turn fifty would be explained with a
        warning from turn three, out loud, to somebody who cannot scroll back
        through the transcript and check.

        It cannot be called `_stderr`: `threading.Thread` keeps a copy of the
        real `sys.stderr` under that name, to report a thread that dies.
        """
        server = self._server
        return server.stderr_since(self._stderr_mark) if server is not None else []

    def accepting_input(self) -> bool:
        return self._accepting_input.is_set() and not self._cancelled

    def _send(self, message: dict) -> bool:
        server = self._server
        if server is None:
            return False
        return server.send(message)

    def _next_id(self) -> int:
        # Ids are the server's, not the turn's: several tabs write to one
        # stdin, and two turns numbering from ten would each be handed the
        # other's replies.
        server = self._server
        if server is not None:
            return server.next_id()
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
        """Stop this turn without stopping the server the other tabs share.

        Codex's app-server multiplexes: one process holds every tab's thread.
        Killing it here would end four other conversations to stop one, so the
        usual "kill if unsure" is replaced by a middle rung. The interrupt is
        sent and waited on, and if Codex does not confirm the turn stopped, the
        THREAD is abandoned rather than the process: the next turn resumes it
        from its rollout, and the wedged conversation is the only thing that
        pays.

        This runs off the window's thread, and everything it waits on is
        bounded, because the window is waiting on it: at worst the grace for
        naming the turn and then the verify, which together fit inside the
        teardown budget with room for the join that follows.
        """
        # Read before the flag is set, so that this only ever means "the turn
        # had already ended when Stop was pressed" and never "the turn ended
        # because of this cancel".
        finished = self._finished.is_set()
        self._cancelled = True
        self._accepting_input.clear()
        if finished:
            # The answer landed while Stop was on its way. There is nothing to
            # interrupt, and waiting for a confirmation of it would spend the
            # whole verify budget and then give up a perfectly good thread:
            # `_release` has already handed back the watch whose completion
            # the reader set, so a fresh one would never be set at all.
            return
        server = self._server
        thread_id = self._thread_id
        if server is None or not thread_id:
            # Nothing has been asked of Codex about this conversation: a turn
            # is only ever started after the reply that names its thread.
            return
        turn_id = self._turn_id
        if not turn_id and self._turn_requested:
            # Stop landed between asking for a turn and being told its id. The
            # turn is running -- writing files, spending tokens -- and the only
            # thing missing is its name, which is usually already on its way.
            self._turn_id_known.wait(_CODEX_TURN_ID_GRACE_SECONDS)
            turn_id = self._turn_id
        if not turn_id:
            if self._turn_requested:
                # A turn was asked for and never named, so there is nothing to
                # interrupt by name. Saying the tab stopped while trusting the
                # conversation anyway would be the lie: whatever that turn goes
                # on to do is not in this thread's history as the next prompt
                # would read it, so the thread is given up instead.
                self._abandon(thread_id)
            return
        if not server.interrupt(thread_id, turn_id, _CODEX_INTERRUPT_VERIFY_SECONDS):
            self._abandon(thread_id, turn_id)

    def _abandon(self, thread_id: str, turn_id: str = "") -> None:
        """Give up this conversation, and warn whoever resumes it next.

        Giving up the thread is only half of it. The turn is STILL GENERATING
        on the server -- that is what an unconfirmed interrupt means -- and the
        next prompt in this tab resumes the same conversation, so that turn's
        words arrive in the middle of somebody else's. `_stale_turns` is how a
        turn recognises another turn's words, and it can only learn a name from
        messages that arrive before this turn asks for anything. A turn that is
        quiet across that window -- mid tool call -- and speaks after it used to
        have its text appended to the next answer.

        So the name is left on the process, which outlives this worker and is
        the one thing the next worker is certain to be holding.
        """
        self.abandoned_thread = thread_id
        server = self._server
        if server is not None:
            server.abandon_turn(thread_id, turn_id)

    def _fail(self, message: str) -> None:
        """Report why the turn ended, once.

        The first account of a failure is the one that can be acted on. A crash
        while cleaning up after it is not worth speaking over the top of it.
        """
        if self._failed:
            return
        self._failed = True
        diagnostics.log_unfinished_turn(
            "codex",
            session_id=self._session_id or "(new)",
            permission_mode=self._permission_mode,
            model=self._model or "(default)",
            cancelled=self._cancelled,
            detail=message,
        )
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
            # This turn is over, however it ended. Read by `cancel` before it
            # interrupts anything, because the watch it would wait on is about
            # to be given back below.
            self._finished.set()
            # Nothing more will be read, so no further name can be learned. A
            # cancel racing the end of the turn stops waiting for one rather
            # than spending its whole grace on a thread that has gone.
            self._turn_id_known.set()
            # The process belongs to the pool now, not to this turn. It is
            # stopped when the conversation goes away, when it is found dead,
            # or when the reaper decides nobody is using it. All the turn
            # gives back is its place in the server's routing tables.
            self._release()
            self._on_done()

    def _release(self) -> None:
        """Stop the shared reader delivering to a turn that has ended."""
        server = self._server
        if server is None:
            return
        if self._watched:
            server.forget_turn(self._watched)
            self._watched = ""
        server.unexpect(self._expected)
        self._expected = []
        inbox = self._inbox
        if inbox is not None:
            # By identity: a turn cancelled before it read the reply that named
            # its conversation never learned the id to detach by.
            server.detach_listener(inbox)
        held = self._held
        if held is not None:
            # Touched before the borrow is given back, so `busy()` cannot go
            # false while the clock still reads turn-start: a reaper sweep
            # landing between these two statements must see either a still
            # busy server or a freshly touched one, never idle-since-the-
            # start-of-a-turn-that-just-finished. The idle clock is time with
            # NO turn, so it starts here rather than when the turn began.
            # Started at the take instead, a turn that ran for fourteen
            # minutes was reaped a minute after it answered -- while the next
            # prompt was being typed.
            held.touch()
        if self._borrowed:
            self._borrowed = False
            server.give_back()

    def _borrow_server(self) -> Optional[backend_pool.HeldProcess]:
        """The app-server this turn will speak through, or None having failed.

        Taking and handing back happen under one lock so that two tabs sending
        their first Codex message together cannot each launch a server, with
        the second one kept displacing and stopping the first mid-turn.
        """
        key = backend_pool.pool_key(BACKEND_CODEX)
        shared = backend_pool.pool()
        with _CODEX_START_LOCK:
            held = shared.take(key)
            # A process started for this turn has said nothing that is not
            # this turn's, so the mark stays where it began.
            reused = held is not None
            if held is None:
                try:
                    started = _start_codex_server()
                except OSError as exc:
                    self._fail(f"Failed to launch Codex: {exc}")
                    return None
                held = backend_pool.HeldProcess(started, codex_adapter())
                self._server = started
                # The handshake belongs to the process, not the turn: a reused
                # server has already been initialized and would refuse a second.
                if not self._handshake():
                    detail = self._why_it_died("Codex did not answer the initialize handshake")
                    self._server = None
                    held.stop()
                    self._fail(detail)
                    return None
            shared.keep(key, held)
            # Registered under the same lock the drop takes, so a turn that
            # has been handed this server is never counted as absent by a turn
            # deciding whether anyone still wants it.
            self._held = held
            server = cast(CodexServer, held.handle)
            self._server = server
            if reused:
                # Everything after this point on stderr is this turn's to be
                # explained by; anything before it belonged to another turn.
                self._stderr_mark = server.stderr_mark()
            self._inbox = server.inbox()
            server.borrow()
            self._borrowed = True
        return held

    def _discard_server(self) -> None:
        """Take a server that could not start this conversation out of the pool.

        A turn that cannot start or resume its thread has usually been handed a
        process that will not serve any turn -- an app-server whose handshake
        went unanswered, say -- and leaving it there would fail every prompt
        for the next quarter of an hour, where a per-turn process used to get a
        fresh start each time. It is only dropped while no other tab is holding
        it -- borrowing, not reading: a turn that has sent `thread/start` and
        is still waiting on the reply is bound to no conversation yet, and is
        exactly the turn that must not have its process stopped underneath it.
        This must never be the way one conversation's bad session id ends
        another's live turn.
        """
        server = self._server
        if server is None:
            return
        with _CODEX_START_LOCK:
            if server.borrower_count() > 1:
                return
            backend_pool.pool().drop(backend_pool.pool_key(BACKEND_CODEX))

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
        held = self._borrow_server()
        if held is None:
            return
        server = cast(CodexServer, held.handle)
        inbox = cast("queue.Queue[object]", self._inbox)

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
        # `expect_thread`, not `expect`: this is the reply that names the
        # conversation, and the reader subscribes it before handing the reply
        # on, leaving no window in which a notification for it has nowhere
        # to go.
        self._expect(thread_request, binds_thread=True)
        self._send(request)

        turn_request = 0
        compact_request = 0
        started_notified = False
        while True:
            if self._cancelled:
                self._name_the_stopped_turn(inbox, turn_request)
                return
            try:
                queued = inbox.get(timeout=_CODEX_POLL_SECONDS)
            except queue.Empty:
                continue
            if queued is _CODEX_CLOSED:
                break
            if queued is _CODEX_ASKED:
                # Everything read before here was said before this turn asked
                # for anything, so none of it was about this turn.
                self._turn_asked = True
                continue
            if not isinstance(queued, dict):
                continue
            message: dict = queued

            if message.get("id") == thread_request:
                error = message.get("error")
                if error:
                    # A server that cannot start a conversation will not start
                    # the next one either; it goes rather than failing every
                    # prompt until the reaper notices it.
                    self._discard_server()
                    self._fail(self._error_text(error, "Could not start a Codex session"))
                    return
                thread = (message.get("result") or {}).get("thread") or {}
                self._thread_id = str(thread.get("id") or self._session_id or "")
                if not self._thread_id:
                    self._discard_server()
                    self._fail("Codex did not return a session id")
                    return
                # Anything given up on this conversation is somebody else's
                # turn, however late it speaks. Read once and cleared: the
                # only turn that has to know is the one now resuming.
                self._stale_turns.update(server.take_abandoned_turns(self._thread_id))
                # The reader has already subscribed this conversation from the
                # reply. This is the belt and braces for the fall back to the
                # session id above, and it is idempotent.
                server.attach(self._thread_id, inbox)
                self._on_session(self._thread_id)
                if self._compact:
                    # Compaction is not a message: it answers immediately with
                    # an empty result and then runs a turn of its own, whose
                    # notifications the loop below already understands.
                    compact_request = self._next_id()
                    # Compaction runs a turn of its own, so from here a cancel
                    # has something to stop -- but the reply to this request is
                    # empty, so nothing but the turn's own notifications will
                    # ever name it.
                    self._turn_requested = True
                    inbox.put(_CODEX_ASKED)
                    self._expect(compact_request)
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
                # Set before the send, not after the reply: from here on a turn
                # may be running that a cancel has no name for, and treating
                # that as "nothing was started" is exactly how one gets left
                # running under a tab that says it stopped.
                self._turn_requested = True
                inbox.put(_CODEX_ASKED)
                self._expect(turn_request)
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
                self._watch_turn()
                self._accepting_input.set()
                if not started_notified:
                    self._on_started()
                    started_notified = True
                continue

            method = str(message.get("method") or "")
            params = message.get("params") or {}
            if not self._is_this_turn(method, params):
                # The conversation is right but the turn is not: Codex is still
                # winding up a turn that was interrupted, and its trailing
                # deltas and its `turn/completed` are not this turn's to act on.
                if not self._turn_asked:
                    # Learned by name, so the rest of that turn is recognised
                    # however late it comes -- including after the point where
                    # position alone would have let it through.
                    named = self._turn_named(params)
                    if named:
                        self._stale_turns.add(named)
                if method and "id" in message:
                    # It asked something, though, and an answer it never gets
                    # is a request id Codex holds for ever.
                    self._decline_server_request(message)
                continue
            if self._compact and not self._turn_id:
                # Only compaction: `thread/compact/start` answers empty, so
                # nothing but the turn's own messages will ever name its turn.
                # Everywhere else the `turn/start` reply names it, and adopting
                # from a notification instead would give a straggler from the
                # turn before this one a way in.
                named = self._turn_named(params)
                if named:
                    self._turn_id = named
                    self._watch_turn()

            if method and "id" in message:
                self._handle_server_request(message)
                continue

            if method == "turn/started":
                turn = params.get("turn") or {}
                self._turn_id = str(turn.get("id") or self._turn_id)
                self._watch_turn()
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
            self._fail(self._why_it_died("Codex app server closed before the turn completed"))

    def _handshake(self) -> bool:
        """Introduce BlindPilot to a freshly started app-server, once.

        This is the process's handshake, not the turn's: a server taken back
        out of the pool has already been through it, and a second `initialize`
        is not something the protocol offers.
        """
        sent = self._send(
            {
                "method": "initialize",
                "id": self._next_id(),
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
        return sent and self._send({"method": "initialized", "params": {}})

    def _expect(self, request_id: int, binds_thread: bool = False) -> None:
        """Have the shared reader deliver this request's reply to this turn."""
        server, inbox = self._server, self._inbox
        if server is None or inbox is None:
            return
        self._expected.append(request_id)
        if binds_thread:
            server.expect_thread(request_id, inbox)
        else:
            server.expect(request_id, inbox)

    def _watch_turn(self) -> None:
        """Let a cancel on another thread find out when this turn stops.

        Registered once per turn id, and given back in `_release`: the server
        counts its watchers, and one that is never given back is one event kept
        for the life of a process that now outlives thousands of turns.
        """
        if not self._turn_id:
            return
        # A cancel on another thread may be waiting to learn this, so that it
        # has something to name in its interrupt.
        self._turn_id_known.set()
        server = self._server
        if server is None or self._turn_id == self._watched:
            return
        if self._watched:
            server.forget_turn(self._watched)
        self._watched = self._turn_id
        server.watch_turn(self._turn_id)

    def _name_the_stopped_turn(self, inbox: "queue.Queue[object]", turn_request: int) -> None:
        """Before leaving, learn the id of a turn that was asked for unnamed.

        Stop can land in the gap between `turn/start` and the reply that names
        the turn. The turn is running on the server -- under `workspaceWrite`
        or `dangerFullAccess`, writing files and spending tokens -- and the
        only thing missing is the name to interrupt it by, which the reply is
        about to carry. So the last thing this turn does is look for that one
        reply, and hand the id to the `cancel` waiting on `_turn_id_known`.

        Only the reply to this turn's own request id is taken. Everything else
        read here is dropped unlooked at: the answer is already abandoned, and
        a message acted on after Stop is how a stopped turn's words get spliced
        into the next one's.
        """
        try:
            if self._turn_id or not turn_request:
                return
            deadline = time.monotonic() + _CODEX_TURN_ID_GRACE_SECONDS
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    return
                try:
                    queued = inbox.get(timeout=min(left, _CODEX_POLL_SECONDS))
                except queue.Empty:
                    continue
                if queued is _CODEX_CLOSED:
                    # The server has gone, so the turn has gone with it.
                    return
                if not isinstance(queued, dict) or queued.get("id") != turn_request:
                    continue
                if queued.get("method") is not None:
                    # A request from Codex, not the reply to ours. Its ids come
                    # from the other direction's namespace and both counters
                    # start small, so one can carry the number this turn is
                    # waiting on; read as the reply it would name no turn and
                    # end the search early.
                    continue
                if queued.get("error"):
                    # Codex refused the turn, so nothing is running to name and
                    # the conversation is as it was.
                    self._turn_requested = False
                    return
                # Shape-checked at every step: a malformed reply here would
                # come back as "BlindPilot stopped reading Codex" on a turn the
                # person has already stopped.
                result = queued.get("result")
                turn = result.get("turn") if isinstance(result, dict) else None
                self._turn_id = str(turn.get("id") or "") if isinstance(turn, dict) else ""
                return
        finally:
            # Whether or not it was found, nobody else is going to find it.
            self._turn_id_known.set()
            if self.abandoned_thread and self._turn_id:
                # The cancel gave up waiting for this name before the reply
                # carrying it arrived. Late is still in time for the next turn.
                self._abandon(self.abandoned_thread, self._turn_id)

    @staticmethod
    def _turn_named(params: dict) -> str:
        turn = params.get("turn")
        if isinstance(turn, dict) and turn.get("id"):
            return str(turn["id"])
        return str(params.get("turnId") or "")

    def _is_this_turn(self, method: str, params: dict) -> bool:
        """Whether a message about a turn is about the turn this worker is running.

        One conversation is one process's thread, and a turn that was stopped
        goes on producing for a moment after the interrupt is sent. Those
        messages arrive on the right thread but belong to the turn before this
        one, and acting on their `turn/completed` used to fail the new turn
        instantly with "Codex turn was interrupted" -- for a person who cannot
        see a spinner, an unexplained failure a retry then fixes.

        What separates the two is not the order the messages arrive in but
        where they sit relative to the point this turn asked for a turn --
        which is what `_CODEX_ASKED` marks. Nothing said before that can be
        about this turn. Anything after it may be, in whatever order it comes,
        so a compaction turn -- whose id no reply ever carries -- does not
        depend on `turn/started` arriving before its items.

        Position alone cannot judge a straggler that arrives after the mark, so
        the turns rejected before it are remembered by name as well. Leftovers
        are exactly what queues ahead of the mark, so in the ordinary case the
        first of them names the turn and the rest are recognised however late
        they come.
        """
        named = self._turn_named(params)
        if not named:
            # Nothing to attribute it to: process-wide, or about the thread
            # rather than a turn.
            return True
        if named in self._stale_turns:
            # Already known to belong to the turn before this one. Position
            # cannot vouch for a straggler that arrives after the mark; a name
            # already seen on the wrong side of it can.
            return False
        if self._turn_id:
            return named == self._turn_id
        if not self._turn_asked:
            # Nothing has been asked for, so this is the tail of the turn that
            # ran before this one -- the one somebody stopped.
            return False
        # Asked, and not yet told which turn we were given. An ending is the
        # exception: a turn cannot finish before the beginning we never saw.
        return method != "turn/completed"

    def _why_it_died(self, fallback: str) -> str:
        """The best account available of why the app-server stopped talking.

        stderr is a different pipe read by a different thread, and the line
        worth having - the panic, the unauthorized, the out of memory - is the
        last one written, which is exactly the one still in flight. Everything
        earlier is already in the list, so the race did not lose noise, it lost
        the reason, and it lost it differently each time.
        """
        self._await_last_words()
        detail = "\n".join(self._stderr_lines[-10:]).strip()
        if detail:
            return detail
        server = self._server
        return (server.read_error() if server is not None else "") or fallback

    def _await_last_words(self) -> None:
        """Let the final line of stderr land before reporting why Codex died.

        Bounded, because a pipe that never closes must not hold the turn open.
        """
        server = self._server
        if server is not None:
            server.await_last_words(_CODEX_LAST_WORDS_SECONDS)

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

    def _decline_server_request(self, message: dict) -> None:
        """Say no to something a turn that is over asked for.

        Codex holds an unanswered request id open, so silence is not the same
        as refusal. Declining is: the turn it belongs to was interrupted, and
        the person is not going to be shown a question from it.
        """
        method = str(message.get("method") or "")
        request_id = message.get("id")
        if method == "item/tool/requestUserInput":
            questions = _codex_questions((message.get("params") or {}).get("questions"))
            answers: dict[str, dict] = {
                question.id or question.question: {"answers": []} for question in questions
            }
            self._send({"id": request_id, "result": {"answers": answers}})
        elif method.endswith("requestApproval"):
            self._send({"id": request_id, "result": {"decision": "decline"}})
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
# The longest a single FreeBuff turn is listened to. Reaching it is not the
# turn finishing, and is reported as what it is - see the end of `_do_run`.
_FREEBUFF_TURN_SECONDS = 60 * 60

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
        folded = character.casefold() if letters_only else character
        kept.append(folded)
        # One position per character of the *key*, not per character of the
        # text. `casefold` is not length-preserving - German "ss" comes from a
        # single letter, Turkish and the ligatures do the same - and a map with
        # one entry per input character then runs short, so the index used to
        # cut the answer pointed at the wrong letter or off the end entirely.
        positions.extend([index] * len(folded))
    # The sentinel, so an answer that was read out in full is still indexable.
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
        diagnostics.log_unfinished_turn(
            "freebuff",
            session_id=self._session_id or "(new)",
            permission_mode="n/a",
            model=self._model or "(default)",
            cancelled=self._cancelled,
            detail=message,
        )
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
        deadline = time.monotonic() + _FREEBUFF_TURN_SECONDS

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
                self._on_activity("notice", f"FreeBuff is still working; {elapsed} seconds elapsed")
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
        # The loop ends either because FreeBuff finished - which breaks out of
        # it - or because the hour ran out underneath a turn that was still
        # going. Those were indistinguishable from here, so a turn cut off
        # mid-sentence was delivered through the same `_on_complete` a finished
        # one uses: announced as the answer, kept in the transcript as the
        # answer, with nothing to suggest it was not the whole of it.
        timed_out = not self._cancelled and time.monotonic() >= deadline
        response = structured_answer or _unwrap_screen_text(screen_response)
        if response:
            # An answer taller than the terminal scrolls its own beginning off
            # the screen, and the reading stops there. The saved chat is the
            # whole of it, so whatever was never read is read now.
            tail = _unspoken_tail(self._narrated.get("assistant", ""), response)
            if tail:
                self._on_activity("assistant", tail)
            if timed_out:
                # Kept, not discarded: an hour of work is worth having. Said
                # first, so it is not mistaken for the end of the answer.
                self._on_activity(
                    "notice",
                    "BlindPilot stopped listening to FreeBuff an hour after the message "
                    "was sent. What follows is as far as it had got, not a finished answer.",
                )
            self._on_complete(response)
        elif timed_out:
            self._fail(
                "FreeBuff was still working an hour after the message was sent and had "
                "produced no answer, so BlindPilot stopped waiting for it."
            )
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


def _opencode_entry_info(entry: dict) -> dict:
    """The `info` block of a history entry, or the entry itself.

    opencode nests message metadata under `info` in some shapes and inlines it
    in others. Written inline as a conditional this called `.get` twice, so the
    guard tested one value and the code then used another - harmless in
    practice, and exactly the shape that stops being harmless after an edit.
    """
    found = entry.get("info")
    return found if isinstance(found, dict) else entry


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
            diagnostics.log_unfinished_turn(
                "opencode",
                session_id=self._session_id or "(new)",
                permission_mode=self._permission_mode,
                model=self._model or "(default)",
                cancelled=self._cancelled,
                detail=message,
            )
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
            info = _opencode_entry_info(entry)
            stamped = info.get("time")
            time = stamped if isinstance(stamped, dict) else {}
            try:
                return float(time.get("created") or info.get("time_created") or 0)
            except (TypeError, ValueError):
                return 0.0

        cutoff_id = ""
        cutoff_stamp = -1.0
        for entry in entries:
            info = _opencode_entry_info(entry)
            if str(info.get("role") or "") != "assistant":
                continue
            message_id = str(info.get("id") or "")
            if not message_id:
                continue
            listed = entry.get("parts")
            parts = listed if isinstance(listed, list) else []
            for part in parts:
                if not isinstance(part, dict) or str(part.get("tool") or "") != "question":
                    continue
                reported = part.get("state")
                state = reported if isinstance(reported, dict) else {}
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
                if _opencode_entry_info(entry).get("id") == cutoff_id
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
            info = _opencode_entry_info(entry)
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
        self._on_activity("notice", f"Could not answer {what}: {problem}")
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

    All four workers are threads, but a thread is not what the window wants
    from them: it wants to start a turn, ask whether it is still running, stop
    it, and wait for it to let go. Saying that here is what lets the window
    hold whichever worker the backend chose without knowing which one it is —
    and lets `cancel` be a method the code is allowed to call, rather than one
    a reader has to take on trust.
    """

    def start(self) -> None: ...

    def is_alive(self) -> bool: ...

    def steer(self, text: str) -> bool: ...

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
