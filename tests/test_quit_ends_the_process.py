"""Quitting ends the process, not just the window.

Reported from the field: Alt+F4 closed the window and BlindPilot stayed in
the task list, running in the background with nothing to show for itself.

The close handler itself was never the problem — it cancels every tab,
stops the shared processes and lets the event through. What nothing did
was make the ending certain. A wx application leaves its event loop when
the last top-level window is destroyed, so any other one left standing —
a dialog parented to nothing, a window a platform component kept — holds
the process open on its own, and a thread a library started without making
it a daemon of holds it open after the loop has gone, because Python waits
for those before it will exit. Both are invisible from the code that is
doing everything right.

So quitting now takes the other windows down with it and asks the loop to
end outright, and once the loop has returned an exit watchdog gives the
process a bounded grace to finish before ending it. These tests pin both
halves: the close handler's new wiring, and the watchdog itself.
"""

from __future__ import annotations

import threading
import time

import blindpilot_app as app
from doubles import panel_stub


class _Window:
    """A top-level window that remembers being destroyed."""

    def __init__(self):
        self.destroyed = False

    def Destroy(self):
        self.destroyed = True


class _Worker:
    def __init__(self):
        self.cancelled = threading.Event()

    def is_alive(self):
        return not self.cancelled.is_set()

    def cancel(self):
        self.cancelled.set()

    def join(self, timeout=None):
        self.cancelled.wait(timeout)


# The real method, kept before anything swaps the class out for a stand-in:
# once `SessionPanel` is pointed at `_Page`, asking it for the method would
# ask `_Page` itself, forever.
_REAL_CANCEL = app.SessionPanel.cancel_worker


class _Page:
    """Stands in for `SessionPanel`, as the closing handlers check by type."""

    def cancel_worker(self, wait=True):
        return _REAL_CANCEL(self, wait=wait)


class _CloseEvent:
    def __init__(self):
        self.skipped = False

    def Skip(self):
        self.skipped = True


def _frame_with(pages):
    frame = type("FrameStub", (), {})()
    frame.chat_panel = None
    frame.notebook = type(
        "Notebook",
        (),
        {
            "GetPageCount": lambda self: len(pages),
            "GetPage": lambda self, i: pages[i],
        },
    )()
    frame._session_panels = lambda: app.MainFrame._session_panels(frame)
    return frame


# ----- the other windows -----


def test_quitting_destroys_the_other_top_level_windows(monkeypatch):
    frame, stray = _Window(), _Window()
    monkeypatch.setattr(app.wx, "GetTopLevelWindows", lambda: [frame, stray])

    app._destroy_other_top_level_windows(frame)

    assert stray.destroyed, "a window that was not the main one survived quitting"
    assert not frame.destroyed, "the window being closed was destroyed twice over"


def test_a_wx_that_cannot_list_its_windows_does_not_stop_quitting(monkeypatch):
    def broken():
        raise RuntimeError("no application")

    monkeypatch.setattr(app.wx, "GetTopLevelWindows", broken)

    app._destroy_other_top_level_windows(_Window())  # must not raise


def test_quitting_takes_the_stray_window_and_ends_the_loop(monkeypatch):
    """The close handler, end to end against its new last steps."""
    monkeypatch.setattr(app, "SessionPanel", _Page)
    pages = [panel_stub(_Page, _worker=_Worker())]
    frame = _frame_with(pages)
    stray = _Window()
    monkeypatch.setattr(app.wx, "GetTopLevelWindows", lambda: [frame, stray])

    ended = threading.Event()

    class _App:
        def ExitMainLoop(self):
            ended.set()

    monkeypatch.setattr(app.wx, "GetApp", lambda: _App())
    monkeypatch.setattr(app.wx, "CallAfter", lambda fn, *args: fn(*args))

    event = _CloseEvent()
    app.MainFrame._on_close(frame, event)

    assert event.skipped, "the close event was swallowed"
    assert stray.destroyed, "quitting left another window standing"
    assert ended.is_set(), "quitting never asked the event loop to end"


# ----- the watchdog -----


def test_the_exit_watchdog_ends_a_process_that_will_not_die(monkeypatch):
    fired = threading.Event()
    monkeypatch.setattr(app.os, "_exit", lambda code: fired.set())

    app._arm_exit_watchdog(0.05)

    assert fired.wait(3), "a stuck process was left running"


def test_the_exit_watchdog_waits_out_its_grace(monkeypatch):
    fired = threading.Event()
    monkeypatch.setattr(app.os, "_exit", lambda code: fired.set())

    app._arm_exit_watchdog(0.4)
    time.sleep(0.1)

    assert not fired.is_set(), "the watchdog fired before its grace was up"


def test_the_exit_watchdog_is_a_daemon(monkeypatch):
    """The watchdog must never be the thing keeping the process alive."""
    monkeypatch.setattr(app.os, "_exit", lambda code: None)

    before = {thread.ident for thread in threading.enumerate()}
    app._arm_exit_watchdog(60)
    watchers = [
        thread
        for thread in threading.enumerate()
        if thread.ident not in before and thread.name == "exit-watchdog"
    ]

    assert watchers and watchers[0].daemon, "the exit watchdog is not a daemon"
