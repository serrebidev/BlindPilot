"""Telling somebody in another window that a turn needs them.

A turn that ended, failed or asked a question while you were in your editor
was announced to a window nobody was looking at. A system notification goes
out instead, only while BlindPilot is in the background, as The Chat Place's
do. In front, the announcement already said it.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app


class _Note:
    shown: list[tuple[str, str]] = []

    def __init__(self, title, message, parent=None):
        self.title, self.message = title, message

    def Bind(self, *_args):
        pass

    def Show(self):
        _Note.shown.append((self.title, self.message))


@pytest.fixture
def panel(monkeypatch):
    _Note.shown = []
    monkeypatch.setattr(app.wx.adv, "NotificationMessage", _Note)
    monkeypatch.setattr(app.SETTINGS, "notify_in_background", True)
    stub = type("PanelStub", (), {})()
    stub.tab_title = "Fix the build"
    stub.cwd = ""
    stub._come_forward = lambda: None
    return stub


def _active(monkeypatch, active):
    monkeypatch.setattr(app.wx, "GetApp", lambda: type("App", (), {"IsActive": lambda s: active})())


def test_in_the_background_a_notification_goes_out(panel, monkeypatch):
    _active(monkeypatch, False)

    app._notify_if_away(panel, "The turn finished")

    assert _Note.shown == [("BlindPilot: Fix the build", "The turn finished")]


def test_in_front_nothing_is_shown(panel, monkeypatch):
    _active(monkeypatch, True)

    app._notify_if_away(panel, "The turn finished")

    assert _Note.shown == []


def test_turned_off_nothing_is_shown(panel, monkeypatch):
    _active(monkeypatch, False)
    monkeypatch.setattr(app.SETTINGS, "notify_in_background", False)

    app._notify_if_away(panel, "The turn finished")

    assert _Note.shown == []


def test_choosing_it_from_chat_mode_shows_the_agent_tabs_again(monkeypatch):
    """Chat hides the session tabs, so the tab that called has to come back."""
    modes: list[str] = []
    frame = type("FrameStub", (), {})()
    frame._app_mode = app.APP_MODE_CHAT
    frame.Iconize = lambda _flag: None
    frame.Raise = lambda: None
    frame._set_app_mode = modes.append
    frame.notebook = None
    panel = type("PanelStub", (), {"__bool__": lambda s: True})()
    panel.focus_prompt = lambda: None
    monkeypatch.setattr(app.wx, "GetTopLevelParent", lambda _w: frame)

    app.SessionPanel._come_forward(panel)

    assert modes == [app.APP_MODE_AGENT]
