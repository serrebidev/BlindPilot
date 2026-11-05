"""The question dialog a backend's mid-run question is asked in.

Extracted from ``blindpilot_app.py`` so the dialog's UI behaviour can be
changed and reviewed independently. The dialog receives the backend's
display name and the ``announce`` callback as constructor dependencies
rather than importing application globals. Assistive-technology
verification of this dialog is covered by the automated behaviour tests;
per issue #136's agreed exception, no real screen-reader verification is
represented as completed for this extraction.
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

import wx

from questions import Question, QuestionOption

# Dialog margin, matching blindpilot_app.PAD_DIALOG (kept local so this
# module does not import the application module).
PAD_DIALOG = 12


def _question_choice(option: QuestionOption) -> str:
    """One answer as a single line, because that is how it is read aloud.

    A colon rather than a dash: the descriptions have dashes of their own, and
    two kinds of dash in one line is a sentence nobody can follow by ear.
    """
    if not option.description:
        return option.label
    return f"{option.label}: {option.description}"


class QuestionDialog(wx.Dialog):
    """A backend's own mid-run question, asked as a dialog.

    Every backend BlindPilot drives can stop a turn to ask something, and all
    four ask the same shape of question: some text, a short list of answers,
    and permission to type one of your own instead. That last one is the
    "Other" every one of their tools tells the model not to write itself, and
    it is offered here as the final choice in each list: picking it opens a
    text box underneath.

    A question that takes one answer gets radio buttons, one that takes several
    gets a checked list. A question with no choices at all (a secret, a sudo
    password, a clarification) is only the text box, shown from the start. Esc
    leaves the question unanswered, which each adapter reports to its backend
    in whatever way that backend understands.
    """

    OTHER = "Other: type your own answer"

    def __init__(
        self,
        parent: wx.Window,
        backend_display_name: str,
        questions: Sequence[Question],
        *,
        announce: Callable[[str], None],
        ended_turn: bool = False,
    ):
        plural = "s" if len(questions) > 1 else ""
        super().__init__(
            parent,
            title=f"{backend_display_name} question{plural}",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._announce = announce
        self._questions = list(questions)
        self._pickers: list[Optional[wx.Window]] = []
        self._texts: list[wx.TextCtrl] = []
        self._labels: list[wx.StaticText] = []
        self._previews: list[Optional[wx.TextCtrl]] = []
        pad = self.FromDIP(PAD_DIALOG)
        # The typed answer sits one dialog margin further in than its
        # question, so it reads as belonging to it.
        indent = 2 * pad
        wrap = self.FromDIP(560)

        sizer = wx.BoxSizer(wx.VERTICAL)
        # Long question sets scroll: the questions live in a scrolled
        # window between the intro and the buttons, so every question and
        # action stays reachable however many questions there are.
        self._scroll = wx.ScrolledWindow(self)
        self._scroll.SetScrollRate(0, 20)
        questions_sizer = wx.BoxSizer(wx.VERTICAL)
        self._scroll.SetSizer(questions_sizer)
        # A question asked through the backend's own tool holds the turn open
        # while this dialog is up; one only written into the answer does not,
        # because the turn is already over. Saying "paused" for the second
        # would be telling somebody their turn is still running when it has
        # finished, and the answer they type starts a new one.
        opening = (
            f"{backend_display_name} ended this turn by asking"
            if ended_turn
            else f"{backend_display_name} has paused this turn to ask"
        )
        intro = wx.StaticText(
            self,
            label=(f"{opening} {'you these questions' if plural else 'you a question'}."),
        )
        sizer.Add(intro, 0, wx.ALL, pad)

        for index, question in enumerate(questions):
            title = question.question
            if len(questions) > 1:
                title = f"{index + 1} of {len(questions)}. {title}"
            if question.header:
                title = f"{title} ({question.header})"
            choices = [_question_choice(option) for option in question.options]
            if question.allow_custom:
                choices.append(self.OTHER)
            picker: Optional[wx.Window]
            if not question.options:
                # Nothing to pick from, so no picker. A RadioBox with "Other" as
                # its only entry starts selected and can never change, so the
                # event that shows the text box never fired and the question
                # could not be answered.
                picker = None
                heading = wx.StaticText(self._scroll, label=title)
                heading.Wrap(wrap)
                questions_sizer.Add(heading, 0, wx.LEFT | wx.RIGHT | wx.TOP, pad)
            elif question.multi_select:
                # A checked list is what a screen reader reads as "check box,
                # not checked" per line, which is what "pick as many as you
                # like" has to sound like.
                heading = wx.StaticText(self._scroll, label=title)
                heading.Wrap(wrap)
                picker = wx.CheckListBox(self._scroll, choices=choices)
                picker.SetName(question.question)
                picker.Bind(wx.EVT_CHECKLISTBOX, self._on_choice)
                questions_sizer.Add(heading, 0, wx.LEFT | wx.RIGHT | wx.TOP, pad)
                questions_sizer.Add(picker, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
            else:
                picker = wx.RadioBox(
                    self._scroll,
                    label=title,
                    choices=choices,
                    majorDimension=1,
                    style=wx.RA_SPECIFY_COLS,
                )
                picker.SetName(question.question)
                picker.Bind(wx.EVT_RADIOBOX, self._on_choice)
                questions_sizer.Add(picker, 0, wx.EXPAND | wx.ALL, pad)
            self._pickers.append(picker)

            label = wx.StaticText(
                self._scroll, label="&Your answer:" if picker is None else "&Your own answer:"
            )
            # Codex can mark a question whose answer is a secret. Masking it is
            # the whole of what that means here: the transcript already keeps
            # the fact of an answer rather than the answer.
            style = wx.TE_PROCESS_ENTER | (wx.TE_PASSWORD if question.secret else 0)
            entry = wx.TextCtrl(self._scroll, style=style)
            entry.SetName(f"Your answer to {question.question}")
            # TE_PROCESS_ENTER takes Enter away from the dialog's default
            # button and gives it to the box, so without this the key does
            # nothing whatsoever in the one place a turn waits to be let go.
            entry.Bind(wx.EVT_TEXT_ENTER, self._on_text_enter)
            label.Show(picker is None)
            entry.Show(picker is None)
            questions_sizer.Add(label, 0, wx.LEFT | wx.RIGHT, indent)
            questions_sizer.Add(entry, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, indent)
            self._labels.append(label)
            self._texts.append(entry)

            # Some options carry a preview: what picking that one would
            # mean, as a mock-up or a sketch. It follows the choice, so it
            # sits under the answer box and is refilled as the choice moves.
            preview: Optional[wx.TextCtrl] = None
            if any(option.preview for option in question.options):
                questions_sizer.Add(
                    wx.StaticText(self._scroll, label="Pre&view:"),
                    0,
                    wx.LEFT | wx.RIGHT,
                    indent,
                )
                preview = wx.TextCtrl(
                    self._scroll, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP
                )
                preview.SetName(f"Preview for {question.question}")
                preview.SetMinSize(self.FromDIP(wx.Size(520, 110)))
                questions_sizer.Add(preview, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, indent)
            self._previews.append(preview)

        self._update_previews()
        sizer.Add(self._scroll, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        ok = self.FindWindowById(wx.ID_OK)
        if isinstance(ok, wx.Button):
            ok.SetLabel("&Send answer" + plural)
        cancel = self.FindWindowById(wx.ID_CANCEL)
        if isinstance(cancel, wx.Button):
            cancel.SetLabel("&Do not answer")
        if buttons is not None:
            sizer.Add(buttons, 0, wx.EXPAND | wx.ALL, pad)
        self.SetSizer(sizer)
        self._scroll.FitInside()
        self.Fit()
        display_height = wx.GetDisplaySize().GetHeight()
        if self.GetSize().GetHeight() > display_height:
            size = self.GetSize()
            size.SetHeight(display_height)
            self.SetSize(size)

        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        if self._pickers:
            self._picker_or_text(0).SetFocus()
        self.CentreOnParent()

    def _picker_or_text(self, index: int) -> wx.Window:
        """The control that answers question `index`: its picker, or its box."""
        picker = self._pickers[index]
        return self._texts[index] if picker is None else picker

    def _picked(self, index: int) -> list[int]:
        """Which entries are chosen for one question, by position in its list.

        By position rather than by what the line says: the line is
        "label — description", and a label with a dash of its own in it would
        not survive being taken apart again.
        """
        picker = self._pickers[index]
        if isinstance(picker, wx.CheckListBox):
            return list(picker.GetCheckedItems())
        if isinstance(picker, wx.RadioBox) and picker.GetSelection() != wx.NOT_FOUND:
            return [picker.GetSelection()]
        return []

    def _wants_custom(self, index: int) -> bool:
        """Whether a typed answer is wanted: "Other" is chosen, or there is no list."""
        question = self._questions[index]
        if not question.options:
            return True
        return question.allow_custom and len(question.options) in self._picked(index)

    def _preview_text(self, index: int) -> str:
        """The previews of the options currently picked for one question.

        A single-answer question shows the picked option's preview. A
        multi-select question shows every picked option's preview, each
        labelled with its option and separated from the next, so the
        consequences of each selection can be reviewed together. "Other"
        and a question with nothing picked have none, and the box reads
        empty rather than keeping the last option's preview under a
        different choice.
        """
        question = self._questions[index]
        picked_previews = [
            question.options[picked]
            for picked in self._picked(index)
            if picked < len(question.options) and question.options[picked].preview
        ]
        if question.multi_select:
            return "\n\n".join(f"{option.label}: {option.preview}" for option in picked_previews)
        return picked_previews[0].preview if picked_previews else ""

    def _update_previews(self) -> None:
        for index, preview in enumerate(self._previews):
            if preview is not None:
                preview.ChangeValue(self._preview_text(index))
                preview.SetInsertionPoint(0)

    def _on_choice(self, event: wx.CommandEvent) -> None:
        """Show the text box as soon as "Other" is chosen, and say so."""
        event.Skip()
        self._update_previews()
        changed = False
        for index in range(len(self._questions)):
            wanted = self._wants_custom(index)
            if wanted == self._texts[index].IsShown():
                continue
            self._labels[index].Show(wanted)
            self._texts[index].Show(wanted)
            changed = True
        if not changed:
            return
        self.Layout()
        self._scroll.FitInside()
        for index in range(len(self._questions)):
            if self._texts[index].IsShown() and not self._texts[index].GetValue():
                # Focus stays on the choice control; the announcement names
                # the question and says how to reach its custom-answer field.
                self._announce(
                    f"Your own answer for {self._questions[index].question}. "
                    "Tab to the Your own answer field to type it."
                )
                break

    def _on_key(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        event.Skip()

    def _answered(self) -> bool:
        """Whether every question has an answer, saying what is missing if not.

        Focus lands on the question that is short, so the next keystroke goes
        somewhere useful rather than leaving the person to find it.
        """
        for index, question in enumerate(self._questions):
            if self._wants_custom(index) and not self._texts[index].GetValue().strip():
                self._announce("Error: type your own answer, or pick one of the choices")
                self._texts[index].SetFocus()
                return False
            if not self._chosen(index):
                self._announce(f"Error: {question.question} has no answer yet")
                self._picker_or_text(index).SetFocus()
                return False
        return True

    def _on_ok(self, event: wx.CommandEvent) -> None:
        """Refuse a half-filled answer rather than send the backend a blank."""
        if self._answered():
            event.Skip()

    def _on_text_enter(self, _event: wx.CommandEvent) -> None:
        """Enter in the answer box sends it, the way it does in the prompt."""
        if self._answered():
            self.EndModal(wx.ID_OK)

    def _chosen(self, index: int) -> list[str]:
        """The answers picked for one question, with "Other" resolved to text."""
        question = self._questions[index]
        answers: list[str] = []
        positions = self._picked(index) if question.options else [0]
        for position in positions:
            if position < len(question.options):
                # The backend wants its own label back, not the line the
                # dialog drew from it.
                answers.append(question.options[position].label)
                continue
            typed = self._texts[index].GetValue().strip()
            if typed:
                answers.append(typed)
        return answers

    def answers(self) -> list[list[str]]:
        """One list of answers per question, in the order they were asked."""
        return [self._chosen(index) for index in range(len(self._questions))]
