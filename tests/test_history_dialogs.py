"""History protection dialogs hang off the Recent Conversations submenu."""

from __future__ import annotations

import pytest

import blindpilot_app as app

wx = pytest.importorskip("wx")


@pytest.fixture()
def frame(wx_app):
    window = wx.Frame(None)
    window._menu_item = lambda *args, **kwargs: app.MainFrame._menu_item(window, *args, **kwargs)
    window._agent_menu_items = []
    window._agent_item = lambda *args, **kwargs: app.MainFrame._agent_item(window, *args, **kwargs)
    for name in (
        "_new_session",
        "_open_history",
        "_open_hermes_sessions",
        "_side_chat_active",
        "_set_projects_folder",
        "_create_desktop_shortcut",
        "_close_current_session",
        "_stop_active",
        "_attach_active",
        "_slash_active",
        "_saved_prompts_active",
        "_compact_active",
        "_new_conversation_active",
        "_find_active",
        "_task_list_active",
        "_queued_messages_active",
        "_computer_use_browse_active",
        "_find_in_all_conversations",
        "_send_to_tab_active",
        "_pass_on_waiting_reply",
        "_jump_to_latest_response",
        "_export_active",
        "_backup_history_active",
        "_restore_history_active",
        "_import_history_active",
        "_kept_history_active",
    ):
        setattr(window, name, lambda: None)
    try:
        yield window
    finally:
        window.Destroy()


def test_recent_conversations_submenu_holds_history_protection(frame):
    menu = app.MainFrame._build_file_menu(frame)
    submenu = next(
        item.GetSubMenu()
        for item in menu.GetMenuItems()
        if item.GetItemLabelText().startswith("Recent Conversations")
    )
    labels = " | ".join(item.GetItemLabelText() for item in submenu.GetMenuItems())
    for wanted in (
        "Export Conversations",
        "Back Up BlindPilot",
        "Restore from Backup",
        "Import Conversation",
        "Kept Transcripts",
    ):
        assert wanted in labels, f"Recent Conversations has no {wanted!r}: {labels}"
