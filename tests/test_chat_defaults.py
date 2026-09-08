"""The account and profile Chat mode starts on, and an empty History a reader can move in.

Chat mode used to open on whichever account sorted first and on no profile at
all, so somebody with several accounts re-picked theirs on every launch. A
"Use as default" box under each list says which one the window opens on, and
the database keeps at most one of them marked so "the default" cannot become
two of them.
"""

from __future__ import annotations

from pathlib import Path

import wx

from accessible_ai.models import Account, Message, Profile
from accessible_ai.services.generation_service import GenerationService
from accessible_ai.services.model_service import ModelService
from accessible_ai.storage.credentials import CredentialStore
from accessible_ai.storage.database import Database
from accessible_ai.ui.accounts import AccountsDialog
from accessible_ai.ui.chat_panel import HISTORY_EMPTY_ROW, ChatPanel
from accessible_ai.ui.profiles import ProfilesDialog


def _app() -> wx.App:
    return wx.GetApp() or wx.App(False)


def _seeded(tmp_path: Path) -> Database:
    db = Database(tmp_path / "chat.sqlite3")
    for name in ("Beta", "Alpha"):
        db.save_account(Account(name=name, provider="openrouter", base_url="https://example"))
    for name in ("Writing", "Answering"):
        db.save_profile(Profile(name=name))
    return db


def _account(db: Database, name: str) -> Account:
    return next(account for account in db.list_accounts() if account.name == name)


def _profile(db: Database, name: str) -> Profile:
    return next(profile for profile in db.list_profiles() if profile.name == name)


def _panel(parent: wx.Window, db: Database) -> ChatPanel:
    credentials = CredentialStore()
    return ChatPanel(
        parent,
        db,
        credentials,
        ModelService(db, credentials),
        GenerationService(credentials),
        lambda _text: None,
        lambda _text: None,
    )


# ----- The database keeps exactly one -----


def test_marking_a_default_takes_it_off_whichever_had_it(tmp_path):
    """Two defaults is not a state the window could act on, so it cannot happen."""
    db = _seeded(tmp_path)
    db.set_default_account(_account(db, "Alpha").id)
    db.set_default_account(_account(db, "Beta").id)
    assert [a.name for a in db.list_accounts() if a.is_default] == ["Beta"]
    assert db.default_account().name == "Beta"


def test_unticking_leaves_no_default_rather_than_choosing_one(tmp_path):
    db = _seeded(tmp_path)
    db.set_default_account(_account(db, "Alpha").id)
    db.set_default_account(None)
    assert db.default_account() is None
    assert not any(account.is_default for account in db.list_accounts())


def test_a_profile_default_works_the_same_way(tmp_path):
    db = _seeded(tmp_path)
    db.set_default_profile(_profile(db, "Writing").id)
    db.set_default_profile(_profile(db, "Answering").id)
    assert db.default_profile().name == "Answering"
    db.set_default_profile(None)
    assert db.default_profile() is None


def test_a_database_written_before_defaults_existed_still_opens(tmp_path):
    """The column is added to an older file rather than the file being rejected."""
    import sqlite3
    from contextlib import closing

    path = tmp_path / "chat.sqlite3"
    # `with sqlite3.connect(...)` commits but does not close, and Windows will
    # not delete a file a handle is still open on.
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            """
            CREATE TABLE accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE, provider TEXT NOT NULL,
                base_url TEXT NOT NULL, models_endpoint TEXT NOT NULL,
                chat_endpoint TEXT NOT NULL, responses_endpoint TEXT NOT NULL,
                messages_endpoint TEXT NOT NULL, api_mode TEXT NOT NULL,
                default_model TEXT NOT NULL DEFAULT '',
                timeout_seconds REAL NOT NULL DEFAULT 120,
                streaming INTEGER NOT NULL DEFAULT 1,
                custom_headers_json TEXT NOT NULL DEFAULT '{}',
                custom_body_json TEXT NOT NULL DEFAULT '{}'
            );
            INSERT INTO accounts (
                name, provider, base_url, models_endpoint, chat_endpoint,
                responses_endpoint, messages_endpoint, api_mode
            ) VALUES ('Old', 'openrouter', 'https://example', '/models',
                      '/chat/completions', '/responses', '/messages', 'auto');
            """
        )
        conn.commit()
    db = Database(path)
    accounts = db.list_accounts()
    assert [account.name for account in accounts] == ["Old"]
    # Nothing was marked, which is the honest answer for a file made before
    # there was anything to mark.
    assert accounts[0].is_default is False
    db.set_default_account(accounts[0].id)
    assert db.default_account().name == "Old"


# ----- The checkbox under each list -----


def test_the_accounts_box_follows_the_account_the_cursor_is_on(tmp_path):
    app = _app()
    db = _seeded(tmp_path)
    db.set_default_account(_account(db, "Beta").id)
    dialog = AccountsDialog(None, db, CredentialStore(), ModelService(db, CredentialStore()))
    try:
        # Sorted by name, so Alpha is row zero and is not the default.
        assert dialog.listbox.GetSelection() == 0
        assert dialog.default_check.GetValue() is False
        dialog.listbox.SetSelection(1)
        dialog.on_selection_changed(wx.CommandEvent())
        assert dialog.default_check.GetValue() is True
        # The row says so too, for somebody arrowing the list rather than
        # tabbing to the box after every move.
        assert dialog.listbox.GetString(1).endswith(", default")
        assert not dialog.listbox.GetString(0).endswith(", default")
    finally:
        dialog.Destroy()
        app.ProcessPendingEvents()


def test_ticking_the_accounts_box_moves_the_default(tmp_path):
    app = _app()
    db = _seeded(tmp_path)
    db.set_default_account(_account(db, "Beta").id)
    dialog = AccountsDialog(None, db, CredentialStore(), ModelService(db, CredentialStore()))
    try:
        dialog.listbox.SetSelection(0)
        dialog.on_selection_changed(wx.CommandEvent())
        dialog.default_check.SetValue(True)
        dialog.on_default_changed(wx.CommandEvent())
        assert db.default_account().name == "Alpha"
        # The list was rebuilt; the cursor stays on the account just marked.
        assert dialog.listbox.GetSelection() == 0
        assert dialog.default_check.GetValue() is True

        dialog.default_check.SetValue(False)
        dialog.on_default_changed(wx.CommandEvent())
        assert db.default_account() is None
    finally:
        dialog.Destroy()
        app.ProcessPendingEvents()


def test_the_profiles_box_marks_and_unmarks_the_same_way(tmp_path):
    app = _app()
    db = _seeded(tmp_path)
    dialog = ProfilesDialog(None, db)
    try:
        assert dialog.default_check.GetValue() is False
        dialog.default_check.SetValue(True)
        dialog.on_default_changed(wx.CommandEvent())
        # Sorted by name: Answering is row zero.
        assert db.default_profile().name == "Answering"
        assert dialog.listbox.GetString(0).endswith(", default")
    finally:
        dialog.Destroy()
        app.ProcessPendingEvents()


def test_the_box_is_dead_rather_than_lying_when_there_is_nothing_to_mark(tmp_path):
    """An empty list has no account under the cursor for the box to describe."""
    app = _app()
    db = Database(tmp_path / "chat.sqlite3")
    dialog = AccountsDialog(None, db, CredentialStore(), ModelService(db, CredentialStore()))
    try:
        assert dialog.default_check.IsEnabled() is False
        assert dialog.default_check.GetValue() is False
    finally:
        dialog.Destroy()
        app.ProcessPendingEvents()


# ----- What the window opens on -----


def test_chat_opens_on_the_default_account_not_the_first_one_alphabetically(tmp_path):
    app = _app()
    db = _seeded(tmp_path)
    db.set_default_account(_account(db, "Beta").id)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        assert panel.selected_account().name == "Beta"
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()


def test_chat_opens_on_the_first_account_while_no_default_is_marked(tmp_path):
    app = _app()
    db = _seeded(tmp_path)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        assert panel.selected_account().name == "Alpha"
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()


def test_chat_opens_on_the_default_profile(tmp_path):
    app = _app()
    db = _seeded(tmp_path)
    db.set_default_profile(_profile(db, "Writing").id)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        assert panel.selected_profile().name == "Writing"
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()


def test_chat_opens_on_no_profile_while_none_is_marked(tmp_path):
    """ "No profile" stays the answer for anyone who has not asked for one."""
    app = _app()
    db = _seeded(tmp_path)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        assert panel.selected_profile() is None
        assert panel.profile_choice.GetSelection() == 0
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()


# ----- An empty History a screen reader can land on -----


def test_an_empty_history_holds_one_row_saying_so(tmp_path):
    """A native list box with no items announces "unknown" and arrows in silence."""
    app = _app()
    db = _seeded(tmp_path)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        # Straight from construction: a window opened on a conversation nobody
        # has started renders nothing, so the row cannot wait for a render.
        assert panel.history_list.GetCount() == 1
        assert panel.history_list.GetString(0) == HISTORY_EMPTY_ROW
        panel._replace_history_entries([])
        assert panel.history_list.GetCount() == 1
        assert panel.history_list.GetString(0) == HISTORY_EMPTY_ROW
        # Focus has somewhere to be, which is the whole point of the row.
        assert panel.history_list.GetSelection() == 0
        # It is not an entry, so nothing offers to copy or edit it.
        assert panel._selected_history_entry() is None
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()


def test_the_empty_row_goes_when_a_real_message_arrives(tmp_path):
    """It is a stand-in for an empty list, not row zero of a full one."""
    app = _app()
    db = _seeded(tmp_path)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        panel._replace_history_entries([])
        panel._append_history_entry(Message(id=1, role="user", content="Hello"))
        assert panel.history_list.GetCount() == 1
        assert panel.history_list.GetString(0) != HISTORY_EMPTY_ROW
        # The rows line up with the entries again, so the selected row is the
        # message it appears to be.
        found = panel._selected_history_entry()
        assert found is not None and found[1].content == "Hello"
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()


def test_a_message_inserted_ahead_of_a_response_also_clears_the_empty_row(tmp_path):
    app = _app()
    db = _seeded(tmp_path)
    frame = wx.Frame(None)
    try:
        panel = _panel(frame, db)
        panel._replace_history_entries([])
        panel._insert_history_entry_before_response(Message(role="status", content="Sending"))
        assert panel.history_list.GetCount() == 1
        assert panel.history_list.GetString(0) != HISTORY_EMPTY_ROW
        assert len(panel.history_entries) == 1
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()
