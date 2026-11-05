from __future__ import annotations

import pytest

import blindpilot_app as app

wx = pytest.importorskip("wx")


@pytest.fixture
def frame():
    owns_app = wx.GetApp() is None
    application = wx.GetApp() or wx.App(False)
    parent = wx.Frame(None)
    yield parent
    parent.Destroy()
    if owns_app:
        application.Destroy()


def test_provider_list_worker_drops_result_after_app_shutdown(frame, monkeypatch):
    workers = []

    class Thread:
        def __init__(self, target, args=(), daemon=False, **_kwargs):
            assert daemon
            self.target = lambda: target(*args)

        def start(self):
            workers.append(self.target)

    monkeypatch.setattr(app.threading, "Thread", Thread)
    monkeypatch.setattr(app, "opencode_providers", lambda: ([], set(), ""))
    monkeypatch.setattr(app, "announce", lambda _message: None)
    dialog = app.ConnectDialog(frame)
    try:
        monkeypatch.setattr(app.wx, "GetApp", lambda: None)

        def unexpected_call_after(*_args, **_kwargs):
            raise AssertionError("wx.CallAfter was called after app shutdown")

        monkeypatch.setattr(app.wx, "CallAfter", unexpected_call_after)
        workers.pop()()
    finally:
        dialog.Destroy()


def test_dispatch_race_with_app_shutdown_drops_the_callback(monkeypatch):
    app_states = iter((object(), None))
    monkeypatch.setattr(app.wx, "GetApp", lambda: next(app_states))

    def destroyed_app(*_args, **_kwargs):
        raise AssertionError("wx.CallAfter raced with app shutdown")

    monkeypatch.setattr(app.wx, "CallAfter", destroyed_app)

    app._call_after_alive(lambda: None)
