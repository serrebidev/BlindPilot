"""A conversation branch owns new rows and preserves its source exactly."""

import sqlite3

import pytest

from accessible_ai.models import Account, Conversation, Message, MessageAttachment, Profile
from accessible_ai.storage.database import Database


@pytest.fixture
def source(tmp_path):
    db = Database(tmp_path / "chat.db")
    account = Account(name="Test")
    db.save_account(account)
    profile = Profile(name="Profile", system_prompt="original", temperature=0.3)
    db.save_profile(profile)
    conversation = Conversation(
        title="Source",
        account_id=account.id,
        profile_id=profile.id,
        model="model",
        system_prompt_snapshot="saved instructions",
    )
    source_id = db.create_conversation(conversation)
    first = Message(
        conversation_id=source_id,
        role="user",
        content="question",
        attachments=[
            MessageAttachment(filename="image.png", mime_type="image/png", data=b"\x00\x01")
        ],
    )
    db.add_message(first)
    second = Message(conversation_id=source_id, role="assistant", content="answer")
    db.add_message(second)
    return db, source_id, first, second


def test_branch_copies_prefix_settings_and_attachment_bytes(source):
    db, source_id, first, second = source
    before = db.list_messages(source_id)
    result = db.branch_conversation(source_id, first.id, "Branch")
    branch = db.get_conversation(result)
    original = db.get_conversation(source_id)
    assert branch.title == "Branch" and result != source_id
    assert (branch.account_id, branch.profile_id, branch.model, branch.system_prompt_snapshot) == (
        original.account_id,
        original.profile_id,
        original.model,
        original.system_prompt_snapshot,
    )
    messages = db.list_messages(result)
    assert len(messages) == 1 and messages[0].content == "question"
    assert messages[0].id not in {first.id, second.id}
    attachment = messages[0].attachments[0]
    assert (attachment.filename, attachment.mime_type, attachment.data) == (
        "image.png",
        "image/png",
        b"\x00\x01",
    )
    assert attachment.id != first.attachments[0].id
    assert db.list_messages(source_id) == before


def test_empty_branch_preserves_settings_without_messages(source):
    db, source_id, _, _ = source
    result = db.branch_conversation(source_id, None, "Before first")
    assert db.list_messages(result) == []
    assert db.get_conversation(result).system_prompt_snapshot == "saved instructions"


def test_branch_through_answer_keeps_message_order(source):
    db, source_id, _, second = source
    result = db.branch_conversation(source_id, second.id, "After answer")
    assert [m.role for m in db.list_messages(result)] == ["user", "assistant"]


def test_foreign_or_missing_boundary_leaves_no_new_conversation(source):
    db, source_id, _, _ = source
    other = db.create_conversation(Conversation(title="Other"))
    boundary = db.add_message(Message(conversation_id=other, content="foreign"))
    before = db.list_conversations()
    for invalid in (boundary, 999999):
        with pytest.raises(ValueError):
            db.branch_conversation(source_id, invalid, "Invalid")
    with pytest.raises(ValueError):
        db.branch_conversation(999999, None, "Missing source")
    assert db.list_conversations() == before


def test_failed_attachment_copy_rolls_back_entire_branch(source):
    db, source_id, first, _ = source
    with db.connect() as conn:
        conn.execute(
            "CREATE TRIGGER fail_copy BEFORE INSERT ON message_attachments "
            "BEGIN SELECT RAISE(ABORT, 'copy failed'); END"
        )
    before = db.list_conversations()
    with pytest.raises(sqlite3.IntegrityError, match="copy failed"):
        db.branch_conversation(source_id, first.id, "Failed")
    assert db.list_conversations() == before


@pytest.fixture
def panel(source):
    import wx
    from accessible_ai.services.generation_service import GenerationService
    from accessible_ai.services.model_service import ModelService
    from accessible_ai.storage.credentials import CredentialStore
    from accessible_ai.ui.chat_panel import ChatPanel

    application = wx.GetApp() or wx.App(False)
    db, source_id, _, _ = source
    credentials = CredentialStore()
    frame = wx.Frame(None)
    spoken = []
    result = ChatPanel(
        frame,
        db,
        credentials,
        ModelService(db, credentials),
        GenerationService(credentials),
        lambda text: None,
        spoken.append,
    )
    result.open_conversation(source_id)
    jobs = []
    result._recovery_tasks._start_worker = jobs.append
    result._recovery_tasks._dispatch = lambda callback, *args: callback(*args)
    yield result, jobs, spoken
    result.shutdown()
    frame.Destroy()
    application.ProcessPendingEvents()


def test_retry_restores_user_draft_and_attachments_without_sending(panel, source):
    result, jobs, _ = panel
    db, source_id, _, _ = source
    result.history_list.SetSelection(0)
    result.on_retry_history_item(None)
    assert len(jobs) == 1 and result.current_conversation_id == source_id
    jobs.pop()()
    assert result.current_conversation_id != source_id
    assert result.message_input.GetValue() == "question"
    assert result.pending_attachments[0].data == b"\x00\x01"
    assert db.list_messages(result.current_conversation_id) == []
    assert not result.generating


def test_branch_preserves_new_typing_when_worker_finishes(panel, source):
    result, jobs, spoken = panel
    _, source_id, _, _ = source
    result.history_list.SetSelection(1)
    result.on_branch_history_item(None)
    result.message_input.SetValue("new draft")
    jobs.pop()()
    assert result.current_conversation_id == source_id
    assert result.message_input.GetValue() == "new draft"
    assert "Recent Conversations" in spoken[-1]


def test_branch_refuses_to_discard_existing_draft(panel):
    result, jobs, spoken = panel
    result.message_input.SetValue("keep this")
    result.history_list.SetSelection(1)
    result.on_branch_history_item(None)
    assert not jobs and result.message_input.GetValue() == "keep this"
    assert "draft" in spoken[-1].lower()


def test_recovery_completion_after_shutdown_cannot_touch_ui(panel, source):
    result, jobs, _ = panel
    _, source_id, _, _ = source
    result.history_list.SetSelection(1)
    result.on_branch_history_item(None)
    result.shutdown()
    jobs.pop()()
    assert result.current_conversation_id == source_id
