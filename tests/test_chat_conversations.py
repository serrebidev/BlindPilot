"""Getting back into a Chat conversation, on the profile it was started on.

Chat mode wrote a conversation row for every conversation -- its profile, its
account, its model and the system prompt it was started with -- and could read
none of it back: the database had only `create_conversation`, and the panel had
no way to open one. Every launch started fresh and everything before it was
reachable only by opening the file by hand.

What matters most here is what happens on the way back in. A conversation that
reopened on whatever the pickers happened to say would be a conversation whose
next message went somewhere else, which is the same defect as a chat running on
the wrong profile -- just delayed by a restart.
"""

from __future__ import annotations

from pathlib import Path

import wx

from accessible_ai.models import Account, Message, Profile
from accessible_ai.services.generation_service import GenerationService
from accessible_ai.services.model_service import ModelService
from accessible_ai.storage.credentials import CredentialStore
from accessible_ai.storage.database import Database
from accessible_ai.ui.chat_panel import ChatPanel
from accessible_ai.ui.conversations import ConversationsDialog


def _app() -> wx.App:
    return wx.GetApp() or wx.App(False)


def _seeded(tmp_path: Path) -> tuple[Database, Account, Account]:
    db = Database(tmp_path / "chat.sqlite3")
    first = Account(
        name="AAA First", provider="openrouter", base_url="https://a", default_model="a/one"
    )
    other = Account(
        name="ZZZ Other", provider="openrouter", base_url="https://z", default_model="z/one"
    )
    db.save_account(first)
    db.save_account(other)
    db.replace_model_cache(int(first.id), ["a/one", "a/two"])
    db.replace_model_cache(int(other.id), ["z/one", "z/two"])
    return db, first, other


def _panel(frame: wx.Frame, db: Database) -> ChatPanel:
    credentials = CredentialStore()
    return ChatPanel(
        frame,
        db,
        credentials,
        ModelService(db, credentials),
        GenerationService(credentials),
        lambda _text: None,
        lambda _text: None,
    )


def _pick(panel: ChatPanel, profile_id: int) -> None:
    row = [profile.id for profile in panel.profiles].index(profile_id) + 1
    panel.profile_choice.SetSelection(row)
    panel.on_profile_changed(wx.CommandEvent())


def _started(panel: ChatPanel, account: Account, model: str, text: str) -> int:
    """A conversation with one message in it, as sending would leave it."""
    panel._ensure_conversation(text, account, model)
    conversation_id = int(panel.current_conversation_id)
    panel.db.add_message(Message(conversation_id=conversation_id, role="user", content=text))
    return conversation_id


# ----- Reading conversations back -----


def test_conversations_come_back_with_what_tells_them_apart(tmp_path):
    app = _app()
    db, first, _other = _seeded(tmp_path)
    profile = Profile(name="Writing", system_prompt="BE BRIEF")
    db.save_profile(profile)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        _pick(panel, int(profile.id))
        _started(panel, first, "a/one", "the first one")

        listed = db.list_conversations()
        assert [row.title for row in listed] == ["the first one"]
        row = listed[0]
        assert row.message_count == 1
        assert row.profile_name == "Writing"
        assert row.account_name == "AAA First"
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()


def test_a_conversation_outlives_the_profile_and_account_it_was_started_on(tmp_path):
    """A left join, so deleting either loses the label rather than the row."""
    app = _app()
    db, first, _other = _seeded(tmp_path)
    profile = Profile(name="Writing")
    db.save_profile(profile)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        _pick(panel, int(profile.id))
        _started(panel, first, "a/one", "still here")
        db.delete_profile(int(profile.id))
        db.delete_account(int(first.id))

        listed = db.list_conversations()
        assert [row.title for row in listed] == ["still here"]
        assert listed[0].profile_name == ""
        assert listed[0].account_name == ""
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()


def test_deleting_a_conversation_takes_its_messages_with_it(tmp_path):
    app = _app()
    db, first, _other = _seeded(tmp_path)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        conversation_id = _started(panel, first, "a/one", "goes away")
        db.delete_conversation(conversation_id)
        assert db.list_conversations() == []
        assert db.list_messages(conversation_id) == []
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()


# ----- Opening one again -----


def test_reopening_restores_the_profile_the_conversation_was_started_on(tmp_path):
    """Not the profile showing in the picker, and not the default."""
    app = _app()
    db, first, other = _seeded(tmp_path)
    started = Profile(
        name="Started on",
        system_prompt="FIRST",
        temperature=0.75,
        default_account_id=other.id,
        default_model="z/two",
    )
    elsewhere = Profile(name="Showing now", system_prompt="SECOND", temperature=0.1)
    db.save_profile(started)
    db.save_profile(elsewhere)
    db.set_default_profile(elsewhere.id)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        _pick(panel, int(started.id))
        conversation_id = _started(panel, other, "z/two", "carry this on")

        # A fresh window, opening on the default profile, as a restart would.
        second = _panel(frame, db)
        assert second.selected_profile().name == "Showing now"
        second.open_conversation(conversation_id)

        assert second.current_conversation_id == conversation_id
        assert second.current_profile_id == started.id
        assert second.selected_profile().name == "Started on"
        assert second.selected_account().name == "ZZZ Other"
        assert second.model_combo.GetValue() == "z/two"

        settings = second._generation_settings(other, "z/two")
        assert settings.messages[0]["content"] == "FIRST"
        assert settings.temperature == 0.75
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()


def test_reopening_brings_the_messages_back(tmp_path):
    app = _app()
    db, first, _other = _seeded(tmp_path)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        conversation_id = _started(panel, first, "a/one", "hello there")
        db.add_message(
            Message(conversation_id=conversation_id, role="assistant", content="hello back")
        )

        second = _panel(frame, db)
        assert second.history_list.GetCount() == 1  # the empty-conversation row
        second.open_conversation(conversation_id)

        assert [entry.content for entry in second.history_entries] == [
            "hello there",
            "hello back",
        ]
        assert second.history_list.GetCount() == 2
        # The next request carries what was said before it.
        settings = second._generation_settings(first, "a/one")
        assert [message["content"] for message in settings.messages] == [
            "hello there",
            "hello back",
        ]
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()


def test_a_conversation_whose_profile_was_deleted_keeps_the_prompt_it_started_with(tmp_path):
    """The snapshot is the conversation's own; the profile it came from is not."""
    app = _app()
    db, first, _other = _seeded(tmp_path)
    profile = Profile(name="Gone", system_prompt="AS WRITTEN", temperature=0.75)
    db.save_profile(profile)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        _pick(panel, int(profile.id))
        conversation_id = _started(panel, first, "a/one", "hello there")
        db.delete_profile(int(profile.id))

        second = _panel(frame, db)
        second.open_conversation(conversation_id)
        assert second.current_profile is None
        assert second.selected_profile() is None

        settings = second._generation_settings(first, "a/one")
        assert settings.messages[0]["content"] == "AS WRITTEN"
        # Nothing is left to say what the temperature was, and inventing one
        # would be worse than letting the provider choose.
        assert settings.temperature is None
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()


def test_opening_a_conversation_that_has_gone_says_so(tmp_path):
    app = _app()
    db, first, _other = _seeded(tmp_path)
    said: list[str] = []
    frame = wx.Frame(None)
    try:
        credentials = CredentialStore()
        panel = ChatPanel(
            frame,
            db,
            credentials,
            ModelService(db, credentials),
            GenerationService(credentials),
            said.append,
            lambda _text: None,
        )
        conversation_id = _started(panel, first, "a/one", "deleted elsewhere")
        db.delete_conversation(conversation_id)
        panel.on_new_conversation(wx.CommandEvent())

        panel.open_conversation(conversation_id)
        assert panel.current_conversation_id is None
        assert any("no longer there" in text for text in said)
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()


# ----- The dialog -----


def test_the_dialog_says_what_each_row_is(tmp_path):
    app = _app()
    db, first, _other = _seeded(tmp_path)
    profile = Profile(name="Writing")
    db.save_profile(profile)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        _pick(panel, int(profile.id))
        _started(panel, first, "a/one", "the only one")
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()

    dialog = ConversationsDialog(None, db)
    try:
        assert dialog.listbox.GetCount() == 1
        row = dialog.listbox.GetString(0)
        assert row.startswith("the only one, 1 message, Writing profile, AAA First")
        assert "1 conversation listed" in dialog.summary.GetLabel()
    finally:
        dialog.Destroy()
        app.ProcessPendingEvents()


def test_the_dialog_says_when_a_filter_matches_nothing(tmp_path):
    app = _app()
    db, first, _other = _seeded(tmp_path)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        _started(panel, first, "a/one", "findable")
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()

    dialog = ConversationsDialog(None, db)
    try:
        dialog.filter_box.SetValue("nothing like it")
        dialog._refresh()
        assert dialog.listbox.GetCount() == 0
        assert dialog.selected() is None
        # A silent empty list is the thing this sentence exists to prevent.
        assert dialog.summary.GetLabel() == "No conversations match that filter."
        assert dialog.open_button.IsEnabled() is False
    finally:
        dialog.Destroy()
        app.ProcessPendingEvents()


def test_an_empty_dialog_says_there_is_nothing_rather_than_nothing(tmp_path):
    app = _app()
    db = Database(tmp_path / "chat.sqlite3")
    dialog = ConversationsDialog(None, db)
    try:
        assert dialog.summary.GetLabel() == "No conversations yet."
        assert dialog.open_button.IsEnabled() is False
        assert dialog.delete_button.IsEnabled() is False
    finally:
        dialog.Destroy()
        app.ProcessPendingEvents()
