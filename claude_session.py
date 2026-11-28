"""One Claude Code process per tab, kept alive between turns.

A turn used to be a process. It was started with --resume, fed one message
over stdin, and its stdin was closed when its result arrived, so the CLI shut
down. Anything the turn had left running inside the CLI, an agent started in
the background or one resumed with SendMessage, died with it. The process now
belongs to the tab. A turn attaches, sends its message, reads to its result
and detaches. What the CLI says while no turn is attached is handed to the
panel, which starts a turn to receive it.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import queue
import re
import shutil
import subprocess
import threading
import uuid
import weakref
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional, cast

import backend_pool
from owned_process import owned_popen
from agent_backends import (
    BACKEND_CLAUDE,
    ask_through_the_tool,
    subprocess_env,
)
from process_utils import (
    end_process_group,
    no_window_kwargs,
    own_group_kwargs,
)

_log = logging.getLogger("blindpilot.claude")

# Swapped by tests for a fake process.
_popen = owned_popen

# How long Stop waits for the CLI to confirm an interrupt before the process
# is stopped instead. The same budget Codex gives its interrupt.
_INTERRUPT_SECONDS = 5.0
# How long a model or permission mode change waits for the CLI's answer.
_CONTROL_SECONDS = 10.0
# Stop asks the CLI to end each running agent before it interrupts the turn.
# Bounded per agent: a CLI too old to know `stop_task` answers an error at
# once, and one that ignores it must not hold the stop for long.
_STOP_TASK_SECONDS = 2.0
# stderr lines kept. The tail is the part that says how a process ended.
_STDERR_KEEP = 4000
_STDERR_TRIM = 2000
# Events kept while no turn is attached, capped the same way: a CLI that
# keeps talking between turns must not grow the list without bound.
_PENDING_KEEP = 4000
_PENDING_TRIM = 2000

# Put on a turn's queue when the process ends, so a reader blocked on the
# queue learns of the death instead of waiting for a result that never comes.
EOF = None

# Claude Code saves a copy of each file its edit tools change, per user
# message, only while this is set; Put Files Back needs it at both ends: on
# the turns that made the changes and on the rewind (checked with 2.1.325).
CHECKPOINT_VAR = "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING"
# How long a rewind may take. No turn runs: the CLI restores files and exits.
_REWIND_SECONDS = 120.0
_SAFE_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z-]{7,63}$")
# Suggested messages: what Claude predicts you will send next. Checked with
# Claude Code 2.1.332: `--prompt-suggestions true` sends nothing without this
# variable; with both, a `prompt_suggestion` event comes a second or two after
# a turn's result, when no turn is reading. Not every turn has one.
SUGGESTION_VAR = "CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION"
# One message, not a document.
MAX_SUGGESTION = 2000
# What may be passed as `--agent`: Claude Code's own names ("Explore",
# "general-purpose", "plugin:agent-name"), nothing that reads as a flag.
_SAFE_AGENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/ -]{0,127}$")
_INIT_SECONDS = 20.0


@dataclass(frozen=True)
class Agent:
    """One agent Claude Code offers for a folder, as `initialize` lists it."""

    name: str
    description: str = ""
    model: str = ""

    @property
    def own_model(self) -> str:
        """The model it always runs on, or "" when it takes the tab's."""
        model = " ".join(self.model.split())
        return "" if model.lower() == "inherit" else model

    def label(self) -> str:
        """The name only: the whole description goes in a box of its own."""
        if self.own_model:
            return f"{self.name} (uses its own model, {self.own_model})"
        return self.name


def is_safe_agent(name: str) -> bool:
    return name == "" or bool(_SAFE_AGENT.fullmatch(name))


def agents_from_init(body: object) -> Optional[list[Agent]]:
    """The agents in an `initialize` answer, or None if it lists none at all."""
    raw = body.get("agents") if isinstance(body, dict) else None
    if not isinstance(raw, list):
        return None
    agents: list[Agent] = []
    seen: set[str] = set()
    for item in raw:
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not name or not is_safe_agent(name) or name in seen:
            continue
        seen.add(name)
        description, model = item.get("description"), item.get("model")
        agents.append(
            Agent(
                name,
                description if isinstance(description, str) else "",
                model if isinstance(model, str) else "",
            )
        )
    return agents


def fetch_agents(binary: str, cwd: Optional[str]) -> Optional[list[Agent]]:
    """The agents Claude Code offers in `cwd`, without running a turn.

    Only `initialize` is sent, and stdin is closed as soon as it is answered,
    so no conversation is made and nothing is billed (about 1.4 seconds with
    Claude Code 2.1.332). Blocking: call it off the GUI thread. None if the
    answer could not be had.
    """
    if cwd and not os.path.isdir(cwd):
        return None
    try:
        proc = subprocess.Popen(
            [
                binary,
                "-p",
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
                "--verbose",
            ],
            cwd=cwd or None,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=claude_env(binary),
            **no_window_kwargs(),
        )
    except OSError:
        return None
    timer = threading.Timer(_INIT_SECONDS, proc.kill)
    timer.start()
    body: object = None
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(
            json.dumps(
                {
                    "type": "control_request",
                    "request_id": "blindpilot-agents",
                    "request": {"subtype": "initialize", "hooks": None},
                }
            )
            + "\n"
        )
        proc.stdin.flush()
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "control_response":
                response = event.get("response")
                body = response.get("response") if isinstance(response, dict) else None
                break
    except (OSError, ValueError):
        body = None
    finally:
        timer.cancel()
        try:
            if proc.stdin is not None:
                proc.stdin.close()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - a stuck probe is killed, never waited on for ever
            proc.kill()
        if proc.stdout is not None:
            proc.stdout.close()
    return agents_from_init(body)


def claude_env(binary: str, api_key: str = "", suggestions: bool = False) -> dict[str, str]:
    """The environment every Claude process BlindPilot starts runs with."""
    env = dict(subprocess_env(binary))
    env[CHECKPOINT_VAR] = "1"
    if suggestions and not api_key:
        env[SUGGESTION_VAR] = "1"
    if api_key:
        import api_credits

        env = api_credits.claude_env(env, api_key)
    return env


def rewind_files(binary: str, cwd: str, session_id: str, message_id: str) -> tuple[bool, str]:
    """Put back the files Claude's edit tools changed since one user message.

    Runs ``claude -p --resume <session> --rewind-files <message>``, which
    restores from Claude Code's own checkpoints and exits without a turn.
    Returns whether it worked and a sentence to say. Blocks; call it off the
    window's thread.
    """
    if not (_SAFE_ID.match(session_id or "") and _SAFE_ID.match(message_id or "")):
        return False, "That message has no saved file checkpoint."
    try:
        done = subprocess.run(
            [binary, "-p", "--resume", session_id, "--rewind-files", message_id],
            cwd=cwd or None,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=claude_env(binary),
            timeout=_REWIND_SECONDS,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return False, "Claude Code did not finish putting the files back in time."
    except OSError as exc:
        return False, f"Claude Code could not be started: {exc}"
    said = [
        line.strip()
        for line in f"{done.stderr or ''}\n{done.stdout or ''}".splitlines()
        if line.strip() and not line.startswith("Warning:")
    ]
    if done.returncode == 0 and any("rewound" in line.lower() for line in said):
        return True, "Files put back to how they were before that message."
    reason = said[-1] if said else f"Claude Code exited with code {done.returncode}."
    if "not enabled" in reason.lower():
        reason = "Claude Code kept no file checkpoints for this conversation."
    return False, reason.removeprefix("Error: ")


@dataclass(frozen=True)
class Wants:
    """What a turn needs the process to have been started with."""

    cwd: str
    permission_mode: str
    model: str = ""
    effort: str = ""
    session_id: Optional[str] = None
    fork_source: str = ""
    fork_at: str = ""
    # Claude in Chrome. Part of what the process is started with, so changing
    # the preference takes effect from each chat's next message.
    chrome: bool = True
    # An Anthropic API key to charge this process to (api_credits); "" runs
    # on whatever Claude Code is signed in with.
    api_key: str = ""
    # Suggested messages (Preferences). Never on API credits: each suggestion
    # is an extra request the reply's cost would not show.
    suggestions: bool = False
    # Run as agent: one of the agents Claude Code offers for the folder, or ""
    # for its own. Fixed at start, so a change starts a new process.
    agent: str = ""


ASK_THROUGH_THE_TOOL = ask_through_the_tool("AskUserQuestion")

# Turned off for the rest of the process by the turn that meets a CLI too
# old to know `--include-partial-messages`, the same way the permission
# prompt tool is dropped. Read by `build_command` on the next start.
include_partial_messages = True

# Preferences, "Let Claude use Chrome". Set by the window's settings; read
# when a turn builds its Wants.
use_chrome = True
# Preferences, "Suggest what to send next after each Claude reply". Off by
# default: each suggestion spends a little of the plan.
use_suggestions = False


# ---------------------------------------------------------------------------
# Finding the CLI.
#
# It lives here because both the window (its installer, its updater and its
# model probe) and the turn worker need it, and neither may import the other:
# this module is the one they already share.
# ---------------------------------------------------------------------------

CLAUDE_BIN = "claude"


# Common install locations to check when `claude` isn't on PATH.
# macOS GUI apps launched from Finder/Dock inherit a minimal PATH
# (/usr/bin:/bin:/usr/sbin:/sbin) and miss Homebrew, nvm, the official
# ~/.claude/local installer, etc. Windows GUI apps usually inherit the
# user PATH, but installs to non-default npm prefixes can still miss it.
def _native_bin_dir() -> Path:
    """Where the official native installer puts the launcher, every platform."""
    return Path.home() / ".local" / "bin"


def _fallback_claude_paths() -> tuple[Path, ...]:
    home = Path.home()
    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA", str(home / "AppData" / "Roaming"))
        local_appdata = os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))
        candidates: list[Path] = []
        for name in ("claude.exe", "claude.cmd", "claude.ps1"):
            candidates.extend(
                [
                    # Native installer (install.ps1 / install.cmd) — the default.
                    _native_bin_dir() / name,
                    # WinGet's shim directory.
                    Path(local_appdata) / "Microsoft" / "WinGet" / "Links" / name,
                    Path(appdata) / "npm" / name,
                    home / ".claude" / "local" / name,
                    home / ".volta" / "bin" / name,
                    Path(local_appdata) / "Programs" / "claude" / name,
                ]
            )
        return tuple(candidates)
    return (
        _native_bin_dir() / "claude",
        home / ".claude" / "local" / "claude",
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
        home / ".npm-global" / "bin" / "claude",
        home / ".volta" / "bin" / "claude",
    )


def _login_shell() -> Optional[str]:
    """The user's real login shell, if there is one (POSIX only)."""
    if platform.system() == "Windows":
        return None
    shell = os.environ.get("SHELL")
    return shell if shell and os.path.isfile(shell) else None


def _login_shell_which(name: str) -> Optional[str]:
    """Resolve *name* the way a login shell would.

    A GUI app launched from Finder or the Dock inherits a minimal PATH, so this
    is how a CLI the user can run is found. It runs `$SHELL -l -c`, a login
    shell that is not interactive, so for zsh it reads .zprofile and not
    .zshrc. `command -v` is POSIX and also works in fish.
    """
    shell = _login_shell()
    if shell is None:
        return None
    try:
        result = subprocess.run(
            [shell, "-l", "-c", f"command -v {name}"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    path = result.stdout.strip().splitlines()[-1].strip()
    if path and os.path.isfile(path) and os.access(path, os.X_OK):
        return path
    return None


def _find_claude() -> Optional[str]:
    """Locate the `claude` binary even when launched from a GUI app.

    Order: PATH, well-known install locations, then (POSIX only) the user's
    login shell so any custom PATH from .zprofile / .bash_profile is honored.
    """
    binary = shutil.which(CLAUDE_BIN)
    if binary:
        return binary

    for candidate in _fallback_claude_paths():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    return _login_shell_which(CLAUDE_BIN)


def build_command(binary: str, wants: Wants, prompt_tool: str) -> list[str]:
    """The command line one process is started with.

    Streaming input mode keeps stdin open, so further messages can be pushed
    into the process while it works and after a turn ends. The prompt tool
    makes AskUserQuestion arrive as a control request on this same stream, and
    the instruction to use it goes on only when it does: a CLI old enough to
    have had the prompt tool dropped has no AskUserQuestion to be sent to.
    """
    cmd = [
        binary,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    if include_partial_messages:
        # Partial messages are the token-level stream a terminal renders as
        # it arrives. Without this the CLI only speaks in whole assistant
        # messages, so the first words of an answer waited for the whole
        # message however long it ran.
        cmd.append("--include-partial-messages")
    if prompt_tool:
        cmd.extend(["--append-system-prompt", ASK_THROUGH_THE_TOOL])
        cmd.extend(["--permission-prompt-tool", prompt_tool])
    if wants.permission_mode:
        cmd.extend(["--permission-mode", wants.permission_mode])
    if wants.model:
        cmd.extend(["--model", wants.model])
    if wants.effort:
        cmd.extend(["--effort", wants.effort])
    # Claude in Chrome: pages in Chrome or Edge, through the extension. Without
    # the extension the CLI simply has no browser tools.
    cmd.append("--chrome" if wants.chrome and not wants.api_key else "--no-chrome")
    if wants.suggestions and not wants.api_key:
        cmd.extend(["--prompt-suggestions", "true"])
    if wants.agent and is_safe_agent(wants.agent):
        cmd.extend(["--agent", wants.agent])
    if wants.fork_source:
        if wants.session_id or not wants.fork_at:
            raise ValueError("A fork needs a source boundary and no destination session yet.")
        cmd.extend(
            ["--resume", wants.fork_source, "--fork-session", "--resume-session-at", wants.fork_at]
        )
    elif wants.session_id:
        cmd.extend(["--resume", wants.session_id])
    return cmd


class ClaudeSession:
    """One live process and the two threads that belong to it, not to a turn."""

    def __init__(self, proc: subprocess.Popen, wants: Wants) -> None:
        self._proc = proc
        self.wants = wants
        self.session_id: Optional[str] = wants.session_id
        # Whether Remote Control is on for this process; it lasts as long
        # as the process, so it is asked for once, not every turn.
        self.remote_control_on = False
        self._write_lock = threading.Lock()
        self._state = threading.Lock()
        self._sink: Optional[queue.Queue] = None
        self._pending: list = []
        self._pending_dropped = 0
        self._idle_sink: Optional[Callable[[], None]] = None
        self._idle_told = False
        self._waiting: dict[str, tuple[threading.Event, dict]] = {}
        self._stderr: list[str] = []
        self._stderr_dropped = 0
        self._stopped = False
        self._ended = threading.Event()
        # The agents this process has running: task id -> the Agent tool
        # call that started it, which is how the window names the agent.
        # Kept on the process rather than the turn because agents outlive
        # the turn that started them, and Stop in a later turn still has to
        # reach them.
        self.agent_tasks: dict[str, str] = {}
        # Every Agent tool call seen, and those of them running in the
        # background, whose tool result is the launch, not the answer.
        self.agent_calls: set[str] = set()
        self.background_agents: set[str] = set()
        # Every task the CLI reports running, agents and background shells
        # alike: task id -> what it is doing. Kept by the reader, so it stays
        # right between turns, when no worker is reading.
        self._running_tasks: dict[str, str] = {}
        # Called on every event the CLI sends, so the pool's idle clock
        # measures silence from the CLI rather than time since the last prompt.
        self.on_event: Optional[Callable[[], None]] = None
        # Given each suggested message. Suggestions come after the turn has
        # ended, so they are kept out of the event stream: a late turn woken
        # for one would have nothing to say.
        self.on_suggestion: Optional[Callable[[str], None]] = None
        self._reader = threading.Thread(target=self._read, name="claude-reader", daemon=True)
        self._drainer = threading.Thread(
            target=self._drain_stderr, name="claude-stderr", daemon=True
        )
        self._reader.start()
        self._drainer.start()

    @classmethod
    def start(
        cls,
        binary: str,
        wants: Wants,
        prompt_tool: str,
        popen_kwargs: Optional[dict] = None,
    ) -> "ClaudeSession":
        proc = _popen(
            build_command(binary, wants, prompt_tool),
            cwd=wants.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            encoding="utf-8",
            # One malformed byte in a long run must not end the stream.
            errors="replace",
            # `claude` is often a shim that has to find `node`, and a window
            # started from a Dock or Start menu has a PATH that holds neither.
            env=claude_env(binary, wants.api_key, wants.suggestions),
            # The shim may have the real agent as its child; stopping has to
            # stop that too.
            **own_group_kwargs(),
            **(popen_kwargs or {}),
        )
        return cls(proc, wants)

    # ----- who hears the events -----
    def attach(self) -> queue.Queue:
        """Become the one turn reading this process. Pending events come first."""
        with self._state:
            if self._sink is not None:
                raise RuntimeError("a turn is already attached to this Claude session")
            sink: queue.Queue = queue.Queue()
            for event in self._pending:
                sink.put(event)
            self._pending.clear()
            self._idle_told = False
            if self._ended.is_set():
                sink.put(EOF)
            self._sink = sink
            return sink

    def detach(self) -> None:
        with self._state:
            self._sink = None

    def set_idle_sink(self, callback: Callable[[], None]) -> None:
        """Who is told, once, when the CLI speaks with no turn attached."""
        tell = None
        with self._state:
            self._idle_sink = callback
            if self._pending and self._sink is None and not self._idle_told:
                self._idle_told = True
                tell = callback
        if tell is not None:
            try:
                tell()
            except Exception:
                _log.exception("the Claude session's idle sink raised")

    def busy(self) -> bool:
        with self._state:
            return self._sink is not None

    def _deliver(self, event: Optional[dict]) -> None:
        tell = None
        with self._state:
            if self._sink is not None:
                self._sink.put(event)
                return
            if event is EOF:
                # A dead process with no turn attached is found by the pool
                # when the next turn takes it. Waking a late turn for it would
                # announce an exit code nobody asked about.
                return
            self._pending.append(event)
            if len(self._pending) > _PENDING_KEEP:
                del self._pending[:_PENDING_TRIM]
                self._pending_dropped += _PENDING_TRIM
            if self._idle_sink is not None and not self._idle_told:
                self._idle_told = True
                tell = self._idle_sink
        if tell is not None:
            try:
                tell()
            except Exception:
                _log.exception("the Claude session's idle sink raised")

    def _read(self) -> None:
        stdout = self._proc.stdout
        try:
            if stdout is not None:
                for raw in stdout:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        _log.warning("malformed Claude JSON line: %r", line[:200])
                        continue
                    self._touch()
                    if event.get("type") == "control_response":
                        self._settle(event)
                        continue
                    if event.get("type") == "prompt_suggestion":
                        self._suggest(event.get("suggestion"))
                        continue
                    if event.get("type") == "system" and event.get("subtype") == "init":
                        sid = event.get("session_id")
                        if sid:
                            self.session_id = sid
                    self._note_task(event)
                    self._deliver(event)
        except Exception as exc:
            # A decode error mid-stream is the known case. Whatever it was,
            # the stream is over and the turn is told so below.
            _log.warning("Claude Code's output stopped being readable: %s", exc)
        finally:
            self._ended.set()
            with self._state:
                waiters = list(self._waiting.values())
                self._waiting.clear()
            for done, _slot in waiters:
                done.set()
            self._deliver(EOF)

    def _suggest(self, text: object) -> None:
        callback = self.on_suggestion
        if not isinstance(text, str) or not text.strip() or callback is None:
            return
        try:
            callback(text.strip()[:MAX_SUGGESTION])
        except Exception:
            _log.exception("the Claude session's suggestion callback raised")

    def _note_task(self, event: dict) -> None:
        if event.get("type") != "system":
            return
        subtype = event.get("subtype")
        task_id = str(event.get("task_id") or "")
        if not task_id:
            return
        with self._state:
            if subtype == "task_started":
                what = str(event.get("description") or event.get("task_type") or "").strip()
                self._running_tasks[task_id] = " ".join(what.split())[:120] or "Background task"
            elif subtype == "task_notification":
                self._running_tasks.pop(task_id, None)

    def running_tasks(self) -> list[tuple[str, str]]:
        """(task id, description) for each task the CLI says is still running."""
        with self._state:
            return list(self._running_tasks.items())

    def stop_task(self, task_id: str) -> bool:
        """Stop one task, leaving the turn and every other task running."""
        return self._confirmed(
            self.send_control("stop_task", timeout=_STOP_TASK_SECONDS, task_id=task_id)
        )

    def _touch(self) -> None:
        callback = self.on_event
        if callback is not None:
            callback()

    # ----- writing -----
    def write_json(self, payload: dict) -> bool:
        """Write one JSON line to the process. False if it could not be."""
        stdin = self._proc.stdin
        with self._state:
            stopped = self._stopped
        if stdin is None or stopped:
            return False
        try:
            with self._write_lock:
                stdin.write(json.dumps(payload) + "\n")
                stdin.flush()
        except (OSError, ValueError):
            return False
        return True

    def send_user(self, text: str) -> bool:
        return self.write_json(
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "text", "text": text}]},
            }
        )

    def send_control(
        self, subtype: str, timeout: Optional[float] = None, **fields: object
    ) -> Optional[dict]:
        """Ask the CLI something and wait for its answer, or None on silence."""
        if timeout is None:
            timeout = _CONTROL_SECONDS
        request_id = uuid.uuid4().hex
        done = threading.Event()
        slot: dict = {}
        with self._state:
            self._waiting[request_id] = (done, slot)
        try:
            sent = self.write_json(
                {
                    "type": "control_request",
                    "request_id": request_id,
                    "request": {"subtype": subtype, **fields},
                }
            )
            if not sent or not done.wait(timeout):
                return None
            return slot.get("response")
        finally:
            with self._state:
                self._waiting.pop(request_id, None)

    @staticmethod
    def _confirmed(response: Optional[dict]) -> bool:
        return response is not None and response.get("subtype") == "success"

    def stop_agents(self) -> None:
        """Ask the CLI to stop every agent it has running.

        An interrupt ends the turn and leaves its agents working; a Stop
        that left them editing files and spending tokens would be no stop.
        `stop_task` ends one by its task id, and the CLI reports each one's
        end as a task_notification.
        """
        for task_id in list(self.agent_tasks):
            self.send_control("stop_task", timeout=_STOP_TASK_SECONDS, task_id=task_id)

    def interrupt(self, timeout: Optional[float] = None) -> bool:
        """Whether the CLI confirmed the running turn was stopped."""
        if timeout is None:
            timeout = _INTERRUPT_SECONDS
        return self._confirmed(self.send_control("interrupt", timeout=timeout))

    def set_model(self, model: str) -> bool:
        if model == self.wants.model:
            return True
        if not model:
            # The CLI has a request for a named model and none for "whatever
            # the default is", so that change restarts the process.
            return False
        if not self._confirmed(self.send_control("set_model", model=model)):
            return False
        self.wants = replace(self.wants, model=model)
        return True

    def set_permission_mode(self, mode: str) -> bool:
        if mode == self.wants.permission_mode:
            return True
        if not mode or not self._confirmed(self.send_control("set_permission_mode", mode=mode)):
            return False
        self.wants = replace(self.wants, permission_mode=mode)
        return True

    def can_serve(self, wants: Wants) -> bool:
        """Whether this process can carry the turn, given what the stream can change.

        The working directory, the effort and the conversation are fixed at
        start. The model and the permission mode travel down the stream, so
        they do not decide this; `adopt` applies them.
        """
        # A stream that has ended is a process on its way out, and `poll()`
        # has no code to say so until it has been reaped.
        if self._ended.is_set():
            return False
        if not self.alive():
            return False
        if (self.wants.cwd, self.wants.effort, self.wants.suggestions, self.wants.agent) != (
            wants.cwd,
            wants.effort,
            wants.suggestions,
            wants.agent,
        ):
            return False
        return wants.session_id is not None and wants.session_id == self.session_id

    def adopt(self, wants: Wants) -> bool:
        return self.set_model(wants.model) and self.set_permission_mode(wants.permission_mode)

    def _settle(self, event: dict) -> None:
        raw = event.get("response")
        response = raw if isinstance(raw, dict) else {}
        request_id = response.get("request_id") or event.get("request_id")
        with self._state:
            waiter = self._waiting.get(request_id) if isinstance(request_id, str) else None
        if waiter is None:
            return
        done, slot = waiter
        slot["response"] = response
        done.set()

    # ----- the process -----
    def alive(self) -> bool:
        with self._state:
            stopped = self._stopped
        return not stopped and self._proc.poll() is None

    def returncode(self) -> Optional[int]:
        return self._proc.poll()

    def wait(self, timeout: float = 2.0) -> Optional[int]:
        """The exit code, waiting briefly for one. None if it never arrived.

        A stream that has ended is not yet a process that has been reaped, so
        `poll()` straight after EOF often has no code to give. Waiting is the
        difference between saying how the CLI ended and saying "None".
        """
        try:
            return self._proc.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def stop(self) -> None:
        """End the process group. Safe to call again; only the first call acts."""
        with self._state:
            if self._stopped:
                return
            self._stopped = True
        end_process_group(self._proc)
        stdin = self._proc.stdin
        if stdin is not None:
            try:
                stdin.close()
            except (OSError, ValueError):
                pass

    # ----- stderr -----
    def _drain_stderr(self) -> None:
        stream = self._proc.stderr
        if stream is None:
            return
        try:
            for line in stream:
                with self._state:
                    self._stderr.append(line)
                    if len(self._stderr) > _STDERR_KEEP:
                        del self._stderr[:_STDERR_TRIM]
                        self._stderr_dropped += _STDERR_TRIM
        except Exception:
            # The pipe closed under us, which is what exiting looks like.
            pass

    def stderr_mark(self) -> int:
        """Where stderr stands now, so a turn can read only its own lines."""
        with self._state:
            return self._stderr_dropped + len(self._stderr)

    def stderr_since(self, mark: int) -> str:
        with self._state:
            start = max(0, mark - self._stderr_dropped)
            return "".join(self._stderr[start:]).strip()

    def wait_stderr(self, timeout: float = 2.0) -> None:
        """Let the drainer catch up once the process has ended."""
        self._drainer.join(timeout)


def claude_adapter() -> backend_pool.Adapter:
    """What the pool needs to know about a Claude process, and nothing more."""
    return backend_pool.Adapter(
        alive=lambda session: cast(ClaudeSession, session).alive(),
        interrupt=lambda session, timeout: cast(ClaudeSession, session).interrupt(timeout),
        stop=lambda session: cast(ClaudeSession, session).stop(),
        busy=lambda session: cast(ClaudeSession, session).busy(),
    )


# Taking and starting happen under one lock per tab, so two turns in the same
# tab can never both start a process, while one tab's slow model change never
# holds up another tab's turn.
_panel_locks: "weakref.WeakKeyDictionary[object, threading.Lock]" = weakref.WeakKeyDictionary()
_panel_locks_guard = threading.Lock()
# Panels are weakly referenced above, and None cannot be, so the shared slot
# backend_pool.pool_key uses when there is no panel keeps a lock of its own.
_no_panel_lock = threading.Lock()


def _lock_for(panel: object) -> threading.Lock:
    """The lock that guards taking and starting for one tab."""
    if panel is None:
        return _no_panel_lock
    with _panel_locks_guard:
        lock = _panel_locks.get(panel)
        if lock is None:
            lock = threading.Lock()
            _panel_locks[panel] = lock
        return lock


def held_session(panel: object) -> Optional[ClaudeSession]:
    """The tab's live Claude process, if it has one. Never starts one."""
    held = backend_pool.pool().take(backend_pool.pool_key(BACKEND_CLAUDE, panel))
    return cast(Optional[ClaudeSession], held.handle if held is not None else None)


def take_or_start(
    panel: object,
    wants: Wants,
    binary: str,
    prompt_tool: str = "",
    idle_sink: Optional[Callable[[], None]] = None,
    popen_kwargs: Optional[dict] = None,
    *,
    late: bool = False,
) -> Optional[ClaudeSession]:
    """The process this turn speaks through, the tab's held one or a new one.

    A held process is reused when it can serve the conversation and accepts
    the turn's model and permission mode. Otherwise it is stopped and a new
    one started with everything on the command line. Raises OSError when the
    binary cannot be started.

    `late` is a turn woken by something the process already said. It takes the
    held process as it stands or nothing at all, and returns None for nothing.
    """
    key = backend_pool.pool_key(BACKEND_CLAUDE, panel)
    shared = backend_pool.pool()
    with _lock_for(panel):
        held = shared.take(key)
        session = cast(Optional[ClaudeSession], held.handle if held is not None else None)
        if late:
            # The events this turn was woken for are in that process. Stopping
            # it to apply a model change, or starting a fresh one, would throw
            # them away and leave the turn reading a stream with nothing to
            # say on it. Its idle sink is already registered: that is how the
            # wake-up reached the tab.
            return session
        if session is not None and not (session.can_serve(wants) and session.adopt(wants)):
            shared.drop(key)
            session = None
        if session is None:
            session = ClaudeSession.start(binary, wants, prompt_tool, popen_kwargs)
            held = backend_pool.HeldProcess(session, claude_adapter())
            session.on_event = held.touch
            shared.keep(key, held)
        if idle_sink is not None:
            session.set_idle_sink(idle_sink)
        return session
