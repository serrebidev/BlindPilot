"""Where a restored mode starts, and menus a keyboard can actually open.

Two things made Chat mode feel like it had to be found rather than opened. The
window asked the Agent page for focus after it was on screen -- a page Chat
mode has hidden -- so the first Tab walked a panel nobody could see until
something later moved focus back. And the Chat menu shared Alt+C with the
Conversation menu, which on Windows means the second one has no access key at
all: every Chat-only command sat behind Alt and four right arrows.
"""

from __future__ import annotations

import contextlib

import wx

import blindpilot_app
import chat_integration


@contextlib.contextmanager
def _running_app():
    owns = wx.GetApp() is None
    app = wx.GetApp() or wx.App(False)
    try:
        yield app
    finally:
        if owns:
            app.ProcessPendingEvents()


def _frame(monkeypatch, tmp_path, mode: str):
    saved: dict[str, object] = {"setup_complete": True, "app_mode": mode}
    monkeypatch.setattr(blindpilot_app, "_load_config", lambda: dict(saved))
    monkeypatch.setattr(blindpilot_app, "_save_config", lambda value: saved.update(value))
    monkeypatch.setattr(chat_integration, "database_path", lambda: tmp_path / "chat.sqlite3")
    monkeypatch.setattr(chat_integration, "import_existing_accessible_ai_data", lambda _t: None)
    return blindpilot_app.MainFrame(str(tmp_path))


def _access_keys(menubar: wx.MenuBar) -> list[str]:
    keys = []
    for index in range(menubar.GetMenuCount()):
        label = menubar.GetMenuLabel(index)
        marker = label.find("&")
        if 0 <= marker < len(label) - 1:
            keys.append(label[marker + 1].lower())
    return keys


def test_every_menu_has_an_access_key_nobody_else_has(monkeypatch, tmp_path):
    """Two menus on one letter is one menu with no access key.

    Windows opens the first match and pressing the key again does not move on
    to the second, so the Chat menu -- Accounts, Conversation profiles,
    Refresh models, History view, Diagnostics -- could not be opened at all.
    """
    with _running_app():
        frame = _frame(monkeypatch, tmp_path, "chat")
        try:
            menubar = frame.GetMenuBar()
            keys = _access_keys(menubar)
            assert len(keys) == menubar.GetMenuCount(), "a menu with no access key at all"
            assert len(set(keys)) == len(keys), f"two menus share an access key: {keys}"
        finally:
            frame.Destroy()


def test_the_chat_menu_can_be_opened_by_keyboard(monkeypatch, tmp_path):
    with _running_app():
        frame = _frame(monkeypatch, tmp_path, "chat")
        try:
            menubar = frame.GetMenuBar()
            labels = [menubar.GetMenuLabel(i) for i in range(menubar.GetMenuCount())]
            assert "Cha&t" in labels
            assert "&Conversation" in labels
        finally:
            frame.Destroy()


def test_a_button_does_not_claim_a_letter_a_menu_has_taken(monkeypatch, tmp_path):
    """A menu takes an access key from a control rather than sharing it.

    So the buttons in the chat panel have to stay off the menu bar's letters,
    or pressing one opens a menu instead.
    """
    with _running_app():
        frame = _frame(monkeypatch, tmp_path, "chat")
        try:
            menu_keys = set(_access_keys(frame.GetMenuBar()))
            panel = frame.chat_panel
            assert panel is not None
            buttons = [
                panel.add_files_button,
                panel.remove_files_button,
                panel.clear_files_button,
                panel.send_button,
                panel.regenerate_button,
                panel.stop_button,
                panel.new_button,
            ]
            for button in buttons:
                label = button.GetLabel()
                marker = label.find("&")
                if marker < 0 or marker >= len(label) - 1:
                    continue
                key = label[marker + 1].lower()
                assert key not in menu_keys, f"{label!r} is shadowed by a menu"
        finally:
            frame.Destroy()


def test_chat_mode_starts_in_the_message_box_not_the_hidden_agent_page(monkeypatch, tmp_path):
    """The Agent page queues its own focus when a session is made.

    In Chat mode that page is hidden, and the queued call used to arrive after
    the window was up and take focus into it -- so Tab and Shift+Tab moved
    around a panel that was not on screen.
    """
    with _running_app() as app:
        frame = _frame(monkeypatch, tmp_path, "chat")
        try:
            # The window queues its own opening focus. Let that run before
            # watching, so what is counted is the call this test makes.
            app.ProcessPendingEvents()
            asked: list[str] = []
            page = frame.notebook.GetCurrentPage()
            assert isinstance(page, blindpilot_app.SessionPanel)
            page.focus_prompt = lambda: asked.append("agent")
            chat_panel = frame.chat_panel
            assert chat_panel is not None
            chat_panel.message_input.SetFocus = lambda: asked.append("chat")

            frame.focus_for_mode()
            app.ProcessPendingEvents()

            assert asked == ["chat"]
        finally:
            frame.Destroy()


def test_agent_mode_still_starts_in_the_prompt(monkeypatch, tmp_path):
    with _running_app() as app:
        frame = _frame(monkeypatch, tmp_path, "agent")
        try:
            app.ProcessPendingEvents()
            asked: list[str] = []
            page = frame.notebook.GetCurrentPage()
            assert isinstance(page, blindpilot_app.SessionPanel)
            page.focus_prompt = lambda: asked.append("agent")

            frame.focus_for_mode()
            app.ProcessPendingEvents()

            assert asked == ["agent"]
        finally:
            frame.Destroy()


def test_a_hidden_agent_page_refuses_the_focus_it_was_queued(monkeypatch, tmp_path):
    """The guard that stops a queued call landing on a page nobody can see."""
    with _running_app():
        frame = _frame(monkeypatch, tmp_path, "chat")
        try:
            page = frame.notebook.GetCurrentPage()
            assert isinstance(page, blindpilot_app.SessionPanel)
            assert not page.IsShownOnScreen()
            taken: list[str] = []
            page.prompt.SetFocus = lambda: taken.append("prompt")

            page.focus_prompt()

            assert taken == []
        finally:
            frame.Destroy()
