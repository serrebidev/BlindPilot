"""Chat's model picker is a list whose order is a persisted user choice."""

from __future__ import annotations

import wx

from accessible_ai.models import Account
from accessible_ai.services.generation_service import GenerationService
from accessible_ai.services.model_service import ModelService
from accessible_ai.storage.credentials import CredentialStore
from accessible_ai.storage.database import Database
from accessible_ai.ui.chat_panel import ChatPanel


def _app() -> wx.App:
    return wx.GetApp() or wx.App(False)


def _panel(frame: wx.Frame, db: Database, order: str) -> ChatPanel:
    credentials = CredentialStore()
    return ChatPanel(
        frame,
        db,
        credentials,
        ModelService(db, credentials),
        GenerationService(credentials),
        lambda _text: None,
        lambda _text: None,
        model_order=order,
    )


def test_model_cache_remembers_when_a_model_first_appeared(tmp_path):
    db = Database(tmp_path / "chat.sqlite3")
    account = Account(name="Test", provider="openrouter", base_url="https://example")
    db.save_account(account)
    db.replace_model_cache(int(account.id), ["older", "newer"])
    with db.connect() as conn:
        conn.execute(
            "UPDATE model_cache SET first_seen_at = CASE model_id "
            "WHEN 'older' THEN 10 ELSE 20 END WHERE account_id = ?",
            (account.id,),
        )

    assert db.get_cached_models(int(account.id), "newest") == ["newer", "older"]
    assert db.get_cached_models(int(account.id), "oldest") == ["older", "newer"]
    assert db.get_cached_models(int(account.id), "name_descending") == ["older", "newer"]


def test_chat_model_list_uses_the_chosen_order_and_keeps_the_selected_model(tmp_path):
    app = _app()
    db = Database(tmp_path / "chat.sqlite3")
    account = Account(name="Test", provider="openrouter", base_url="https://example")
    db.save_account(account)
    db.replace_model_cache(int(account.id), ["older", "newer"])
    with db.connect() as conn:
        conn.execute(
            "UPDATE model_cache SET first_seen_at = CASE model_id "
            "WHEN 'older' THEN 10 ELSE 20 END WHERE account_id = ?",
            (account.id,),
        )
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db, "newest")
        assert isinstance(panel.model_combo, wx.Choice)
        assert [panel.model_combo.GetString(index) for index in range(2)] == ["newer", "older"]
        panel.model_combo.SetSelection(1)
        panel.set_model_order("oldest")
        assert [panel.model_combo.GetString(index) for index in range(2)] == ["older", "newer"]
        assert panel.model_combo.GetStringSelection() == "older"
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()
