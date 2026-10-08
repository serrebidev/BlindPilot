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
