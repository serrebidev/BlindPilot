"""A turn that ends by asking, without ever asking through a question tool.

Every backend opens BlindPilot's question dialog from a structured event of
its own. A model that writes its question into its answer sends none of them,
so nothing is announced and no dialog opens: the turn ends, and somebody
listening is given no sign that a turn is waiting on them. That is what an
interview skill does as a matter of course -- "grill me" is the one this was
found through -- and on Command Code, which withholds ask_user_question from
headless runs, it is the only way a question can ever arrive.

These cover the decision and the wiring around it. Whether a given piece of
prose counts as a question is tests/test_trailing_question.py.
"""

from __future__ import annotations

import blindpilot_app as app


class _Panel:
    """Only the parts of SessionPanel the methods under test touch."""

    def __init__(self, **kwargs):
        self._structured_question_asked = False
        self._replaying = False
        self._question_in_the_answer = ""
        self._stopping = False
        self._worker = None
        self._turns = []
        self._pending_messages = []
        self._queue_paused = False
        self._late_turn_waiting = None
        self.send_btn = None
        self.steer_btn = None
        self.stop_btn = None
        self.announced = []
        self.sent_queued = 0
        self._earcons = type("Earcons", (), {"stop_progress": lambda self: None})()
        self.__dict__.update(kwargs)

    def _hide_working(self):
        pass

    def _announce(self, text, urgent=False):
        self.announced.append(text)

    def _send_queued_message(self):
        self.sent_queued += 1

    def _finish_stopped_turn(self):
        pass

    def _start_late_turn(self, generation):
        pass

    def _ask_question_left_in_the_answer(self, question):
        # Stood in for so the test can see what was posted without a dialog.
        pass


def _posted(monkeypatch):
    """Capture what _on_worker_finished posts back to the GUI thread."""
    calls = []
    monkeypatch.setattr(app.wx, "CallAfter", lambda fn, *a, **k: calls.append((fn, a)))
    return calls


# ----- Which questions are picked up -----
def test_a_question_left_in_the_answer_is_picked_up():
    found = app.question_left_in_the_answer(
        "Which name do you want?", asked_properly=False, replaying=False
    )
    assert found == "Which name do you want?"


def test_a_backend_that_asked_properly_is_not_asked_over_again():
    # The tool question was announced and answered in a dialog already, and
    # the answer that follows usually repeats it back.
    found = app.question_left_in_the_answer("Which name?", asked_properly=True, replaying=False)
    assert found == ""


def test_a_reopened_conversation_does_not_re_ask_its_own_history():
    found = app.question_left_in_the_answer("Which name?", asked_properly=False, replaying=True)
    assert found == ""


# ----- What happens when the turn is done -----
def test_the_question_is_put_once_the_turn_is_fully_finished(monkeypatch):
    posted = _posted(monkeypatch)
    monkeypatch.setattr(app.SETTINGS, "ask_written_questions", True)
    panel = _Panel(_question_in_the_answer="Which name do you want?")

    app.SessionPanel._on_worker_finished(panel)

    assert [args for fn, args in posted] == [("Which name do you want?",)]
    assert posted[0][0].__name__ == "_ask_question_left_in_the_answer"


def test_the_question_is_cleared_so_the_next_turn_does_not_re_ask_it(monkeypatch):
    _posted(monkeypatch)
    monkeypatch.setattr(app.SETTINGS, "ask_written_questions", True)
    panel = _Panel(_question_in_the_answer="Which name?", _structured_question_asked=True)

    app.SessionPanel._on_worker_finished(panel)

    assert panel._question_in_the_answer == ""
    assert panel._structured_question_asked is False


def test_turning_it_off_leaves_the_turn_to_end_quietly(monkeypatch):
    posted = _posted(monkeypatch)
    monkeypatch.setattr(app.SETTINGS, "ask_written_questions", False)
    panel = _Panel(_question_in_the_answer="Which name?")

    app.SessionPanel._on_worker_finished(panel)

    assert posted == []


def test_a_queued_message_is_the_answer_and_no_dialog_opens(monkeypatch):
    # Somebody who lined up what to say next has already answered. Opening a
    # dialog over their own queued message would ask them to say it twice.
    posted = _posted(monkeypatch)
    monkeypatch.setattr(app.SETTINGS, "ask_written_questions", True)
    panel = _Panel(_question_in_the_answer="Which name?", _pending_messages=["use toml"])

    app.SessionPanel._on_worker_finished(panel)

    assert panel.sent_queued == 1
    assert posted == []


def test_a_turn_waiting_to_start_is_not_interrupted_by_a_dialog(monkeypatch):
    posted = _posted(monkeypatch)
    monkeypatch.setattr(app.SETTINGS, "ask_written_questions", True)
    panel = _Panel(_question_in_the_answer="Which name?", _late_turn_waiting=3)

    app.SessionPanel._on_worker_finished(panel)

    assert posted == []


# ----- Answering it -----
class _Prompt:
    def __init__(self):
        self.value = ""

    def SetValue(self, text):
        self.value = text


class _Answering(_Panel):
    def __init__(self, answers, **kwargs):
        super().__init__(**kwargs)
        self._answers = answers
        self.prompt = _Prompt()
        self.shown = []
        self.sent = 0

    def __bool__(self):
        return True

    def _run_in_progress(self):
        return False

    def _show_question_dialog(self, questions, *, ended_turn=False):
        self.shown.append((list(questions), ended_turn))
        return self._answers

    def _on_send(self):
        self.sent += 1


def test_the_answer_is_sent_as_the_next_turn():
    panel = _Answering([["Call it blindpilot.toml"]])

    app.SessionPanel._ask_question_left_in_the_answer(panel, "Which name do you want?")

    assert panel.prompt.value == "Call it blindpilot.toml"
    assert panel.sent == 1


def test_the_question_is_shown_as_one_that_ended_its_turn():
    # The turn is over, so nothing is being held open waiting on this. Saying
    # it was "paused" would be telling somebody their turn is still running.
    panel = _Answering([["yes"]])

    app.SessionPanel._ask_question_left_in_the_answer(panel, "Ready?")

    questions, ended_turn = panel.shown[0]
    assert ended_turn is True
    assert questions[0].question == "Ready?"
    assert questions[0].options == (), "a written question has no options to offer"


def test_closing_the_dialog_sends_nothing():
    panel = _Answering(None)

    app.SessionPanel._ask_question_left_in_the_answer(panel, "Ready?")

    assert panel.prompt.value == ""
    assert panel.sent == 0


def test_an_answer_of_only_spaces_is_not_a_turn():
    panel = _Answering([["   "]])

    app.SessionPanel._ask_question_left_in_the_answer(panel, "Ready?")

    assert panel.sent == 0


def test_a_message_sent_in_the_meantime_is_the_answer():
    # The dialog is posted, not opened on the spot, so something can be typed
    # and sent between the turn ending and this running.
    panel = _Answering([["yes"]])
    panel._run_in_progress = lambda: True

    app.SessionPanel._ask_question_left_in_the_answer(panel, "Ready?")

    assert panel.shown == []
    assert panel.sent == 0
