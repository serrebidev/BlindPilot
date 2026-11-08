"""FreeBuff Desktop history is reviewed before it starts a fresh conversation."""

from __future__ import annotations

import json
import sqlite3

import blindpilot_app as app
import pytest
import session_history
import wx
from test_tab_strip_focus import _frame, _running_app
from test_history_resume_async import _wait


def _entry(cwd: str) -> session_history.HistoryEntry:
    return session_history.HistoryEntry(
        "freebuff", "desktop-original", "Desktop chat", "desktop-v2.db", 1, cwd=cwd
    )


def _turns() -> list[session_history.HistoryTurn]:
    return [
        session_history.HistoryTurn("Old request", "Old answer"),
        session_history.HistoryTurn("Sensitive request", "Sensitive answer"),
    ]


def test_import_preview_can_exclude_turn_edit_context_and_cancel_without_sending(
    monkeypatch, tmp_path
):
    with _running_app():
        frame = _frame(monkeypatch, tmp_path)
        sent = []
        monkeypatch.setattr(app.backend_workers, "create_worker", lambda *_a: sent.append(_a))
        try:
            dialog = app.DesktopImportDialog(
                frame, _entry(str(tmp_path)), _turns(), "codex", str(tmp_path)
            )
            try:
                _wait(lambda: dialog.context.IsEnabled())
                assert "Codex" in dialog.backend_text.GetLabel()
                assert "Old request" in dialog.context.GetValue()
                dialog.turns.Check(1, False)
                dialog._selection_changed()
                _wait(lambda: dialog.context.IsEnabled())
                assert "Sensitive request" not in dialog.context.GetValue()
                dialog.context.SetValue("Reviewed and edited context")
                assert dialog.context.GetValue() == "Reviewed and edited context"
                assert sent == []
            finally:
                dialog.Destroy()
            assert frame.notebook.GetPageCount() == 1
            assert sent == []
        finally:
            frame.Destroy()


@pytest.mark.parametrize("saved_folder_exists", [True, False])
@pytest.mark.parametrize("intent_selection", [0, 1])
def test_approval_sends_reviewed_context_to_new_selected_backend_session(
    monkeypatch, tmp_path, saved_folder_exists, intent_selection
):
    with _running_app():
        frame = _frame(monkeypatch, tmp_path)
        project = tmp_path / "saved project"
        if saved_folder_exists:
            project.mkdir()
        frame._set_backend("codex")
        requests = []
        spoken = []

        class Worker:
            def start(self):
                pass

        def create_worker(backend, request):
            requests.append((backend, request))
            return Worker()

        monkeypatch.setattr(app.backend_workers, "create_worker", create_worker)
        monkeypatch.setattr(app, "announce", lambda text, **_k: spoken.append(text))

        def review(dialog):
            assert requests == []
            _wait(lambda: dialog.context.IsEnabled())
            assert "Codex" in dialog.backend_text.GetLabel()
            assert (
                "Saved folder unavailable" in dialog.folder_text.GetLabel()
            ) != saved_folder_exists
            dialog.turns.Check(1, False)
            dialog._selection_changed()
            _wait(lambda: dialog.context.IsEnabled())
            dialog.context.SetValue("Reviewed and edited context")
            dialog.instruction.SetValue("Fix the tests")
            dialog.intent.SetSelection(intent_selection)
            dialog._approve()
            return wx.ID_OK

        monkeypatch.setattr(app.DesktopImportDialog, "ShowModal", review)
        try:
            frame._import_desktop_history(_entry(str(project)), _turns())
            assert len(requests) == 1
            backend, request = requests[0]
            assert backend == "codex"
            assert request.session_id is None
            assert request.cwd == str(project if saved_folder_exists else tmp_path)
            assert "Reviewed and edited context" in request.prompt
            assert "Sensitive request" not in request.prompt
            assert "Fix the tests" in request.prompt
            assert "reference only" in request.prompt
            assert "fresh instruction below governs" in request.prompt
            assert ("Continue the prior work only" in request.prompt) == (intent_selection == 1)
            panel = frame.notebook.GetCurrentPage()
            notes = [row for row in panel._rows if row.label.startswith("Imported context:")]
            assert len(notes) == 1 and notes[0].payload == "Reviewed and edited context"
            assert any("unavailable" in text for text in spoken) != saved_folder_exists
        finally:
            frame.Destroy()


def test_cancelling_review_opens_no_tab_and_sends_nothing(monkeypatch, tmp_path):
    with _running_app():
        frame = _frame(monkeypatch, tmp_path)
        sent = []
        monkeypatch.setattr(app.backend_workers, "create_worker", lambda *_a: sent.append(_a))
        monkeypatch.setattr(app, "announce", lambda *_a, **_k: None)
        monkeypatch.setattr(app.DesktopImportDialog, "ShowModal", lambda _dialog: wx.ID_CANCEL)
        try:
            frame._import_desktop_history(_entry(str(tmp_path)), _turns())
            assert frame.notebook.GetPageCount() == 1
            assert sent == []
        finally:
            frame.Destroy()


def test_history_picker_import_route_reaches_fresh_send_only_after_review(monkeypatch, tmp_path):
    with _running_app():
        frame = _frame(monkeypatch, tmp_path)
        frame._set_backend("freebuff")
        monkeypatch.setattr(session_history, "_home", lambda: tmp_path)
        project = tmp_path / ".config" / "freebuff-desktop" / "projects" / "app-1234"
        project.mkdir(parents=True)
        with sqlite3.connect(project / "desktop-v2.db") as db:
            db.execute(
                "CREATE TABLE threads (id TEXT, title TEXT, project_path TEXT, updated_at INTEGER)"
            )
            db.execute(
                "INSERT INTO threads VALUES (?,?,?,?)",
                ("desktop-original", "Desktop chat", str(tmp_path), 5000),
            )
            db.execute(
                "CREATE TABLE messages (seq INTEGER, thread_id TEXT, role TEXT, parts_json TEXT)"
            )
            db.execute(
                "INSERT INTO messages VALUES (?,?,?,?)",
                (
                    1,
                    "desktop-original",
                    "user",
                    json.dumps([{"kind": "text", "text": "Old request"}]),
                ),
            )
        requests = []

        class Worker:
            def start(self):
                pass

        monkeypatch.setattr(
            app.backend_workers,
            "create_worker",
            lambda backend, request: (requests.append((backend, request)), Worker())[1],
        )
        monkeypatch.setattr(app, "announce", lambda *_a, **_k: None)

        def pick(dialog):
            _wait(lambda: len(dialog._shown) == 1)
            assert dialog.import_button.IsEnabled()
            assert requests == []
            dialog._import()
            return wx.ID_OK

        def review(dialog):
            assert requests == []
            _wait(lambda: dialog.context.IsEnabled())
            dialog.instruction.SetValue("Start here")
            dialog._approve()
            return wx.ID_OK

        monkeypatch.setattr(app.HistoryDialog, "ShowModal", pick)
        monkeypatch.setattr(app.DesktopImportDialog, "ShowModal", review)
        try:
            frame._open_history()
            _wait(lambda: len(requests) == 1)
            assert requests[0][1].session_id is None
            assert frame.notebook.GetPageCount() == 2
        finally:
            progress = getattr(frame, "_history_open_progress", None)
            if progress:
                progress.Destroy()
            frame.Destroy()
