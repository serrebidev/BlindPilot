"""How full the conversation's context is.

A conversation that fills its context window is compacted by the backend
without asking, or fails, and neither came with a warning. Claude Code and
Codex both report the tokens each reply was sent; the last turn's figure is
kept per tab, shown in Session Status, and announced once when it passes 80%,
as The Chat Place does.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app


@pytest.fixture
def panel():
    stub = type("PanelStub", (), {})()
    stub.spoken: list[str] = []
    stub._announce = lambda text, urgent=False: stub.spoken.append(text)
    return stub


def test_the_line_says_percent_and_tokens():
    assert app._context_line(124_000, 200_000) == "Context 62% full: 124,000 of 200,000 tokens"
    assert app._context_line(5_000, 0) == "Context: 5,000 tokens used"
    assert app._context_line(0, 0) == "Context: not reported yet"


def test_passing_eighty_percent_is_announced_once(panel):
    app.SessionPanel._note_context(panel, (100_000, 200_000))
    assert panel.spoken == []

    app.SessionPanel._note_context(panel, (170_000, 200_000))
    assert len(panel.spoken) == 1 and "85% full" in panel.spoken[0]

    app.SessionPanel._note_context(panel, (180_000, 200_000))
    assert len(panel.spoken) == 1


def test_a_turn_with_no_window_keeps_the_one_already_known(panel):
    app.SessionPanel._note_context(panel, (10_000, 200_000))
    app.SessionPanel._note_context(panel, (20_000, 0))

    assert panel._context == (20_000, 200_000)


def test_nothing_reported_changes_nothing(panel):
    app.SessionPanel._note_context(panel, None)

    assert not hasattr(panel, "_context")
