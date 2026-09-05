"""The dialog a backend's mid-run question is asked in.

These build the real dialog rather than a stand-in, because what is being
checked is what a screen reader would read out: one radio button per answer,
a checked list where several answers are allowed, and a text box that appears
when "Other" is chosen. They skip themselves where no screen is available, so
a headless machine does not fail on a window it could never have opened.
"""

from __future__ import annotations

import pytest

from agent_backends import Question, QuestionOption

wx = pytest.importorskip("wx")


@pytest.fixture(scope="module")
def wx_app():
    try:
        application = wx.App(False)
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"no display for wxPython: {exc}")
    yield application


@pytest.fixture
def frame(wx_app):
    window = wx.Frame(None)
    try:
        yield window
    finally:
        window.Destroy()


QUESTIONS = (
    Question(
        question="Tabs or spaces?",
        header="Indent",
        options=(
            QuestionOption("Tabs", "Tab characters - one per level."),
            QuestionOption("Spaces", "Four spaces per level."),
        ),
    ),
    Question(
        question="Which extras?",
        header="Extras",
        multi_select=True,
        options=(
            QuestionOption("Tests", "Add tests."),
            QuestionOption("Docs", "Add docs."),
        ),
    ),
)


def _dialog(frame, questions=QUESTIONS):
    import blindpilot_app

    return blindpilot_app.QuestionDialog(frame, "claude", questions)


def test_one_answer_gets_radio_buttons_and_several_gets_a_checked_list(frame):
    dlg = _dialog(frame)
    try:
        single, several = dlg._pickers
        assert isinstance(single, wx.RadioBox)
        assert isinstance(several, wx.CheckListBox)
        # Each answer reads as one line: the label, then what choosing it means.
        assert single.GetString(0) == "Tabs: Tab characters - one per level."
        # Every backend leaves the "Other" answer to the client, so the dialog
        # is where it is added - once, at the end of each list.
        assert single.GetString(single.GetCount() - 1) == dlg.OTHER
        assert several.GetString(several.GetCount() - 1) == dlg.OTHER
    finally:
        dlg.Destroy()


def test_answers_come_back_as_the_backends_own_labels(frame):
    dlg = _dialog(frame)
    try:
        single, several = dlg._pickers
        single.SetSelection(1)
        several.Check(0, True)
        several.Check(1, True)
        # Not the line the dialog drew: the label the backend asked with.
        assert dlg.answers() == [["Spaces"], ["Tests", "Docs"]]
    finally:
        dlg.Destroy()


def test_choosing_other_opens_a_box_to_type_in(frame):
    dlg = _dialog(frame)
    try:
        single, _several = dlg._pickers
        assert not dlg._texts[0].IsShown()

        single.SetSelection(single.GetCount() - 1)
        dlg._on_choice(wx.CommandEvent())

        assert dlg._texts[0].IsShown()
        dlg._texts[0].SetValue("  two spaces, always  ")
        assert dlg.answers()[0] == ["two spaces, always"]
    finally:
        dlg.Destroy()


def test_a_typed_answer_can_be_added_to_the_ones_on_the_list(frame):
    dlg = _dialog(frame)
    try:
        _single, several = dlg._pickers
        several.Check(0, True)
        several.Check(several.GetCount() - 1, True)
        dlg._on_choice(wx.CommandEvent())
        dlg._texts[1].SetValue("Benchmarks")

        assert dlg.answers()[1] == ["Tests", "Benchmarks"]
    finally:
        dlg.Destroy()


def test_a_question_that_takes_no_typed_answer_does_not_offer_one(frame):
    fixed = (
        Question(
            question="Which one?",
            options=(QuestionOption("A"), QuestionOption("B")),
            allow_custom=False,
        ),
    )
    dlg = _dialog(frame, fixed)
    try:
        (picker,) = dlg._pickers
        assert [picker.GetString(item) for item in range(picker.GetCount())] == ["A", "B"]
        assert dlg._wants_custom(0) is False
    finally:
        dlg.Destroy()


def _press_enter(entry) -> None:
    """The event a text box with wx.TE_PROCESS_ENTER raises when Enter is hit."""
    event = wx.CommandEvent(wx.EVT_TEXT_ENTER.typeId, entry.GetId())
    event.SetEventObject(entry)
    entry.GetEventHandler().ProcessEvent(event)


def test_enter_in_the_typed_answer_box_sends_the_answer(frame):
    """The box asks for Enter and then does nothing with it.

    `wx.TE_PROCESS_ENTER` takes Enter away from the dialog's default button and
    hands it to the control, so without a handler the key is simply eaten. This
    is the one dialog that opens unannounced in the middle of a run and holds
    the turn until it is answered, and the obvious way out of it did nothing.
    """
    dlg = _dialog(frame)
    try:
        single, several = dlg._pickers
        single.SetSelection(single.GetCount() - 1)
        several.Check(0, True)
        dlg._on_choice(wx.CommandEvent())
        dlg._texts[0].SetValue("two spaces, always")
        ended: list[int] = []
        dlg.EndModal = ended.append

        _press_enter(dlg._texts[0])

        assert ended == [wx.ID_OK], "Enter did nothing at all"
        assert dlg.answers() == [["two spaces, always"], ["Tests"]]
    finally:
        dlg.Destroy()


def test_enter_does_not_send_a_question_that_has_no_answer_yet(frame):
    """Enter has to refuse exactly what the Send answer button refuses."""
    dlg = _dialog(frame)
    try:
        single, _several = dlg._pickers
        single.SetSelection(single.GetCount() - 1)
        dlg._on_choice(wx.CommandEvent())
        dlg._texts[0].SetValue("two spaces, always")
        # The second question is still unanswered.
        ended: list[int] = []
        dlg.EndModal = ended.append

        _press_enter(dlg._texts[0])

        assert ended == [], "a half-filled answer was sent to the backend"
    finally:
        dlg.Destroy()


def test_a_question_with_no_choices_offers_only_the_text_box(frame):
    """A secret or sudo prompt, or a clarify question without choices.

    Before, such a question got a RadioBox whose only entry was "Other". A
    RadioBox starts with item 0 selected and with one entry the selection can
    never change, so the EVT_RADIOBOX that shows the text box never fired,
    and the dialog could not be answered at all. The text box is the whole
    question here, so it is shown from the start and takes focus.
    """
    dlg = _dialog(frame, (Question(question="API key?", secret=True),))
    try:
        assert dlg._pickers == [None]
        assert dlg._texts[0].IsShown()
        assert dlg._texts[0].GetWindowStyleFlag() & wx.TE_PASSWORD
        assert dlg._answered() is False

        dlg._texts[0].SetValue("hunter2")

        assert dlg._answered() is True
        assert dlg.answers() == [["hunter2"]]
    finally:
        dlg.Destroy()


def test_a_free_text_answer_is_sent_with_enter(frame):
    dlg = _dialog(frame, (Question(question="Which branch?"),))
    try:
        assert not dlg._texts[0].GetWindowStyleFlag() & wx.TE_PASSWORD
        dlg._texts[0].SetValue("main")
        ended: list[int] = []
        dlg.EndModal = ended.append

        _press_enter(dlg._texts[0])

        assert ended == [wx.ID_OK]
        assert dlg.answers() == [["main"]]
    finally:
        dlg.Destroy()
