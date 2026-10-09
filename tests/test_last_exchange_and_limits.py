"""Recent Conversations' Last exchange box, and Claude Code's limit warnings.

The dialog is built for real with wx assertions raised, as
test_history_dialog_builds.py does. Reading a transcript runs on a thread, so
the tests call the pieces either side of it.
"""

from __future__ import annotations

import pytest

import agent_backends
import blindpilot_app
from session_history import HistoryEntry, HistoryTurn


def test_the_last_exchange_skips_empty_turns_and_cuts_long_text():
    turns = [
        HistoryTurn(prompt="first", response="one"),
        HistoryTurn(prompt="fix it", response="word " * 400),
        HistoryTurn(),
    ]
    text = blindpilot_app._last_exchange(turns, limit=20)
    assert text.startswith("You: fix it\n\nAnswer: word word")
    assert text.endswith("…")
    assert "Nothing to show" in blindpilot_app._last_exchange([])


def test_the_box_follows_the_selection_and_ignores_a_late_answer(frame, monkeypatch):
    import wx

    entries = [
        HistoryEntry("claude", f"s{n}", f"Conversation {n}", f"p{n}", 100.0 - n) for n in range(2)
    ]
    monkeypatch.setattr(blindpilot_app, "list_history", lambda *_a, **_k: entries)
    app = wx.GetApp()
    old = app.GetAssertMode()
    app.SetAssertMode(wx.APP_ASSERT_EXCEPTION)
    try:
        dialog = blindpilot_app.HistoryDialog(frame, backend="claude", cwd=".")
        try:
            first = dialog._shown[0]
            assert dialog._preview_for == first
            assert dialog.preview.GetValue() == "Reading…"
            dialog.list_box.SetSelection(1)
            dialog._schedule_preview()
            dialog._show_preview(first, "stale")
            assert dialog.preview.GetValue() == "Reading…"
            dialog._show_preview(dialog._shown[1], "You: hi")
            assert dialog.preview.GetValue() == "You: hi"
        finally:
            dialog._preview_timer.Stop()
            dialog.Destroy()
    finally:
        app.SetAssertMode(old)


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(agent_backends, "_CLAUDE_LIMITS_SAID", set())


INFO = {
    "status": "allowed_warning",
    "resetsAt": 1791838800,
    "rateLimitType": "seven_day",
    "utilization": 0.92,
}


def test_a_warning_is_said_once_per_window(fresh):
    said = agent_backends.claude_limit_warning(INFO)
    assert said.startswith("Claude Code Weekly limit: 92% used, resets ")
    assert agent_backends.claude_limit_warning(INFO) == ""
    assert agent_backends.claude_limit_warning({**INFO, "resetsAt": 1792443600})


def test_a_reached_limit_is_said_even_after_its_warning(fresh):
    agent_backends.claude_limit_warning(INFO)
    said = agent_backends.claude_limit_warning({**INFO, "status": "rejected"})
    assert said.endswith("The limit is reached.")


@pytest.mark.parametrize("info", [{**INFO, "status": "allowed"}, None, "x", {}])
def test_nothing_is_said_while_within_the_limit(fresh, info):
    assert agent_backends.claude_limit_warning(info) == ""
