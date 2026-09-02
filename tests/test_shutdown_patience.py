"""Waiting for Claude Code to shut down, and saying who ended it.

Closing the CLI's input told it to finish. BlindPilot then gave it five
seconds flat and killed it. On Windows `Popen.kill` is
`TerminateProcess(handle, 1)`, so the exit code of a killed process is
*exactly* 1 — and the next thing BlindPilot did was report "Claude Code
exited with code 1", blaming the CLI for something BlindPilot had just done.

Shutting down is not instant. A session is written to disk, MCP servers are
torn down, and a run that has just kept a fan-out of agents alive is the one
with the most to put away — so the fix for that bug made this one more likely
to fire, not less.

Time is the wrong measure. A CLI still writing is still working; what deserves
to be killed is one that has gone quiet.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import threading
import time

import pytest

import blindpilot_app as app


class _Proc:
    """A process that exits after a given number of `wait` attempts."""

    def __init__(self, exits_after: int, returncode: int = 0):
        self._left = exits_after
        self._rc = returncode
        self.returncode = None
        self.killed = False

    def wait(self, timeout=None):
        if self._left > 0:
            self._left -= 1
            # Sleep the timeout it was given, so the caller's own clock
            # advances the way it would against a real process.
            time.sleep(timeout or 0)
            raise subprocess.TimeoutExpired("claude", timeout or 0)
        self.returncode = self._rc
        return self._rc

    def kill(self):
        self.killed = True
        # What TerminateProcess actually does, and the whole reason the old
        # message was wrong: a killed process reports exit code 1.
        self.returncode = 1
        self._left = 0


def _worker(proc, stderr_lines=None):
    worker = object.__new__(app.ClaudeWorker)
    worker._proc = proc
    worker._stderr_lines = stderr_lines if stderr_lines is not None else []
    worker._stopped_by_us = False
    return worker


def test_a_cli_that_exits_promptly_is_not_killed():
    proc = _Proc(exits_after=0)
    worker = _worker(proc)

    assert worker._wait_for_shutdown() is True
    assert proc.killed is False


def test_a_slow_shutdown_is_waited_out_rather_than_killed(monkeypatch):
    """Five seconds was arbitrary. Taking a while is not the same as stuck."""
    monkeypatch.setattr(app, "_SHUTDOWN_QUIET_SECONDS", 5.0)
    # Longer than the old five-second limit allowed for, but it does finish.
    proc = _Proc(exits_after=3)
    worker = _worker(proc)

    assert worker._wait_for_shutdown() is True
    assert proc.killed is False


def test_a_cli_that_keeps_writing_is_given_more_time(monkeypatch):
    """A CLI dying mid-turn explains itself on stderr, and that explanation is
    all BlindPilot has to report, so the clock restarts while it arrives.

    This was once believed to cover the end of every turn. It does not: a
    healthy CLI shutting down writes nothing to stderr, having no errors to
    write, so this fake — which writes a line on every poll — is the one case
    that never happens on that path. See the section at the end of this file.
    """
    monkeypatch.setattr(app, "_SHUTDOWN_QUIET_SECONDS", 0.4)
    lines: list[str] = []
    proc = _Proc(exits_after=6)

    class _Talkative(_Proc):
        def wait(self, timeout=None):
            # Still writing to stderr each time it is asked.
            lines.append("still tearing down")
            return _Proc.wait(self, timeout)

    proc = _Talkative(exits_after=3)
    worker = _worker(proc, stderr_lines=lines)

    assert worker._wait_for_shutdown() is True
    assert proc.killed is False


def test_a_cli_that_has_gone_quiet_is_eventually_stopped(monkeypatch):
    """Still bounded: a genuinely stuck process must not hang the turn."""
    monkeypatch.setattr(app, "_SHUTDOWN_QUIET_SECONDS", 0.3)
    proc = _Proc(exits_after=10_000)
    worker = _worker(proc)

    assert worker._wait_for_shutdown() is False
    assert proc.killed is False, "the wait itself must not kill; the caller does"


def test_the_message_says_who_stopped_it(monkeypatch):
    """The old sentence blamed the CLI for BlindPilot's own timeout, and on
    Windows even the number was BlindPilot's."""
    monkeypatch.setattr(app, "_SHUTDOWN_QUIET_SECONDS", 0.2)
    proc = _Proc(exits_after=10_000)
    worker = _worker(proc)

    assert worker._wait_for_shutdown() is False
    proc.kill()
    worker._stopped_by_us = True

    note = worker._ending_note(proc.returncode, detail="")

    assert "BlindPilot" in note, note
    assert "exited with code" not in note, note


def test_a_genuine_failure_still_names_the_exit_code():
    """A CLI that really did fail must still be reported as such."""
    worker = _worker(_Proc(exits_after=0, returncode=2))

    note = worker._ending_note(2, detail=": something went wrong")

    assert "exited with code 2" in note
    assert "something went wrong" in note


@pytest.mark.parametrize("code", [1, 2, 137])
def test_only_our_own_kill_is_attributed_to_us(code):
    worker = _worker(_Proc(exits_after=0))

    assert "exited with code" in worker._ending_note(code, detail="")


# ----- what a real shutdown actually looks like -----
#
# The tests above are satisfied by a process that writes to stderr while it
# shuts down. Real CLIs do not. stderr is where errors go, and a CLI that is
# writing its session file and stopping its MCP servers has no errors to
# report — so `_stderr_lines` never grows, the clock never restarts, and the
# "still writing is still working" signal above is inert in the field. What
# shipped was not a patient wait; it was a thirty-second hard timeout wearing
# one, and a user hit it.


class _FakeStdin:
    """Closing this is how the CLI is told the turn is over."""

    def __init__(self):
        self.closed = False

    def write(self, data):
        pass

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _NeverExits:
    """A CLI that has answered and is now taking its time shutting down.

    Silent on stderr throughout, because that is what a clean shutdown looks
    like from the outside. Alive, busy, and saying nothing.
    """

    def __init__(self, stdout_lines):
        self.stdin = _FakeStdin()
        self.stdout = iter(stdout_lines)
        self.stderr = io.StringIO("")
        self.returncode = None
        self.killed = False
        self.waits: list[float | None] = []

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.returncode is not None:
            return self.returncode
        if timeout is None:
            raise AssertionError("an unbounded wait on a process that never exits")
        time.sleep(timeout)
        raise subprocess.TimeoutExpired("claude", timeout)

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = 1  # TerminateProcess(handle, 1)


ANSWER = (
    json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "the answer"}]}}
    )
    + "\n"
)
RESULT = json.dumps({"type": "result"}) + "\n"


def _turn(proc, timeout=20.0):
    """Run one whole turn over `proc`, off the main thread as the window does."""
    activity: list[tuple[str, str]] = []
    completed: list[str] = []
    failures: list[str] = []

    real_popen, real_find = subprocess.Popen, app._find_claude
    subprocess.Popen = lambda *_a, **_k: proc  # type: ignore[assignment]
    app._find_claude = lambda: "claude"  # type: ignore[assignment]
    try:
        worker = app.ClaudeWorker(
            "hi",
            None,
            os.getcwd(),
            "default",
            on_session=lambda _sid: None,
            on_started=lambda: None,
            on_activity=lambda kind, text: activity.append((kind, text)),
            on_complete=completed.append,
            on_failed=failures.append,
            on_done=lambda: None,
        )
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        thread.join(timeout)
        assert not thread.is_alive(), "the turn never ended"
    finally:
        subprocess.Popen = real_popen  # type: ignore[assignment]
        app._find_claude = real_find  # type: ignore[assignment]
    return activity, completed, failures


def test_a_turn_that_answered_does_not_wait_on_the_shutdown(monkeypatch):
    """The answer is in. Whatever the process does now changes nothing anybody
    hears, so the turn must not be held open for it."""
    monkeypatch.setattr(app, "_SHUTDOWN_QUIET_SECONDS", 5.0)
    proc = _NeverExits([ANSWER, RESULT])

    started = time.monotonic()
    _activity, completed, failures = _turn(proc)
    took = time.monotonic() - started

    assert completed == ["the answer"], (completed, failures)
    assert took < 2.0, f"the finished turn waited {took:.1f}s on a process it was done with"


def test_a_healthy_cli_is_not_killed_for_being_slow_to_tidy_up(monkeypatch):
    """Killing mid-shutdown truncates the session file the next resume needs,
    and cuts MCP servers off rather than stopping them."""
    monkeypatch.setattr(app, "_SHUTDOWN_QUIET_SECONDS", 1.0)
    proc = _NeverExits([ANSWER, RESULT])

    _turn(proc)

    assert proc.killed is False, "BlindPilot killed a CLI that had done nothing wrong"


def test_a_finished_turn_says_nothing_about_the_shutdown(monkeypatch):
    """What the user reported: a good answer, then a sentence saying BlindPilot
    had stopped Claude Code. Nothing went wrong, so nothing is worth saying."""
    monkeypatch.setattr(app, "_SHUTDOWN_QUIET_SECONDS", 1.0)
    proc = _NeverExits([ANSWER, RESULT])

    activity, completed, _failures = _turn(proc)

    assert completed == ["the answer"]
    said = " ".join(text for _kind, text in activity)
    assert "shutting down" not in said, said
    assert "BlindPilot stopped" not in said, said


def test_the_process_is_still_reaped_rather_than_leaked(monkeypatch):
    """Not waiting for it is not the same as walking away from it: every turn
    starts one of these, and a session is many turns."""
    monkeypatch.setattr(app, "_SHUTDOWN_QUIET_SECONDS", 1.0)
    proc = _NeverExits([ANSWER, RESULT])

    _turn(proc)

    deadline = time.monotonic() + 2.0
    while app._REAP_SECONDS not in proc.waits and time.monotonic() < deadline:
        time.sleep(0.01)

    assert app._REAP_SECONDS in proc.waits, (
        f"nobody is waiting on the process, so it will never be reaped: {proc.waits}"
    )
    assert all(
        thread.daemon for thread in threading.enumerate() if thread.name == "claude-shutdown"
    ), "a reaper must never hold the application open at exit"
