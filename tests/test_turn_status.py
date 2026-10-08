"""Asking how a turn is getting on.

With Keep up narration, or live activity off, a long turn is silent apart from
the working sound, which says nothing about whether it is three seconds or
thirty minutes in, or what it is doing. Turn Status (Ctrl+Shift+T) says how
long it has run, its last step and what is queued, as The Chat Place's does.
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


def test_a_running_turn_says_how_long_and_its_last_step(panel, monkeypatch):
    monkeypatch.setattr(app.time, "monotonic", lambda: 1125.0)
    panel._turn_started_at = 1000.0
    panel._last_step = "Bash: git status"
    panel._pending_messages = [("next", [], "claude")]

    app.SessionPanel.turn_status(panel)

    assert panel.spoken == [
        "Working for 2 minutes 5 seconds. Last step: Bash: git status. 1 message queued."
    ]


def test_an_attached_turn_says_it_counts_from_the_attach(panel, monkeypatch):
    monkeypatch.setattr(app.time, "monotonic", lambda: 70.0)
    panel._turn_started_at = 10.0
    panel._turn_attached = True

    app.SessionPanel.turn_status(panel)

    assert panel.spoken == ["Attached to a running turn 1 minute ago. No tool used yet."]


def test_with_no_turn_running_it_says_how_long_the_last_one_took(panel):
    panel._turn_started_at = None
    panel._last_turn_seconds = 3661

    app.SessionPanel.turn_status(panel)

    assert panel.spoken == ["No turn is running. The last one took 1 hour 1 minute 1 second."]


def test_before_any_turn_it_just_says_none_is_running(panel):
    app.SessionPanel.turn_status(panel)

    assert panel.spoken == ["No turn is running."]
