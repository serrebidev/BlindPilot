"""What Codex said on its way out.

When the Codex app server dies mid-turn, stdout reaches EOF and the worker
reports the failure straight away, using the last few lines of stderr as the
detail. stderr is a different pipe, read by a different thread, and nothing
waited for it.

The line worth having is the last one written — the panic, the "unauthorized",
the out-of-memory. Being last is exactly what makes it the one still in flight
when the turn is failed. Earlier lines are already in the list, so the race
does not lose noise, it loses the reason.

What the user hears instead is "Codex app server closed before the turn
completed", which says only that something happened, and says it
non-deterministically: the same crash reports its cause or does not depending
on how the two pipes were scheduled.
"""

from __future__ import annotations

import threading
import time

import agent_backends
from agent_backends import CodexWorker

PANIC = "thread 'main' panicked at src/auth.rs:91: unauthorized"


class _Stdin:
    def write(self, _data):
        pass

    def flush(self):
        pass

    def close(self):
        pass


class _LateStderr:
    """Delivers its one line just after stdout has already ended."""

    def __init__(self, line, delay=0.2):
        self._line = line
        self._delay = delay
        self._sent = False

    def __iter__(self):
        return self

    def __next__(self):
        if self._sent:
            raise StopIteration
        time.sleep(self._delay)
        self._sent = True
        return self._line + "\n"


class _DeadProc:
    """A server whose stdout has already closed."""

    def __init__(self, stderr):
        self.stdin = _Stdin()
        self.stdout = iter(())
        self.stderr = stderr
        self.returncode = 1
        self.pid = 4242

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        pass

    def terminate(self):
        pass


def _run(monkeypatch, stderr):
    failures: list[str] = []
    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _backend: "codex")
    monkeypatch.setattr(agent_backends, "end_process_group", lambda *a, **k: None)
    monkeypatch.setattr(agent_backends.subprocess, "Popen", lambda *a, **k: _DeadProc(stderr))
    worker = CodexWorker(
        "test",
        None,
        ".",
        "default",
        on_session=lambda _s: None,
        on_started=lambda: None,
        on_activity=lambda _k, _v: None,
        on_complete=lambda _v: None,
        on_failed=failures.append,
        on_done=lambda: None,
    )
    worker._do_run()
    return failures


def test_the_reason_codex_died_is_reported(monkeypatch):
    """The bug: this arrived a fraction too late and was thrown away."""
    failures = _run(monkeypatch, _LateStderr(PANIC))

    assert failures, "the turn did not fail at all"
    assert PANIC in failures[0], f"the reason was lost: {failures[0]!r}"


def test_a_death_with_nothing_on_stderr_still_says_something(monkeypatch):
    failures = _run(monkeypatch, iter(()))

    assert failures == ["Codex app server closed before the turn completed"]


def test_waiting_for_stderr_is_bounded(monkeypatch):
    """A pipe that never closes must not hold the turn open."""

    class _NeverEnds:
        def __iter__(self):
            return self

        def __next__(self):
            time.sleep(30)
            raise StopIteration

    started = time.monotonic()
    _run(monkeypatch, _NeverEnds())
    took = time.monotonic() - started

    assert took < 5, f"the turn waited {took:.1f}s on a stderr that never ended"


def test_the_reader_thread_does_not_outlive_the_python_process(monkeypatch):
    """It blocks on a pipe read, so it has to be a daemon or exit hangs."""
    _run(monkeypatch, _LateStderr(PANIC))

    readers = [t for t in threading.enumerate() if t.name == "codex-stderr"]
    assert all(t.daemon for t in readers)
