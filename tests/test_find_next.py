"""F3 and Shift+F3: the next and previous row holding the last search.

Find in Responses filters the list, and clearing the filter forgot the term,
so there was no way to step from one match to the next in the whole
conversation. The Chat Place offers F3 and Shift+F3 for exactly that.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app
from markdown_rows import Row


@pytest.fixture
def panel():
    rows = [
        Row(kind="prose", label=text, payload=text, response_number=1)
        for text in ("build failed", "nothing here", "build passed")
    ]
    stub = type("PanelStub", (), {})()
    stub._displayed = rows
    stub._find_term = "build"
    stub.selected = 0
    stub.spoken: list[str] = []
    stub._announce = lambda text, urgent=False: stub.spoken.append(text)
    stub._selected_row = lambda: stub.selected
    stub._focus_row = lambda index: setattr(stub, "selected", index)
    return stub


def test_f3_moves_to_the_next_match(panel):
    app.SessionPanel.find_next(panel, 1)

    assert panel.selected == 2 and panel.spoken == []


def test_going_past_the_end_wraps_and_says_so(panel):
    panel.selected = 2

    app.SessionPanel.find_next(panel, 1)

    assert panel.selected == 0 and panel.spoken == ["Wrapped to the top"]


def test_shift_f3_moves_back(panel):
    panel.selected = 2

    app.SessionPanel.find_next(panel, -1)

    assert panel.selected == 0 and panel.spoken == []


def test_with_no_search_yet_it_says_how_to_start(panel):
    panel._find_term = ""

    app.SessionPanel.find_next(panel, 1)

    assert "Ctrl+F" in panel.spoken[0]
