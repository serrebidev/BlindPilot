"""Backup, restore, kept transcripts, and imports for history protection."""

from __future__ import annotations

import json
import threading
import zipfile

import pytest

import history_backup as backup
from accessible_ai.models import Conversation, Message, MessageAttachment
from accessible_ai.storage.database import Database


@pytest.fixture()
def live_db(tmp_path):
    """A small live database plus a settings file, under tmp."""
    db_path = tmp_path / "live" / "chat.sqlite3"
    database = Database(db_path)
    account = database.save_account(
        __import__("accessible_ai.models", fromlist=["Account"]).Account(
            name="Acct", provider="openrouter", base_url="https://x.test"
        )
    )
    conversation_id = database.create_conversation(Conversation(title="T1", account_id=account))
    database.add_message(
        Message(
            conversation_id=conversation_id,
            role="user",
            content="hi",
            attachments=[MessageAttachment(filename="a.txt", mime_type="text/plain", data=b"abc")],
        )
    )
    database.add_message(
        Message(conversation_id=conversation_id, role="assistant", content="hello")
    )
    settings = tmp_path / "live" / "settings.json"
    settings.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    return tmp_path, db_path, settings


def test_backup_preview_round_trip(live_db):
    tmp, db_path, settings = live_db
    archive = tmp / "b.zip"
    backup.create_backup(archive, db_path, settings, None, False)
    preview = backup.preview_backup(archive)
    assert preview.conversations == 1
    assert preview.messages == 2
    assert preview.accounts == 1
    assert preview.total_bytes > 0
    assert preview.exclusions  # The manifest says what is left out.


def test_backup_never_contains_key_material(live_db):
    tmp, db_path, settings = live_db
    archive = tmp / "b.zip"
    backup.create_backup(archive, db_path, settings, None, False)
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        assert any("api_key" in name.lower() for name in names) is False
        assert {n.split("/")[-1] for n in names} <= {
            "manifest.json",
            "chat.sqlite3",
            "settings.json",
            "index.json",
        } | {n.split("/")[-1] for n in names if n.startswith("kept_transcripts/")}


def test_preview_rejects_a_non_backup(live_db):
    tmp, _, _ = live_db
    bad = tmp / "bad.zip"
    with zipfile.ZipFile(bad, "w") as bundle:
        bundle.writestr("nope.txt", "x")
    with pytest.raises(backup.HistoryBackupError):
        backup.preview_backup(bad)


def test_preview_rejects_traversal_entries(live_db):
    tmp, _, _ = live_db
    bad = tmp / "evil.zip"
    with zipfile.ZipFile(bad, "w") as bundle:
        bundle.writestr("../escape.txt", "x")
    with pytest.raises(backup.HistoryBackupError):
        backup.preview_backup(bad)


def test_cancelled_backup_leaves_no_final_file(live_db):
    tmp, db_path, settings = live_db
    cancel = threading.Event()
    cancel.set()
    archive = tmp / "b.zip"
    with pytest.raises(backup.HistoryBackupError):
        backup.create_backup(archive, db_path, settings, None, False, cancel=cancel)
    assert not archive.exists()


def test_restore_preserves_live_state_then_installs(live_db):
    tmp, db_path, settings = live_db
    archive = tmp / "b.zip"
    backup.create_backup(archive, db_path, settings, None, False)
    # Change the live state after the backup.
    database = Database(db_path)
    database.create_conversation(Conversation(title="Later"))
    preview = backup.restore_backup(archive, db_path, settings, None)
    assert preview.conversations == 1
    restored = Database(db_path)
    assert [c.title for c in restored.list_conversations()] == ["T1"]
    aside = sorted(db_path.parent.glob("chat.sqlite3.before-restore-*"))
    assert aside, "live database must be kept aside, not overwritten"


def test_restore_failure_keeps_live_state(live_db):
    tmp, db_path, settings = live_db
    restored_db = Database(db_path)
    before = [c.title for c in restored_db.list_conversations()]
    with pytest.raises(backup.HistoryBackupError):
        backup.restore_backup(tmp / "does-not-exist.zip", db_path, settings, None)
    after = [c.title for c in Database(db_path).list_conversations()]
    assert after == before


def test_kept_transcripts_copy_hash_and_delete_never_touch_source(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_bytes(b'{"line": 1}\n')
    kept = backup.KeptTranscripts(tmp_path / "kept")
    assert kept.keep(source) is True
    assert kept.keep(source) is True  # Same content: no duplicate.
    count, total = kept.storage_report()
    assert count == 1 and total == len(b'{"line": 1}\n')
    (entry,) = kept.entries()
    assert (kept.copies / entry.copy_name).read_bytes() == source.read_bytes()
    removed = kept.delete([entry.copy_name])
    assert removed == 1
    assert source.read_bytes() == b'{"line": 1}\n'
    assert kept.storage_report() == (0, 0)


def test_kept_transcripts_missing_source_is_false(tmp_path):
    kept = backup.KeptTranscripts(tmp_path / "kept")
    assert kept.keep(tmp_path / "missing.jsonl") is False


def _write_bskc(path, conversation):
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("conversation.json", json.dumps(conversation))
        bundle.writestr("attachments/note.txt", b"attached bytes")


def test_bskc_import_round_trip(tmp_path):
    bskc = tmp_path / "c.bskc"
    _write_bskc(
        bskc,
        {
            "version": 3,
            "title": "Basilisk chat",
            "systems": [{"role": "system", "content": "Be brief."}],
            "messages": [
                {
                    "request": {
                        "role": "user",
                        "content": "Question?",
                        "attachments": [{"type": "file", "location": "attachments/note.txt"}],
                    },
                    "response": {"role": "assistant", "content": "Answer."},
                    "model": {"provider_id": "openai", "model_id": "gpt-x"},
                    "temperature": 0.5,
                },
                {
                    "request": {"role": "user", "content": "Second"},
                    "response": None,
                    "model": {"provider_id": "openai", "model_id": "gpt-x"},
                },
            ],
        },
    )
    database = Database(tmp_path / "chat.sqlite3")
    report = backup.import_bskc(bskc, database)
    assert report.title == "Basilisk chat"
    assert report.messages == 3
    assert report.attachments == 1
    assert any("temperature" in item for item in report.unsupported)
    assert any("gpt-x" in item for item in report.unsupported)
    messages = database.list_messages(report.conversation_id)
    assert [m.content for m in messages] == ["Question?", "Answer.", "Second"]
    assert messages[0].attachments[0].data == b"attached bytes"
    conversation = database.get_conversation(report.conversation_id)
    assert conversation is not None and conversation.system_prompt_snapshot == "Be brief."


def test_bskc_import_rejects_a_non_zip(tmp_path):
    bad = tmp_path / "bad.bskc"
    bad.write_bytes(b"not a zip")
    database = Database(tmp_path / "chat.sqlite3")
    with pytest.raises(backup.HistoryBackupError):
        backup.import_bskc(bad, database)


def test_interchange_round_trip(live_db):
    tmp, db_path, _ = live_db
    database = Database(db_path)
    (source,) = database.list_conversations()
    data = backup.export_interchange(source.id, database)
    database2 = Database(tmp / "second" / "chat.sqlite3")
    report = backup.import_interchange(data, database2)
    assert report.messages == 2
    assert report.attachments == 1
    messages = database2.list_messages(report.conversation_id)
    assert messages[0].attachments[0].data == b"abc"
    with pytest.raises(backup.HistoryBackupError):
        backup.import_interchange({"format": "nope"}, database2)
