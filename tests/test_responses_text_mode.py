"""The read-only text field: one row per line, found by character offset.

Lines wrap now, so the control's own line numbers count wrapped lines and no
longer match rows. Each row's start offset does.
"""

from __future__ import annotations

import blindpilot_app as app


def test_a_caret_anywhere_in_a_row_names_that_row():
    starts = [0, 6, 14]  # "hello\nworld!!\nlast"
    assert app._row_at(starts, 0) == 0
    assert app._row_at(starts, 3) == 0
    assert app._row_at(starts, 5) == 0
    assert app._row_at(starts, 6) == 1
    assert app._row_at(starts, 13) == 1
    assert app._row_at(starts, 14) == 2
    assert app._row_at(starts, 99) == 2


def test_no_rows_means_no_row():
    assert app._row_at([], 0) == -1


class _Text:
    def __init__(self):
        self.value = ""
        self.caret = 0

    def ChangeValue(self, text):
        self.value = text

    def AppendText(self, text):
        self.value += text

    def GetLastPosition(self):
        return len(self.value)

    def GetNumberOfLines(self):
        return self.value.count("\n") + 1 if self.value else 1

    def GetInsertionPoint(self):
        return self.caret

    def SetInsertionPoint(self, pos):
        self.caret = pos


def _panel(rows):
    from markdown_rows import Row

    panel = type("PanelStub", (), {})()
    panel.responses_text = _Text()
    panel.responses = None
    panel._rows = [Row(kind="prose", label=r, payload=r, response_number=1) for r in rows]
    panel._displayed = []
    panel._search_term = ""
    panel._row_starts = []
    panel._row_count = lambda: len(panel._displayed)
    panel._selected_row = lambda: app.SessionPanel._selected_row(panel)
    panel._select_row = lambda i: app.SessionPanel._select_row(panel, i)
    panel._append_rows = lambda rows: app.SessionPanel._append_rows(panel, rows)
    return panel


def test_offsets_follow_the_rows_through_a_rebuild_and_an_append(monkeypatch):
    monkeypatch.setattr(app.SETTINGS, "text_view", True)
    panel = _panel(["hello", "world!!"])
    app.SessionPanel._refresh_list(panel)
    assert panel._row_starts == [0, 6]
    panel._rows.append(
        type(panel._rows[0])(kind="prose", label="last", payload="last", response_number=1)
    )
    app.SessionPanel._refresh_list(panel)
    assert panel._row_starts == [0, 6, 14]
    assert panel.responses_text.value == "hello\nworld!!\nlast"


def test_selecting_a_row_puts_the_caret_at_its_start(monkeypatch):
    monkeypatch.setattr(app.SETTINGS, "text_view", True)
    panel = _panel(["hello", "world!!", "last"])
    app.SessionPanel._refresh_list(panel)
    app.SessionPanel._select_row(panel, 2)
    assert panel.responses_text.caret == 14
    assert app.SessionPanel._selected_row(panel) == 2
