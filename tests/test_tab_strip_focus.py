"""The session tab strip can be entered, used, and left again.

Two separate ways in which Shift+Tab into the strip used to be a dead end.

Arrowing along the strip changed the session, and showing the newly selected
page took focus off the strip and put it in that page's prompt - so the first
arrow press threw the user out of the tab control and the second never reached
it. That was fixed once, in v0.5.0, when the strip and the pages were the same
control; separating them in v0.8.0 brought it back, because wxSimplebook
focuses the page it shows from C++ on every selection change, whether or not
anybody asked it to.

And Tab out of the strip is routed by hand, which meant a route that did not
actually move focus still swallowed the key: press Tab, nothing happens, press
it again, nothing happens.

Run from the project root:

    python -m pytest tests/test_tab_strip_focus.py -q
"""

from __future__ import annotations

import contextlib

import wx

import blindpilot_app
import chat_integration


@contextlib.contextmanager
def _running_app():
    """Own the wx.App only if this test is the one that made it.

    Leaving one behind crashes the interpreter on the way out, long after
    every test has passed, which reads in the report as no failure at all.
    """
    owns = wx.GetApp() is None
    app = wx.GetApp() or wx.App(False)
    try:
        yield app
    finally:
        app.ProcessPendingEvents()
        wx.Yield()
        if owns:
            app.Destroy()


def _frame(monkeypatch, tmp_path):
    saved: dict[str, object] = {"setup_complete": True, "app_mode": "agent"}
    monkeypatch.setattr(blindpilot_app, "_load_config", lambda: dict(saved))
    monkeypatch.setattr(blindpilot_app, "_save_config", lambda value: saved.update(value))
    monkeypatch.setattr(chat_integration, "database_path", lambda: tmp_path / "chat.sqlite3")
    monkeypatch.setattr(chat_integration, "import_existing_accessible_ai_data", lambda _t: None)
    return blindpilot_app.MainFrame(str(tmp_path))


def test_arrowing_the_strip_leaves_focus_on_the_strip(monkeypatch, tmp_path):
    with _running_app():
        frame = _frame(monkeypatch, tmp_path)
        try:
            frame._add_session(str(tmp_path))
            assert frame.notebook.GetPageCount() == 2

            spoken: list[str] = []
            monkeypatch.setattr(blindpilot_app, "announce", lambda text: spoken.append(text))
            strip_focus: list[str] = []
            frame.tab_switcher.SetFocus = lambda: strip_focus.append("strip")
            # Where focus actually is cannot be read back on a headless runner,
            # so say it is on the strip - which is the case this is about.
            monkeypatch.setattr(
                type(frame), "_focus_is_within", staticmethod(lambda focus, control: True)
            )

            incoming = frame.notebook.GetPage(0)
            enabled_during_switch: list[bool] = []
            frame.notebook.Bind(
                wx.EVT_BOOKCTRL_PAGE_CHANGED,
                lambda event: (enabled_during_switch.append(incoming.IsEnabled()), event.Skip()),
            )

            frame._select_session(0)
            wx.Yield()

            assert frame.notebook.GetSelection() == 0
            # The page cannot accept the focus the book offers it, and the
            # strip is asked for it back either way.
            assert enabled_during_switch == [False]
            assert incoming.IsEnabled()
            assert strip_focus == ["strip"]
            # The native tab control says which tab this is; saying it again
            # here would speak everything twice.
            assert spoken == []
        finally:
            frame.Destroy()


def test_switching_sessions_from_elsewhere_still_lands_in_the_conversation(monkeypatch, tmp_path):
    with _running_app():
        frame = _frame(monkeypatch, tmp_path)
        try:
            frame._add_session(str(tmp_path))
            spoken: list[str] = []
            monkeypatch.setattr(blindpilot_app, "announce", lambda text: spoken.append(text))
            monkeypatch.setattr(
                type(frame), "_focus_is_within", staticmethod(lambda focus, control: False)
            )
            prompted: list[str] = []
            frame.notebook.GetPage(0).focus_prompt = lambda: prompted.append("prompt")

            frame._cycle_tab(+1)
            wx.Yield()

            assert frame.notebook.GetSelection() == 0
            assert prompted == ["prompt"]
            assert spoken and spoken[0].startswith("Session 1 of 2")
        finally:
            frame.Destroy()


def test_a_boundary_move_that_moved_nothing_hands_the_key_back():
    with _running_app():
        here = wx.Window.FindFocus()
        assert blindpilot_app.MainFrame._moved_focus(here, lambda: None) is False


def test_the_first_control_falls_through_to_the_prompt_when_responses_cannot_take_focus(
    monkeypatch, tmp_path
):
    with _running_app():
        frame = _frame(monkeypatch, tmp_path)
        try:
            page = frame.notebook.GetCurrentPage()
            page._displayed.append("a row")
            asked: list[str] = []
            page.prompt.SetFocus = lambda: asked.append("prompt")
            responses = page._responses_ctrl()
            responses.SetFocus = lambda: asked.append("responses")

            responses.Hide()
            page.focus_first_control()
            assert asked == ["prompt"]

            responses.Show()
            page.focus_first_control()
            assert asked == ["prompt", "responses"]
        finally:
            frame.Destroy()


def test_tab_switcher_mirrors_the_session_pages(monkeypatch, tmp_path):
    """The strip is the session navigation: it follows the book, and drives it."""
    with _running_app():
        frame = _frame(monkeypatch, tmp_path)
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
