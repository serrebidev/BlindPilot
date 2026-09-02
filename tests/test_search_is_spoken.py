"""Saying what a search did.

`open_find` reported its outcome with `_set_status`, which writes the status
bar and nothing else — no screen reader reads a status bar it was not asked to.
When the search matched something, focus moved to the first hit and the reader
announced that row, so there was at least a sign of life. When it matched
nothing, focus did not move either: the list quietly emptied and not one thing
said so. Searching for a typo was indistinguishable from the application
ignoring the keystroke.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app
from markdown_rows import Row


class _Dialog:
    """`wx.TextEntryDialog` as `open_find` uses it: a context manager."""

    def __init__(self, value, accepted=True):
        self._value = value
        self._accepted = accepted

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def ShowModal(self):
        return app.wx.ID_OK if self._accepted else app.wx.ID_CANCEL

    def GetValue(self):
        return self._value


@pytest.fixture
def panel(monkeypatch):
    rows = [
        Row(kind="prose", label="the first thing", payload="the first thing", response_number=1),
        Row(kind="prose", label="the second thing", payload="the second thing", response_number=1),
    ]
    stub = type("PanelStub", (), {})()
    stub._rows = rows
    stub._displayed = list(rows)
    stub._search_term = ""
    stub.spoken: list[str] = []
    stub.status: list[str] = []
    stub._announce = lambda text, urgent=False: stub.spoken.append(text)
    stub._set_status = stub.status.append
    stub._refresh_list = lambda: None
    stub._row_count = lambda: len(stub._displayed)
    stub.focused: list[int] = []
    stub._focus_row = stub.focused.append
    return stub


def _find(panel, monkeypatch, term, accepted=True):
    monkeypatch.setattr(app.wx, "TextEntryDialog", lambda *a, **k: _Dialog(term, accepted))
    app.SessionPanel.open_find(panel)


def test_a_search_that_finds_nothing_says_so(panel, monkeypatch):
    """The whole bug: the list empties and nothing tells you."""
    panel._displayed = []

    _find(panel, monkeypatch, "nothing matches this")

    assert panel.spoken, "a search that matched nothing said nothing"
    assert "0" in panel.spoken[0]


def test_a_search_that_finds_something_says_how_much(panel, monkeypatch):
    _find(panel, monkeypatch, "second")

    assert panel.spoken, "the result count was never spoken"
    assert "2" in panel.spoken[0]


def test_clearing_the_search_says_so(panel, monkeypatch):
    panel._search_term = "second"

    _find(panel, monkeypatch, "")

    assert panel.spoken == ["Search cleared"]


def test_a_hit_still_takes_you_to_the_first_one(panel, monkeypatch):
    _find(panel, monkeypatch, "thing")

    assert panel.focused == [0]


def test_cancelling_the_dialog_changes_nothing(panel, monkeypatch):
    panel._search_term = "kept"

    _find(panel, monkeypatch, "typed but cancelled", accepted=False)

    assert panel._search_term == "kept"
    assert panel.spoken == []
