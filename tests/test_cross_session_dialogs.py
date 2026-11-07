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


def test_a_worktree_is_left_for_the_caller_to_make(frame, tmp_path, monkeypatch, refusals):
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
        # The dialog closes without waiting for git; the caller does that work.
        assert made == []
        assert dlg.worktree == (str(tmp_path), "fix")
        assert dlg.path == str(tmp_path)
        assert refusals == []
    finally:
        dlg.Destroy()


def test_a_finished_worktree_opens_the_session_and_says_so(monkeypatch):
    added: list = []
    spoken: list[str] = []
    status: list[str] = []
    stub = type("FrameStub", (), {})()
    stub._add_session = lambda cwd, session_title="": added.append((cwd, session_title))
    stub._set_status_text = status.append
    monkeypatch.setattr(blindpilot_app, "announce", lambda text, *a, **k: spoken.append(text))
    monkeypatch.setattr(blindpilot_app, "_remember_folder", lambda path: None)

    blindpilot_app.MainFrame._worktree_ready(stub, "C:/proj-fix", "", "fix", "Fix it")

    assert added == [("C:/proj-fix", "Fix it")]
    assert spoken == ["New session: Fix it"]
    # Reachable again with the review cursor, the same as any other announcement.
    assert status == spoken


def test_a_worktree_that_failed_says_why_and_opens_nothing(monkeypatch):
    added: list = []
    spoken: list[str] = []
    status: list[str] = []
    stub = type("FrameStub", (), {})()
    stub._add_session = lambda cwd, session_title="": added.append(cwd)
    stub._set_status_text = status.append
    monkeypatch.setattr(blindpilot_app, "announce", lambda text, *a, **k: spoken.append(text))

    blindpilot_app.MainFrame._worktree_ready(
        stub, "", "git could not make the worktree: boom", "fix", ""
    )

    assert added == []
    assert spoken == ["git could not make the worktree: boom"]
    assert status == spoken


def test_creating_a_worktree_speaks_first_and_off_the_window_thread(frame, monkeypatch):
    """The stall this fixed was git running where the window could not paint."""
    import threading

    events: list = []
    workers: list = []
    finished = threading.Event()

    def fake_worktree(folder, branch):
        events.append("checkout")
        workers.append(threading.current_thread())
        finished.set()
        return "C:/proj-fix", ""

    monkeypatch.setattr(blindpilot_app, "create_worktree", fake_worktree)
    monkeypatch.setattr(blindpilot_app, "announce", lambda text, *a, **k: events.append(text))

    stub = type("FrameStub", (), {})()
    stub._worktree_ready = lambda *a: None
    blindpilot_app.MainFrame._open_worktree_session(stub, "C:/proj", "fix", "Fix it")

    assert finished.wait(timeout=5)

    # The sentence is spoken before the thread starts, so it always comes first.
    assert events[0] == "Creating a worktree for fix."
    assert events[1:] == ["checkout"]
    assert workers[0] is not threading.current_thread()


def test_the_send_dialog_offers_the_other_sessions_and_an_editable_message(frame):
    dlg = SendToTabDialog(frame, ["Codex (api)", "Claude Code (web)"], "Claude replied:\n\nHi")
    try:
        assert dlg.target.GetStrings() == ["Codex (api)", "Claude Code (web)"]
        assert dlg.target.GetSelection() == 0
        assert dlg.message.GetValue() == "Claude replied:\n\nHi"
        assert not dlg.relay.IsChecked()
        assert dlg.turns.GetValue() == 6
        assert dlg.third.GetStrings() == ["None", "Codex (api)", "Claude Code (web)"]
    finally:
        dlg.Destroy()


def test_a_kind_of_conversation_fills_in_the_roles_and_turns_the_relay_on(frame):
    dlg = SendToTabDialog(frame, ["Codex (api)"], "Hi")
    try:
        dlg.kind.SetSelection(1)  # Builder and reviewer
        dlg._on_kind()
        assert dlg.first_role.GetValue().startswith("Build what the person asked for")
        assert "DONE" in dlg.second_role.GetValue()
        assert dlg.relay.IsChecked()
    finally:
        dlg.Destroy()
