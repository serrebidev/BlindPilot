"""What Codex, FreeBuff and opencode do when the turn itself falls over.

`run()` was `try`/`finally` with no `except` in all three adapters. The
`finally` calls `on_done`, which re-enables Send and stops the progress
earcon, so a turn killed by an exception was indistinguishable from one that
answered - except that no answer arrived and nothing was said about it. The
traceback went to a stderr the packaged windowed build does not have.
`ClaudeWorker` was fixed for exactly this; these three were not.
"""

from __future__ import annotations

import io
import subprocess
import threading

import pytest

import agent_backends
from agent_backends import BACKEND_CODEX, CodexWorker, FreebuffWorker, OpencodeWorker

WORKERS = [CodexWorker, FreebuffWorker, OpencodeWorker]


class _Recorder:
    """One turn's callbacks, and whether the worker thread survived it."""

    def __init__(self, worker_class, prompt: str = "do the work"):
        self.failures: list[str] = []
        self.completed: list[str] = []
        self.done = 0
        self.escaped: list[BaseException] = []
        self.worker = worker_class(
            prompt,
            None,
            ".",
            "default",
            on_session=lambda _value: None,
            on_started=lambda: None,
            on_activity=lambda _kind, _value: None,
            on_complete=self.completed.append,
            on_failed=self.failures.append,
            on_done=self._finish,
        )

    def _finish(self) -> None:
        self.done += 1

    def drive(self, timeout: float = 10.0) -> None:
        """Run the turn on its own thread, as the window does."""

        def go():
            try:
                self.worker.run()
            except BaseException as exc:  # noqa: BLE001 - the point of the test
                self.escaped.append(exc)

        thread = threading.Thread(target=go, daemon=True)
        thread.start()
        thread.join(timeout)
        assert not thread.is_alive(), "the turn never ended"


@pytest.mark.parametrize("worker_class", WORKERS, ids=lambda cls: cls.__name__)
def test_a_crash_mid_turn_is_reported_rather_than_passing_for_a_finished_turn(worker_class):
    """Send comes back on either way, so silence here reads as a finished turn.

    For a screen-reader user there is nothing to see: the earcon stops, Send is
    live again, and the only difference from a normal turn is that no answer
    was ever spoken.
    """
    recorder = _Recorder(worker_class)
    recorder.worker._do_run = lambda: (_ for _ in ()).throw(RuntimeError("the reader fell over"))

    recorder.drive()

    assert not recorder.escaped, f"the error escaped the worker: {recorder.escaped}"
    assert recorder.done == 1, "the window was never told the turn was over"
    assert recorder.failures, "the turn died and the person was told nothing"
    assert "the reader fell over" in recorder.failures[0]


@pytest.mark.parametrize("worker_class", WORKERS, ids=lambda cls: cls.__name__)
def test_a_turn_that_already_said_why_it_failed_does_not_say_it_twice(worker_class):
    """The first account is the useful one; the crash behind it is not news.

    Reporting again would speak a second error row over the top of the real
    reason, which is the one thing the person can act on.
    """
    recorder = _Recorder(worker_class)

    def report_then_crash():
        recorder.worker._fail("the CLI refused the prompt")
        raise RuntimeError("and then cleanup fell over")

    recorder.worker._do_run = report_then_crash

    recorder.drive()

    assert not recorder.escaped, f"the error escaped the worker: {recorder.escaped}"
    assert recorder.failures == ["the CLI refused the prompt"]


def test_codex_reports_a_read_loop_that_raises_instead_of_exiting_in_silence(monkeypatch):
    """The same failure over a real app-server stream, not a stubbed `_do_run`.

    Anything the reader raises - a broken pipe, a callback that could not draw
    its row - used to end the turn through the `finally` alone.
    """

    class _Stdin:
        def write(self, _data):
            pass

        def flush(self):
            pass

        def close(self):
            pass

    class _Stdout:
        def __iter__(self):
            return self

        def __next__(self):
            raise OSError("the pipe to Codex broke")

    class _Proc:
        def __init__(self):
            self.stdin = _Stdin()
            self.stdout = _Stdout()
            self.stderr = io.StringIO("")
            self.returncode = 1

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _backend: "codex")
    monkeypatch.setattr(agent_backends, "_codex_app_server_binary", lambda binary: binary)
    monkeypatch.setattr(agent_backends, "subprocess_env", lambda _binary: {})
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: _Proc())
    assert agent_backends.find_backend_cli(BACKEND_CODEX) == "codex"

    recorder = _Recorder(CodexWorker)
    recorder.drive()

    assert not recorder.escaped, f"the error escaped the worker: {recorder.escaped}"
    assert recorder.done == 1
    assert recorder.failures, "Codex died mid-stream and said nothing"
    assert "the pipe to Codex broke" in recorder.failures[0]
