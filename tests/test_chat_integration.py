"""Chat mode's plumbing: where its data goes, and how AccessibleAI's is imported."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import chat_integration
from accessible_ai import logging_setup
from accessible_ai.models import Conversation, Message, MessageAttachment
from accessible_ai.storage import paths
from accessible_ai.storage.database import Database


def test_the_chat_log_path_is_not_fixed_at_import_time(monkeypatch, tmp_path):
    """Importing the package must not decide, or create, the real data folder.

    The autouse fixture in conftest points the data folder at a temporary
    directory; a path computed once at import would have ignored it.
    """
    monkeypatch.setattr(paths, "system_config_dir", lambda: tmp_path / "elsewhere")
    assert logging_setup.log_path() == tmp_path / "elsewhere" / "blindpilot-chat.log"
    assert (tmp_path / "elsewhere").is_dir()


def test_tests_never_touch_the_installed_apps_data_folder(tmp_path):
    """What the conftest fixture promises, checked from the outside."""
    real = Path.home() / "AppData" / "Roaming" / "BlindPilot"
    assert not str(paths.app_data_dir()).startswith(str(real))
    assert not str(logging_setup.log_path()).startswith(str(real))


def test_a_damaged_accessibleai_database_is_skipped_and_leaves_no_half_copy(monkeypatch, tmp_path):
    """sqlite3 raises its own errors, not OSError, for a corrupt or locked source.

    Unhandled, that took Chat mode down on every launch. A partial copy is
    worse: it exists, so the import is never retried, and the next launch
    opens a truncated database.
    """
    source = tmp_path / "accessible_ai.sqlite3"
    source.write_bytes(b"this is not a database")
    monkeypatch.setattr(chat_integration, "_legacy_database_candidates", lambda: (source,))
    target = tmp_path / "chat.sqlite3"

    assert chat_integration.import_existing_accessible_ai_data(target) is None
    assert not target.exists()
    Database(target).list_accounts()


def test_a_sound_accessibleai_database_is_copied(monkeypatch, tmp_path):
    source = tmp_path / "accessible_ai.sqlite3"
    Database(source).create_conversation(Conversation(title="carried over"))
    monkeypatch.setattr(chat_integration, "_legacy_database_candidates", lambda: (source,))
    target = tmp_path / "chat.sqlite3"

    assert chat_integration.import_existing_accessible_ai_data(target) == source
    with sqlite3.connect(target) as copied:
        assert copied.execute("SELECT title FROM conversations").fetchone() == ("carried over",)
    copied.close()


def test_the_last_message_is_read_without_its_attachments(monkeypatch, tmp_path):
    """Checking whether the last turn was the assistant's must not reload every file.

    `_update_regenerate_enabled` runs after every turn, and a conversation
    carrying a large attachment was reading the whole blob back each time.
    """
    from contextlib import contextmanager
    from types import SimpleNamespace

    from accessible_ai.ui.chat_panel import ChatPanel

    db = Database(tmp_path / "chat.sqlite3")
    conversation_id = db.create_conversation(Conversation(title="files"))
    db.add_message(
        Message(
            conversation_id=conversation_id,
            role="user",
            content="read this",
            attachments=[MessageAttachment(filename="big.bin", data=b"x" * 4096)],
        )
    )
    answer_id = db.add_message(
        Message(conversation_id=conversation_id, role="assistant", content="done")
    )

    statements: list[str] = []
    original = Database.connect

    @contextmanager
    def traced(self):
        with original(self) as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(Database, "connect", traced)
    panel = SimpleNamespace(current_conversation_id=conversation_id, db=db)

    last = ChatPanel._last_assistant_message(panel)

    assert last is not None and last.id == answer_id and last.role == "assistant"
    assert not any("message_attachments" in sql for sql in statements)
    assert db.last_message(conversation_id).content == "done"
    assert db.last_message(conversation_id + 1) is None


def test_editing_an_account_says_a_blank_key_keeps_the_stored_one(tmp_path):
    """The field is empty on open; what an empty field means has to be said.

    Leaving the key alone is the intent (a custom server may need none, and a
    stored key must not vanish because the person tabbed past the field), so
    the label says so, and the stored key really is kept.
    """
    import wx

    from accessible_ai.models import Account, PROVIDER_OPENAI_COMPATIBLE
    from accessible_ai.ui.accounts import AccountEditorDialog

    class _Credentials:
        def __init__(self):
            self.keys = {}

        def get_api_key(self, account_id):
            return self.keys.get(account_id, "")

        def set_api_key(self, account_id, api_key):
            self.keys[account_id] = api_key

        def delete_api_key(self, account_id):
            self.keys.pop(account_id, None)

    owns_app = wx.GetApp() is None
    app = wx.GetApp() or wx.App(False)
    db = Database(tmp_path / "chat.sqlite3")
    credentials = _Credentials()
    account = Account(
        name="Local", provider=PROVIDER_OPENAI_COMPATIBLE, base_url="http://localhost:1234/v1"
    )
    account_id = db.save_account(account)
    credentials.set_api_key(account_id, "old-key")

    frame = wx.Frame(None)
    try:
        new_dialog = AccountEditorDialog(frame, db, credentials)
        try:
            assert new_dialog.api_key_label.GetLabel() == "API key:"
        finally:
            new_dialog.Destroy()

        dialog = AccountEditorDialog(frame, db, credentials, db.get_account(account_id))
        try:
            assert dialog.api_key_label.GetLabel() == "API key, blank keeps the stored key:"
            dialog.api_key.SetValue("")
            try:
                dialog.on_ok(None)
            except wx.wxAssertionError:
                # EndModal on a dialog never shown modally; the save came first.
                pass
            assert credentials.get_api_key(account_id) == "old-key"
        finally:
            dialog.Destroy()
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()
        wx.Yield()
        if owns_app:
            app.Destroy()
