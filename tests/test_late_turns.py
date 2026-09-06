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
    panel._launch_turn = lambda send_text, backend, extra: launched.append(
        (send_text, backend, extra)
    )
    panel._queue_worker_event = lambda name, *args: panel.queued.append((name, args))
    panel.queued: list[tuple] = []
    panel._finish_stopped_turn = lambda: None
    return panel, launched


def test_the_claude_worker_is_told_which_tab_holds_it_and_how_to_wake_it():
    panel, _launched = _panel()
    extra = app.SessionPanel._claude_worker_extra(panel)
    assert extra["held_for"] is panel
    extra["on_unsolicited"]()
    assert panel.queued == [("late_turn", ())], (
        "the wake-up goes through the mailbox, onto the GUI thread"
    )


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
