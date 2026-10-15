"""Work that finishes after the application is gone is dropped, not posted.

`update_dialog` already declines to call wx.CallAfter once wx.App is gone,
where it raises and the thread hook logs a critical line for a callback
nobody is left to run. The same guard now stands in front of the three
places a worker thread or a background load posts to the GUI thread: the
session panel's worker-event queue, and the history dialog's list and
preview loads.
"""

from __future__ import annotations

import threading

import pytest

import blindpilot_app as app

wx = pytest.importorskip("wx")


class _Thread:
    """`threading.Thread` with the work run on the spot, so the test sees
    exactly what the worker would have posted."""

    def __init__(self, target, daemon=False):
        self._target = target

    def start(self):
        self._target()


def _gone_app(monkeypatch, queued: list):
    monkeypatch.setattr(wx, "GetApp", lambda: None)
    monkeypatch.setattr(wx, "CallAfter", lambda fn, *args: queued.append((fn, args)))


def _worker_panel():
    panel = type("Panel", (), {})()
    panel._worker_event_lock = threading.Lock()
    panel._worker_events = []
    panel._worker_events_scheduled = False
    panel._drain_worker_events = lambda: None
    return panel


def test_a_worker_event_after_the_app_is_gone_is_not_posted(monkeypatch):
    queued: list = []
    _gone_app(monkeypatch, queued)
    panel = _worker_panel()

    app.SessionPanel._queue_worker_event(panel, "complete", "the answer")

    assert queued == []


def test_a_worker_event_while_the_app_lives_is_posted(monkeypatch):
    queued: list = []
    monkeypatch.setattr(wx, "GetApp", lambda: object())
    monkeypatch.setattr(wx, "CallAfter", lambda fn, *args: queued.append((fn, args)))
    panel = _worker_panel()

    app.SessionPanel._queue_worker_event(panel, "complete", "the answer")

    assert queued == [(panel._drain_worker_events, ())]


def test_a_history_load_finishing_after_the_app_is_gone_is_not_posted(monkeypatch):
    queued: list = []
    _gone_app(monkeypatch, queued)
    monkeypatch.setattr(app.threading, "Thread", _Thread)
    monkeypatch.setattr(app, "list_history", lambda backend, cwd: [])
    dialog = type("Dialog", (), {})()
    dialog._load_serial = 0
    dialog._loading = True
    dialog._load_error = ""
    dialog._entries = []
    dialog._selected_backend = lambda: "claude"
    dialog._selected_cwd = lambda: None
    dialog._refresh = lambda: None
    dialog._history_loaded = lambda *args: None

    app.HistoryDialog._reload(dialog)

    assert queued == []


def test_a_preview_load_finishing_after_the_app_is_gone_is_not_posted(monkeypatch):
    queued: list = []
    _gone_app(monkeypatch, queued)
    monkeypatch.setattr(app.threading, "Thread", _Thread)
    monkeypatch.setattr(app, "load_turns", lambda entry: "the last exchange")
    dialog = type("Dialog", (), {})()
    dialog._preview_for = object()
    dialog._show_preview = lambda entry, text: None

    app.HistoryDialog._load_preview(dialog)

    assert queued == []
