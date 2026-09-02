"""Reading the response list while a turn is still writing to it.

`_refresh_list` rebuilt the whole control every time it ran: `Set(labels)` on
the list box, `ChangeValue(text)` on the text view. Both throw the contents
away and put them back, which loses the selection — so the row the reader was
on had to be restored afterwards, and *restoring* it is the problem. Setting
the selection on a native list box fires the accessibility event NVDA reads
the row from, and moving the insertion point in the text view is a caret move
NVDA reads the line from.

During a turn this ran once per drained batch. The row somebody was reading
was announced to them again every few hundredths of a second, on top of the
narration of the turn itself, which is not a list anybody can read.

Nothing about the rows requires it. Output is appended, so an append leaves
the selection where it is and nothing that speaks has to happen at all.
"""

from __future__ import annotations


import blindpilot_app as app
from markdown_rows import Row


class _ListBox:
    """A native list box, in the respects that matter here.

    `Set` clearing the selection is the wx behaviour the old code was written
    around; `selection_events` counts the announcements a real one would cause.
    """

    def __init__(self, labels=(), selection=-1):
        self.labels = list(labels)
        self.selection = selection
        self.rebuilds = 0
        self.selection_events = 0

    def Set(self, labels):
        self.labels = list(labels)
        self.selection = app.wx.NOT_FOUND
        self.rebuilds += 1

    def AppendItems(self, labels):
        self.labels.extend(labels)

    def SetSelection(self, index):
        self.selection = index
        self.selection_events += 1

    def GetSelection(self):
        return self.selection

    def GetCount(self):
        return len(self.labels)


class _TextView:
    """A read-only text control, likewise."""

    def __init__(self, text=""):
        self.text = text
        self.insertion = 0
        self.rebuilds = 0
        self.caret_moves = 0

    def ChangeValue(self, text):
        self.text = text
        self.insertion = 0
        self.rebuilds += 1

    def AppendText(self, text):
        self.text += text
        self.insertion = len(self.text)

    def SetInsertionPoint(self, pos):
        if pos != self.insertion:
            self.caret_moves += 1
        self.insertion = pos

    def GetInsertionPoint(self):
        return self.insertion

    def GetLastPosition(self):
        return len(self.text)

    def GetNumberOfLines(self):
        # A real TextCtrl reports one (empty) line before anything is written.
        return len(self.text.split("\n"))

    def PositionToXY(self, pos):
        before = self.text[:pos]
        line = before.count("\n")
        return True, len(before) - (before.rfind("\n") + 1), line

    def XYToPosition(self, col, line):
        lines = self.text.split("\n")
        return sum(len(entry) + 1 for entry in lines[:line]) + col


def _rows(*labels):
    return [Row(kind="prose", label=label, payload=label, response_number=1) for label in labels]


def _panel(monkeypatch, *, text_view, displayed, rows, control):
    monkeypatch.setattr(app.SETTINGS, "text_view", text_view)
    panel = type("PanelStub", (), {})()
    panel._rows = rows
    panel._displayed = list(displayed)
    panel._search_term = ""
    if text_view:
        panel.responses_text = control
    else:
        panel.responses = control
    panel._selected_row = lambda: app.SessionPanel._selected_row(panel)
    panel._select_row = lambda index: app.SessionPanel._select_row(panel, index)
    panel._append_rows = lambda labels: app.SessionPanel._append_rows(panel, labels)
    panel._row_count = lambda: len(panel._displayed)
    return panel


# ----- the list box -----
def test_output_arriving_does_not_re_announce_the_row_being_read(monkeypatch):
    """The whole bug in one assertion: no selection event, no announcement."""
    read_along = _rows("First", "Reading this")
    control = _ListBox([row.label for row in read_along], selection=1)
    panel = _panel(
        monkeypatch,
        text_view=False,
        displayed=read_along,
        rows=read_along + _rows("New output"),
        control=control,
    )

    app.SessionPanel._refresh_list(panel)

    assert control.selection_events == 0, "the row being read was announced again"
    assert control.rebuilds == 0, "the list was thrown away and rebuilt to add one row"


def test_the_new_output_still_arrives(monkeypatch):
    read_along = _rows("First", "Reading this")
    control = _ListBox([row.label for row in read_along], selection=1)
    panel = _panel(
        monkeypatch,
        text_view=False,
        displayed=read_along,
        rows=read_along + _rows("New output"),
        control=control,
    )

    app.SessionPanel._refresh_list(panel)

    assert control.labels == ["First", "Reading this", "New output"]
    assert control.selection == 1, "and the reader is still on their row"


def test_a_refresh_that_changes_nothing_touches_nothing(monkeypatch):
    """`_refresh_list` is called on events that add no rows at all."""
    same = _rows("First", "Reading this")
    control = _ListBox([row.label for row in same], selection=1)
    panel = _panel(monkeypatch, text_view=False, displayed=same, rows=same, control=control)

    app.SessionPanel._refresh_list(panel)

    assert (control.rebuilds, control.selection_events) == (0, 0)


def test_a_search_still_rebuilds_and_keeps_the_reader_in_place(monkeypatch):
    """When the rows really do change shape there is no way around a rebuild,
    and then restoring the selection is right rather than wrong."""
    rows = _rows("alpha", "beta", "gamma")
    control = _ListBox([row.label for row in rows], selection=2)
    panel = _panel(monkeypatch, text_view=False, displayed=rows, rows=rows, control=control)
    panel._search_term = "a"

    app.SessionPanel._refresh_list(panel)

    assert control.labels == ["alpha", "beta", "gamma"]  # every one contains an "a"


def test_rows_disappearing_is_still_a_rebuild(monkeypatch):
    rows = _rows("alpha", "beta", "gamma")
    control = _ListBox([row.label for row in rows], selection=1)
    panel = _panel(monkeypatch, text_view=False, displayed=rows, rows=rows, control=control)
    panel._search_term = "gam"

    app.SessionPanel._refresh_list(panel)

    assert control.rebuilds == 1
    assert control.labels == ["gamma"]


# ----- the text view -----
def test_the_caret_does_not_leave_the_line_being_read(monkeypatch):
    """Same bug, said in the other control's terms: a caret move is what the
    screen reader announces here."""
    read_along = _rows("First", "Reading this")
    control = _TextView("First\nReading this")
    control.insertion = control.XYToPosition(0, 1)
    panel = _panel(
        monkeypatch,
        text_view=True,
        displayed=read_along,
        rows=read_along + _rows("New output"),
        control=control,
    )
    was_at = control.insertion

    app.SessionPanel._refresh_list(panel)

    assert control.insertion == was_at, "the reader was moved off their line"
    assert control.rebuilds == 0, "the whole view was rewritten to add one line"
    assert control.text == "First\nReading this\nNew output"


def test_a_label_spanning_lines_still_maps_one_row_to_one_line(monkeypatch):
    """A line number is a row number here, so a stray newline breaks the map."""
    control = _TextView("")
    rows = _rows("one")
    panel = _panel(monkeypatch, text_view=True, displayed=[], rows=rows, control=control)
    panel._rows = [Row(kind="prose", label="two\nlines", payload="x", response_number=1)]

    app.SessionPanel._refresh_list(panel)

    assert "\n" not in control.text


# ----- the assumption underneath the append path -----
def test_clearing_the_conversation_empties_the_control(monkeypatch):
    """clear_conversation empties _rows and _displayed while the control still
    shows the old rows, and then refreshes. The model must not mistake a
    control it has not cleared for one that is empty and up to date."""
    rows = _rows("old answer")
    control = _ListBox([row.label for row in rows])
    panel = _panel(monkeypatch, text_view=False, displayed=rows, rows=[], control=control)
    # clear_conversation's reset happens before the refresh it then calls:
    panel._displayed = []

    app.SessionPanel._refresh_list(panel)

    assert control.labels == [], "the cleared conversation is still on screen"


def test_clearing_the_conversation_empties_the_text_view(monkeypatch):
    """The same reset through the other control."""
    rows = _rows("old answer")
    control = _TextView("old answer")
    panel = _panel(monkeypatch, text_view=True, displayed=rows, rows=[], control=control)
    panel._displayed = []

    app.SessionPanel._refresh_list(panel)

    assert control.text == "", "the cleared conversation is still on screen"


def test_the_other_control_is_filled_when_it_becomes_the_visible_one(monkeypatch):
    """apply_view_mode shows whichever control Options asks for — and only the
    visible one is ever filled. A control holding nothing the model put there
    has to be rebuilt, not appended to."""
    rows = _rows("one", "two", "three")
    control = _TextView("")  # never the visible control
    panel = _panel(monkeypatch, text_view=True, displayed=rows, rows=rows, control=control)

    app.SessionPanel._refresh_list(panel)

    assert control.text.split("\n") == [row.label for row in rows]
