"""Slow history stores must not block the dialog or replace a newer scope."""

import threading
import time

import wx

import blindpilot_app as app
from session_history import HistoryEntry


def _wait(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        wx.GetApp().Yield()
        time.sleep(0.01)
    assert predicate()


def test_slow_history_opens_immediately_and_ignores_the_previous_scope(frame, monkeypatch):
    release = threading.Event()
    finished = threading.Event()
    threads = []
    folder = HistoryEntry("claude", "folder", "Old scope", "", 1)
    all_folders = HistoryEntry("claude", "all", "All folders", "", 2)

    def scan(backend, cwd):
        threads.append(threading.get_ident())
        if cwd:
            release.wait(3)
            finished.set()
            return [folder]
        return [all_folders]

    monkeypatch.setattr(app, "list_history", scan)
    monkeypatch.setattr(app, "announce", lambda *_a, **_k: None)
    dialog = app.HistoryDialog(frame, "claude", ".")
    delivered = []
    loaded = dialog._history_loaded

    def receive(*args):
        loaded(*args)
        delivered.append(args[0])

    monkeypatch.setattr(dialog, "_history_loaded", receive)
    try:
        assert not finished.is_set(), "constructing the dialog waited for the scan"
        assert not dialog.FindWindow(wx.ID_OK).IsEnabled()
        assert "Loading" in dialog.summary.GetLabel()
        dialog.filter_box.ChangeValue("All")
        dialog._refresh()
        assert "Loading" in dialog.summary.GetLabel()
        dialog.scope_picker.SetSelection(1)
        dialog._reload()
        _wait(lambda: dialog._shown == [all_folders])
        assert dialog.FindWindow(wx.ID_OK).IsEnabled()
        release.set()
        _wait(lambda: len(delivered) == 2)
        assert dialog._shown == [all_folders]
        assert all(t != threading.get_ident() for t in threads)
    finally:
        release.set()
        dialog._preview_timer.Stop()
        dialog.Destroy()


def test_history_loading_failure_is_spoken_and_leaves_no_openable_row(frame, monkeypatch):
    def scan(*_a):
        raise OSError("History unavailable")

    spoken = []
    monkeypatch.setattr(app, "list_history", scan)
    monkeypatch.setattr(app, "announce", lambda text, **_k: spoken.append(text))
    dialog = app.HistoryDialog(frame, "claude", ".")
    try:
        _wait(lambda: any("History unavailable" in text for text in spoken))
        assert dialog.list_box.GetCount() == 0
        assert not dialog.FindWindow(wx.ID_OK).IsEnabled()
        assert "History unavailable" in dialog.summary.GetLabel()
    finally:
        dialog._preview_timer.Stop()
        dialog.Destroy()


def test_closing_during_loading_discards_the_result(frame, monkeypatch):
    release = threading.Event()

    def scan(*_a):
        release.wait(3)
        return [HistoryEntry("claude", "late", "Late result", "", 1)]

    spoken = []
    monkeypatch.setattr(app, "list_history", scan)
    monkeypatch.setattr(app, "announce", lambda text, **_k: spoken.append(text))
    dialog = app.HistoryDialog(frame, "claude", ".")
    delivered = threading.Event()
    loaded = dialog._history_loaded

    def receive(*args):
        loaded(*args)
        delivered.set()

    monkeypatch.setattr(dialog, "_history_loaded", receive)
    dialog.Destroy()
    _wait(lambda: not dialog)
    release.set()
    _wait(delivered.is_set)
    # A callback on a destroyed wx object must not update controls or speak.
    assert spoken == []
