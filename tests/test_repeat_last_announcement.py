"""Hearing the last announcement again.

Everything BlindPilot says goes through `announce`, queued behind whatever the
screen reader is already reading. A keystroke cuts that queue short, and a line
cut short was gone: the status bar holds only the visible tab's last status,
and Chat mode and the menus never write it at all. Repeat Last Announcement
(Ctrl+Shift+R) says the last line again, as The Chat Place does.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app


class _Speaker:
    def __init__(self):
        self.said: list[str] = []

    def speak(self, text, interrupt=False):
        self.said.append(text)


@pytest.fixture
def speaker(monkeypatch):
    spoken = _Speaker()
    monkeypatch.setattr(app, "_SPEAKER", spoken)
    monkeypatch.setattr(app, "_last_announcement", "")
    return spoken


def test_the_last_line_is_said_again(speaker):
    app.announce("Receiving response")
    app.announce("Turn finished")

    app.repeat_last_announcement()

    assert speaker.said[-1] == "Turn finished"


def test_with_nothing_said_yet_it_says_so(speaker):
    app.repeat_last_announcement()

    assert speaker.said == ["Nothing has been announced yet"]
