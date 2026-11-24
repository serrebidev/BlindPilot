"""Switching back to the window must not move focus to the Attach button.

Issue #25: Alt+Tab back into BlindPilot landed on Attach instead of the
prompt, so every message started with a hunt for the field it is typed in.
The frame now remembers which of its controls had focus when it was
deactivated and restores that control on activation, falling back to the
mode's starting control (the prompt) when there is nothing to restore.
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


def _frame(monkeypatch, tmp_path):
    saved: dict[str, object] = {"setup_complete": True, "app_mode": "agent"}
    monkeypatch.setattr(blindpilot_app, "_load_config", lambda: dict(saved))
    monkeypatch.setattr(blindpilot_app, "_save_config", lambda value: saved.update(value))
    monkeypatch.setattr(chat_integration, "database_path", lambda: tmp_path / "chat.sqlite3")
    monkeypatch.setattr(chat_integration, "import_existing_accessible_ai_data", lambda _t: None)
    return blindpilot_app.MainFrame(str(tmp_path))


def _activate(frame, active: bool) -> wx.ActivateEvent:
    return wx.ActivateEvent(wx.wxEVT_ACTIVATE, active, frame.GetId())


def test_activation_restores_the_control_that_had_focus(monkeypatch, tmp_path):
    with _running_app() as app:
        frame = _frame(monkeypatch, tmp_path)
        try:
            app.ProcessPendingEvents()
            page = frame.notebook.GetCurrentPage()
            assert isinstance(page, blindpilot_app.SessionPanel)
            monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: page.prompt))
            frame._on_window_activate(_activate(frame, False))

            taken: list[str] = []
            # The test frame is never shown; on a real screen the prompt is.
            page.prompt.IsShownOnScreen = lambda: True
            page.prompt.SetFocus = lambda: taken.append("prompt")
            frame.focus_for_mode = lambda: taken.append("fallback")

            frame._on_window_activate(_activate(frame, True))

            assert taken == ["prompt"]
        finally:
            frame.Destroy()


def test_activation_without_a_remembered_control_starts_in_the_prompt(monkeypatch, tmp_path):
    with _running_app() as app:
        frame = _frame(monkeypatch, tmp_path)
        try:
            app.ProcessPendingEvents()
            taken: list[str] = []
            frame.focus_for_mode = lambda: taken.append("fallback")

            frame._on_window_activate(_activate(frame, True))

            assert taken == ["fallback"]
        finally:
            frame.Destroy()


def test_a_remembered_control_that_is_gone_falls_back(monkeypatch, tmp_path):
    with _running_app() as app:
        frame = _frame(monkeypatch, tmp_path)
        try:
            app.ProcessPendingEvents()

            class Gone:
                def IsShownOnScreen(self) -> bool:
                    return False

                def IsEnabled(self) -> bool:
                    return True

                def SetFocus(self) -> None:
                    raise AssertionError("a hidden control must not take focus")

            frame._focus_before_deactivate = Gone()  # type: ignore[assignment]
            taken: list[str] = []
            frame.focus_for_mode = lambda: taken.append("fallback")

            frame._on_window_activate(_activate(frame, True))

            assert taken == ["fallback"]
        finally:
            frame.Destroy()
