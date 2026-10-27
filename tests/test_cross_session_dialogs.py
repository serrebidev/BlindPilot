"""The new dialogs, built for real: New Session's recent folders and worktree,
and Send Response to Another Session.

Built rather than stubbed because the installed build raises wx asserts the
test environment otherwise hides, and a dialog that asserts never opens.
"""

from __future__ import annotations

import pytest

wx = pytest.importorskip("wx")

import blindpilot_app  # noqa: E402
from blindpilot_app import NewSessionDialog, SendToTabDialog  # noqa: E402


@pytest.fixture
def frame():
    owns_app = wx.GetApp() is None
    app = wx.GetApp() or wx.App(False)
    previous = app.GetAssertMode()
    app.SetAssertMode(wx.APP_ASSERT_EXCEPTION)
    top = wx.Frame(None)
    yield top
    top.Destroy()
    app.SetAssertMode(previous)
    if owns_app:
        app.Destroy()


@pytest.fixture
def refusals(monkeypatch):
    collected: list[str] = []
    monkeypatch.setattr(
        NewSessionDialog, "_reject", lambda _self, message: collected.append(message)
    )
    return collected


def _ok(dlg):
    event = wx.CommandEvent(wx.wxEVT_BUTTON, wx.ID_OK)
    skipped: list[bool] = []
    event.Skip = lambda skip=True: skipped.append(skip)  # type: ignore[method-assign]
    dlg._on_ok(event)
    return bool(skipped)


def test_the_newest_recent_folder_is_filled_in(frame, tmp_path, monkeypatch):
    older, newer = tmp_path / "older", tmp_path / "newer"
    older.mkdir()
    newer.mkdir()
    monkeypatch.setattr(
        blindpilot_app, "_recent_folders", lambda: [str(newer), str(tmp_path / "gone"), str(older)]
    )

    dlg = NewSessionDialog(frame, default_dir=str(tmp_path))
    try:
        assert dlg.folder_box.GetValue() == str(newer)
        # A folder that no longer exists is not offered.
        assert dlg.folder_box.GetStrings() == [str(newer), str(older)]
    finally:
        dlg.Destroy()


def test_a_worktree_needs_a_branch_name(frame, tmp_path, monkeypatch, refusals):
    monkeypatch.setattr(blindpilot_app, "_recent_folders", list)
    dlg = NewSessionDialog(frame, default_dir=str(tmp_path))
    try:
        dlg.folder_box.SetValue(str(tmp_path))
        dlg.worktree_box.SetValue(True)

        assert not _ok(dlg)
        assert refusals == ["Type a branch name for the worktree."]
    finally:
        dlg.Destroy()


def test_a_worktree_session_opens_in_the_new_worktree(frame, tmp_path, monkeypatch, refusals):
    monkeypatch.setattr(blindpilot_app, "_recent_folders", list)
    made: list[tuple[str, str]] = []

    def fake_worktree(folder, branch):
        made.append((folder, branch))
        return str(tmp_path / "proj-fix"), ""

    monkeypatch.setattr(blindpilot_app, "create_worktree", fake_worktree)
    dlg = NewSessionDialog(frame, default_dir=str(tmp_path))
    try:
        dlg.folder_box.SetValue(str(tmp_path))
        dlg.worktree_box.SetValue(True)
        dlg.branch_box.SetValue("fix")

        assert _ok(dlg)
        assert made == [(str(tmp_path), "fix")]
        assert dlg.path == str(tmp_path / "proj-fix")
        assert refusals == []
    finally:
        dlg.Destroy()


def test_the_send_dialog_offers_the_other_sessions_and_an_editable_message(frame):
    dlg = SendToTabDialog(frame, ["Codex (api)", "Claude Code (web)"], "Claude replied:\n\nHi")
    try:
        assert dlg.target.GetStrings() == ["Codex (api)", "Claude Code (web)"]
        assert dlg.target.GetSelection() == 0
        assert dlg.message.GetValue() == "Claude replied:\n\nHi"
        assert not dlg.relay.IsChecked()
        assert dlg.turns.GetValue() == 6
    finally:
        dlg.Destroy()
