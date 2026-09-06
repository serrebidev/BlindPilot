"""What the Claude reader does when the stream misbehaves.

Every case here was reachable from an ordinary turn that fanned out subagents,
and every one of them ended the same way for the person watching: the turn
stopped, and all BlindPilot could say was that Claude Code had exited.
"""

from __future__ import annotations

import io
import os
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
        self.stdout_done = False
        self.stdout = self._watch(stdout_iter)
        self.stderr = stderr if stderr is not None else io.StringIO("")
        self.returncode = returncode
        self.killed = False

    def _watch(self, it):
        try:
            for line in it:
                yield line
        finally:
            self.stdout_done = True

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode if self.stdout_done else None

    def kill(self):
        self.killed = True
        self.stdout_done = True
        if self.returncode is None:
            self.returncode = 1


def _drive(proc, on_activity=None, timeout=10.0, on_worker=None):
    """Run one worker turn over `proc`, off the main thread so a stall shows up.

    `on_worker` is handed the worker before it starts, so a test can stop the
    turn from its own thread while the worker is reading.

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

    import claude_session

    real_popen, real_find = claude_session._popen, blindpilot_app._find_claude
    claude_session._popen = lambda *_a, **_k: proc  # type: ignore[assignment]
    blindpilot_app._find_claude = lambda: "claude"  # type: ignore[assignment]

    try:
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
        if on_worker is not None:
            on_worker(worker)

        def go():
            try:
                worker.run()
            except BaseException as exc:  # noqa: BLE001 - the point of the test
                raised.append(exc)

        thread = threading.Thread(target=go, daemon=True)
        thread.start()
        thread.join(timeout)
        finished = not thread.is_alive()
    finally:
        # A test that fails part way through must not leave the real Popen
        # swapped out, or every test after it launches the fake.
        claude_session._popen = real_popen  # type: ignore[assignment]
        blindpilot_app._find_claude = real_find  # type: ignore[assignment]
        # Not this function's job to drop the held process: a test that
        # asserts on `proc` after this call needs it exactly as the worker
        # left it. `no_backend_process_outlives_its_test` in conftest.py
        # empties the pool around every test regardless.
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
    the read loop. The session's reader catches it and hands EOF to the turn,
    which is what turns it into a reported failure instead of a silent one.
    No answer came before it, so there is nothing here for the turn to keep.
    """

    def stdout():
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        yield  # pragma: no cover - generator marker

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
    assert not proc.stdin.closed


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
        # The turn's stderr mark is taken before its prompt is written, so
        # waiting for the prompt here keeps this line - which runs on the
        # reader thread the moment the session starts - from racing ahead of
        # that mark and landing before it, which would make it look like it
        # happened before this turn instead of during it.
        deadline = time.monotonic() + 3.0
        while not proc.stdin.written and time.monotonic() < deadline:
            time.sleep(0.001)
        stderr.feed("FATAL ERROR: JavaScript heap out of memory\n")
        stderr.close()
        return
        yield  # pragma: no cover - generator marker

    proc = _FakeProc(stdout(), stderr=stderr, returncode=1)
    _activity, _completed, failures, _raised, finished = _drive(proc)

    assert finished
    assert failures
    assert "heap out of memory" in failures[0]


def test_a_turn_ends_at_its_result_and_the_process_stays_for_the_agents():
    """A turn's own result ends the turn; the process is somebody else's business.

    This used to be the fan-out failure: five agents launched in the
    background, the main turn answered "they are running", and ending the run
    there stopped the CLI and killed all five before any of them reported.
    Now the result always ends the turn, and the process stays regardless of
    what it left running: what the agents find arrives as turns of their own.
    """

    def stdout():
        yield _line(ANSWER)
        # The turn is done; both agents are still out there. That no longer
        # changes anything about this turn.
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

    proc = _FakeProc(stdout())
    _activity, completed, _failures, _raised, finished = _drive(proc)

    assert finished
    assert completed == ["the answer"], completed
    assert not proc.stdin.closed
    assert not proc.killed


def test_an_answer_survives_a_nonzero_exit():
    """A turn that answered must not lose the answer to how the process died.

    The process now ends only if it dies. A process that dies mid-turn, after
    answering but before its result arrived, still gave up an answer worth
    keeping.
    """

    def stdout():
        yield _line(ANSWER)

    proc = _FakeProc(stdout(), returncode=1)
    activity, completed, failures, _raised, finished = _drive(proc)

    assert finished
    assert completed == ["the answer"], f"the answer was discarded: {failures}"
    # How it ended is still worth saying — just not instead of the answer.
    assert any("exited with code 1" in text for _kind, text in activity)
    assert not failures, f"a turn that answered was still reported as failed: {failures}"


def test_a_result_for_a_queued_turn_does_not_end_the_run():
    """The first result on the stream is not always the result of our turn.

    A resumed CLI can have a turn of its own to run first, such as the notice
    about a background command its previous process left behind. That turn
    answers with nothing and its result carries the count of turns still
    queued, ours among them. Reading it as our result closed stdin with the
    prompt still inside the CLI, and the kill that followed was reported as
    the answer.
    """

    def stdout():
        yield _line({"type": "result", "queued_turn_count": 1})
        yield _line(ANSWER)
        yield _line({"type": "result", "queued_turn_count": 0})

    proc = _FakeProc(stdout())
    _activity, completed, failures, _raised, finished = _drive(proc)

    assert finished
    assert not failures, failures
    assert completed == ["the answer"], completed
    assert not proc.killed


def test_a_completed_turn_with_no_answer_says_so_and_is_not_killed():
    """A turn that reached its result without saying anything is over.

    It was treated as a turn that never finished: waited on for thirty
    seconds, killed, and the kill announced as why the turn failed. The
    person heard that BlindPilot had stopped Claude Code, over a turn that
    had ended normally.
    """
    proc = _FakeProc(iter([_line(RESULT)]))
    activity, completed, failures, _raised, finished = _drive(proc)

    assert finished
    assert not proc.killed, "a finished turn was killed for being slow to exit"
    assert not failures, failures
    assert completed == [""], completed
    assert any("without saying anything" in text for _kind, text in activity), activity
    # The old bug followed a finished turn with a shutdown complaint.
    assert not any("BlindPilot stopped" in text for _kind, text in activity), activity


def test_an_error_result_arriving_after_an_answer_keeps_the_answer():
    """An error result must not throw away an answer that already arrived.

    The exit-code path deliberately keeps an answer that arrived before the
    process ended badly. This path did not: it failed the turn and threw the
    answer away, and `_on_failed` then dropped the turn from the transcript.
    """

    def stdout():
        yield _line(ANSWER)
        yield _line({"type": "result", "is_error": True, "result": "an agent could not finish"})

    proc = _FakeProc(stdout())
    _activity, completed, failures, raised, finished = _drive(proc)

    assert finished and not raised
    assert completed, f"the answer was discarded; only failures were reported: {failures}"
    assert "the answer" in completed[0]
    assert not proc.stdin.closed


def _interrupt_id(stdin, patience: float = 3.0) -> str:
    """The id of the interrupt the worker wrote, once it has written one."""
    import json

    deadline = time.monotonic() + patience
    while time.monotonic() < deadline:
        for raw in list(stdin.written):
            event = json.loads(raw)
            request = event.get("request") or {}
            if event.get("type") == "control_request" and request.get("subtype") == "interrupt":
                return str(event.get("request_id"))
        time.sleep(0.01)
    raise AssertionError("the worker never sent an interrupt")


def test_a_stopped_turn_ends_at_the_drain_when_no_result_follows(monkeypatch):
    """A Stop the CLI confirms must not leave the reader parked for ever.

    The reader waits on the turn's queue with no timeout, and the drain is
    only asked for when the next event is asked for. So a Stop that landed
    while the reader was parked started no drain at all: a confirmed
    interrupt whose result never arrived held the turn, and the tab's
    process with it, for good.
    """
    import blindpilot_app

    monkeypatch.setattr(blindpilot_app, "_CANCEL_DRAIN_SECONDS", 0.3)
    workers: list = []
    first_row = threading.Event()

    def stdout():
        yield _line(ANSWER)
        # The CLI confirms the stop and then says nothing more: no result for
        # the interrupted turn, and no exit either.
        yield _line(
            {
                "type": "control_response",
                "response": {"subtype": "success", "request_id": _interrupt_id(proc.stdin)},
            }
        )
        while not proc.killed:
            time.sleep(0.01)

    proc = _FakeProc(stdout())

    def note_first_row(kind, _text):
        if kind == "assistant":
            first_row.set()

    def stop_after_the_first_row():
        first_row.wait(3.0)
        # Long enough for the reader to be back on the queue, which is the
        # state Stop was never heard in.
        time.sleep(0.1)
        if workers:
            workers[0].cancel()

    stopper = threading.Thread(target=stop_after_the_first_row, daemon=True)
    stopper.start()
    try:
        _activity, completed, failures, raised, finished = _drive(
            proc, on_activity=note_first_row, timeout=3.0, on_worker=workers.append
        )
    finally:
        stopper.join(3.0)

    assert finished, "the stopped turn never ended: the reader stayed parked on the queue"
    assert not raised, f"the worker thread died: {raised}"
    assert proc.killed, "the process that kept the result was left running"
    assert not failures, f"a cancelled turn is not a failure, but it said: {failures}"
    assert not completed, completed


def test_stop_interrupts_the_turn_and_keeps_the_process():
    """Stop ends the turn, not the tab's process, when the CLI confirms it."""
    workers: list = []

    def stdout():
        yield _line(ANSWER)
        # The test cancels the worker when that row is shown. The CLI then
        # confirms the interrupt and sends the interrupted turn's result.
        request_id = _interrupt_id(proc.stdin)
        yield _line(
            {
                "type": "control_response",
                "response": {"subtype": "success", "request_id": request_id},
            }
        )
        yield _line({"type": "result", "subtype": "success"})

    # The generator names `proc` before this line runs; that is fine, because
    # its body only runs once the session's reader starts iterating it.
    proc = _FakeProc(stdout())

    def cancel_after_first_row(kind, _text):
        if kind == "assistant":
            threading.Thread(target=workers[0].cancel, daemon=True).start()

    _activity, completed, failures, raised, finished = _drive(
        proc, on_activity=cancel_after_first_row, on_worker=workers.append
    )

    assert finished and not raised
    assert not proc.killed, "a confirmed interrupt must leave the process alone"
    assert not proc.stdin.closed
    assert not failures, failures
    # A stopped turn reports nothing; the panel keeps the rows it streamed.
    assert completed == []


def test_the_panel_join_budget_outlasts_an_interrupt_and_its_drain():
    """Stop must not call itself a failure while the stop is still landing.

    The panel cancels and then waits for the worker thread, and that thread
    spends the interrupt budget waiting for the CLI to confirm and then the
    drain waiting for the stopped turn's result. A shorter wait announced
    "Could not stop the task. It is still running" over a Stop that was
    working exactly as designed.
    """
    import blindpilot_app
    import claude_session

    assert blindpilot_app._CANCEL_JOIN_SECONDS > (
        claude_session._INTERRUPT_SECONDS + blindpilot_app._CANCEL_DRAIN_SECONDS
    )
