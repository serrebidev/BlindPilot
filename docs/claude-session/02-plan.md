# Held Claude Code Process Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep one Claude Code process per tab alive between turns, so agents a turn started in the background or resumed with SendMessage outlive the turn, and their follow-up answers arrive as late turns.

**Architecture:** A new module `claude_session.py` owns the process, its stdout reader and stderr drainer, and registers it in `backend_pool` under a per-panel key. `ClaudeWorker` stays the per-turn thread but borrows the session, writes its message and reads events from a queue until its result, then detaches without closing stdin. Events with no turn attached are handed to the panel, which starts a prompt-less worker to receive them.

**Tech Stack:** Python 3.13, wxPython 4.3, `subprocess`, `threading`, `queue`, pytest, ruff, mypy. Claude Code CLI 2.1.258 in `-p --input-format stream-json --output-format stream-json` mode with `control_request` subtypes `interrupt`, `set_model`, `set_permission_mode`.

**Spec:** `docs/claude-session/01-design.md`

## Global Constraints

- Branch `feat/held-claude-session`, from `main` at v0.21.6. Never commit with `--no-gpg-sign`; 1Password signs.
- Nothing a screen reader hears may change and no key may change what it does, apart from the one new announcement when a late turn starts.
- Comments are plain: no em dashes, no puffery, no colons as mid-sentence connectors.
- No process is shut down at the end of a turn. Only `HeldProcess.stop` (through the pool's drop, drop_all and reap) ends a Claude process.
- `ClaudeWorker`'s constructor keeps its positional and keyword parameters; new ones are keyword-only with defaults, because `worker_class` builds every backend's worker the same way.
- Python is `C:/Python313/python.exe`. Do not run the full pytest suite inside a task; run the named files. The full suite, `ruff check .`, `ruff format --check .`, `mypy`, and `python blind_pilot.py --startup-gui-smoke` run once in Task 8.
- Tests must leave `backend_pool.pool()` empty: every test that reaches the pool calls `backend_pool.pool().drop_all()` in a `finally` or fixture.

## File Structure

- Create `claude_session.py`: `Wants`, `build_command`, `ClaudeSession`, `claude_adapter`, `take_or_start`, `EOF`. Knows processes and the pool. Imports nothing from `blindpilot_app`.
- Create `tests/test_claude_session.py`: the session on a fake process.
- Modify `blindpilot_app.py`: `ClaudeWorker` (about lines 3082 to 3800) borrows a session; `SessionPanel` gains `_claude_worker_extra`, `_launch_turn`, `_start_late_turn`, a `late_turn` mailbox event and the `_late_turn_waiting` flag.
- Modify `tests/test_claude_stream_resilience.py`: `_drive` patches `claude_session._popen`; tests that asserted stdin closing or a shutdown wait change as listed in Task 6.
- Modify `tests/test_pool_contract.py`: the contract runs against `claude_adapter`.
- Create `tests/test_late_turns.py`: the panel's late-turn path on a stub.
- Modify `mypy.ini`: add `claude_session.py`.
- Create `docs/claude-session/applied.md`: what changed, tests, what was checked live.

---

### Task 1: The session, its reader, and who hears its events

**Files:**
- Create: `claude_session.py`
- Test: `tests/test_claude_session.py`

**Interfaces:**
- Produces: `Wants(cwd, permission_mode, model="", effort="", session_id=None)` frozen dataclass; `build_command(binary, wants, prompt_tool) -> list[str]`; `EOF` sentinel (`None`); `ClaudeSession(proc, wants)` with `session_id`, `wants`, `attach() -> queue.Queue`, `detach()`, `set_idle_sink(callback)`, `busy()`, `alive()`, `returncode()`, `stop()`, `write_json(payload) -> bool`, `send_user(text) -> bool`, `stderr_mark() -> int`, `stderr_since(mark) -> str`, `wait_stderr(timeout)`, `on_event: Optional[Callable[[], None]]`; `ClaudeSession.start(binary, wants, prompt_tool, popen_kwargs=None)`; module attribute `_popen` tests swap.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_claude_session.py`:

```python
"""One Claude Code process kept between turns: who hears what it says."""

from __future__ import annotations

import io
import json
import queue
import threading
import time

import claude_session as cs


class _FeedStdout:
    """A stdout the test writes to while the reader runs."""

    def __init__(self):
        self._lines: queue.Queue = queue.Queue()

    def feed(self, event):
        self._lines.put(json.dumps(event) + "\n")

    def feed_raw(self, text):
        self._lines.put(text)

    def close(self):
        self._lines.put(None)

    def __iter__(self):
        return self

    def __next__(self):
        line = self._lines.get()
        if line is None:
            raise StopIteration
        return line


class _Stdin:
    def __init__(self):
        self.written: list[str] = []
        self.closed = False

    def write(self, data):
        if self.closed:
            raise ValueError("closed")
        self.written.append(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True

    def payloads(self):
        return [json.loads(line) for line in self.written]


class _Proc:
    def __init__(self, stdout=None, stderr=None):
        self.stdin = _Stdin()
        self.stdout = stdout if stdout is not None else _FeedStdout()
        self.stderr = stderr if stderr is not None else io.StringIO("")
        self.returncode = None
        self.pid = 4242

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = 1
        self.stdout.close()

    def terminate(self):
        self.kill()


def _settle(predicate, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


WANTS = cs.Wants(cwd="C:/work", permission_mode="default")


def test_the_command_line_is_the_one_a_turn_used_to_build():
    wants = cs.Wants(
        cwd="C:/work", permission_mode="plan", model="opus", effort="high", session_id="s1"
    )
    assert cs.build_command("claude", wants, "stdio") == [
        "claude",
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-prompt-tool",
        "stdio",
        "--permission-mode",
        "plan",
        "--model",
        "opus",
        "--effort",
        "high",
        "--resume",
        "s1",
    ]
    bare = cs.build_command("claude", cs.Wants(cwd="C:/work", permission_mode=""), "")
    assert "--permission-mode" not in bare and "--resume" not in bare


def test_an_attached_turn_receives_events_in_order_and_the_result_closes_nothing():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    events = session.attach()
    proc.stdout.feed({"type": "assistant", "n": 1})
    proc.stdout.feed({"type": "result"})
    assert events.get(timeout=1) == {"type": "assistant", "n": 1}
    assert events.get(timeout=1) == {"type": "result"}
    session.detach()
    assert not proc.stdin.closed, "the result ended the turn, not the process"
    assert session.alive()


def test_the_init_event_names_the_session():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    events = session.attach()
    proc.stdout.feed({"type": "system", "subtype": "init", "session_id": "abc"})
    events.get(timeout=1)
    assert session.session_id == "abc"


def test_events_with_no_turn_attached_wake_the_idle_sink_once_and_wait_for_the_next_turn():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    woken = []
    session.set_idle_sink(lambda: woken.append(1))
    proc.stdout.feed({"type": "assistant", "n": 1})
    proc.stdout.feed({"type": "assistant", "n": 2})
    assert _settle(lambda: len(woken) == 1)
    time.sleep(0.05)
    assert woken == [1], "the sink was told once, not once per event"
    events = session.attach()
    assert events.get(timeout=1)["n"] == 1
    assert events.get(timeout=1)["n"] == 2
    session.detach()
    proc.stdout.feed({"type": "assistant", "n": 3})
    assert _settle(lambda: len(woken) == 2), "a later idle event wakes the sink again"


def test_a_sink_set_after_events_arrived_is_woken_at_once():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    proc.stdout.feed({"type": "assistant"})
    assert _settle(lambda: session._pending)
    woken = []
    session.set_idle_sink(lambda: woken.append(1))
    assert woken == [1]


def test_only_one_turn_can_be_attached():
    session = cs.ClaudeSession(_Proc(), WANTS)
    session.attach()
    try:
        session.attach()
    except RuntimeError:
        pass
    else:
        raise AssertionError("two turns read the same stream")
    assert session.busy()
    session.detach()
    assert not session.busy()


def test_a_process_that_ends_hands_eof_to_the_turn_and_not_to_the_idle_sink():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    events = session.attach()
    proc.returncode = 1
    proc.stdout.close()
    assert events.get(timeout=1) is cs.EOF
    session.detach()
    woken = []
    session.set_idle_sink(lambda: woken.append(1))
    time.sleep(0.05)
    assert woken == [], "a dead process is the pool's business, not a late turn"
    assert not session.alive()
    assert session.returncode() == 1


def test_a_turn_attaching_after_the_end_learns_of_it():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    proc.returncode = 0
    proc.stdout.close()
    assert _settle(lambda: session._ended.is_set())
    assert session.attach().get(timeout=1) is cs.EOF


def test_a_malformed_line_is_skipped_not_fatal():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    events = session.attach()
    proc.stdout.feed_raw("not json\n")
    proc.stdout.feed({"type": "result"})
    assert events.get(timeout=1) == {"type": "result"}


def test_user_messages_are_written_as_the_cli_expects():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    assert session.send_user("hello")
    assert proc.stdin.payloads() == [
        {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        }
    ]
    session.stop()
    assert not session.send_user("again"), "a stopped session refuses writes"


def test_stderr_is_drained_and_a_turn_reads_only_its_own_lines():
    stderr = _FeedStdout()
    proc = _Proc(stderr=stderr)
    session = cs.ClaudeSession(proc, WANTS)
    stderr.feed_raw("old news\n")
    assert _settle(lambda: session.stderr_since(0) == "old news")
    mark = session.stderr_mark()
    stderr.feed_raw("this turn's complaint\n")
    assert _settle(lambda: "complaint" in session.stderr_since(mark))
    assert "old news" not in session.stderr_since(mark)


def test_every_event_touches_the_clock():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    touched = []
    session.on_event = lambda: touched.append(1)
    session.attach()
    proc.stdout.feed({"type": "assistant"})
    assert _settle(lambda: touched == [1])


def test_stop_ends_the_process_group_once(monkeypatch):
    ended = []
    monkeypatch.setattr(cs, "end_process_group", lambda proc, timeout=0.0: ended.append(proc))
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    session.stop()
    session.stop()
    assert ended == [proc]
    assert proc.stdin.closed
    assert not session.alive()


def test_start_runs_the_command_in_the_working_directory(monkeypatch):
    seen = {}

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setattr(cs, "_popen", fake_popen)
    monkeypatch.setattr(cs, "subprocess_env", lambda binary: {"PATH": "x"})
    session = cs.ClaudeSession.start("claude", WANTS, "stdio", {"creationflags": 7})
    assert seen["cmd"][0] == "claude" and "--permission-prompt-tool" in seen["cmd"]
    assert seen["kwargs"]["cwd"] == "C:/work"
    assert seen["kwargs"]["env"] == {"PATH": "x"}
    assert seen["kwargs"]["creationflags"] == 7
    assert seen["kwargs"]["errors"] == "replace"
    assert session.alive()
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `C:/Python313/python.exe -m pytest tests/test_claude_session.py -q -p no:randomly`
Expected: FAIL with `ModuleNotFoundError: No module named 'claude_session'`

- [ ] **Step 3: Write the module**

Create `claude_session.py`:

```python
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
import uuid
from dataclasses import dataclass, replace
from typing import Callable, Optional, cast

import backend_pool
from agent_backends import BACKEND_CLAUDE, end_process_group, own_group_kwargs, subprocess_env

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
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `C:/Python313/python.exe -m pytest tests/test_claude_session.py -q -p no:randomly`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add claude_session.py tests/test_claude_session.py
git commit -m "Give each tab one Claude Code process that outlives its turns"
```

---

### Task 2: Control requests: interrupt, model and permission mode

**Files:**
- Modify: `claude_session.py`
- Test: `tests/test_claude_session.py`

**Interfaces:**
- Consumes: `ClaudeSession.write_json`, `_settle`, `_waiting`, `wants`.
- Produces: `send_control(subtype, timeout=_CONTROL_SECONDS, **fields) -> Optional[dict]`, `interrupt(timeout=_INTERRUPT_SECONDS) -> bool`, `set_model(model) -> bool`, `set_permission_mode(mode) -> bool`, `can_serve(wants) -> bool`, `adopt(wants) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_claude_session.py`:

```python
def _answer_controls(proc, subtype="success"):
    """A CLI that answers every control request it is sent, on a thread."""

    def worker():
        seen = 0
        while True:
            time.sleep(0.005)
            payloads = proc.stdin.payloads()
            for payload in payloads[seen:]:
                if payload.get("type") == "control_request":
                    proc.stdout.feed(
                        {
                            "type": "control_response",
                            "response": {"subtype": subtype, "request_id": payload["request_id"]},
                        }
                    )
            seen = len(payloads)
            if proc.returncode is not None:
                return

    threading.Thread(target=worker, daemon=True).start()


def test_an_interrupt_the_cli_confirms_is_true_and_leaves_the_process_alive():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    _answer_controls(proc)
    assert session.interrupt(timeout=2.0)
    sent = [p for p in proc.stdin.payloads() if p["type"] == "control_request"]
    assert sent[0]["request"] == {"subtype": "interrupt"}
    assert session.alive()


def test_an_interrupt_nobody_answers_is_false():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    assert not session.interrupt(timeout=0.1)


def test_an_interrupt_the_cli_refuses_is_false():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    _answer_controls(proc, subtype="error")
    assert not session.interrupt(timeout=2.0)


def test_a_model_change_goes_down_the_stream_and_is_remembered():
    proc = _Proc()
    session = cs.ClaudeSession(proc, cs.Wants(cwd="C:/work", permission_mode="default", model="a"))
    _answer_controls(proc)
    assert session.set_model("b")
    sent = [p for p in proc.stdin.payloads() if p["type"] == "control_request"]
    assert sent[-1]["request"] == {"subtype": "set_model", "model": "b"}
    assert session.wants.model == "b"
    assert session.set_model("b"), "the model it already runs costs no request"
    assert len([p for p in proc.stdin.payloads() if p["type"] == "control_request"]) == 1


def test_a_permission_mode_change_goes_down_the_stream():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    _answer_controls(proc)
    assert session.set_permission_mode("plan")
    sent = [p for p in proc.stdin.payloads() if p["type"] == "control_request"]
    assert sent[-1]["request"] == {"subtype": "set_permission_mode", "mode": "plan"}
    assert session.wants.permission_mode == "plan"


def test_the_default_model_cannot_be_asked_for_by_name():
    proc = _Proc()
    session = cs.ClaudeSession(proc, cs.Wants(cwd="C:/work", permission_mode="default", model="a"))
    _answer_controls(proc)
    assert not session.set_model(""), "there is no request for 'the default', so the process restarts"


def test_can_serve_needs_the_same_directory_effort_and_session():
    proc = _Proc()
    session = cs.ClaudeSession(proc, cs.Wants(cwd="C:/work", permission_mode="default"))
    session.session_id = "s1"
    same = cs.Wants(cwd="C:/work", permission_mode="plan", model="b", session_id="s1")
    assert session.can_serve(same), "model and mode differ but both travel down the stream"
    assert not session.can_serve(replace_wants(same, cwd="D:/other"))
    assert not session.can_serve(replace_wants(same, effort="high"))
    assert not session.can_serve(replace_wants(same, session_id="s2"))
    assert not session.can_serve(replace_wants(same, session_id=None))
    proc.returncode = 0
    assert not session.can_serve(same), "a dead process serves nobody"


def replace_wants(wants, **changes):
    from dataclasses import replace

    return replace(wants, **changes)


def test_adopt_applies_model_then_mode_and_fails_on_a_refusal():
    proc = _Proc()
    session = cs.ClaudeSession(proc, cs.Wants(cwd="C:/work", permission_mode="default", model="a"))
    _answer_controls(proc)
    assert session.adopt(cs.Wants(cwd="C:/work", permission_mode="plan", model="b"))
    assert session.wants.model == "b" and session.wants.permission_mode == "plan"
    refusing = _Proc()
    session2 = cs.ClaudeSession(refusing, cs.Wants(cwd="C:/work", permission_mode="default"))
    _answer_controls(refusing, subtype="error")
    assert not session2.adopt(cs.Wants(cwd="C:/work", permission_mode="plan"))
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `C:/Python313/python.exe -m pytest tests/test_claude_session.py -q -p no:randomly`
Expected: the eight new tests FAIL with `AttributeError` on `interrupt`, `set_model`, `set_permission_mode`, `can_serve`, `adopt`

- [ ] **Step 3: Add the control requests**

Insert into `ClaudeSession`, after `send_user`:

```python
    def send_control(
        self, subtype: str, timeout: float = _CONTROL_SECONDS, **fields: object
    ) -> Optional[dict]:
        """Ask the CLI something and wait for its answer, or None on silence."""
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
        return bool(response) and response.get("subtype") == "success"

    def interrupt(self, timeout: float = _INTERRUPT_SECONDS) -> bool:
        """Whether the CLI confirmed the running turn was stopped."""
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
        if not self.alive():
            return False
        if (self.wants.cwd, self.wants.effort) != (wants.cwd, wants.effort):
            return False
        return wants.session_id is not None and wants.session_id == self.session_id

    def adopt(self, wants: Wants) -> bool:
        return self.set_model(wants.model) and self.set_permission_mode(wants.permission_mode)
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `C:/Python313/python.exe -m pytest tests/test_claude_session.py -q -p no:randomly`
Expected: 22 passed

- [ ] **Step 5: Commit**

```bash
git add claude_session.py tests/test_claude_session.py
git commit -m "Let a held Claude process be interrupted and change model or mode in place"
```

---

### Task 3: The pool adapter and how a turn gets its session

**Files:**
- Modify: `claude_session.py`
- Test: `tests/test_claude_session.py`, `tests/test_pool_contract.py`

**Interfaces:**
- Consumes: `backend_pool.pool()`, `pool_key(BACKEND_CLAUDE, panel)`, `HeldProcess`, `Adapter`.
- Produces: `claude_adapter() -> backend_pool.Adapter`; `take_or_start(panel, wants, binary, prompt_tool="", idle_sink=None, popen_kwargs=None) -> ClaudeSession`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_claude_session.py`:

```python
import backend_pool


def _fresh_pool(monkeypatch):
    pool = backend_pool.BackendPool()
    monkeypatch.setattr(backend_pool, "pool", lambda: pool)
    return pool


def _starting(monkeypatch, procs):
    """Each start takes the next fake process from `procs`."""
    made = []

    def fake_popen(cmd, **kwargs):
        proc = procs.pop(0)
        proc.cmd = cmd
        made.append(proc)
        return proc

    monkeypatch.setattr(cs, "_popen", fake_popen)
    monkeypatch.setattr(cs, "subprocess_env", lambda binary: {})
    return made


def test_the_adapter_reports_the_session_alive_busy_and_stops_it(monkeypatch):
    monkeypatch.setattr(cs, "end_process_group", lambda proc, timeout=0.0: proc.kill())
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    adapter = cs.claude_adapter()
    assert adapter.alive(session)
    assert not adapter.busy(session)
    session.attach()
    assert adapter.busy(session)
    session.detach()
    adapter.stop(session)
    assert not adapter.alive(session)


def test_the_first_turn_starts_a_process_and_keeps_it(monkeypatch):
    pool = _fresh_pool(monkeypatch)
    made = _starting(monkeypatch, [_Proc()])
    panel = object()
    session = cs.take_or_start(panel, WANTS, "claude", "stdio")
    assert len(made) == 1
    assert pool.held_count() == 1
    assert session.on_event is not None, "events touch the pool's idle clock"
    assert "--permission-prompt-tool" in made[0].cmd


def test_the_next_turn_reuses_the_process_when_the_conversation_matches(monkeypatch):
    _fresh_pool(monkeypatch)
    made = _starting(monkeypatch, [_Proc(), _Proc()])
    panel = object()
    first = cs.take_or_start(panel, WANTS, "claude")
    first.session_id = "s1"
    again = cs.take_or_start(panel, replace_wants(WANTS, session_id="s1"), "claude")
    assert again is first
    assert len(made) == 1


def test_a_different_effort_or_directory_or_conversation_starts_a_new_process(monkeypatch):
    _fresh_pool(monkeypatch)
    made = _starting(monkeypatch, [_Proc(), _Proc(), _Proc()])
    monkeypatch.setattr(cs, "end_process_group", lambda proc, timeout=0.0: proc.kill())
    panel = object()
    first = cs.take_or_start(panel, WANTS, "claude")
    first.session_id = "s1"
    second = cs.take_or_start(panel, replace_wants(WANTS, session_id="s1", effort="high"), "claude")
    assert second is not first and not first.alive(), "the old process was stopped, not leaked"
    second.session_id = "s1"
    third = cs.take_or_start(panel, replace_wants(WANTS, session_id="s2", effort="high"), "claude")
    assert third is not second
    assert len(made) == 3


def test_a_model_change_is_sent_down_the_stream_to_the_held_process(monkeypatch):
    _fresh_pool(monkeypatch)
    procs = [_Proc()]
    made = _starting(monkeypatch, list(procs))
    panel = object()
    first = cs.take_or_start(panel, WANTS, "claude")
    first.session_id = "s1"
    _answer_controls(procs[0])
    again = cs.take_or_start(panel, replace_wants(WANTS, session_id="s1", model="b"), "claude")
    assert again is first and first.wants.model == "b"
    assert len(made) == 1


def test_a_model_change_the_cli_does_not_answer_restarts_the_process(monkeypatch):
    _fresh_pool(monkeypatch)
    made = _starting(monkeypatch, [_Proc(), _Proc()])
    monkeypatch.setattr(cs, "end_process_group", lambda proc, timeout=0.0: proc.kill())
    monkeypatch.setattr(cs, "_CONTROL_SECONDS", 0.1)
    panel = object()
    first = cs.take_or_start(panel, WANTS, "claude")
    first.session_id = "s1"
    # Nobody answers the set_model request, so the change cannot be trusted
    # to have landed and the process is replaced with the model on its command line.
    second = cs.take_or_start(panel, replace_wants(WANTS, session_id="s1", model="c"), "claude")
    assert second is not first and not first.alive()
    assert len(made) == 2 and "--model" in made[1].cmd


def test_two_tabs_get_two_processes(monkeypatch):
    _fresh_pool(monkeypatch)
    made = _starting(monkeypatch, [_Proc(), _Proc()])
    one = cs.take_or_start(object(), WANTS, "claude")
    two = cs.take_or_start(object(), WANTS, "claude")
    assert one is not two and len(made) == 2


def test_the_idle_sink_is_registered_on_take(monkeypatch):
    _fresh_pool(monkeypatch)
    _starting(monkeypatch, [_Proc()])
    woken = []
    session = cs.take_or_start(object(), WANTS, "claude", idle_sink=lambda: woken.append(1))
    session._proc.stdout.feed({"type": "assistant"})
    assert _settle(lambda: woken == [1])
```

Append to `tests/test_pool_contract.py`, following the file's existing pattern for Codex (read the file first and copy how it builds a `HeldProcess` for `codex_adapter`):

```python
def test_the_claude_adapter_keeps_the_contract(monkeypatch):
    import claude_session as cs
    from tests.test_claude_session import _Proc

    monkeypatch.setattr(cs, "end_process_group", lambda proc, timeout=0.0: proc.kill())

    def build():
        return backend_pool.HeldProcess(cs.ClaudeSession(_Proc(), cs.Wants("C:/w", "default")), cs.claude_adapter())

    for check in pool_contract.ALL_CHECKS:
        check(build, "claude")
```

If `pool_contract.py` exposes its checks under another name, use the same list the Codex test iterates over; if it has no list, call each `check_*` function in turn.

- [ ] **Step 2: Run the tests to see them fail**

Run: `C:/Python313/python.exe -m pytest tests/test_claude_session.py tests/test_pool_contract.py -q -p no:randomly`
Expected: FAIL with `AttributeError: module 'claude_session' has no attribute 'claude_adapter'`

- [ ] **Step 3: Add the adapter and the taker**

Append to `claude_session.py`:

```python
def claude_adapter() -> backend_pool.Adapter:
    """What the pool needs to know about a Claude process, and nothing more."""
    return backend_pool.Adapter(
        alive=lambda session: cast(ClaudeSession, session).alive(),
        interrupt=lambda session, timeout: cast(ClaudeSession, session).interrupt(timeout),
        stop=lambda session: cast(ClaudeSession, session).stop(),
        busy=lambda session: cast(ClaudeSession, session).busy(),
    )


# Taking and starting happen under one lock so two turns in two tabs cannot
# each decide the other's process is theirs, and a settings change is applied
# to a process before anyone else takes it.
_START_LOCK = threading.Lock()


def take_or_start(
    panel: object,
    wants: Wants,
    binary: str,
    prompt_tool: str = "",
    idle_sink: Optional[Callable[[], None]] = None,
    popen_kwargs: Optional[dict] = None,
) -> ClaudeSession:
    """The process this turn speaks through: the tab's held one, or a new one.

    A held process is reused when it can serve the conversation and accepts
    the turn's model and permission mode. Otherwise it is stopped and a new
    one started with everything on the command line. Raises OSError when the
    binary cannot be started.
    """
    key = backend_pool.pool_key(BACKEND_CLAUDE, panel)
    shared = backend_pool.pool()
    with _START_LOCK:
        held = shared.take(key)
        session = cast(Optional[ClaudeSession], held.handle if held is not None else None)
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
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `C:/Python313/python.exe -m pytest tests/test_claude_session.py tests/test_pool_contract.py tests/test_backend_pool.py -q -p no:randomly`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add claude_session.py tests/test_claude_session.py tests/test_pool_contract.py
git commit -m "Hold the Claude process in the pool and hand each turn its tab's own"
```

---

### Task 4: The worker borrows the session instead of owning a process

**Files:**
- Modify: `blindpilot_app.py` (`ClaudeWorker`, about lines 3082 to 3800: `__init__`, `_write_json`, `steer`, `cancel`, `_do_run`; delete `_close_stdin`, `_wait_for_shutdown`, `_reap_in_background`, `_reap`, `_drain_stderr`, `_stderr_text`, `_background_agents_running`; keep `_ending_note`, `_count`, `_log_unfinished_turn`, `_handle_control_request`, `_answer_ask_user_question`, `_retry_without_prompt_tool`)
- Test: `tests/test_claude_stream_resilience.py` (the `_drive` helper only; the tests themselves change in Task 6)

**Interfaces:**
- Consumes: `claude_session.Wants`, `take_or_start`, `ClaudeSession.attach/detach/send_user/write_json/stderr_mark/stderr_since/wait_stderr/returncode/interrupt`, `claude_session.EOF`, `claude_session._INTERRUPT_SECONDS`.
- Produces: `ClaudeWorker(prompt: Optional[str], session_id, cwd, permission_mode, *, model="", effort="", on_session, on_started, on_activity, on_complete, on_failed, on_done, on_question=None, held_for: object = None, on_unsolicited: Optional[Callable[[], None]] = None)`. A worker with `prompt=None` sends nothing and reads the pending events.

- [ ] **Step 1: Point the test driver at the session's process factory**

In `tests/test_claude_stream_resilience.py`, `_drive`, replace the two lines that swap `subprocess.Popen` and the two that restore it with:

```python
    import claude_session

    real_popen, real_find = claude_session._popen, blindpilot_app._find_claude
    claude_session._popen = lambda *_a, **_k: proc  # type: ignore[assignment]
    blindpilot_app._find_claude = lambda: "claude"  # type: ignore[assignment]
```

and, in the restore block:

```python
    claude_session._popen = real_popen  # type: ignore[assignment]
    blindpilot_app._find_claude = real_find  # type: ignore[assignment]
    import backend_pool

    backend_pool.pool().drop_all()
```

Also give `_FakeProc` a `poll` that reports alive until the stream ends: replace its `poll` with

```python
    def poll(self):
        return self.returncode if self.stdout_done else None
```

and set `self.stdout_done = False` in `__init__`, wrapping the iterator so it flips: in `__init__`, replace `self.stdout = stdout_iter` with

```python
        self.stdout = self._watch(stdout_iter)

    def _watch(self, it):
        try:
            for line in it:
                yield line
        finally:
            self.stdout_done = True
```

`kill` sets `self.killed = True` and `self.stdout_done = True` and `self.returncode = 1` when it was `None`.

- [ ] **Step 2: Run the file to see what breaks**

Run: `C:/Python313/python.exe -m pytest tests/test_claude_stream_resilience.py -q -p no:randomly`
Expected: every test FAILS or hangs at the 10 second `_drive` timeout, because the worker still calls `subprocess.Popen` itself. This is the RED for the worker change.

- [ ] **Step 3: Rewrite the worker on the session**

In `blindpilot_app.py`, add `import claude_session` with the other project imports (near `import backend_pool`).

`ClaudeWorker.__init__`: change the first parameter to `prompt: Optional[str]`, add keyword-only parameters after `on_question`:

```python
        held_for: object = None,
        on_unsolicited: Optional[Callable[[], None]] = None,
```

store them as `self._held_for` and `self._on_unsolicited`, and add `self._session: Optional[claude_session.ClaudeSession] = None`. Remove `self._proc`, `self._stderr_lines`, `self._stderr_thread`. Keep `_cancelled`, `_stopped_by_us`, `_accepting_input`, `_failed`. Remove `self._write_lock` (the session has one).

Replace `_write_json`:

```python
    def _write_json(self, payload: dict) -> bool:
        session = self._session
        return session is not None and session.write_json(payload)
```

Delete `_close_stdin`, `_wait_for_shutdown`, `_reap_in_background`, `_reap`, `_drain_stderr`, `_stderr_text`, `_background_agents_running`, and the module constants `_SHUTDOWN_QUIET_SECONDS` and `_REAP_SECONDS` with their comments (grep for other uses first; `_ending_note` mentions `_SHUTDOWN_QUIET_SECONDS`, see below).

Replace `_ending_note`'s stopped-by-us branch with:

```python
        if self._stopped_by_us:
            return (
                "BlindPilot stopped Claude Code: it did not confirm the stop within "
                f"{int(claude_session._INTERRUPT_SECONDS)} seconds. "
                "Whatever the turn had already produced is kept."
            )
```

Replace `cancel`:

```python
    def cancel(self) -> None:
        """Stop the turn, and only the turn when the CLI lets us.

        An interrupt the CLI confirms ends this turn with a result and leaves
        the process, and every agent it holds, running. One it does not
        confirm means the process cannot be trusted with the next turn, so
        it is dropped from the pool, which stops it.
        """
        self._accepting_input.clear()
        self._cancelled = True
        session = self._session
        if session is None:
            return
        if not session.interrupt(claude_session._INTERRUPT_SECONDS):
            self._stopped_by_us = True
            self._drop_process()

    def _drop_process(self) -> None:
        backend_pool.pool().drop(backend_pool.pool_key(BACKEND_CLAUDE, self._held_for))
```

Replace `steer`'s body after the `accepting_input` check with `return self._session is not None and self._session.send_user(text)`; delete `_write_message` and use `session.send_user` directly.

Replace `_do_run` from its start through the end of the event loop's aftermath with:

```python
    def _do_run(self) -> None:
        binary = _find_claude()
        if binary is None:
            self._on_failed("Claude Code not installed. Install from claude.com/claude-code")
            return

        wants = claude_session.Wants(
            cwd=self._cwd,
            permission_mode=self._permission_mode,
            model=self._model,
            effort=self._effort,
            session_id=self._session_id,
        )
        try:
            session = claude_session.take_or_start(
                self._held_for,
                wants,
                binary,
                _CLAUDE_PERMISSION_PROMPT_TOOL,
                idle_sink=self._on_unsolicited,
                popen_kwargs=_no_window_kwargs(),
            )
        except OSError as exc:
            self._fail(f"Failed to launch Claude Code: {exc}")
            return
        self._session = session
        events = session.attach()
        mark = session.stderr_mark()
        try:
            self._read_turn(session, events, mark)
        finally:
            session.detach()
            self._accepting_input.clear()

    def _read_turn(
        self, session: claude_session.ClaudeSession, events: "queue.Queue", mark: int
    ) -> None:
        if self._prompt is not None and not session.send_user(self._prompt):
            self._fail("Could not send the prompt to Claude Code")
            return
        self._accepting_input.set()

        text_parts: list[str] = []
        first_assistant_seen = False
        complete = False
        died = False

        while True:
            try:
                # After Stop, the CLI's result for the interrupted turn is due
                # at once; a process that keeps it is not trusted with the next.
                event = events.get(timeout=_CANCEL_DRAIN_SECONDS if self._cancelled else None)
            except queue.Empty:
                self._stopped_by_us = True
                self._drop_process()
                return
            if event is claude_session.EOF:
                died = True
                break
            if self._cancelled:
                # Read to the result so it is not left for a late turn to find.
                if event.get("type") == "result":
                    return
                continue

            etype = event.get("type")
            ... the existing control_request, system/init, assistant and user
            branches, unchanged ...

            elif etype == "result":
                complete = True
                queued = self._count(event.get("queued_turn_count"))
                if not event.get("is_error") and queued:
                    # A resumed CLI can have a turn of its own to run first.
                    # That turn's result arrives before ours and says nothing.
                    logging.getLogger("blindpilot.claude").info(
                        "result for another turn: %d still queued, reading on", queued
                    )
                    continue
                if event.get("is_error"):
                    detail = (event.get("result") or "").strip()
                    if _looks_like_auth_error(detail):
                        self._fail(AUTH_HINT)
                        return
                    note = detail or "Claude Code returned an error"
                    if text_parts:
                        # The turn answered before it ended badly. Saying how it
                        # ended instead of the answer threw away work.
                        self._on_activity("notice", note)
                        break
                    self._fail(note)
                    return
                # The turn is over. The process is not: it belongs to the tab,
                # and whatever it left running keeps running.
                break

        if self._cancelled:
            return

        if died:
            session.wait_stderr()
            rc = session.returncode()
            stderr_text = session.stderr_since(mark)
            if _looks_like_auth_error(stderr_text):
                self._fail(AUTH_HINT)
                return
            if self._retry_without_prompt_tool(stderr_text):
                # The installed Claude Code is older than the flag. Turn it off
                # for the rest of the session and send the message again.
                self._do_run()
                return
            self._log_unfinished_turn(rc, complete, stderr_text)
            detail = f": {stderr_text}" if stderr_text else ""
            if not detail:
                detail = (
                    " without finishing the turn, and without saying why. "
                    f"BlindPilot kept a note of it in {self._diagnostic_path()}."
                )
            note = self._ending_note(rc, detail)
            if not text_parts:
                self._fail(note)
                return
            self._on_activity("notice", note)
            self._on_complete("\n\n".join(text_parts).strip())
            return

        if not text_parts:
            self._on_activity("notice", "Claude Code finished the turn without saying anything.")
        self._on_complete("\n\n".join(text_parts).strip())
```

Add the constant next to the other worker constants: `_CANCEL_DRAIN_SECONDS = 5.0`, with a comment saying it is how long a stopped turn waits for the CLI's result before the process is dropped. `import queue` joins the standard imports. The `run()` wrapper that reports an exception from `_do_run` stays as it is, minus any mention of closing stdin in its comment.

`_retry_without_prompt_tool` sets the module global and returns True; `_do_run` then takes the pool again, finds the dead process (the pool discards it) and starts a new one without the flag. Check the method reads nothing from `self._proc`; if it does, give it the stderr text it already receives.

- [ ] **Step 4: Run the driver's simplest tests to see the worker turn again**

Run: `C:/Python313/python.exe -m pytest tests/test_claude_stream_resilience.py -q -p no:randomly -k "subagent_narration or queued_turn"`
Expected: 2 passed. Then `C:/Python313/python.exe -m ruff check blindpilot_app.py && C:/Python313/python.exe -m mypy` clean, and `C:/Python313/python.exe blind_pilot.py --startup-gui-smoke` exits 0. Other tests in the file fail until Task 6; do not fix them here.

- [ ] **Step 5: Commit**

```bash
git add blindpilot_app.py tests/test_claude_stream_resilience.py
git commit -m "Let a Claude turn borrow the tab's process instead of owning one"
```

---

### Task 5: Late turns: the panel receives what arrives with no turn running

**Files:**
- Modify: `blindpilot_app.py` (`SessionPanel.__init__` near line 5294; `_drain_worker_events` near 6099; `_on_send` near 6280, the block from `worker_type = worker_class(...)` to `self.stop_btn.Enable()`; `_on_worker_finished` near 6952)
- Create: `tests/test_late_turns.py`

**Interfaces:**
- Consumes: `ClaudeWorker(..., held_for=, on_unsolicited=)`, `_queue_worker_event`.
- Produces: `SessionPanel._claude_worker_extra() -> dict`, `SessionPanel._launch_turn(send_text: Optional[str], selected_backend: str, extra: dict) -> None`, `SessionPanel._start_late_turn() -> None`, `SessionPanel._late_turn_waiting: bool`, mailbox event name `"late_turn"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_late_turns.py`:

```python
"""What the panel does when the CLI speaks with no turn of ours running."""

from __future__ import annotations

import blindpilot_app as app
from agent_backends import BACKEND_CLAUDE


class _Btn:
    def __init__(self):
        self.enabled = True

    def Enable(self):
        self.enabled = True

    def Disable(self):
        self.enabled = False

    def __bool__(self):
        return True


class _Earcons:
    def __init__(self):
        self.calls: list[str] = []

    def start_progress(self):
        self.calls.append("start")

    def stop_progress(self):
        self.calls.append("stop")

    def play_send(self):
        self.calls.append("send")


def _panel(worker=None):
    launched: list[tuple] = []

    class _Panel:
        pass

    panel = _Panel()
    panel._worker = worker
    panel._late_turn_waiting = False
    panel._earcons = _Earcons()
    panel.send_btn, panel.steer_btn, panel.stop_btn = _Btn(), _Btn(), _Btn()
    panel._stopping = False
    panel._replaying = False
    panel._assistant_narrated_this_turn = True
    panel._streamed_assistant = "left over"
    panel._turns = []
    panel.announced: list[str] = []
    panel._announce = lambda text, urgent=False: panel.announced.append(text)
    panel._show_working = lambda: panel._earcons.calls.append("indicator")
    panel._run_in_progress = lambda: app.SessionPanel._run_in_progress(panel)
    panel._claude_worker_extra = lambda: app.SessionPanel._claude_worker_extra(panel)
    panel._launch_turn = lambda send_text, backend, extra: launched.append((send_text, backend, extra))
    panel._queue_worker_event = lambda name, *args: panel.queued.append((name, args))
    panel.queued: list[tuple] = []
    panel._finish_stopped_turn = lambda: None
    return panel, launched


def test_the_claude_worker_is_told_which_tab_holds_it_and_how_to_wake_it():
    panel, _launched = _panel()
    extra = app.SessionPanel._claude_worker_extra(panel)
    assert extra["held_for"] is panel
    extra["on_unsolicited"]()
    assert panel.queued == [("late_turn", ())], "the wake-up goes through the mailbox, onto the GUI thread"


def test_a_late_turn_starts_a_prompt_less_claude_turn_with_the_earcon_running():
    panel, launched = _panel()
    app.SessionPanel._start_late_turn(panel)
    assert launched and launched[0][0] is None
    assert launched[0][1] == BACKEND_CLAUDE
    assert launched[0][2]["held_for"] is panel
    assert "start" in panel._earcons.calls and "indicator" in panel._earcons.calls
    assert panel._streamed_assistant == "" and panel._assistant_narrated_this_turn is False
    assert panel._turns and panel._turns[-1].prompt == "", "the answer needs a turn to land in"
    assert any("background" in text.lower() for text in panel.announced)


def test_a_late_turn_waits_while_a_turn_is_still_finishing_and_starts_after_it():
    class _Worker:
        pass

    panel, launched = _panel(worker=_Worker())
    app.SessionPanel._start_late_turn(panel)
    assert not launched and panel._late_turn_waiting
    app.SessionPanel._on_worker_finished(panel)
    assert panel._worker is None
    assert launched and launched[0][0] is None
    assert not panel._late_turn_waiting


def test_the_mailbox_routes_the_wake_up_to_the_late_turn():
    import inspect

    source = inspect.getsource(app.SessionPanel._drain_worker_events)
    assert '"late_turn"' in source and "_start_late_turn" in source
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `C:/Python313/python.exe -m pytest tests/test_late_turns.py -q -p no:randomly`
Expected: 4 FAIL with `AttributeError` on `_claude_worker_extra` and `_start_late_turn`

- [ ] **Step 3: Wire the panel**

In `SessionPanel.__init__`, next to `self._session_id: Optional[str] = None`, add:

```python
        # Set when the CLI spoke with no turn running while the last turn's
        # `done` was still in the mailbox; the late turn starts once it drains.
        self._late_turn_waiting = False
```

In `_on_send`, extract the block from `worker_type = worker_class(selected_backend, ClaudeWorker)` through `self.stop_btn.Enable()` into a method, and have `_on_send` end with:

```python
        extra = dict(worker_extra or {})
        if selected_backend == BACKEND_HERMES:
            extra.update(self._hermes_worker_extra(outgoing_files))
        elif selected_backend == BACKEND_CLAUDE:
            extra.update(self._claude_worker_extra())
        self._launch_turn(send_text, selected_backend, extra)
```

with the method:

```python
    def _launch_turn(self, send_text: Optional[str], selected_backend: str, extra: dict) -> None:
        """Start the worker for one turn. `send_text` is None for a late turn,
        which has nothing to send and only reads what has already arrived."""
        worker_type = worker_class(selected_backend, ClaudeWorker)
        self._worker = worker_type(
            send_text,
            ... the existing arguments, unchanged ...
            **extra,
        )
        try:
            self._worker.start()
        except RuntimeError as exc:
            ... the existing handling, unchanged ...
            return
        self.steer_btn.Enable()
        self.stop_btn.Enable()

    def _claude_worker_extra(self) -> dict:
        """What a Claude turn needs beyond the message: whose process it borrows,
        and how to wake this tab when the CLI speaks with no turn running."""
        return {
            "held_for": self,
            "on_unsolicited": lambda: self._queue_worker_event("late_turn"),
        }

    def _start_late_turn(self) -> None:
        """Receive what the CLI says with no turn of ours running.

        An agent this tab started in the background, or resumed, has finished,
        and Claude is answering what it found. It is a turn like any other
        except that nobody typed anything, so there is no "You:" row.
        """
        if not self:
            return
        if self._run_in_progress():
            self._late_turn_waiting = True
            return
        self._late_turn_waiting = False
        self._assistant_narrated_this_turn = False
        self._streamed_assistant = ""
        self._stopping = False
        self._turns.append(Turn(prompt=""))
        self._announce("A background agent has reported. Receiving response")
        self._earcons.start_progress()
        self._show_working()
        self._launch_turn(None, BACKEND_CLAUDE, self._claude_worker_extra())
```

In `_drain_worker_events`, after the `elif name == "done":` branch add:

```python
                elif name == "late_turn":
                    self._start_late_turn()
```

At the end of `_on_worker_finished`, after `self._replaying = False`, add:

```python
        if self._late_turn_waiting:
            self._start_late_turn()
```

If `_on_worker_finished` is reached on stubs without the flag (existing tests), use `getattr(self, "_late_turn_waiting", False)`.

- [ ] **Step 4: Run the tests to see them pass**

Run: `C:/Python313/python.exe -m pytest tests/test_late_turns.py tests/test_live_rows.py tests/test_send_race.py tests/test_cancel_off_the_gui_thread.py tests/test_worker_contract.py tests/test_hermes_worker_feedback.py -q -p no:randomly`
Expected: all pass. `C:/Python313/python.exe blind_pilot.py --startup-gui-smoke` exits 0.

- [ ] **Step 5: Commit**

```bash
git add blindpilot_app.py tests/test_late_turns.py
git commit -m "Receive what a background agent reports after the turn as a turn of its own"
```

---

### Task 6: The resilience tests say what the held process changed

**Files:**
- Modify: `tests/test_claude_stream_resilience.py`

**Interfaces:**
- Consumes: the `_drive` helper from Task 4.

- [ ] **Step 1: Change each test to the new contract**

Go through the file's tests in order and make exactly these changes:

1. `test_a_chatty_child_never_fills_the_stderr_pipe`: unchanged; it must still pass (stderr is drained by the session).
2. `test_a_decode_error_on_stdout_is_reported_rather_than_raised`: unchanged in intent; the session's reader catches the error and hands EOF to the turn, which reports a failure. Keep the assertions.
3. `test_a_crash_while_showing_a_row_is_reported_rather_than_closing_stdin`: keep the assertions and add `assert not proc.stdin.closed`.
4. `test_subagent_narration_stays_out_of_the_final_answer`: unchanged.
5. `test_a_nonzero_exit_still_reports_what_stderr_said`: unchanged.
6. `test_a_run_is_not_ended_while_background_agents_are_still_working`: rewrite as `test_a_turn_ends_at_its_result_and_the_process_stays_for_the_agents`. Feed `ANSWER` then the result with `started_in_background: 2`, then stop. Assert `finished`, `completed == ["the answer"]`, `not proc.stdin.closed`, `not proc.killed`. Update the docstring: the agents' reports now arrive as late turns, so the turn ends at its own result and the process is what stays.
7. `test_an_answer_survives_a_nonzero_exit`: the process now ends only if it dies. Feed `ANSWER` then close stdout with `returncode=1` and no result. Assert `completed == ["the answer"]`, a notice containing `"exited with code 1"`, and `not failures`.
8. `test_a_result_without_agent_counts_does_not_end_a_run_still_working`: delete. The behaviour it guarded (do not stop the process on a result without counts) is now unconditional and covered by test 6.
9. `test_a_result_for_a_queued_turn_does_not_end_the_run`: unchanged.
10. `test_a_completed_turn_with_no_answer_is_not_waited_on_and_killed`: rename to `test_a_completed_turn_with_no_answer_says_so_and_is_not_killed`, drop the `monkeypatch` of `_SHUTDOWN_QUIET_SECONDS` and the `_LingeringProc` class, use `_FakeProc(iter([_line(RESULT)]))`, keep the other assertions.
11. `test_an_error_result_arriving_after_an_answer_keeps_the_answer`: feed `ANSWER` then the error result directly (drop the `working` result). Keep the assertions and add `assert not proc.stdin.closed`.

Add one new test:

```python
def test_stop_interrupts_the_turn_and_keeps_the_process():
    """Stop ends the turn, not the tab's process, when the CLI confirms it."""
    def stdout():
        yield _line(ANSWER)
        # The test cancels the worker when that row is shown. The CLI then
        # confirms the interrupt and sends the interrupted turn's result.
        deadline = time.monotonic() + 5
        requests: list = []
        while time.monotonic() < deadline and not requests:
            requests = [json.loads(w) for w in proc.stdin.written if '"control_request"' in w]
            time.sleep(0.005)
        assert requests, "Stop sent no interrupt"
        request_id = requests[0]["request_id"]
        yield _line(
            {"type": "control_response", "response": {"subtype": "success", "request_id": request_id}}
        )
        yield _line({"type": "result", "subtype": "success"})

    # The generator names `proc` before this line runs; that is fine, because
    # its body only runs once the session's reader starts iterating it.
    proc = _FakeProc(stdout())
    holder: dict = {}

    def cancel_after_first_row(kind, _text):
        if kind == "assistant":
            threading.Thread(target=holder["worker"].cancel, daemon=True).start()

    _activity, completed, failures, raised, finished = _drive(
        proc, on_activity=cancel_after_first_row, worker_out=holder
    )

    assert finished and not raised
    assert not proc.killed, "a confirmed interrupt must leave the process alone"
    assert not proc.stdin.closed
    assert not failures, failures
    # A stopped turn reports nothing; the panel keeps the rows it streamed.
    assert completed == []
```

Add `import json` to the file's imports. Extend `_drive` with a keyword parameter `worker_out: Optional[dict] = None`; when given, set `worker_out["worker"] = worker` right after the worker is constructed and before the thread starts.

- [ ] **Step 2: Run the file**

Run: `C:/Python313/python.exe -m pytest tests/test_claude_stream_resilience.py -q -p no:randomly`
Expected: all pass, no hangs. If a test hangs at the 10 second driver timeout, the worker is waiting on the queue for an event the fake never sends; fix the test's stdout so it closes, not the worker.

- [ ] **Step 3: Commit**

```bash
git add tests/test_claude_stream_resilience.py
git commit -m "Say in the stream tests that a result ends the turn and nothing else"
```

---

### Task 7: mypy, the drop sites, and the applied report

**Files:**
- Modify: `mypy.ini`
- Test: `tests/test_held_process_drop_sites.py` (read only, confirm)
- Create: `docs/claude-session/applied.md`

- [ ] **Step 1: Add the module to mypy**

In `mypy.ini`, add `claude_session.py` to the `files =` list after `backend_pool.py`. Run `C:/Python313/python.exe -m mypy`; expected clean. Fix any report in `claude_session.py` or the worker.

- [ ] **Step 2: Confirm the drop sites already cover Claude**

Run: `C:/Python313/python.exe -m pytest tests/test_held_process_drop_sites.py -q -p no:randomly`
Expected: all pass. `_drop_held_backends` already drops `pool_key(BACKEND_CLAUDE, self)`, and `test_dropping_stops_every_backend_this_panel_held` keeps a Claude handle. Also remove the comment in `_drop_held_backends` that says the three backends "are still starting fresh each turn", since Claude no longer does; make it say Hermes and FreeBuff still start fresh.

- [ ] **Step 3: Write the applied report**

Create `docs/claude-session/applied.md` with these sections, in plain words: what changed per file (`claude_session.py` new; `ClaudeWorker` borrows the session; the panel's late turns; the deleted shutdown code), the tests by file and name, the announcement a late turn makes ("A background agent has reported. Receiving response"), what the other backends do (copied from the design's section), what was not checked live and why (the audit copy could not sign in; see the design's Testing section for the live steps to run once `claude /login` has been done), and the one CLI fact this rests on (resumed agents are not counted in `subagent_stats`, CLI 2.1.258).

- [ ] **Step 4: Commit**

```bash
git add mypy.ini blindpilot_app.py docs/claude-session/applied.md
git commit -m "Check the held Claude process with mypy and write down what changed"
```

---

### Task 8: The whole suite, then the pull request

- [ ] **Step 1: Run everything**

Run: `C:/Python313/python.exe -m pytest -q --ignore=docs && C:/Python313/python.exe -m ruff check . && C:/Python313/python.exe -m ruff format --check . && C:/Python313/python.exe -m mypy && C:/Python313/python.exe blind_pilot.py --startup-gui-smoke`
Expected: all clean, exit 0. Then `C:/Python313/python.exe -m pytest --collect-only -q --ignore=docs | tail -1` collects at least as many tests as `main` less the one deleted in Task 6.

- [ ] **Step 2: Live check, if the CLI can sign in**

Only if `claude -p "say ok" --output-format text` answers from a fresh shell: launch the audit copy (docs/visual-audit/README.md recipe), send "Use the Agent tool once, foreground, to run `echo hi`; reply DONE", then "Use SendMessage to that agent: run `sleep 20` then reply LATE; do not wait; reply RESUMED", end both turns, and confirm a late turn arrives with the agent's LATE reply and that the Claude process id (Task Manager or `Get-Process`) did not change across the three turns. Record the result in `docs/claude-session/applied.md`. If the CLI cannot sign in, record that instead.

- [ ] **Step 3: Push and open the PR** (only when the user says so)

```bash
git push -u fork feat/held-claude-session
gh pr create --repo serrebidev/BlindPilot --base main --head blindndangerous:feat/held-claude-session --title "Keep the Claude Code process between turns so the agents a turn leaves running survive it" --body-file <body written from docs/claude-session/applied.md>
```

---

## Self-review

- Spec coverage: components `ClaudeSession` (Tasks 1, 2), adapter and `take_or_start` (3), worker (4), panel late turns and Send during a late turn (5; Send during any running turn already steers), drop sites (7), reaper announcement (unchanged, existing), error handling: dead on take (pool, existing), dies between turns (pool `take` announces `REAP_DIED`, existing), interrupt unconfirmed (4), refused settings (3), control request between turns (late turn worker, 5), idle reap (pool, existing). Testing section: each bullet maps to a test in Tasks 1 to 6 and the pool contract in 3. Live check in 8.
- Placeholders: Task 4 Step 3 says "the existing control_request, system/init, assistant and user branches, unchanged" and "the existing arguments, unchanged" in Task 5; both refer to code the implementer has in front of them at the named lines, not to code that does not exist. Task 3's refusing-model test carries its own correction paragraph; the implementer applies it.
- Names: `Wants`, `build_command`, `EOF`, `ClaudeSession`, `attach`, `detach`, `set_idle_sink`, `busy`, `alive`, `returncode`, `stop`, `write_json`, `send_user`, `send_control`, `interrupt`, `set_model`, `set_permission_mode`, `can_serve`, `adopt`, `stderr_mark`, `stderr_since`, `wait_stderr`, `on_event`, `claude_adapter`, `take_or_start`, `_popen`, `_INTERRUPT_SECONDS`, `_CONTROL_SECONDS`, `held_for`, `on_unsolicited`, `_claude_worker_extra`, `_launch_turn`, `_start_late_turn`, `_late_turn_waiting`, `"late_turn"`, `_CANCEL_DRAIN_SECONDS`, `_read_turn`, `_drop_process` are used consistently across tasks.
