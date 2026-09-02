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


class _Prompt:
    def __init__(self, text: str):
        self._text = text

    def GetValue(self) -> str:
        return self._text

    def SetValue(self, text: str) -> None:
        self._text = text


class _Button:
    def __init__(self):
        self.enabled = True

    def Enable(self, value: bool = True) -> None:
        self.enabled = bool(value)

    def Disable(self) -> None:
        self.enabled = False

    def __bool__(self) -> bool:
        return True


class _Earcons:
    def __init__(self):
        self.events: list[str] = []

    def play_send(self) -> None:
        self.events.append("send")

    def start_progress(self) -> None:
        self.events.append("start")

    def stop_progress(self) -> None:
        self.events.append("stop")

    def play_received(self) -> None:
        self.events.append("received")


def _panel(prompt_text: str = "the second question"):
    """A session panel mid-gap: turn 1 done, its events not yet drained."""
    panel = type("PanelStub", (), {})()
    panel._worker = _DeadWorker()
    panel.prompt = _Prompt(prompt_text)
    panel._attachments = []
    panel._turns = [app.Turn(prompt="the first question")]
    panel._rows = []
    panel._response_count = 1
    panel._stream_response = 1
    panel._streamed_assistant = "part of the first answer"
    panel._stopping = False
    panel._session_id = "session-1"
    panel._session_backend = app.BACKEND_CLAUDE
    panel._assistant_narrated_this_turn = True
    panel.model = ""
    panel.effort = ""
    panel._cli_model = ""
    panel._cli_effort = ""
    panel.cwd = "."
    panel.mode = "default"
    panel._earcons = _Earcons()
    panel.send_btn = _Button()
    panel.steer_btn = _Button()
    panel.stop_btn = _Button()
    panel.announced = []
    panel.status = []
    panel._announce = lambda text: (panel.announced.append(text), panel.status.append(text))
    panel._set_status = lambda text: panel.status.append(text)
    panel._refresh_list = lambda: None
    panel._say = lambda _text: False
    # The real one, not a stand-in: it is the thing under test.
    panel._run_in_progress = lambda: app.SessionPanel._run_in_progress(panel)
    panel.selected_backend = lambda: app.BACKEND_CLAUDE
    panel._on_steer = lambda: panel.announced.append("STEERED")
    panel._build_send_text = lambda text: text
    panel._add_your_message = lambda *_a, **_k: None
    panel._queue_worker_event = lambda *_a, **_k: None
    panel._ask_questions = None
    panel._on_title = lambda *_a: None
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
    assert panel._earcons.events == [], f"earcons fired for a refused send: {panel._earcons.events}"


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
