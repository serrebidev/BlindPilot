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
import queue
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Optional

import backend_pool  # noqa: F401 - wired up by the pool adapter task
from agent_backends import (  # noqa: F401 - BACKEND_CLAUDE is wired up by a later task
    BACKEND_CLAUDE,
    end_process_group,
    own_group_kwargs,
    subprocess_env,
)

_log = logging.getLogger("blindpilot.claude")

# Swapped by tests for a fake process.
_popen = subprocess.Popen

# How long Stop waits for the CLI to confirm an interrupt before the process
# is stopped instead. The same budget Codex gives its interrupt.
_INTERRUPT_SECONDS = 5.0
# How long a model or permission mode change waits for the CLI's answer.
_CONTROL_SECONDS = 10.0
# stderr lines kept. The tail is the part that says how a process ended.
_STDERR_KEEP = 4000
_STDERR_TRIM = 2000

# Put on a turn's queue when the process ends, so a reader blocked on the
# queue learns of the death instead of waiting for a result that never comes.
EOF = None


@dataclass(frozen=True)
class Wants:
    """What a turn needs the process to have been started with."""

    cwd: str
    permission_mode: str
    model: str = ""
    effort: str = ""
    session_id: Optional[str] = None


def build_command(binary: str, wants: Wants, prompt_tool: str) -> list[str]:
    """The command line one process is started with.

    Streaming input mode keeps stdin open, so further messages can be pushed
    into the process while it works and after a turn ends. The prompt tool
    makes AskUserQuestion arrive as a control request on this same stream.
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
    if prompt_tool:
        cmd.extend(["--permission-prompt-tool", prompt_tool])
    if wants.permission_mode:
        cmd.extend(["--permission-mode", wants.permission_mode])
    if wants.model:
        cmd.extend(["--model", wants.model])
    if wants.effort:
        cmd.extend(["--effort", wants.effort])
    if wants.session_id:
        cmd.extend(["--resume", wants.session_id])
    return cmd


class ClaudeSession:
    """One live process and the two threads that belong to it, not to a turn."""

    def __init__(self, proc: subprocess.Popen, wants: Wants) -> None:
        self._proc = proc
        self.wants = wants
        self.session_id: Optional[str] = wants.session_id
        self._write_lock = threading.Lock()
        self._state = threading.Lock()
        self._sink: Optional[queue.Queue] = None
        self._pending: list = []
        self._idle_sink: Optional[Callable[[], None]] = None
        self._idle_told = False
        self._waiting: dict[str, tuple[threading.Event, dict]] = {}
        self._stderr: list[str] = []
        self._stderr_dropped = 0
        self._stopped = False
        self._ended = threading.Event()
        # Called on every event the CLI sends, so the pool's idle clock
        # measures silence from the CLI rather than time since the last prompt.
        self.on_event: Optional[Callable[[], None]] = None
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
            env=subprocess_env(binary),
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
            tell()

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
                    if event.get("type") == "system" and event.get("subtype") == "init":
                        sid = event.get("session_id")
                        if sid:
                            self.session_id = sid
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

    def _touch(self) -> None:
        callback = self.on_event
        if callback is not None:
            callback()

    # ----- writing -----
    def write_json(self, payload: dict) -> bool:
        """Write one JSON line to the process. False if it could not be."""
        stdin = self._proc.stdin
        if stdin is None or self._stopped:
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
        return not self._stopped and self._proc.poll() is None

    def returncode(self) -> Optional[int]:
        return self._proc.poll()

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
