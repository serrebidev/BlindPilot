"""Installing an update while a turn runs, or with a message not yet sent.

Restart now closed BlindPilot whatever was happening: a running turn was
stopped and a typed prompt was gone. The Chat Place refuses to install mid-turn
and warns about unsent text; BlindPilot now says what would be lost and asks,
with No the default.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app


class _Prompt:
    def __init__(self, text):
        self._text = text

    def GetValue(self):
        return self._text


def _panel(running=False, text=""):
    panel = type("PanelStub", (), {})()
    panel._worker = object() if running else None
    panel.prompt = _Prompt(text)
    return panel


@pytest.fixture
def asked(monkeypatch):
    seen: list[str] = []

    class _Dialog:
        def __init__(self, _parent, message, *_a, **_k):
            seen.append(message)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def ShowModal(self):
            return app.wx.ID_NO

    monkeypatch.setattr(app.wx, "MessageDialog", _Dialog)
    return seen


def _frame(*panels):
    frame = type("FrameStub", (), {})()
    frame._session_panels = lambda: list(panels)
    return frame


def test_nothing_running_and_nothing_typed_installs_without_asking(asked):
    assert app.MainFrame._confirm_update_install(_frame(_panel())) is True
    assert asked == []


def test_a_running_turn_and_unsent_text_are_named_and_no_is_the_answer(asked):
    frame = _frame(_panel(running=True), _panel(text="half a thought"))

    assert app.MainFrame._confirm_update_install(frame) is False
    assert "running in 1 tab" in asked[0] and "not sent" in asked[0]
