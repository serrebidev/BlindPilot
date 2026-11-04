"""The GitHub picker leaves wx free while gh lists and clones repositories."""

from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest

wx = pytest.importorskip("wx")

import blindpilot_app as app  # noqa: E402


@pytest.fixture
def frame():
    owns_app = wx.GetApp() is None
    application = wx.GetApp() or wx.App(False)
    frame = wx.Frame(None)
    yield frame
    frame.Destroy()
    if owns_app:
        application.Destroy()


@pytest.fixture
def queued(monkeypatch):
    workers = []
    callbacks = []

    class Thread:
        def __init__(self, target, daemon=False):
            assert daemon
            self.target = target

        def start(self):
            workers.append(self.target)

    monkeypatch.setattr(app.threading, "Thread", Thread)
    monkeypatch.setattr(
        app, "_call_after_alive", lambda callback, *args: callbacks.append((callback, args))
    )
    return workers, callbacks


def _deliver(callbacks):
    callback, args = callbacks.pop(0)
    callback(*args)


def test_list_and_clone_start_after_the_button_callback(frame, tmp_path, monkeypatch, queued):
    workers, callbacks = queued
    commands = []
    spoken = []
    monkeypatch.setattr(app, "announce", lambda message: spoken.append(message))

    def run(_self, command, _timeout, _cancelled):
        commands.append(command)
        output = "owner/repo\n" if command[2] == "list" else ""
        return subprocess.CompletedProcess(command, 0, output, ""), ""

    class Choice:
        def __init__(self, _parent, _message, _title, choices):
            assert choices == ["Type a repository name (owner/name)\u2026", "owner/repo"]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def ShowModal(self):
            return wx.ID_OK

        def GetStringSelection(self):
            return "owner/repo"

    monkeypatch.setattr(app.NewSessionDialog, "_run_github", run)
    monkeypatch.setattr(wx, "SingleChoiceDialog", Choice)
    dialog = app.NewSessionDialog(frame, default_dir=str(tmp_path))
    try:
        dialog._from_github()
        assert len(workers) == 1 and commands == []
        assert dialog.github_cancel.IsShown() and not dialog.github_btn.IsEnabled()
        assert "Loading GitHub repositories" in spoken[0]

        workers.pop(0)()
        _deliver(callbacks)
        assert len(workers) == 1 and commands[0][:3] == ["gh", "repo", "list"]
        assert dialog.github_cancel.IsShown()

        workers.pop(0)()
        _deliver(callbacks)
        assert commands[1][:3] == ["gh", "repo", "clone"]
        assert dialog.folder_box.GetValue() == str(tmp_path / "repo")
        assert dialog.github_btn.IsEnabled() and not dialog.github_cancel.IsShown()
        assert spoken[-1] == f"Folder set to {tmp_path / 'repo'}"
    finally:
        dialog.Destroy()


def test_cancel_discards_late_listing(frame, tmp_path, monkeypatch, queued):
    workers, callbacks = queued
    spoken = []
    monkeypatch.setattr(app, "announce", lambda message: spoken.append(message))
    monkeypatch.setattr(
        app.NewSessionDialog,
        "_run_github",
        lambda _self, command, *_args: (
            subprocess.CompletedProcess(command, 0, "owner/repo", ""),
            "",
        ),
    )
    dialog = app.NewSessionDialog(frame, default_dir=str(tmp_path))
    try:
        dialog._from_github()

        dialog._cancel_github()
        assert dialog._github_cancel.is_set()
        assert dialog.github_btn.IsEnabled() and not dialog.github_cancel.IsShown()
        assert spoken[-1] == "GitHub operation cancelled"

        workers.pop(0)()
        _deliver(callbacks)
        assert dialog.folder_box.GetValue() != str(tmp_path / "repo")
    finally:
        dialog.Destroy()


def test_destroyed_dialog_discards_a_late_result(frame, tmp_path, monkeypatch, queued):
    workers, callbacks = queued
    monkeypatch.setattr(app, "announce", lambda _message: None)
    monkeypatch.setattr(
        app.NewSessionDialog,
        "_run_github",
        lambda _self, command, *_args: (
            subprocess.CompletedProcess(command, 0, "owner/repo", ""),
            "",
        ),
    )
    dialog = app.NewSessionDialog(frame, default_dir=str(tmp_path))
    dialog._from_github()
    workers.pop(0)()
    dialog.Destroy()
    _deliver(callbacks)


def test_cancelled_clone_does_not_fill_the_folder(frame, tmp_path, monkeypatch, queued):
    workers, callbacks = queued
    monkeypatch.setattr(app, "announce", lambda _message: None)
    monkeypatch.setattr(
        app.NewSessionDialog,
        "_run_github",
        lambda _self, command, *_args: (
            subprocess.CompletedProcess(
                command, 0, "owner/repo" if command[2] == "list" else "", ""
            ),
            "",
        ),
    )

    class Choice:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def ShowModal(self):
            return wx.ID_OK

        def GetStringSelection(self):
            return "owner/repo"

    monkeypatch.setattr(wx, "SingleChoiceDialog", Choice)
    dialog = app.NewSessionDialog(frame, default_dir=str(tmp_path))
    try:
        before = dialog.folder_box.GetValue()
        dialog._from_github()
        workers.pop(0)()
        _deliver(callbacks)
        assert len(workers) == 1
        dialog._cancel_github()
        workers.pop(0)()
        _deliver(callbacks)
        assert dialog.folder_box.GetValue() == before
    finally:
        dialog.Destroy()


def test_listing_error_restores_available_controls(frame, tmp_path, monkeypatch, queued):
    workers, callbacks = queued
    errors = []
    monkeypatch.setattr(app, "announce", lambda _message: None)
    monkeypatch.setattr(app.NewSessionDialog, "_run_github", lambda *_args: (None, "offline"))
    monkeypatch.setattr(
        app.NewSessionDialog, "_reject", lambda _self, message: errors.append(message)
    )
    dialog = app.NewSessionDialog(frame, default_dir=str(tmp_path))
    try:
        dialog._from_github()
        workers.pop(0)()
        _deliver(callbacks)
        assert errors == ["The GitHub CLI could not list your repositories:\noffline"]
        assert dialog.github_btn.IsEnabled() and not dialog.github_cancel.IsShown()
    finally:
        dialog.Destroy()


def test_cancellation_stops_a_running_command(frame):
    dialog = app.NewSessionDialog(frame)
    cancelled = threading.Event()
    timer = threading.Timer(0.1, cancelled.set)
    timer.start()
    try:
        started = time.monotonic()
        result, error = dialog._run_github(
            [sys.executable, "-c", "import time; time.sleep(30)"], 30, cancelled
        )
        assert result is None and error == ""
        assert time.monotonic() - started < 10
    finally:
        timer.join()
        dialog.Destroy()
