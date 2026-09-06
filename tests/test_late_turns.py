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
    panel._late_turn_waiting = None
    panel._claude_generation = 0
    panel._session_backend = BACKEND_CLAUDE
    panel._held_hermes = None
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
    panel._hide_working = lambda: panel._earcons.calls.append("indicator off")
    panel._run_in_progress = lambda: app.SessionPanel._run_in_progress(panel)
    panel._claude_worker_extra = lambda: app.SessionPanel._claude_worker_extra(panel)
    panel._launch_turn = lambda send_text, backend, extra: launched.append(
        (send_text, backend, extra)
    )
    panel._start_late_turn = lambda generation: app.SessionPanel._start_late_turn(panel, generation)
    panel._queue_worker_event = lambda name, *args: panel.queued.append((name, args))
    panel.queued: list[tuple] = []
    panel._finish_stopped_turn = lambda: None
    return panel, launched


def test_the_claude_worker_is_told_which_tab_holds_it_and_how_to_wake_it():
    panel, _launched = _panel()
    extra = app.SessionPanel._claude_worker_extra(panel)
    assert extra["held_for"] is panel
    extra["on_unsolicited"]()
    assert panel.queued == [("late_turn", (0,))], (
        "the wake-up goes through the mailbox, onto the GUI thread, "
        "naming the conversation it belongs to"
    )


def test_a_late_turn_starts_a_prompt_less_claude_turn_with_the_earcon_running():
    panel, launched = _panel()
    app.SessionPanel._start_late_turn(panel, 0)
    assert launched and launched[0][0] is None
    assert launched[0][1] == BACKEND_CLAUDE
    assert launched[0][2]["held_for"] is panel
    assert "start" in panel._earcons.calls and "indicator" in panel._earcons.calls
    assert panel._streamed_assistant == "" and panel._assistant_narrated_this_turn is False
    assert panel._turns and panel._turns[-1].prompt == "", "the answer needs a turn to land in"
    assert any("background" in text.lower() for text in panel.announced)
    assert not panel.send_btn.enabled


def test_a_late_turn_waits_while_a_turn_is_still_finishing_and_starts_after_it():
    class _Worker:
        pass

    panel, launched = _panel(worker=_Worker())
    app.SessionPanel._start_late_turn(panel, 0)
    assert not launched and panel._late_turn_waiting == 0
    app.SessionPanel._on_worker_finished(panel)
    assert panel._worker is None
    assert launched and launched[0][0] is None
    assert panel._late_turn_waiting is None


def test_the_mailbox_routes_the_wake_up_to_the_late_turn():
    import inspect

    source = inspect.getsource(app.SessionPanel._drain_worker_events)
    assert '"late_turn"' in source and "_start_late_turn" in source


def test_a_late_turn_queued_before_the_conversation_changed_is_dropped():
    """A wake-up belongs to the conversation whose process sent it.

    Between the idle sink firing and the mailbox draining, the tab can have
    started a new conversation, restored an old one or moved to another
    backend. The event that was queued would then open a late turn on a
    conversation the report has nothing to do with.
    """
    panel, launched = _panel()
    wake = app.SessionPanel._claude_worker_extra(panel)["on_unsolicited"]
    wake()
    ((_name, args),) = panel.queued

    app.SessionPanel._drop_held_backends(panel)
    app.SessionPanel._start_late_turn(panel, int(args[0]))

    assert not launched, "a late turn opened on a conversation it was not woken for"
    assert not panel._turns

    # The conversation running now wakes the tab in its own name, and is taken.
    fresh = app.SessionPanel._claude_worker_extra(panel)["on_unsolicited"]
    fresh()
    assert panel.queued[-1][1][0] != args[0]
    app.SessionPanel._start_late_turn(panel, int(panel.queued[-1][1][0]))
    assert launched and launched[0][0] is None


def test_a_late_turn_does_not_start_on_a_tab_that_now_runs_another_backend():
    panel, launched = _panel()
    panel._session_backend = "codex"
    app.SessionPanel._start_late_turn(panel, 0)
    assert not launched


def test_the_wake_up_does_not_keep_the_tab_alive():
    """The pool holds the idle sink for as long as it holds the process.

    A tab closed without teardown is collected only if nothing the process
    holds points back at it, and the sink was a lambda over `self`.
    """
    import gc
    import weakref

    panel, _launched = _panel()
    # Only the callback, which is the part the session keeps.
    wake = app.SessionPanel._claude_worker_extra(panel)["on_unsolicited"]
    ref = weakref.ref(panel)
    del panel
    gc.collect()

    assert ref() is None, "the idle sink held the tab it was made for"
    wake()  # a wake-up for a tab that is gone does nothing rather than raise


def test_a_late_turn_that_read_nothing_leaves_no_empty_entry():
    """A late turn appends a turn to answer into; an empty one is not a turn.

    The transcript would otherwise grow a blank question with a blank answer
    every time a wake-up turned out to have nothing behind it.
    """
    panel, _launched = _panel()
    panel._turns = [app.Turn(prompt="what did it find", response="a file"), app.Turn(prompt="")]

    app.SessionPanel._on_worker_finished(panel)

    assert [turn.prompt for turn in panel._turns] == ["what did it find"]


def test_a_late_turn_that_answered_keeps_its_entry():
    panel, _launched = _panel()
    panel._turns = [app.Turn(prompt="", response="the agent reported back")]

    app.SessionPanel._on_worker_finished(panel)

    assert len(panel._turns) == 1


def test_a_typed_turn_with_no_answer_is_left_alone():
    panel, _launched = _panel()
    panel._turns = [app.Turn(prompt="say nothing")]

    app.SessionPanel._on_worker_finished(panel)

    assert [turn.prompt for turn in panel._turns] == ["say nothing"]
