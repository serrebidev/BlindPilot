"""Find useful material without arrowing through every conversation row."""

from types import SimpleNamespace

import wx

import blindpilot_app as app
from conversation_items import ConversationItemsDialog, collect_items, can_open_link
from markdown_rows import parse_response, Row


def test_code_keeps_whitespace_and_lists_every_block_with_its_source():
    items = collect_items(
        [("Response 1", "```python\n  print('first')\n```\n\n~~~sh\necho second\n~~~")],
        "code",
    )
    assert [item.payload for item in items] == ["  print('first')\n", "echo second\n"]
    assert "Response 1" in items[0].label
    assert "python" in items[0].label


def test_links_include_markdown_autolinks_and_bare_addresses_without_duplicates():
    items = collect_items(
        [
            (
                "Response 2",
                "[Docs](https://example.com/a_(b)) <https://example.org> "
                "https://example.net/path. [Again](https://example.org)",
            )
        ],
        "links",
    )
    assert [item.payload for item in items] == [
        "https://example.com/a_(b)",
        "https://example.org",
        "https://example.net/path",
    ]
    assert "Docs" in items[0].label


def test_code_links_are_identified_and_file_links_are_copy_only():
    items = collect_items(
        [
            (
                "You",
                "[local](file:///tmp/readme) `https://example.com`\n\n"
                "```sh\ncurl https://example.org\n```",
            )
        ],
        "links",
    )
    assert [item.payload for item in items] == [
        "file:///tmp/readme",
        "https://example.com",
        "https://example.org",
    ]
    assert "copy only" in items[0].label
    assert all("in code" in item.label for item in items[1:])


def test_only_valid_web_addresses_can_open():
    for address in ("https://example.com", "http://localhost:8080/path"):
        assert can_open_link(address)
    for address in (
        "javascript:alert(1)",
        "file:///tmp/a",
        "https://",
        "https://[bad",
        "mailto:a@b.com",
        "https://example.com:not-a-port",
        "https://example.com:70000",
        "https://example.com/\nargument",
    ):
        assert not can_open_link(address)


def test_empty_messages_have_no_placeholder_items():
    assert collect_items([], "code") == []
    assert collect_items([("Response 1", "Plain words")], "links") == []


def test_dialog_filters_code_copies_exact_text_and_reports_clipboard_failure(frame):
    copied, spoken, read = [], [], []
    items = collect_items(
        [("Response 1", "```py\n  first()\n```\n```sh\necho second\n```")], "code"
    )
    dialog = ConversationItemsDialog(
        frame,
        items,
        "Code Blocks",
        links=False,
        copy=lambda value: copied.append(value) or False,
        read=read.append,
        announce=spoken.append,
    )
    try:
        dialog.filter.ChangeValue("second")
        dialog._refresh()
        assert dialog.list.GetCount() == 1
        assert dialog.text.GetValue() == "echo second\n"
        dialog._on_read()
        dialog._on_copy()
        assert read == [items[1]]
        assert copied == ["echo second\n"]
        assert spoken == ["Error: Could not access clipboard"]
        dialog.filter.ChangeValue("no match")
        dialog._refresh()
        assert not dialog.copy_button.IsEnabled()
        assert not dialog.read_button.IsEnabled()
    finally:
        dialog.Destroy()


def test_links_open_only_after_an_action_and_never_launch_copy_only_items(frame, monkeypatch):
    opened = []
    monkeypatch.setattr(wx, "LaunchDefaultBrowser", lambda url: opened.append(url) or True)
    items = collect_items(
        [("Response 1", "[local](file:///tmp/a) [web](https://example.com)")], "links"
    )
    dialog = ConversationItemsDialog(
        frame,
        items,
        "Links",
        links=True,
        copy=lambda value: True,
        read=lambda item: None,
        announce=lambda value: None,
    )
    try:
        assert opened == []
        assert not dialog.open_button.IsEnabled()
        dialog._on_open()
        assert opened == []
        dialog.list.SetSelection(1)
        dialog._select()
        assert dialog.open_button.IsEnabled()
        dialog._on_open()
        assert opened == ["https://example.com"]
    finally:
        dialog.Destroy()


def test_agent_scope_uses_each_original_message_once(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "_show_conversation_items", lambda *args: calls.append(args))
    rows = [Row("you", "You", "https://example.net", 1)]
    rows += parse_response("[Docs](https://example.com)\n```py\nanswer()\n```", 1)
    rows += parse_response("Another response", 2)
    panel = SimpleNamespace(_rows=rows)
    app.SessionPanel.open_items(panel, "links", 1)
    assert calls[0][1] == [("You", "https://example.net"), ("Response 1", rows[1].payload)]


def test_agent_browser_keeps_symbols_removed_from_spoken_rows(monkeypatch):
    calls = []
    monkeypatch.setattr(
        app,
        "_show_conversation_items",
        lambda _parent, messages, kind: calls.extend(collect_items(messages, kind)),
    )
    panel = SimpleNamespace(_rows=parse_response('```python\nstatus = "✅ ready"\n```', 1))
    app.SessionPanel.open_items(panel, "code")
    assert calls[0].payload == 'status = "✅ ready"\n'


def test_browser_can_read_the_current_stream_without_waiting_for_completion(monkeypatch):
    calls = []
    monkeypatch.setattr(
        app,
        "_show_conversation_items",
        lambda _parent, messages, kind: calls.extend(collect_items(messages, kind)),
    )
    panel = SimpleNamespace(
        _rows=[Row("header", "Response 1", "", 1)],
        _stream_response=1,
        _live_answer=[1, "Assistant", "```py\nworking()\n```", 0, 0],
    )
    app.SessionPanel.open_items(panel, "code")
    assert calls[0].payload == "working()\n"


def test_chat_routes_user_and_assistant_messages_without_thinking(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "_show_conversation_items", lambda *args: calls.append(args))
    panel = SimpleNamespace(
        history_entries=[
            SimpleNamespace(role=role, content=role) for role in ("user", "thinking", "assistant")
        ],
        _history_role_label=lambda role: role.title(),
    )
    frame = SimpleNamespace(_app_mode=app.APP_MODE_CHAT, chat_panel=panel)
    app.MainFrame._browse_items_active(frame, "code")
    assert calls == [(panel, [("User 1", "user"), ("Assistant 3", "assistant")], "code")]


def test_browser_menu_keeps_shortcuts_available_in_chat_mode(frame):
    # The ordinary Find action remains Agent-only; both new lists must work in Chat.
    frame._menu_item = lambda *a, **kw: app.MainFrame._menu_item(frame, *a, **kw)
    frame._agent_item = lambda *a, **kw: app.MainFrame._agent_item(frame, *a, **kw)
    frame._agent_menu_items = []
    for name in (
        "_stop_active",
        "_attach_active",
        "_slash_active",
        "_saved_prompts_active",
        "_compact_active",
        "_new_conversation_active",
        "_find_active",
        "_task_list_active",
        "_find_in_all_conversations",
        "_send_to_tab_active",
        "_jump_to_latest_response",
        "_export_active",
    ):
        setattr(frame, name, lambda: None)
    menu = app.MainFrame._build_conversation_menu(frame)
    try:
        browse = next(item.GetSubMenu() for item in menu.GetMenuItems() if item.GetSubMenu())
        items = [item for item in browse.GetMenuItems() if not item.IsSeparator()]
        assert items[0] in frame._agent_menu_items
        for item, chord in zip(items[1:3], ("Ctrl+Shift+B", "Ctrl+Shift+L"), strict=True):
            assert item not in frame._agent_menu_items
            assert item.GetItemLabel().endswith(chord)
            assert item.GetAccel() is not None
        # Task List, Commands, Decisions, Find in All Conversations, Send
        # Response and Properties act on Agent sessions only.
        agent_chords = (
            "Ctrl+Shift+J",
            "Ctrl+Shift+X",
            "Ctrl+Shift+Y",
            "Ctrl+Shift+S",
            "Ctrl+Shift+O",
            "Alt+Enter",
        )
        for item, chord in zip(items[3:], agent_chords, strict=True):
            assert item in frame._agent_menu_items
            assert item.GetItemLabel().endswith(chord)
    finally:
        menu.Destroy()
