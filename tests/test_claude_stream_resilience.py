"""What the Claude reader does when the stream misbehaves.

Every case here was reachable from an ordinary turn that fanned out subagents,
and every one of them ended the same way for the person watching: the turn
stopped, and all BlindPilot could say was that Claude Code had exited.
"""

from __future__ import annotations

import io
import os
import subprocess
import threading
import time


class _FakeStdin:
    def __init__(self):
        self.written: list[str] = []
        self.closed = False

    def write(self, data):
        if self.closed:
            raise ValueError("write to closed pipe")
        self.written.append(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _PipeStderr:
    """A stderr pipe with a real buffer, so failing to drain it has a cost.

    An operating system pipe holds a few kilobytes. A child that writes past
    that blocks until somebody reads, which is exactly what a reader that only
    looks at stderr after the process exits never does.
    """

    def __init__(self, capacity: int = 8):
        self._lines: list[str] = []
        self._closed = False
        self._lock = threading.Lock()
        self.capacity = capacity
        self.overflowed = False

    def feed(self, line: str, patience: float = 2.0) -> None:
        """The child writes one line, and waits when the pipe is full.

        Waiting is the part that matters. A real child does not get an error
        when nobody drains it; it stops, which is why the turn it was in the
        middle of never finishes.
        """
        deadline = time.monotonic() + patience
        while True:
            with self._lock:
                if len(self._lines) < self.capacity:
                    self._lines.append(line)
                    return
            if time.monotonic() >= deadline:
                self.overflowed = True
                raise BlockingIOError("stderr pipe stayed full: nobody is reading")
            time.sleep(0.001)

    def close(self) -> None:
        self._closed = True

    def __iter__(self):
        return self

    def __next__(self) -> str:
        while True:
            with self._lock:
                if self._lines:
                    return self._lines.pop(0)
                if self._closed:
                    raise StopIteration
            time.sleep(0.001)

    def read(self) -> str:
        with self._lock:
            text = "".join(self._lines)
            self._lines.clear()
        return text


class _FakeProc:
    def __init__(self, stdout_iter, stderr=None, returncode=0):
        self.stdin = _FakeStdin()
        self.stdout = stdout_iter
        self.stderr = stderr if stderr is not None else io.StringIO("")
        self.returncode = returncode
        self.killed = False

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True


def _drive(proc, on_activity=None, timeout=10.0):
    """Run one worker turn over `proc`, off the main thread so a stall shows up.

    Returns (activity, completed, failures, raised, finished).
    """
    import blindpilot_app

    activity: list[tuple[str, str]] = []
    completed: list[str] = []
    failures: list[str] = []
    raised: list[BaseException] = []

    def record(kind, text):
        activity.append((kind, text))
        if on_activity is not None:
            on_activity(kind, text)

    real_popen, real_find = subprocess.Popen, blindpilot_app._find_claude
    subprocess.Popen = lambda *_a, **_k: proc  # type: ignore[assignment]
    blindpilot_app._find_claude = lambda: "claude"  # type: ignore[assignment]

    worker = blindpilot_app.ClaudeWorker(
        "hi",
        None,
        os.getcwd(),
        "default",
        on_session=lambda _sid: None,
        on_started=lambda: None,
        on_activity=record,
        on_complete=completed.append,
        on_failed=failures.append,
        on_done=lambda: None,
    )

    def go():
        try:
            worker.run()
        except BaseException as exc:  # noqa: BLE001 - the point of the test
            raised.append(exc)

    thread = threading.Thread(target=go, daemon=True)
    thread.start()
    thread.join(timeout)
    finished = not thread.is_alive()

    subprocess.Popen = real_popen  # type: ignore[assignment]
    blindpilot_app._find_claude = real_find  # type: ignore[assignment]
    return activity, completed, failures, raised, finished


def _line(event) -> str:
    import json

    return json.dumps(event) + "\n"


ANSWER = {"type": "assistant", "message": {"content": [{"type": "text", "text": "the answer"}]}}
RESULT = {"type": "result"}


def test_a_chatty_child_never_fills_the_stderr_pipe():
    """stderr has to be read while the turn runs, not after it ends.

    A fan-out of subagents is the loudest a turn ever gets. If nobody is
    reading stderr by then, the child blocks writing its own diagnostics and
    the turn dies with nothing to show for it.
    """
    stderr = _PipeStderr(capacity=8)

    def stdout():
        for index in range(40):
            # The child writes to both streams, as a real one does.
            stderr.feed(f"warning {index}\n")
            yield _line(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": f"step {index}"}]},
                }
            )
        yield _line(RESULT)
        stderr.close()

    proc = _FakeProc(stdout(), stderr=stderr)
    _activity, completed, failures, raised, finished = _drive(proc)

    assert finished, "the turn never ended: the child stalled on a full stderr pipe"
    assert not raised, f"the worker thread died: {raised}"
    assert not stderr.overflowed, "stderr was never drained while the turn ran"
    assert completed and not failures


def test_a_decode_error_on_stdout_is_reported_rather_than_raised():
    """One bad byte must not take the whole turn down without a word.

    The stream is decoded strictly, so a single malformed byte raises inside
    the read loop. Nothing catches it, so the thread dies, stdin closes, and
    the CLI is left to exit on its own.
    """

    def stdout():
        yield _line(ANSWER)
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    proc = _FakeProc(stdout(), returncode=1)
    _activity, _completed, failures, raised, finished = _drive(proc)

    assert finished
    assert not raised, f"the decode error escaped the worker: {raised}"
    assert failures, "the person was told nothing about why the turn stopped"


def test_a_crash_while_showing_a_row_is_reported_rather_than_closing_stdin():
    """A callback that raises must not silently EOF a running turn.

    `run()` had a `finally` and no `except`, so anything thrown while a row was
    being shown closed the CLI's stdin mid-turn. The CLI exited, and the exit
    code was the only explanation anybody got.
    """

    def stdout():
        yield _line(ANSWER)
        yield _line(RESULT)

    def explode(kind, _text):
        if kind == "assistant":
            raise RuntimeError("the row could not be shown")

    proc = _FakeProc(stdout(), returncode=1)
    _activity, _completed, failures, raised, finished = _drive(proc, on_activity=explode)

    assert finished
    assert not raised, f"the callback's error escaped the worker: {raised}"
    assert failures, "the turn ended with nothing said about the crash"
    assert "could not be shown" in failures[0]


def test_subagent_narration_stays_out_of_the_final_answer():
    """Work a subagent narrates is not the answer the main turn gives.

    Subagent events carry `parent_tool_use_id`. Treating them as the parent's
    own words puts five agents' running commentary into one reply.
    """

    def stdout():
        yield _line(
            {
                "type": "assistant",
                "parent_tool_use_id": "toolu_abc",
                "message": {"content": [{"type": "text", "text": "subagent chatter"}]},
            }
        )
        yield _line(ANSWER)
        yield _line(RESULT)

    proc = _FakeProc(stdout())
    activity, completed, _failures, _raised, finished = _drive(proc)

    assert finished
    assert completed == ["the answer"], f"subagent text leaked into the answer: {completed}"
    # It still deserves to be shown live; it just is not the answer.
    # Shown live, but on its own channel: "subagent" is what lets Keep up
    # leave five agents' commentary in the list instead of reading it out.
    assert ("subagent", "subagent chatter") in activity
    assert ("assistant", "the answer") in activity


def test_a_nonzero_exit_still_reports_what_stderr_said():
    """Draining stderr early must not lose it: it is the only explanation."""

    stderr = _PipeStderr(capacity=64)

    def stdout():
        stderr.feed("FATAL ERROR: JavaScript heap out of memory\n")
        stderr.close()
        return
        yield  # pragma: no cover - generator marker

    proc = _FakeProc(stdout(), stderr=stderr, returncode=1)
    _activity, _completed, failures, _raised, finished = _drive(proc)

    assert finished
    assert failures
    assert "heap out of memory" in failures[0]


def test_a_run_is_not_ended_while_background_agents_are_still_working():
    """The turn ending is not the run ending when agents were left running.

    This is the fan-out failure: five agents launched in the background, the
    main turn answers "they are running", and ending the run there stopped the
    CLI and killed all five before any of them reported.
    """

    def stdout():
        yield _line(ANSWER)
        # The turn is done; both agents are still out there.
        yield _line(
            {
                "type": "result",
                "subagent_stats": {
                    "started_in_background": 2,
                    "completed": 0,
                    "failed": 0,
                    "killed": {"parent": 0, "user": 0, "system": 0},
                },
            }
        )
        yield _line(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "agent one reported"}]},
            }
        )
        yield _line(
            {
                "type": "result",
                "subagent_stats": {
                    "started_in_background": 2,
                    "completed": 2,
                    "failed": 0,
                    "killed": {"parent": 0, "user": 0, "system": 0},
                },
            }
        )

    proc = _FakeProc(stdout())
    activity, completed, _failures, _raised, finished = _drive(proc)

    assert finished
    assert completed, "the run produced nothing"
    assert "agent one reported" in completed[0], (
        f"the run ended before the agents reported: {completed}"
    )
    # The person is told why the turn is still going, and how to stop it.
    assert any("background" in text for _kind, text in activity)


def test_an_answer_survives_a_nonzero_exit():
    """A turn that answered must not lose the answer to how the process ended.

    A run that fans out background agents ends with the CLI still working, and
    stopping it makes the exit code non-zero. Reporting that in place of the
    reply threw away a turn that had already succeeded.
    """

    def stdout():
        yield _line(ANSWER)
        yield _line(RESULT)

    proc = _FakeProc(stdout(), returncode=1)
    activity, completed, failures, _raised, finished = _drive(proc)

    assert finished
    assert completed == ["the answer"], f"the answer was discarded: {failures}"
    # How it ended is still worth saying — just not instead of the answer.
    assert any("exited with code 1" in text for _kind, text in activity)
    assert not failures, f"a turn that answered was still reported as failed: {failures}"


def test_a_result_without_agent_counts_does_not_end_a_run_still_working():
    """Silence about the agents is not the same as the agents being done.

    The count is read out of each result event on its own. A later event that
    simply does not mention subagents returned zero, which ended the run and
    killed every agent — the original bug, restored by nothing more than a
    field going missing. The plain `{"type": "result"}` used everywhere else
    in this file is exactly that shape.
    """
    working = {
        "type": "result",
        "subagent_stats": {"started_in_background": 2, "completed": 0, "failed": 0},
    }
    done = {
        "type": "result",
        "subagent_stats": {"started_in_background": 2, "completed": 2, "failed": 0},
    }

    def stdout():
        yield _line(working)
        # No subagent_stats at all. The run must not take this as "finished".
        yield _line(RESULT)
        yield _line(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "an agent reported back"}]},
            }
        )
        yield _line(done)

    proc = _FakeProc(stdout())
    activity, completed, failures, raised, finished = _drive(proc)

    assert finished and not raised
    spoken = " | ".join(text for _kind, text in activity)
    assert "an agent reported back" in spoken, (
        f"the run ended at the event with no counts, killing the agents: {spoken}"
    )
    assert completed and not failures


def test_an_error_result_arriving_after_an_answer_keeps_the_answer():
    """Waiting for agents made a late error result reachable for the first time.

    The exit-code path deliberately keeps an answer that arrived before the
    process ended badly. This path did not: it failed the turn and threw the
    answer away, and `_on_failed` then dropped the turn from the transcript.
    """
    working = {
        "type": "result",
        "subagent_stats": {"started_in_background": 1, "completed": 0, "failed": 0},
    }

    def stdout():
        yield _line(ANSWER)
        yield _line(working)
        yield _line({"type": "result", "is_error": True, "result": "an agent could not finish"})

    proc = _FakeProc(stdout())
    _activity, completed, failures, raised, finished = _drive(proc)

    assert finished and not raised
    assert completed, f"the answer was discarded; only failures were reported: {failures}"
    assert "the answer" in completed[0]
