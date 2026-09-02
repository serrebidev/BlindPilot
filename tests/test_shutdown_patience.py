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

import subprocess
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
    """The clock restarts whenever it says anything, so a busy shutdown is
    never mistaken for a stuck one however long it takes."""
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
