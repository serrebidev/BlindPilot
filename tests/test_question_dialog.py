"""The dialog a backend's mid-run question is asked in.

These build the real dialog rather than a stand-in, because what is being
checked is what a screen reader would read out: one radio button per answer,
a checked list where several answers are allowed, and a text box that appears
when "Other" is chosen. They skip themselves where no screen is available, so
a headless machine does not fail on a window it could never have opened.
"""

from __future__ import annotations

import pytest

from questions import Question, QuestionOption

wx = pytest.importorskip("wx")


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


def _dialog(frame, questions=QUESTIONS, announce=None):
    import question_dialog

    return question_dialog.QuestionDialog(
        frame,
        "Claude Code",
        questions,
        announce=announce if announce is not None else (lambda _text: None),
    )


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


def _intro(dlg) -> str:
    """The sentence at the top, which is what the dialog opens by reading."""
    for child in dlg.GetChildren():
        if isinstance(child, wx.StaticText):
            return child.GetLabel()
    raise AssertionError("the dialog opened with nothing said at the top")


def test_a_question_asked_through_a_tool_says_the_turn_is_paused(frame):
    dlg = _dialog(frame, QUESTIONS[:1])
    try:
        assert _intro(dlg) == "Claude Code has paused this turn to ask you a question."
    finally:
        dlg.Destroy()


def test_a_question_only_written_into_the_answer_says_the_turn_ended(frame):
    """The turn is over, and the answer typed here starts a new one.

    Nothing is holding this open: a question written into a reply sent no
    event, so there is no call waiting on it. Calling that "paused" would be
    telling somebody their turn is still running when it has finished.
    """
    import question_dialog

    dlg = question_dialog.QuestionDialog(
        frame,
        "Command Code",
        (Question(question="Which name do you want?"),),
        announce=lambda _text: None,
        ended_turn=True,
    )
    try:
        assert _intro(dlg) == "Command Code ended this turn by asking you a question."
        # No options to pick from, so the box to type in is there from the
        # start rather than behind an "Other" nobody can select.
        assert dlg._pickers == [None]
        assert dlg._texts[0].IsShown()
    finally:
        dlg.Destroy()


def test_option_previews_follow_the_choice(frame):
    questions = (
        Question(
            question="Which layout?",
            options=(
                QuestionOption("Wide", "Two columns.", preview="Wide: [list | detail]"),
                QuestionOption("Narrow", "One column."),
                QuestionOption("Stacked", "Rows.", preview="Stacked: list over detail"),
            ),
        ),
    )
    dlg = _dialog(frame, questions)
    try:
        preview = dlg._previews[0]
        assert preview is not None
        # The first option starts chosen, so its preview is what shows.
        assert preview.GetValue() == "Wide: [list | detail]"
        dlg._pickers[0].SetSelection(2)
        dlg._update_previews()
        assert preview.GetValue() == "Stacked: list over detail"
        # An option with no preview leaves the box empty, not stale.
        dlg._pickers[0].SetSelection(1)
        dlg._update_previews()
        assert preview.GetValue() == ""
    finally:
        dlg.Destroy()


def test_no_preview_box_when_no_option_has_one(frame):
    dlg = _dialog(frame)
    try:
        assert dlg._previews == [None, None]
    finally:
        dlg.Destroy()


def test_the_dialog_lives_in_its_own_module():
    import question_dialog

    assert question_dialog.QuestionDialog.__module__ == "question_dialog"


def test_choosing_other_announces_the_specific_question_and_how_to_tab_to_its_field(frame):
    said: list[str] = []
    dlg = _dialog(frame, announce=said.append)
    try:
        single, _several = dlg._pickers
        single.SetSelection(single.GetCount() - 1)
        dlg._on_choice(wx.CommandEvent())

        assert said, "choosing Other said nothing"
        assert "Tabs or spaces?" in said[-1]
        assert "Tab" in said[-1]
        # The announcement, not a focus move, tells the person where the
        # new field is: the text box never grabs focus in _on_choice.
        assert not dlg._texts[0].HasFocus()
    finally:
        dlg.Destroy()


def test_multi_select_previews_show_every_selected_option_labelled(frame):
    questions = (
        Question(
            question="Which extras?",
            multi_select=True,
            options=(
                QuestionOption("Tests", "Add tests.", preview="Runs the suite."),
                QuestionOption("Docs", "Add docs.", preview="Writes the guide."),
                QuestionOption("Lint", "Add lint."),
            ),
        ),
    )
    dlg = _dialog(frame, questions)
    try:
        picker = dlg._pickers[0]
        picker.Check(0, True)
        picker.Check(1, True)
        dlg._update_previews()

        value = dlg._previews[0].GetValue()
        assert "Tests: Runs the suite." in value
        assert "Docs: Writes the guide." in value
    finally:
        dlg.Destroy()


def test_preview_controls_stay_reachable_in_the_tab_order(frame):
    questions = (
        Question(
            question="Which layout?",
            options=(QuestionOption("Wide", "Two columns.", preview="Wide preview."),),
        ),
    )
    dlg = _dialog(frame, questions)
    try:
        preview = dlg._previews[0]
        assert preview is not None
        assert preview.IsShown()
        # A plain TextCtrl takes part in Tab traversal and accepts focus;
        # nothing here takes the preview out of the Tab order.
        assert preview.AcceptsFocus()
    finally:
        dlg.Destroy()


def test_long_question_sets_live_in_a_scrolling_window(frame):
    many = tuple(
        Question(
            question=f"Question number {index}?",
            options=(QuestionOption("Yes"), QuestionOption("No")),
        )
        for index in range(12)
    )
    dlg = _dialog(frame, many)
    try:
        scrolled = [child for child in dlg.GetChildren() if isinstance(child, wx.ScrolledWindow)]
        assert len(scrolled) == 1
        assert scrolled[0].GetScrollPixelsPerUnit()[1] > 0
        # Every question's picker is inside the scrolled window, so all of
        # them stay reachable by scrolling.
        assert all(picker.GetParent() is scrolled[0] for picker in dlg._pickers)
        assert len(dlg._pickers) == 12
    finally:
        dlg.Destroy()
