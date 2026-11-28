"""Claude's suggested next message, put in the tab's message box."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import blindpilot_app as app


class _Box:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.selected = False

    def GetValue(self) -> str:
        return self.text

    def ChangeValue(self, text: str) -> None:
        self.text = text

    def SetSelection(self, start: int, end: int) -> None:
        self.selected = (start, end) == (-1, -1)


def _tab(text: str = "") -> SimpleNamespace:
    said: list[str] = []
    return SimpleNamespace(
        prompt=_Box(text), _claude_generation=3, _announce=said.append, said=said
    )


@pytest.fixture
def suggestions_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.SETTINGS, "prompt_suggestions", True)


def test_an_empty_box_gets_the_suggestion_selected(suggestions_on: None) -> None:
    tab = _tab()
    app.SessionPanel._on_suggestion(tab, 3, "Now show test_todo.py")
    assert tab.prompt.text == "Now show test_todo.py" and tab.prompt.selected
    assert tab.said == ["Suggested message in the message box"]


def test_your_own_text_is_never_replaced(suggestions_on: None) -> None:
    tab = _tab("my half-typed message")
    app.SessionPanel._on_suggestion(tab, 3, "Now show test_todo.py")
    assert tab.prompt.text == "my half-typed message" and tab.said == []


def test_a_suggestion_for_another_conversation_or_with_the_setting_off_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app.SETTINGS, "prompt_suggestions", True)
    stale = _tab()
    app.SessionPanel._on_suggestion(stale, 2, "old")
    monkeypatch.setattr(app.SETTINGS, "prompt_suggestions", False)
    off = _tab()
    app.SessionPanel._on_suggestion(off, 3, "new")
    assert stale.prompt.text == "" and off.prompt.text == ""
