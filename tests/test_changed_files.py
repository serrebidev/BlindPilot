"""Which files a turn changed, and what changed in them.

Reading an agent's edits meant leaving for a terminal and a git command whose
output is a wall of plus and minus signs. Changed Files (Ctrl+Shift+D) lists
what differs from the last commit, with lines added and removed, and reads a
file's changes as Added, Removed and Unchanged lines, as The Chat Place does.
It asks git rather than a transcript, so it is the same for every backend.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import threading
import time

import pytest

import blindpilot_app as app

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture(autouse=True)
def no_repository_above(tmp_path, monkeypatch):
    """The test folder sits inside BlindPilot's own checkout; git stops here."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("one\ntwo\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "start")
    yield tmp_path
    # Git writes its objects read-only, which Windows will not delete.
    for folder, _dirs, names in os.walk(tmp_path):
        for name in names:
            os.chmod(os.path.join(folder, name), stat.S_IWRITE | stat.S_IREAD)


def _wait(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        app.wx.GetApp().Yield()
        time.sleep(0.01)
    assert predicate()


class _Focus:
    def __init__(self):
        self.focused = False

    def SetFocus(self):
        self.focused = True


class _ChangedFilesDialog:
    made = []
    actions = []

    def __init__(self, _parent, message, caption, choices, *_args, **_kwargs):
        self.message = message
        self.caption = caption
        self.choices = list(choices)
        self.selection = 0
        type(self).made.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def SetSelection(self, index):
        self.selection = index

    def ShowModal(self):
        action = type(self).actions.pop(0) if type(self).actions else app.wx.ID_CANCEL
        if action == "select":
            self.selection = 0
            return app.wx.ID_OK
        if action == "refresh":
            self.selection = len(self.choices) - 1
            return app.wx.ID_OK
        return action

    def GetSelection(self):
        return self.selection


class _ChangedFilesUI:
    def __init__(self, cwd):
        self.cwd = cwd
        self.spoken = []
        self.prompt = _Focus()
        self._changed_files_serial = 0

    def _announce(self, text, urgent=False):
        self.spoken.append(text)

    def open_changed_files(self):
        app.SessionPanel.open_changed_files(self)

    def _load_changed_files(self, cwd):
        app.SessionPanel._load_changed_files(self, cwd)

    def _changed_files_loaded(self, *args):
        app.SessionPanel._changed_files_loaded(self, *args)

    def _show_changed_files(self, *args):
        app.SessionPanel._show_changed_files(self, *args)

    def _changed_file_loaded(self, *args):
        app.SessionPanel._changed_file_loaded(self, *args)

    def _load_changed_file(self, *args):
        app.SessionPanel._load_changed_file(self, *args)


@pytest.fixture
def changed_files_dialog(monkeypatch):
    _ChangedFilesDialog.made.clear()
    _ChangedFilesDialog.actions.clear()
    monkeypatch.setattr(app.wx, "SingleChoiceDialog", _ChangedFilesDialog)
    yield _ChangedFilesDialog
    _ChangedFilesDialog.made.clear()
    _ChangedFilesDialog.actions.clear()


def test_edited_and_new_files_are_listed_with_their_counts(repo):
    (repo / "src" / "main.py").write_text("one\nTWO\nthree\n", encoding="utf-8")
    (repo / "notes.txt").write_text("hi\n", encoding="utf-8")

    _root, files = app.changed_files(str(repo / "src"))

    assert ("src/main.py", "main.py, 2 lines added, 1 removed, in src", False) in files
    assert ("notes.txt", "notes.txt, new file", True) in files


def test_a_clean_repository_has_nothing_to_list(repo):
    assert app.changed_files(str(repo))[1] == []


def test_outside_a_repository_it_says_none(tmp_path):
    assert app.changed_files(str(tmp_path)) is None


def test_changed_file_discovery_runs_on_a_worker(
    tmp_path, monkeypatch, wx_app, changed_files_dialog
):
    cwd = str(tmp_path)
    started = threading.Event()
    release = threading.Event()
    found = (cwd, [("notes.txt", "notes.txt, new file", True)])

    def slow_discovery(_cwd):
        started.set()
        assert release.wait(5)
        return found

    monkeypatch.setattr(app, "changed_files", slow_discovery)
    ui = _ChangedFilesUI(cwd)

    began = time.monotonic()
    app.SessionPanel.open_changed_files(ui)

    assert time.monotonic() - began < 1
    assert started.wait(1), "the discovery never started"
    assert changed_files_dialog.made == []
    assert any("Looking for changed files" in text for text in ui.spoken)

    release.set()
    _wait(lambda: bool(changed_files_dialog.made))
    assert changed_files_dialog.made[0].choices == [
        "notes.txt, new file",
        "Refresh the list (asks Git)",
    ]
    assert app.cached_changed_files(cwd) == found


def test_a_warm_folder_opens_its_list_without_asking_git(
    tmp_path, monkeypatch, changed_files_dialog
):
    cwd = str(tmp_path)
    found = (cwd, [("notes.txt", "notes.txt, new file", True)])
    app.remember_changed_files(cwd, found)
    calls = []
    monkeypatch.setattr(app, "changed_files", lambda folder: calls.append(folder))
    ui = _ChangedFilesUI(cwd)

    app.SessionPanel.open_changed_files(ui)

    assert calls == []
    assert len(changed_files_dialog.made) == 1
    assert changed_files_dialog.made[0].choices == [
        "notes.txt, new file",
        "Refresh the list (asks Git)",
    ]
    assert ui.prompt.focused


def test_warm_lists_belong_to_their_own_folder(tmp_path):
    first = (str(tmp_path / "first"), [("a.txt", "a.txt, new file", True)])
    second = (str(tmp_path / "second"), [("b.txt", "b.txt, new file", True)])
    app.remember_changed_files(first[0], first)
    app.remember_changed_files(second[0], second)

    assert app.cached_changed_files(first[0]) == first
    assert app.cached_changed_files(second[0]) == second

    app.forget_changed_files(first[0])

    assert app.cached_changed_files(first[0]) is None
    assert app.cached_changed_files(second[0]) == second


def test_refresh_discards_the_warm_list_and_asks_git_again(
    tmp_path, monkeypatch, wx_app, changed_files_dialog
):
    cwd = str(tmp_path)
    app.remember_changed_files(cwd, (cwd, [("old.txt", "old.txt, new file", True)]))
    fresh = (cwd, [("new.txt", "new.txt, new file", True)])
    calls = []

    def discover(folder):
        calls.append(folder)
        return fresh

    monkeypatch.setattr(app, "changed_files", discover)
    changed_files_dialog.actions.append("refresh")
    ui = _ChangedFilesUI(cwd)

    app.SessionPanel.open_changed_files(ui)

    _wait(lambda: len(changed_files_dialog.made) == 2)
    assert calls == [cwd]
    assert app.cached_changed_files(cwd) == fresh
    assert changed_files_dialog.made[1].choices[0] == "new.txt, new file"


def test_a_stale_result_does_not_fill_the_current_list(tmp_path, changed_files_dialog):
    cwd = str(tmp_path)
    found = (cwd, [("old.txt", "old.txt, new file", True)])
    ui = _ChangedFilesUI(cwd)
    ui._changed_files_serial = 2

    app.SessionPanel._changed_files_loaded(ui, 1, cwd, found, "")

    assert changed_files_dialog.made == []
    assert app.cached_changed_files(cwd) is None
    assert ui.spoken == []


def test_a_git_failure_is_announced_without_opening_a_list(
    tmp_path, monkeypatch, wx_app, changed_files_dialog
):
    cwd = str(tmp_path)
    started = threading.Event()

    def fail(_cwd):
        started.set()
        raise OSError("git could not start")

    monkeypatch.setattr(app, "changed_files", fail)
    ui = _ChangedFilesUI(cwd)

    app.SessionPanel.open_changed_files(ui)

    assert started.wait(1)
    _wait(lambda: any("git could not be run" in text for text in ui.spoken))
    assert any("git could not start" in text for text in ui.spoken)
    assert changed_files_dialog.made == []
    assert app.cached_changed_files(cwd) is None


def test_opening_a_changed_file_reads_its_diff_on_a_worker(
    tmp_path, monkeypatch, wx_app, changed_files_dialog
):
    cwd = str(tmp_path)
    files = [("changed.py", "changed.py, 1 line added, 1 removed", False)]
    found = (cwd, files)
    app.remember_changed_files(cwd, found)
    started = threading.Event()
    release = threading.Event()
    views = []

    class _ReadView:
        def __init__(self, _parent, text, title, monospace=False):
            self.text = text
            self.title = title
            self.monospace = monospace
            views.append(self)

        def ShowModal(self):
            return app.wx.ID_CANCEL

        def Destroy(self):
            pass

    def slow_diff(_root, *args):
        started.set()
        assert release.wait(5)
        return type("Result", (), {"stdout": "+updated\n"})()

    monkeypatch.setattr(app, "_git", slow_diff)
    monkeypatch.setattr(app, "ReadView", _ReadView)
    changed_files_dialog.actions.extend(["select", app.wx.ID_CANCEL])
    ui = _ChangedFilesUI(cwd)

    began = time.monotonic()
    app.SessionPanel.open_changed_files(ui)

    assert time.monotonic() - began < 1
    assert started.wait(1), "the diff did not start"
    assert views == []
    release.set()
    _wait(lambda: bool(views))
    assert views[0].text == "Added: updated"
    assert views[0].title == "changed.py"
    assert ui.prompt.focused


def test_a_stale_file_read_does_not_open_a_view_or_replace_the_list(tmp_path, monkeypatch):
    views = []
    monkeypatch.setattr(app, "ReadView", lambda *_args, **_kwargs: views.append(True))
    ui = _ChangedFilesUI(str(tmp_path))
    ui._changed_files_serial = 2

    app.SessionPanel._changed_file_loaded(
        ui, 1, ui.cwd, ui.cwd, "changed.py", [], 0, "Added: old result"
    )

    assert views == []


def test_a_diff_reads_as_what_each_line_is():
    diff = (
        "diff --git a/x b/x\nindex 1..2 100644\n--- a/x\n+++ b/x\n"
        "@@ -3,2 +3,2 @@ def f():\n keep\n-old\n+new\n\\ No newline at end of file\n"
    )

    assert app.readable_diff(diff) == "At line 3:\nUnchanged: keep\nRemoved: old\nAdded: new"
