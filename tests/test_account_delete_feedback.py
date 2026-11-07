"""Deleting an Account says what happened and where the list now is.

It used to be logged and nothing more: the row vanished, focus stayed on the
Delete button, and the list reselected row zero without a word.
"""

from __future__ import annotations

from pathlib import Path

import wx

from accessible_ai.models import Account
from accessible_ai.services.model_service import ModelService
from accessible_ai.storage.credentials import CredentialStore
from accessible_ai.storage.database import Database
from accessible_ai.ui.accounts import AccountsDialog


def _app() -> wx.App:
    return wx.GetApp() or wx.App(False)


def _database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "chat.sqlite3")
    for name in ("Beta", "Alpha"):
        db.save_account(Account(name=name, provider="openrouter", base_url="https://example"))
    return db


def test_deleting_an_account_is_spoken_and_names_the_new_selection(
    tmp_path: Path, monkeypatch
) -> None:
    app = _app()
    db = _database(tmp_path)
    spoken: list[str] = []
    dialog = AccountsDialog(
        None,
        db,
        CredentialStore(),
        ModelService(db, CredentialStore()),
        speak=spoken.append,
    )
    try:
        monkeypatch.setattr(wx, "MessageBox", lambda *args, **kwargs: wx.YES)
        dialog.listbox.SetSelection(1)  # sorted by name: Alpha, then Beta
        dialog.on_delete(wx.CommandEvent())

        assert [account.name for account in db.list_accounts()] == ["Alpha"]
        assert spoken == ["Deleted Beta. Alpha is selected."]
    finally:
        dialog.Destroy()
        app.ProcessPendingEvents()


def test_deleting_the_last_account_does_not_promise_a_selection(
    tmp_path: Path, monkeypatch
) -> None:
    """There is nobody left to be selected, so naming one would be a lie."""
    app = _app()
    db = Database(tmp_path / "chat.sqlite3")
    db.save_account(Account(name="Beta", provider="openrouter", base_url="https://example"))
    spoken: list[str] = []
    dialog = AccountsDialog(
        None,
        db,
        CredentialStore(),
        ModelService(db, CredentialStore()),
        speak=spoken.append,
    )
    try:
        monkeypatch.setattr(wx, "MessageBox", lambda *args, **kwargs: wx.YES)
        dialog.listbox.SetSelection(0)
        dialog.on_delete(wx.CommandEvent())

        assert db.list_accounts() == []
        assert spoken == ["Deleted Beta."]
    finally:
        dialog.Destroy()
        app.ProcessPendingEvents()
