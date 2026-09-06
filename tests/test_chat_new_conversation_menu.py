"""The conversation menu's New Conversation chord reaches Chat mode too."""

from __future__ import annotations

import wx

import blindpilot_app


def _destroy(frame, app, owns_app):
    frame.Destroy()
    app.ProcessPendingEvents()
    wx.Yield()
    if owns_app:
        app.Destroy()


def test_new_conversation_stays_reachable_in_chat_mode(monkeypatch, tmp_path):
    """The item is built as an agent item, and _set_app_mode greys every agent
    item out when Chat mode is shown. So the menu said Start New Conversation,
    carried Ctrl+Shift+N on its label, and did neither: the chord was dead,
    and every message kept landing in the same conversation. Only the small
    button on the panel still worked, and nothing said the menu's copy had
    been left behind."""
    owns_app = wx.GetApp() is None
    app = wx.GetApp() or wx.App(False)
    saved: dict[str, object] = {"setup_complete": True, "app_mode": "agent"}
    monkeypatch.setattr(blindpilot_app, "_load_config", lambda: dict(saved))
    monkeypatch.setattr(blindpilot_app, "_save_config", lambda cfg: saved.update(cfg))

    frame = blindpilot_app.MainFrame(initial_cwd=str(tmp_path))
    try:
        # Find the item by label wherever the menus keep it.
        item = None
        menu_bar = frame.GetMenuBar()
        for menu_index in range(menu_bar.GetMenuCount()):
            for candidate in menu_bar.GetMenu(menu_index).GetMenuItems():
                if candidate.GetItemLabelText() == "Start New Conversation":
                    item = candidate
        assert item is not None, "the New Conversation menu item is missing"

        frame._set_app_mode(blindpilot_app.APP_MODE_AGENT)
        assert item.IsEnabled()
        # Chat mode routes the same command through the chat panel.
        assert frame._app_mode == blindpilot_app.APP_MODE_AGENT
        frame._set_app_mode(blindpilot_app.APP_MODE_CHAT)
        assert item.IsEnabled(), "New Conversation must stay enabled in Chat mode"
    finally:
        _destroy(frame, app, owns_app)


def test_new_conversation_command_reaches_the_chat_panel(monkeypatch, tmp_path):
    """_new_conversation_active is the same handler for both modes, so once
    the item is reachable the panel's own reset is what runs."""
    owns_app = wx.GetApp() is None
    app = wx.GetApp() or wx.App(False)
    saved: dict[str, object] = {"setup_complete": True, "app_mode": "agent"}
    monkeypatch.setattr(blindpilot_app, "_load_config", lambda: dict(saved))
    monkeypatch.setattr(blindpilot_app, "_save_config", lambda cfg: saved.update(cfg))

    frame = blindpilot_app.MainFrame(initial_cwd=str(tmp_path))
    try:
        frame._set_app_mode(blindpilot_app.APP_MODE_CHAT)
        assert frame.chat_panel is not None
        calls: list[bool] = []
        monkeypatch.setattr(
            frame.chat_panel, "on_new_conversation", lambda event: calls.append(True)
        )
        frame._new_conversation_active()
        assert calls == [True]
    finally:
        _destroy(frame, app, owns_app)
