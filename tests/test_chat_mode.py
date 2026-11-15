"""The Agent/Chat selector and embedded chat surface stay accessible."""

from __future__ import annotations

import wx

import blindpilot_app
import chat_integration


class _TabEvent:
    def __init__(self, *, shift: bool):
        self._shift = shift
        self.skipped = False

    def GetKeyCode(self):
        return wx.WXK_TAB

    def ShiftDown(self):
        return self._shift

    def Skip(self):
        self.skipped = True


def test_mode_combo_opens_embedded_chat_without_replacing_agent_sessions(monkeypatch, tmp_path):
    owns_app = wx.GetApp() is None
    app = wx.GetApp() or wx.App(False)
    saved: dict[str, object] = {"setup_complete": True, "app_mode": "agent"}
    monkeypatch.setattr(blindpilot_app, "_load_config", lambda: dict(saved))
    monkeypatch.setattr(blindpilot_app, "_save_config", lambda value: saved.update(value) or True)
    monkeypatch.setattr(chat_integration, "database_path", lambda: tmp_path / "chat.sqlite3")
    monkeypatch.setattr(
        chat_integration,
        "import_existing_accessible_ai_data",
        lambda _target: None,
    )

    frame = blindpilot_app.MainFrame(str(tmp_path))
    try:
        assert frame.mode_combo.GetName() == "Mode"
        assert [frame.mode_combo.GetString(index) for index in range(2)] == [
            "Agent",
            "Chat",
        ]
        assert frame.notebook.IsShown()
        assert isinstance(frame.notebook, wx.Simplebook)
        assert not frame._chat_refresh_item.IsEnabled()
        assert frame.tab_switcher.GetName() == "Session tabs"
        assert frame.tab_switcher.IsShown()
        page = frame.notebook.GetCurrentPage()
        assert isinstance(page, blindpilot_app.SessionPanel)

        boundary_calls: list[str] = []
        page.focus_first_control = lambda: boundary_calls.append("first")
        page.focus_last_control = lambda: boundary_calls.append("last")
        page.focus_first_action_delayed = lambda: boundary_calls.append("delayed")
        page._focus_before = lambda: boundary_calls.append("before")
        page._focus_after = lambda: boundary_calls.append("after")

        assert page.prompt.GetWindowStyleFlag() & wx.TE_RICH2
        assert frame._route_agent_tab(frame.tab_switcher, shift=False)
        assert frame._route_agent_tab(frame.mode_combo, shift=True)
        assert frame._route_agent_tab(page.prompt, shift=False)
        frame._on_mode_combo_key(_TabEvent(shift=True))
        page._on_list_key(_TabEvent(shift=True))
        page._on_mode_key(_TabEvent(shift=False))
        page._on_prompt_key(_TabEvent(shift=True))
        assert boundary_calls == [
            "first",
            "last",
            "delayed",
            "last",
            "before",
            "after",
            "before",
        ]
        # Shift+Tab out of the tab strip is the only way back to Mode. Where
        # focus actually lands cannot be read back on a headless runner, so
        # record the request rather than querying the platform for it.
        mode_focus_calls: list[str] = []
        frame.mode_combo.SetFocus = lambda: mode_focus_calls.append("mode")
        assert frame._route_agent_tab(frame.tab_switcher, shift=True)
        assert mode_focus_calls == ["mode"]

        frame._set_app_mode(blindpilot_app.APP_MODE_CHAT, announce_change=False)

        assert frame.chat_panel is not None
        assert frame.chat_panel.IsShown()
        assert not frame.notebook.IsShown()
        assert frame.chat_panel.message_input.GetName() == "Message"
        assert frame.chat_panel.account_choice.GetName() == "Account"
        assert frame.chat_panel.model_combo.GetName() == "Model"
        assert isinstance(frame.chat_panel.model_combo, wx.Choice)
        assert frame._chat_model_order_items["newest"].IsChecked()
        assert frame._chat_refresh_item.IsEnabled()
        assert frame._chat_accounts_item.IsEnabled()
        assert frame._chat_profiles_item.IsEnabled()
        frame._set_chat_model_order("name_descending")
        assert frame._chat_model_order == "name_descending"
        assert frame._chat_model_order_items["name_descending"].IsChecked()
        assert saved["chat_model_order"] == "name_descending"

        frame._set_chat_history_view("text")
        assert frame.chat_panel.transcript.IsShown()
        assert not frame.chat_panel.history_list.IsShown()
        assert frame._chat_history_text_item.IsChecked()
        assert saved["app_mode"] == "chat"
    finally:
        if frame.chat_panel is not None:
            frame.chat_panel.shutdown()
        frame.Destroy()
        app.ProcessPendingEvents()
        wx.Yield()
        if owns_app:
            app.Destroy()


def _frame(monkeypatch, tmp_path, saved):
    monkeypatch.setattr(blindpilot_app, "_load_config", lambda: dict(saved))
    monkeypatch.setattr(blindpilot_app, "_save_config", lambda value: saved.update(value) or True)
    monkeypatch.setattr(chat_integration, "database_path", lambda: tmp_path / "chat.sqlite3")
    monkeypatch.setattr(chat_integration, "import_existing_accessible_ai_data", lambda _t: None)
    return blindpilot_app.MainFrame(str(tmp_path))


def _destroy(frame, app, owns_app):
    frame.Destroy()
    app.ProcessPendingEvents()
    wx.Yield()
    if owns_app:
        app.Destroy()


def test_a_chat_mode_that_cannot_open_falls_back_to_agent_mode_completely(monkeypatch, tmp_path):
    """Before, the handler returned early after the message box: the saved
    mode still said chat (so the box came back on every launch), Compact and
    the Hermes list had been refreshed for a mode the window was not in, and
    nothing had focus."""
    owns_app = wx.GetApp() is None
    app = wx.GetApp() or wx.App(False)
    saved: dict[str, object] = {"setup_complete": True, "app_mode": "chat"}

    def broken(*_args, **_kwargs):
        raise RuntimeError("the chat database is unreadable")

    monkeypatch.setattr(chat_integration, "create_chat_panel", broken)
    boxes: list[str] = []
    monkeypatch.setattr(wx, "MessageBox", lambda message, *args, **kwargs: boxes.append(message))

    frame = _frame(monkeypatch, tmp_path, saved)
    try:
        assert boxes and "Chat mode could not be opened" in boxes[0]
        assert saved["app_mode"] == "agent"
        assert frame._app_mode == blindpilot_app.APP_MODE_AGENT
        assert frame.mode_combo.GetSelection() == 0
        assert frame.notebook.IsShown()
        assert frame._compact_item.IsEnabled()
        assert all(item.IsEnabled() for item in frame._agent_menu_items)
        assert not frame._chat_refresh_item.IsEnabled()
    finally:
        _destroy(frame, app, owns_app)


def test_agent_only_commands_are_greyed_out_in_chat_mode(monkeypatch, tmp_path):
    """The notebook is hidden in Chat mode, and every one of these acts on
    its current page: Find opened a search over an invisible list, New
    Session put focus into a control nobody could see. Disabled is what a
    screen reader announces, so the item says why it does nothing."""
    owns_app = wx.GetApp() is None
    app = wx.GetApp() or wx.App(False)
    saved: dict[str, object] = {"setup_complete": True, "app_mode": "agent"}

    frame = _frame(monkeypatch, tmp_path, saved)
    try:
        labels = {item.GetItemLabelText() for item in frame._agent_menu_items}
        assert {"Side Chat in This Folder", "Find in Responses…", "Backend"} <= labels, labels
        assert all(item.IsEnabled() for item in frame._agent_menu_items)

        frame._set_app_mode(blindpilot_app.APP_MODE_CHAT)
        # The muse.ai submenu sits out of the Model menu until a muse.ai tab
        # needs it, and is set as it goes back in; wxGTK cannot enable it while out.
        shown = [i for i in frame._agent_menu_items if i in i.GetMenu().GetMenuItems()]
        assert not any(item.IsEnabled() for item in shown)
        assert not frame._compact_item.IsEnabled()
        assert not frame._connect_item.IsEnabled()

        frame._set_app_mode(blindpilot_app.APP_MODE_AGENT)
        assert all(item.IsEnabled() for item in frame._agent_menu_items)
        assert frame._compact_item.IsEnabled()
    finally:
        _destroy(frame, app, owns_app)


def test_chat_mode_has_tabs_like_agent_mode(monkeypatch, tmp_path):
    """Chat mode keeps several conversations open as tabs. The same strip,
    Ctrl+T, Ctrl+W and Ctrl+Tab that work on Agent sessions work on chats,
    and switching modes puts each mode's own tabs back on the strip."""
    owns_app = wx.GetApp() is None
    app = wx.GetApp() or wx.App(False)
    saved: dict[str, object] = {"setup_complete": True, "app_mode": "agent"}
    monkeypatch.setattr(blindpilot_app, "announce", lambda _text: None)
    frame = _frame(monkeypatch, tmp_path, saved)
    try:
        frame._set_app_mode(blindpilot_app.APP_MODE_CHAT, announce_change=False)
        book = frame.chat_book
        assert book is not None and book.GetPageCount() == 1
        assert frame.tab_switcher.IsShown()
        assert frame.tab_switcher.GetName() == "Chat tabs"
        assert frame.tab_switcher.GetPageText(0) == "New chat"
        assert frame._new_tab_item.IsEnabled()
        assert frame._new_tab_item.GetItemLabelText() == "New Chat"
        assert frame._close_tab_item.GetItemLabelText() == "Close Chat"

        first = frame.chat_panel
        frame._new_tab_command()
        second = frame.chat_panel
        assert second is not first
        assert book.GetPageCount() == 2 and book.GetSelection() == 1
        assert frame.tab_switcher.GetPageCount() == 2

        second._set_tab_title("Rust lifetimes question")
        assert frame.tab_switcher.GetPageText(1) == "Rust lifetimes question"

        frame._cycle_tab(+1)
        assert book.GetSelection() == 0 and frame.chat_panel is first
        frame._jump_to_tab(1)
        assert frame.chat_panel is second

        frame._close_tab_command()
        assert book.GetPageCount() == 1 and frame.chat_panel is first
        frame._close_tab_command()
        assert book.GetPageCount() == 1

        frame._set_app_mode(blindpilot_app.APP_MODE_AGENT, announce_change=False)
        assert frame.tab_switcher.GetName() == "Session tabs"
        assert frame.tab_switcher.GetPageCount() == frame.notebook.GetPageCount()
        assert frame._new_tab_item.GetItemLabelText() == "New Session…"
    finally:
        if frame.chat_book is not None:
            for index in range(frame.chat_book.GetPageCount()):
                frame.chat_book.GetPage(index).shutdown()
        _destroy(frame, app, owns_app)
