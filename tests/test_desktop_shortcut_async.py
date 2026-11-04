"""Creating the desktop shortcut must not hold the GUI callback."""

from __future__ import annotations

import blindpilot_app as app


def test_shortcut_creation_runs_in_worker_and_reports_result(frame, monkeypatch):
    workers = []
    callbacks = []
    spoken = []

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
    monkeypatch.setattr(
        app, "create_desktop_shortcut", lambda: "C:/Users/test/Desktop/BlindPilot.lnk"
    )
    frame._desktop_shortcut_serial = 0
    frame._desktop_shortcut_running = False
    frame._announce_setting = spoken.append
    frame._desktop_shortcut_finished = lambda *args: app.MainFrame._desktop_shortcut_finished(
        frame, *args
    )

    app.MainFrame._create_desktop_shortcut(frame)
    assert len(workers) == 1
    assert frame._desktop_shortcut_running
    assert spoken == ["Creating desktop shortcut…"]

    # A second activation cannot start a concurrent PowerShell operation.
    app.MainFrame._create_desktop_shortcut(frame)
    assert len(workers) == 1

    workers.pop()()
    callback, args = callbacks.pop()
    callback(*args)
    assert not frame._desktop_shortcut_running
    assert spoken[-1] == "Desktop shortcut created at C:/Users/test/Desktop/BlindPilot.lnk"


def test_shortcut_creation_reports_worker_error(frame, monkeypatch):
    callbacks = []
    spoken = []
    monkeypatch.setattr(
        app, "_call_after_alive", lambda callback, *args: callbacks.append((callback, args))
    )
    monkeypatch.setattr(
        app, "create_desktop_shortcut", lambda: (_ for _ in ()).throw(OSError("PowerShell failed"))
    )
    monkeypatch.setattr(
        app.threading,
        "Thread",
        lambda target, daemon=False: type("Thread", (), {"start": lambda _self: target()})(),
    )
    frame._desktop_shortcut_serial = 0
    frame._desktop_shortcut_running = False
    frame._announce_setting = spoken.append
    frame._desktop_shortcut_finished = lambda *args: app.MainFrame._desktop_shortcut_finished(
        frame, *args
    )

    app.MainFrame._create_desktop_shortcut(frame)
    callback, args = callbacks.pop()
    callback(*args)
    assert not frame._desktop_shortcut_running
    assert spoken[-1] == "The desktop shortcut could not be created: PowerShell failed"


def test_shortcut_result_after_close_is_discarded(frame, monkeypatch):
    spoken = []
    frame._desktop_shortcut_serial = 4
    frame._desktop_shortcut_running = True
    frame._links_timer = None
    frame._model_timer = None
    frame.chat_panel = None
    frame._session_panels = list
    frame._announce_setting = spoken.append
    monkeypatch.setattr(app, "_destroy_other_top_level_windows", lambda _frame: None)
    monkeypatch.setattr(
        app.threading,
        "Thread",
        lambda **_kwargs: type("Thread", (), {"start": lambda _self: None})(),
    )

    app.MainFrame._desktop_shortcut_finished(frame, 4, "shortcut.lnk", "")
    assert spoken == ["Desktop shortcut created at shortcut.lnk"]

    class Event:
        def Skip(self):
            pass

    app.MainFrame._on_close(frame, Event())
    app.MainFrame._desktop_shortcut_finished(frame, 4, "late.lnk", "")
    assert spoken == ["Desktop shortcut created at shortcut.lnk"]
