"""A Hermes turn's own end must not be re-announced, and work must count as work.

Two measured shapes a screen reader cannot afford to get wrong: a turn whose
answer was delivered but whose completion callback raised was announced as
failed on top of the answer, and a stream carrying only delegate_task child
progress produced no rows the silence clock could see, so a busy delegated
turn was diagnosed as hung and then killed by the idle limit.
"""

from __future__ import annotations

import threading

import hermes_worker
from hermes_worker import HermesWorker


def _callbacks(**overrides):
    callbacks = {
        "on_session": lambda _sid: None,
        "on_started": lambda: None,
        "on_activity": lambda _kind, _text: None,
        "on_complete": lambda _text: None,
        "on_failed": lambda _message: None,
        "on_done": lambda: None,
    }
    callbacks.update(overrides)
    return callbacks


def _event(event: str, payload: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {"type": event, "session_id": "s", "payload": payload},
    }


class _ScriptedTransport:
    """Replays a canned event script; nothing is sent back."""

    def __init__(self, frames: list[dict]) -> None:
        self._frames = list(frames)
        self._closed = False

    def send(self, _message: dict) -> bool:
        return not self._closed

    def receive(self, _timeout: float) -> dict | None:
        if self._closed:
            return None
        return self._frames.pop(0) if self._frames else None

    def connected(self) -> bool:
        return not self._closed and bool(self._frames)

    def failure_detail(self) -> str:
        return "the connection to Hermes was lost"

    def close(self) -> None:
        self._closed = True


def _drive(worker: HermesWorker, timeout: float = 10.0) -> list[BaseException]:
    """Run the turn on its own thread, as the window does."""
    escaped: list[BaseException] = []

    def go() -> None:
        try:
            worker.run()
        except BaseException as exc:  # noqa: BLE001 - the point of the test
            escaped.append(exc)

    thread = threading.Thread(target=go, daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), "the turn never ended"
    return escaped


def test_a_completion_callback_that_raises_is_not_announced_as_a_failed_turn():
    """The turn ended cleanly before the callback ran; the crash guard in
    run() reads _clean_end, so the listener's own failure is not the turn's."""
    completed: list[str] = []
    failures: list[str] = []

    def boom(text: str) -> None:
        completed.append(text)
        raise RuntimeError("the listener fell over")

    worker = HermesWorker(
        "hi", None, ".", "default", **_callbacks(on_complete=boom, on_failed=failures.append)
    )
    worker._transport = _ScriptedTransport(
        [_event("message.complete", {"status": "complete", "text": "the answer"})]
    )
    worker._do_run = worker._consume_turn

    escaped = _drive(worker)

    assert not escaped, f"the error escaped the worker: {escaped}"
    assert completed == ["the answer"]
    assert failures == []


class _ProgressThenComplete:
    """Subagent progress frames only, then the answer."""

    def __init__(self, count: int) -> None:
        self._count = count
        self._closed = False
        self.reads = 0

    def send(self, _message: dict) -> bool:
        return not self._closed

    def receive(self, _timeout: float) -> dict | None:
        if self._closed:
            return None
        self.reads += 1
        if self._count:
            self._count -= 1
            return _event(
                "subagent.progress",
                {"subagent_id": "a1", "goal": "dig", "text": "still digging"},
            )
        return _event("message.complete", {"status": "complete", "text": "done"})

    def connected(self) -> bool:
        return not self._closed

    def failure_detail(self) -> str:
        return "the connection to Hermes was lost"

    def close(self) -> None:
        self._closed = True


def test_a_stream_of_subagent_progress_does_not_trip_the_idle_path(monkeypatch):
    """A delegate-only turn is work, not silence.

    Subagent frames carry their rows to on_subagent, which the silence clock
    could not see, so a busy delegated turn was diagnosed as silent and given
    up. The clock is scaled down here the same way the other consume-loop
    tests scale it: each read of the clock advances it by _READ_TIMEOUT.
    """
    monkeypatch.setattr(hermes_worker, "_SILENCE_DIAGNOSTIC_SECONDS", 5.0)
    monkeypatch.setattr(hermes_worker, "_PROGRESS_NOTICE_SECONDS", 1000.0)
    monkeypatch.setattr(hermes_worker, "_CONNECTION_CHECK_SECONDS", 1000.0)
    monkeypatch.setattr(hermes_worker, "_IDLE_LIMIT", 1000.0)

    fake_clock = {"t": 0.0}

    def clock() -> float:
        fake_clock["t"] += hermes_worker._READ_TIMEOUT
        return fake_clock["t"]

    monkeypatch.setattr(hermes_worker, "_now", clock)

    rows: list[tuple[str, str]] = []
    failures: list[str] = []
    completed: list[str] = []
    worker = HermesWorker(
        "hi",
        None,
        ".",
        "default",
        **_callbacks(
            on_activity=lambda kind, text: rows.append((kind, text)),
            on_failed=failures.append,
            on_complete=completed.append,
        ),
    )
    # 20 progress frames at half a clock-second each crosses the 5s
    # diagnostic limit unless every one of them counts as activity.
    worker._transport = _ProgressThenComplete(20)
    worker._do_run = worker._consume_turn

    escaped = _drive(worker)

    assert not escaped, f"the error escaped the worker: {escaped}"
    assert completed == ["done"], f"the delegated turn did not finish: {failures}"
    assert failures == []
    silent = [text for _kind, text in rows if "silent" in text.lower()]
    assert not silent, f"a turn reporting subagent progress was diagnosed as silent: {silent}"


# --------------------------------------------------------------------------
# Work that must not start after the turn has already ended
# --------------------------------------------------------------------------


def test_a_prompt_is_not_sent_after_the_turn_has_ended():
    completed: list[str] = []
    failures: list[str] = []
    worker = HermesWorker(
        "hi",
        None,
        ".",
        "default",
        **_callbacks(on_complete=completed.append, on_failed=failures.append),
    )
    worker._ended = True
    calls: list[str] = []
    worker._call = lambda *args, **_kwargs: calls.append(args[0])

    worker._run_turn()

    assert calls == []
    assert completed == []
    assert failures == []


def test_a_slash_check_that_ends_the_turn_does_not_send_the_prompt():
    # The completion can arrive while complete.slash is being answered; the
    # turn is over by the time that check returns, so nothing follows it.
    completed: list[str] = []
    worker = HermesWorker(
        "/compact",
        None,
        ".",
        "default",
        **_callbacks(on_complete=completed.append),
    )
    worker._transport = _ScriptedTransport(
        [_event("message.complete", {"status": "complete", "text": "the answer"})]
    )
    calls: list[str] = []
    worker._call = lambda *args, **_kwargs: calls.append(args[0])

    worker._run_turn()

    assert completed == ["the answer"]
    assert calls == []


def test_compaction_is_not_started_after_the_turn_has_ended():
    completed: list[str] = []
    failures: list[str] = []
    worker = HermesWorker(
        "",
        None,
        ".",
        "default",
        compact=True,
        **_callbacks(on_complete=completed.append, on_failed=failures.append),
    )
    worker._ended = True
    calls: list[str] = []
    worker._call = lambda *args, **_kwargs: calls.append(args[0]) or {}

    worker._run_compaction()

    assert calls == []
    assert completed == []
    assert failures == []


def test_a_completion_after_the_turn_ended_is_not_announced_again():
    for flag in ("_ended", "_clean_end"):
        completed: list[str] = []
        failures: list[str] = []
        worker = HermesWorker(
            "hi",
            None,
            ".",
            "default",
            **_callbacks(on_complete=completed.append, on_failed=failures.append),
        )
        setattr(worker, flag, True)

        ended = worker._turn_complete({"status": "complete", "text": "the answer"})

        assert ended is True, flag
        assert completed == [], f"{flag} allowed the ending to be announced again"
        assert failures == [], f"{flag} allowed a failure on top of the ending"


def test_a_replay_is_not_reopened_after_the_turn_has_ended():
    failures: list[str] = []
    worker = HermesWorker(
        "", "sess-9", ".", "default", resume_only=True, **_callbacks(on_failed=failures.append)
    )
    worker._ended = True
    calls: list[str] = []
    worker._call = lambda *args, **_kwargs: calls.append(args[0]) or {}

    worker._run_replay()

    assert calls == []
    assert failures == []


def test_a_session_is_not_created_after_the_turn_has_ended():
    failures: list[str] = []
    worker = HermesWorker("hi", None, ".", "default", **_callbacks(on_failed=failures.append))
    worker._ended = True
    calls: list[str] = []
    worker._call = lambda *args, **_kwargs: calls.append(args[0]) or {}

    created = worker._ensure_session()

    assert created is False
    assert calls == []
    assert failures == []
