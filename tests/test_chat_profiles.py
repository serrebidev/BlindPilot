"""A conversation belongs to one profile, and a profile means one thing.

Two ways a profile becomes the selected one -- picked from the list, or
restored at startup because it is the default -- had two different effects: the
first moved the account and model pickers to the ones the profile names, the
second only showed its name. So the first conversation of every session was
created on whatever account happened to be showing and recorded against a
profile that names another one.

And a conversation read half its settings from the profile it started on and
half from that profile as it stood at the moment of each request, so editing a
profile part-way through changed the temperature of a conversation whose system
prompt stayed as it was.
"""

from __future__ import annotations

from pathlib import Path

import wx

from accessible_ai.models import Account, Profile
from accessible_ai.services.generation_service import GenerationService
from accessible_ai.services.model_service import ModelService
from accessible_ai.storage.credentials import CredentialStore
from accessible_ai.storage.database import Database
from accessible_ai.ui.chat_panel import ChatPanel


def _app() -> wx.App:
    return wx.GetApp() or wx.App(False)


def _seeded(tmp_path: Path) -> tuple[Database, Account, Account]:
    """Two accounts whose names sort the wrong way round for what is wanted."""
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
    """Choose a profile the way the picker does, event and all."""
    row = [profile.id for profile in panel.profiles].index(profile_id) + 1
    panel.profile_choice.SetSelection(row)
    panel.on_profile_changed(wx.CommandEvent())


# ----- A profile means the same thing however it was selected -----


def test_a_default_profile_applies_its_account_and_model_at_startup(tmp_path):
    app = _app()
    db, _first, other = _seeded(tmp_path)
    profile = Profile(name="Named account", default_account_id=other.id, default_model="z/two")
    db.save_profile(profile)
    db.set_default_profile(profile.id)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        assert panel.selected_profile().name == "Named account"
        # Not "AAA First", which is what the account list alone would have said.
        assert panel.selected_account().name == "ZZZ Other"
        assert panel.model_combo.GetValue() == "z/two"
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()


def test_picking_that_profile_by_hand_does_exactly_the_same(tmp_path):
    app = _app()
    db, _first, other = _seeded(tmp_path)
    profile = Profile(name="Named account", default_account_id=other.id, default_model="z/two")
    db.save_profile(profile)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        assert panel.selected_account().name == "AAA First"
        _pick(panel, int(profile.id))
        assert panel.selected_account().name == "ZZZ Other"
        assert panel.model_combo.GetValue() == "z/two"
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()


def test_a_profile_still_applies_its_model_when_its_account_is_gone(tmp_path):
    """Deleting an account leaves the profile pointing at nothing.

    The model it names is still the model it names; the account on screen is
    whichever one is selected now.
    """
    app = _app()
    db, _first, other = _seeded(tmp_path)
    profile = Profile(name="Orphaned", default_account_id=other.id, default_model="a/two")
    db.save_profile(profile)
    db.delete_account(int(other.id))
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        _pick(panel, int(profile.id))
        assert panel.selected_account().name == "AAA First"
        assert panel.model_combo.GetValue() == "a/two"
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()


# ----- A conversation belongs to the profile it was started on -----


def test_the_conversation_records_the_profile_it_was_started_on(tmp_path):
    app = _app()
    db, first, _other = _seeded(tmp_path)
    profile = Profile(name="Writing", system_prompt="BE BRIEF", temperature=0.75)
    db.save_profile(profile)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        _pick(panel, int(profile.id))
        panel._ensure_conversation("hello there", first, "a/one")
        assert panel.current_profile_id == profile.id
        settings = panel._generation_settings(first, "a/one")
        assert settings.messages[0] == {"role": "system", "content": "BE BRIEF"}
        assert settings.temperature == 0.75
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()


def test_moving_the_picker_does_not_change_the_conversation_underneath(tmp_path):
    """The picker chooses what the NEXT conversation starts on, not this one."""
    app = _app()
    db, first, _other = _seeded(tmp_path)
    started = Profile(name="Started on", system_prompt="FIRST", temperature=0.75)
    other = Profile(name="Picked later", system_prompt="SECOND", temperature=0.1)
    db.save_profile(started)
    db.save_profile(other)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        _pick(panel, int(started.id))
        panel._ensure_conversation("hello there", first, "a/one")
        _pick(panel, int(other.id))

        settings = panel._generation_settings(first, "a/one")
        assert settings.messages[0]["content"] == "FIRST"
        assert settings.temperature == 0.75
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()


def test_editing_a_profile_does_not_change_a_conversation_already_running_on_it(tmp_path):
    """One conversation, one set of settings.

    The system prompt was snapshotted onto the conversation and everything else
    re-read on each request, so an edit changed a conversation's temperature
    while leaving the prompt it was started with -- half of one profile and
    half of another.
    """
    app = _app()
    db, first, _other = _seeded(tmp_path)
    profile = Profile(name="Writing", system_prompt="AS WRITTEN", temperature=0.75)
    db.save_profile(profile)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        _pick(panel, int(profile.id))
        panel._ensure_conversation("hello there", first, "a/one")

        edited = db.get_profile(int(profile.id))
        edited.system_prompt = "EDITED"
        edited.temperature = 0.05
        edited.max_output_tokens = 32
        db.save_profile(edited)

        settings = panel._generation_settings(first, "a/one")
        assert settings.messages[0]["content"] == "AS WRITTEN"
        assert settings.temperature == 0.75
        assert settings.max_output_tokens is None
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()


def test_starting_a_new_conversation_takes_the_profile_showing_now(tmp_path):
    app = _app()
    db, first, _other = _seeded(tmp_path)
    started = Profile(name="Started on", system_prompt="FIRST", temperature=0.75)
    later = Profile(name="Picked later", system_prompt="SECOND", temperature=0.1)
    db.save_profile(started)
    db.save_profile(later)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        _pick(panel, int(started.id))
        panel._ensure_conversation("first message", first, "a/one")
        _pick(panel, int(later.id))
        panel.on_new_conversation(wx.CommandEvent())
        assert panel.current_profile is None
        panel._ensure_conversation("second message", first, "a/one")

        settings = panel._generation_settings(first, "a/one")
        assert settings.messages[0]["content"] == "SECOND"
        assert settings.temperature == 0.1
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()


def test_no_profile_sends_no_system_message_and_no_settings(tmp_path):
    app = _app()
    db, first, _other = _seeded(tmp_path)
    db.save_profile(Profile(name="Writing", system_prompt="BE BRIEF", temperature=0.75))
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        panel.profile_choice.SetSelection(0)
        panel.on_profile_changed(wx.CommandEvent())
        panel._ensure_conversation("hello there", first, "a/one")

        settings = panel._generation_settings(first, "a/one")
        assert panel.current_profile_id is None
        assert all(message["role"] != "system" for message in settings.messages)
        assert settings.temperature is None
        assert settings.max_output_tokens is None
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()
