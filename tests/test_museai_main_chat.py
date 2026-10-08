# SPDX-License-Identifier: MIT
"""Muse.ai's main chat is listed and selected without creating a side chat."""

import json
import sys

import agent_backends
import blindpilot_app
import museai_backend
import pytest
import session_history
import wx
from test_tab_strip_focus import _frame, _running_app


def test_python_cli_probes_can_print_unicode_chat_history(monkeypatch):
    monkeypatch.setenv("PYTHONIOENCODING", "ascii")
    code, text = agent_backends._probe_backend(sys.executable, ["-c", "print('\u2192')"], 10)
    assert (code, text.strip()) == (0, "\u2192")


def test_restored_main_chat_follows_changes_from_its_loaded_sequence(monkeypatch, tmp_path):
    with _running_app():
        frame = _frame(monkeypatch, tmp_path)
        try:
            _gateway(monkeypatch, tmp_path)
            panel = frame.notebook.GetCurrentPage()
            entry = session_history.HistoryEntry("museai", "main", "Main chat", "", 0)
            panel.restore_history(
                entry, [session_history.HistoryTurn("Old question", "Old reply", sequence=10)]
            )
            assert panel._museai_seen == 10
            launches = []
            monkeypatch.setattr(panel, "_launch_turn", lambda *args: launches.append(args))
            panel._museai_checked("main", 11)
            assert launches and panel._museai_follow_after == 10
        finally:
            frame.Destroy()


def _gateway(monkeypatch, tmp_path):
    config = tmp_path / ".config" / "muse-cli" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(session_history, "_home", lambda: tmp_path)
    monkeypatch.setattr(museai_backend, "museai_config_path", lambda: config)
    chats = [
        {"session_id": "side", "thread": True, "title": "Side chat", "archived": False},
        {"session_id": "main", "thread": False, "title": "Last topic", "archived": False},
        {"session_id": "old", "thread": True, "title": "Old chat", "archived": True},
        {"thread": False, "title": "Missing id"},
    ]
    calls = []

    def probe(_binary, args, _timeout):
        calls.append(args)
        if args == ["threads"]:
            return 0, json.dumps(chats)
        if args[0] == "history":
            assert args[1:3] in (["--thread", "main"], ["--thread", "side"])
            return 0, json.dumps(
                [{"role": "user", "text": "Remember me?"}, {"role": "assistant", "text": "Yes."}]
            )
        raise AssertionError(f"Unexpected muse-cli command: {args}")

    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _backend: "muse-cli")
    monkeypatch.setattr(agent_backends, "_probe_backend", probe)
    return chats, calls


def test_discuss_prepares_main_chat_draft_and_preserves_another_tabs_prompt(monkeypatch, tmp_path):
    with _running_app():
        frame = _frame(monkeypatch, tmp_path)
        try:
            _chats, calls = _gateway(monkeypatch, tmp_path)
            previous = frame.notebook.GetCurrentPage()
            previous.prompt.SetValue("Keep this draft")

            class Discussion:
                chat_request = ("draft", "Article context\nMy question: ", "Article")

                def __init__(self, *_args):
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    pass

                def ShowModal(self):
                    return wx.ID_OK

            monkeypatch.setattr(blindpilot_app, "MuseAiDialog", Discussion)
            frame._museai_active("feed")
            panel = frame.notebook.GetCurrentPage()
            assert panel is not previous and panel._session_id == "main"
            assert panel.prompt.GetValue() == "Article context\nMy question: "
            assert previous.prompt.GetValue() == "Keep this draft"
            assert not any(call[0] in {"send", "session-start"} for call in calls)
        finally:
            frame.Destroy()


def test_main_chat_is_in_recent_conversations_with_a_recognizable_name(monkeypatch, tmp_path):
    _gateway(monkeypatch, tmp_path)
    entries = session_history.list_history("museai", cwd="C:/another-project")
    assert {entry.session_id: entry.title for entry in entries} == {
        "side": "Side chat",
        "main": "Main chat",
    }
    main = next(entry for entry in entries if entry.session_id == "main")
    assert [(t.prompt, t.response) for t in session_history.load_turns(main)] == [
        ("Remember me?", "Yes.")
    ]


def test_switching_to_museai_loads_main_and_new_conversation_still_starts_a_side_chat(
    monkeypatch, tmp_path
):
    with _running_app():
        frame = _frame(monkeypatch, tmp_path)
        try:
            _chats, calls = _gateway(monkeypatch, tmp_path)
            monkeypatch.setattr(frame, "_announce_setting", lambda _text: None)
            frame._set_backend("museai")
            panel = frame.notebook.GetCurrentPage()
            assert panel._session_id == "main"
            assert [(t.prompt, t.response) for t in panel._turns] == [("Remember me?", "Yes.")]
            assert "Main chat" in frame.notebook.GetPageText(frame.notebook.GetSelection())
            assert not any(call[0] in {"send", "session-start"} for call in calls)

            panel.clear_conversation()
            assert panel._session_id is None

            new_panel = frame._add_session(str(tmp_path))
            assert new_panel._session_id == "main"
        finally:
            frame.Destroy()


def test_reopening_a_side_chat_does_not_load_main_first(monkeypatch, tmp_path):
    with _running_app():
        frame = _frame(monkeypatch, tmp_path)
        try:
            _chats, calls = _gateway(monkeypatch, tmp_path)
            monkeypatch.setattr(frame, "_announce_setting", lambda _text: None)
            frame._set_backend("museai")
            calls.clear()
            entry = session_history.HistoryEntry("museai", "side", "Side chat", "", 0)
            frame._resume_history(entry)
            assert frame.notebook.GetCurrentPage()._session_id == "side"
            assert calls == [["history", "--thread", "side", "--limit", "300"]]
        finally:
            frame.Destroy()


def test_clearing_an_unanswered_main_chat_cancels_its_queued_follow(monkeypatch, tmp_path):
    with _running_app():
        frame = _frame(monkeypatch, tmp_path)
        try:
            _gateway(monkeypatch, tmp_path)
            monkeypatch.setattr(frame, "_announce_setting", lambda _text: None)
            frame._set_backend("museai")
            wx.Yield()
            panel = frame.notebook.GetCurrentPage()
            launches = []
            monkeypatch.setattr(panel, "_launch_turn", lambda *args: launches.append(args))
            entry = session_history.HistoryEntry("museai", "main", "Main chat", "", 0)
            panel.restore_history(entry, [session_history.HistoryTurn("Still working", "")])
            panel.clear_conversation()
            wx.Yield()
            assert launches == []
        finally:
            frame.Destroy()


def test_a_failed_main_chat_lookup_keeps_the_prompt_without_sending_to_a_side_chat(
    monkeypatch, tmp_path
):
    with _running_app():
        frame = _frame(monkeypatch, tmp_path)
        try:
            chats, calls = _gateway(monkeypatch, tmp_path)
            chats[:] = [chat for chat in chats if chat.get("thread")]
            monkeypatch.setattr(frame, "_announce_setting", lambda _text: None)
            frame._set_backend("museai")
            panel = frame.notebook.GetCurrentPage()
            panel.prompt.SetValue("Send this to the main chat")
            panel.send_now()
            assert panel.prompt.GetValue() == "Send this to the main chat"
            assert panel._session_id is None
            assert calls == [["threads"], ["threads"]]

            # Choosing an existing side chat must release the failed default lookup.
            entry = session_history.HistoryEntry("museai", "side", "Side chat", "", 0)
            panel.restore_history(entry, [session_history.HistoryTurn("Question", "Answer")])
            assert not panel._museai_main_required
        finally:
            frame.Destroy()


@pytest.mark.parametrize("running", [False, True])
def test_switching_backends_never_replaces_an_active_turn(monkeypatch, tmp_path, running):
    with _running_app():
        frame = _frame(monkeypatch, tmp_path)
        try:
            _gateway(monkeypatch, tmp_path)
            panel = frame.notebook.GetCurrentPage()
            panel._session_id = "claude-session"
            panel._turns = [blindpilot_app.Turn("Old question", "Old answer")]
            panel._pending_messages = ["Old queued message"]
            panel._steering_context = "Old steering"
            panel._queue_paused = True
            monkeypatch.setattr(panel, "_run_in_progress", lambda: running)
            monkeypatch.setattr(frame, "_announce_setting", lambda _text: None)
            frame._set_backend("museai")
            assert panel._session_id == ("claude-session" if running else "main")
            assert panel._turns[0].prompt == ("Old question" if running else "Remember me?")
            assert panel._pending_messages == (["Old queued message"] if running else [])
            assert panel._steering_context == ("Old steering" if running else "")
            assert panel._queue_paused is running
        finally:
            frame.Destroy()
