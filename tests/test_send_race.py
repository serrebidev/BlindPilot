"""Pressing Enter in the gap between a turn ending and the window knowing.

A worker thread dies the moment it has *queued* its last event, not when the
window has acted on it. `on_done` goes into a mailbox that `_drain_worker_events`
empties sixteen events at a time, yielding to the native queue between batches
so that keystrokes and screen-reader events get a turn — which means a waiting
Enter is dispatched *inside* that gap by design, not by bad luck.

For the whole of that gap `is_alive()` says False while turn 1's `complete`
and `done` are still pending. Anything that asked `is_alive()` therefore
believed no run was in progress, and let the next turn start on top of the
last one's unfinished bookkeeping.
"""

from __future__ import annotations

import blindpilot_app as app
from doubles import Prompt, panel_stub


class _DeadWorker:
    """Turn 1's worker: finished, queued its events, thread already gone."""

    def __init__(self):
        self.cancelled = False

    def is_alive(self) -> bool:
        return False

    def accepting_input(self) -> bool:
        return False

    def cancel(self) -> None:
        self.cancelled = True


def _panel(prompt_text: str = "the second question"):
    """A session panel mid-gap: turn 1 done, its events not yet drained."""
    panel = panel_stub(
        _worker=_DeadWorker(),
        prompt=Prompt(prompt_text),
        _attachments=[],
        _turns=[app.Turn(prompt="the first question")],
        _response_count=1,
        _stream_response=1,
        _streamed_assistant="part of the first answer",
        _session_id="session-1",
        model="",
        effort="",
        _cli_model="",
        _cli_effort="",
        cwd=".",
        mode="default",
        selected_backend=lambda: app.BACKEND_CLAUDE,
        _build_send_text=lambda text: text,
        _add_your_message=lambda *_a, **_k: None,
        _queue_worker_event=lambda *_a, **_k: None,
        _ask_questions=None,
        _on_title=lambda *_a: None,
    )
    panel._on_steer = lambda: panel.announced.append("STEERED")
    # The real methods, not stand-ins: Task 5 moved the worker-building tail
    # of _on_send into _launch_turn, and a Claude turn is now given
    # _claude_worker_extra() as well.
    panel._claude_worker_extra = lambda: app.SessionPanel._claude_worker_extra(panel)
    panel._launch_turn = lambda send_text, backend, extra: app.SessionPanel._launch_turn(
        panel, send_text, backend, extra
    )
    return panel


def test_a_send_in_the_gap_does_not_start_a_second_turn():
    """The whole bug in one call: Enter, while turn 1 is still being applied.

    If this starts a worker, turn 1's pending `complete` is applied to turn 2
    and turn 1's pending `done` sets `_worker` to None while turn 2 is running
    — which loses the reference that Stop, Steer and the tab-close cleanup all
    reach the running backend through.
    """
    panel = _panel()
    first = panel._worker

    app.SessionPanel._on_send(panel)

    assert panel._worker is first, "a second worker was started on top of the first turn"
    assert panel.announced, "the send was neither refused nor explained"
    assert "STEERED" not in panel.announced


def test_the_refusal_says_what_is_actually_happening():
    panel = _panel()

    app.SessionPanel._on_send(panel)

    assert any("finishing" in text for text in panel.announced), panel.announced


def test_the_first_turn_is_left_exactly_as_it_was():
    """Nothing about turn 1's bookkeeping may be touched by the refused send."""
    panel = _panel()

    app.SessionPanel._on_send(panel)

    assert [turn.prompt for turn in panel._turns] == ["the first question"]
    assert panel._stream_response == 1, "turn 1's response number was cleared under it"
    assert panel._streamed_assistant == "part of the first answer"
    assert panel._stopping is False
    assert panel._earcons.calls == [], f"earcons fired for a refused send: {panel._earcons.calls}"


def test_a_new_conversation_is_refused_in_the_same_gap():
    """`clear_conversation` empties `_turns`, and turn 1's `complete` is still
    queued behind it — which then writes into a list that has nothing in it."""
    panel = _panel()

    app.SessionPanel.clear_conversation(panel)

    assert panel._turns, "the conversation was cleared while a turn was still being applied"
    assert panel.announced


def test_a_send_once_the_window_has_caught_up_is_allowed():
    """The guard must not brick Send: once `done` is drained, sending works."""
    panel = _panel()
    app.SessionPanel._on_worker_finished(panel)

    assert panel._worker is None
    started: list[str] = []
    panel._queue_worker_event = lambda *_a, **_k: None

    class _Worker:
        def __init__(self, *_a, **_k):
            started.append("built")

        def start(self):
            started.append("started")

    # Substitute the worker class rather than launching a real backend.
    real = app.worker_class
    app.worker_class = lambda *_a, **_k: _Worker
    try:
        app.SessionPanel._on_send(panel)
    finally:
        app.worker_class = real

    assert started == ["built", "started"], f"the next turn never started: {started}"
