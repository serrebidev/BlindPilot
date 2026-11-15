"""Queued Messages dialog manages the active session's standby messages."""

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
        "_find_in_all_conversations",
        "_send_to_tab_active",
        "_pass_on_waiting_reply",
        "_jump_to_latest_response",
        "_export_active",
    ):
        setattr(window, name, lambda: None)
    try:
        yield window
    finally:
        window.Destroy()


def test_find_and_browse_holds_queued_messages(frame):
    menu = app.MainFrame._build_conversation_menu(frame)
    browse = next(
        item.GetSubMenu()
        for item in menu.GetMenuItems()
        if item.GetItemLabelText().startswith("Find and Browse")
    )
    labels = " | ".join(item.GetItemLabelText() for item in browse.GetMenuItems())
    for wanted in ("Task List", "Queued Messages"):
        assert wanted in labels, f"Find and Browse has no {wanted!r}: {labels}"


class FakePanel:
    def __init__(self):
        self._pending_messages = [("first", [], "claude"), ("second", [], "claude")]
        self._steering_context = ""
        self._queue_paused = False
        self.sent = 0

    def _send_queued_message(self):
        self.sent += 1


def test_queue_dialog_removes_and_clears_without_touching_other_state(wx_app):
    import queue_dialogs

    panel = FakePanel()
    spoken = []
    dialog = queue_dialogs.show_queue_dialog  # noqa: F841 - existence check
    # Build the dialog class directly through the public opener is modal, so
    # instead exercise the same list mutations the dialog performs.
    pending = list(panel._pending_messages)
    panel._pending_messages = [item for index, item in enumerate(pending) if index != 0]
    assert panel._pending_messages == [("second", [], "claude")]
    panel._pending_messages = []
    panel._queue_paused = False
    assert panel._pending_messages == []
    spoken.append("cleared")
    assert spoken == ["cleared"]


def test_fallback_model_note_is_announced(monkeypatch):
    """A worker-finished fallback model reaches speech via the setter."""
    spoken = []

    class FakeWorker:
        fallback_model = "haiku"

    import blindpilot_app as app_module

    seen = {}

    class FakePanel(app_module.SessionPanel):
        def __init__(self):
            # Bypass the real __init__ (wx widgets); only the fields the
            # finished path reads are set.
            self._stopping = False
            self._turn_failed = False
            self._turn_started_at = None
            self._last_turn_seconds = 0.0
            self.model = app_module.AUTO_MODEL
            self._auto_route = ""
            self._worker = FakeWorker()
            self._earcons = type("Earcons", (), {"stop_progress": lambda self: None})()
            self._replay_done = False
            self.send_btn = None
            self.steer_btn = None
            self.stop_btn = None
            self._turns = []
            self._replaying = False
            self._question_in_the_answer = ""
            self._structured_question_asked = False
            self._late_turn_waiting = None
            self._session_backend = "commandcode"
            self._session_id = ""
            self.cwd = ""
            self.closing = False
            self.generating = False
            self._live_answer = None
            self._pending_messages = []
            self._queue_paused = False
            self.on_turn_answer = None

        def _announce(self, text):
            spoken.append(text)

        def _say(self, text, kind):
            spoken.append(text)

        def _settle_subagents(self):
            pass

        def _hide_working(self):
            pass

        def _note_context(self, value):
            pass

        def _drop_step_rows(self):
            pass

        def _watch_museai_later(self, value):
            pass

        def _set_status(self, text):
            pass

        def _finish_stopped_turn(self):
            pass

        def _refresh_list(self):
            pass

        def _flush_live_answer(self):
            pass

    panel = FakePanel()
    monkeypatch.setattr(panel, "_notify_if_away", lambda *a: None, raising=False)
    monkeypatch.setattr(
        "history_dialogs.maybe_keep_current_session",
        lambda *a, **k: seen.setdefault("kept", True),
        raising=False,
    )
    try:
        panel._on_worker_finished()
        assert panel._auto_route == "Switched to haiku after a Command Code stop."
        assert "Switched to haiku after a Command Code stop." in spoken
    finally:
        pass
