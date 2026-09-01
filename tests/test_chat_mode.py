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
    monkeypatch.setattr(blindpilot_app, "_save_config", lambda value: saved.update(value))
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
        assert not hasattr(frame, "_on_picker_focus")
        assert not hasattr(frame, "session_picker")
        assert frame.tab_switcher.GetName() == "Session tabs"
        assert frame.tab_switcher.IsShown()
        page = frame.notebook.GetCurrentPage()
        assert isinstance(page, blindpilot_app.SessionPanel)
        assert not hasattr(page, "_on_mode_picker_focus")

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
        assert not hasattr(frame.chat_panel, "accounts_button")
        assert not hasattr(frame.chat_panel, "profiles_button")
        assert not hasattr(frame.chat_panel, "diagnostics_button")
        assert not hasattr(frame.chat_panel, "refresh_models_button")
        assert not hasattr(frame.chat_panel, "history_view_choice")
        assert frame._chat_refresh_item.IsEnabled()
        assert frame._chat_accounts_item.IsEnabled()
        assert frame._chat_profiles_item.IsEnabled()

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


def test_tab_switcher_mirrors_the_session_pages(monkeypatch, tmp_path):
    """The strip is the session navigation: it follows the book, and drives it."""
    owns_app = wx.GetApp() is None
    app = wx.GetApp() or wx.App(False)
    saved: dict[str, object] = {"setup_complete": True, "app_mode": "agent"}
    monkeypatch.setattr(blindpilot_app, "_load_config", lambda: dict(saved))
    monkeypatch.setattr(blindpilot_app, "_save_config", lambda value: saved.update(value))

    frame = blindpilot_app.MainFrame(str(tmp_path))
    try:
        switcher, book = frame.tab_switcher, frame.notebook

        def labels(control):
            return [control.GetPageText(i) for i in range(control.GetPageCount())]

        assert labels(switcher) == labels(book)
        assert switcher.GetSelection() == book.GetSelection() == 0

        second = tmp_path / "second"
        second.mkdir()
        frame._add_session(str(second))
        assert switcher.GetPageCount() == book.GetPageCount() == 2
        assert labels(switcher) == labels(book)
        assert switcher.GetSelection() == book.GetSelection() == 1

        # Renaming a conversation relabels its tab.
        panel = book.GetPage(1)
        frame._panel_title_changed(panel, "Fix the parser")
        assert labels(switcher) == labels(book)
        assert "Fix the parser" in switcher.GetPageText(1)

        # Ctrl+Tab wraps, and the strip follows.
        frame._cycle_tab(+1)
        assert book.GetSelection() == 0
        assert switcher.GetSelection() == 0
        frame._cycle_tab(-1)
        assert book.GetSelection() == 1
        assert switcher.GetSelection() == 1

        # Arrowing the strip switches the session.
        event = wx.BookCtrlEvent(wx.wxEVT_BOOKCTRL_PAGE_CHANGED, switcher.GetId(), 0, 1)
        frame._on_tab_switcher_changed(event)
        assert book.GetSelection() == 0

        frame._close_current_session()
        assert switcher.GetPageCount() == book.GetPageCount() == 1
        assert labels(switcher) == labels(book)
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()
        wx.Yield()
        if owns_app:
            app.Destroy()
