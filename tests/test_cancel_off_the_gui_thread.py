"""Cancelling a turn without freezing the window.

`_on_stop` already knows the rule, and says so: "cancel() waits on the
process, so it must not run on the UI thread." It starts a thread.

The two paths that close things did not. `_close_current_session` calls
`cancel_worker` straight from the menu handler, and it waits on the process and
then joins for up to three more seconds — so closing a tab mid-turn froze the
whole window, every tab of it, with no earcon and no announcement, because the
thread that would have spoken is the one that is blocked.

`_on_close` does the same thing once per tab, in a loop, so the freeze is
multiplied by however many turns are running.

Neither wait buys anything for a tab being destroyed. Quitting is different:
the CLI subprocesses have to actually be killed or they outlive the
application, so that one still waits — but on one shared budget rather than
three seconds per tab.
"""

from __future__ import annotations

import threading
import time


import blindpilot_app as app


class _Worker:
    """A worker whose cancel takes a moment, as a real one does."""

    def __init__(self, delay=0.3):
        self.delay = delay
        self.cancelled = threading.Event()
        self.cancel_thread = None

    def is_alive(self):
        return not self.cancelled.is_set()

    def cancel(self):
        self.cancel_thread = threading.current_thread()
        time.sleep(self.delay)
        self.cancelled.set()

    def join(self, timeout=None):
        self.cancelled.wait(timeout)


# The real method, kept before anything swaps the class out for a stand-in.
_REAL_CANCEL = app.SessionPanel.cancel_worker


class _Page:
    """Stands in for `SessionPanel`, which the closing handlers check by type.

    A real one cannot be built without a parent window, and wxPython refuses
    `object.__new__`, so the type check is pointed at this instead.
    """

    def cancel_worker(self, wait=True):
        return _REAL_CANCEL(self, wait=wait)


def _panel(worker=None):
    panel = _Page()
    panel._worker = worker
    panel._earcons = type("E", (), {"stop_progress": lambda self: None})()
    panel._dictation_timer = None
    panel._close_question_dialog = lambda: None
    return panel


# ----- closing one tab -----
def test_closing_a_tab_does_not_block_the_window(monkeypatch):
    """The bug: the menu handler waited on a subprocess."""
    worker = _Worker(delay=0.5)
    panel = _panel(worker)

    started = time.monotonic()
    app.SessionPanel.cancel_worker(panel, wait=False)
    took = time.monotonic() - started

    assert took < 0.2, f"the caller was blocked for {took:.2f}s"


def test_the_turn_is_still_cancelled(monkeypatch):
    worker = _Worker(delay=0.05)
    panel = _panel(worker)

    app.SessionPanel.cancel_worker(panel, wait=False)

    assert worker.cancelled.wait(3), "the turn was never cancelled"
    assert worker.cancel_thread is not threading.main_thread()


def test_the_close_handler_uses_the_non_blocking_form(monkeypatch):
    """Verifying the wiring, not just that the option exists."""
    waits: list[bool] = []
    monkeypatch.setattr(_Page, "cancel_worker", lambda self, wait=True: waits.append(wait))

    monkeypatch.setattr(app, "SessionPanel", _Page)
    frame = type("FrameStub", (), {})()
    page = _panel(_Worker())
    frame.notebook = type(
        "Notebook",
        (),
        {
            "GetPageCount": lambda self: 2,
            "GetSelection": lambda self: 0,
            "GetPage": lambda self, i: page,
            "DeletePage": lambda self, i: None,
        },
    )()
    frame._sync_tab_switcher = lambda: None
    frame._set_status_text = lambda text: None

    app.MainFrame._close_current_session(frame)

    assert waits == [False], "closing a tab still waits on the subprocess"


# ----- quitting -----
def test_quitting_cancels_every_tab(monkeypatch):
    monkeypatch.setattr(app, "SessionPanel", _Page)
    workers = [_Worker(delay=0.05) for _ in range(3)]
    pages = [_panel(worker) for worker in workers]
    frame = _frame_with(pages)

    app.MainFrame._on_close(frame, _CloseEvent())

    for worker in workers:
        assert worker.cancelled.is_set(), "a CLI was left running after quitting"


def test_quitting_does_not_take_three_seconds_per_tab(monkeypatch):
    """One shared budget: the tabs are cancelled at the same time, not in a
    queue behind each other."""
    monkeypatch.setattr(app, "SessionPanel", _Page)
    workers = [_Worker(delay=0.4) for _ in range(4)]
    pages = [_panel(worker) for worker in workers]
    frame = _frame_with(pages)

    started = time.monotonic()
    app.MainFrame._on_close(frame, _CloseEvent())
    took = time.monotonic() - started

    assert took < 1.2, f"quitting took {took:.2f}s, which is one tab after another"


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
    # The real helper, over the stub notebook, so quitting sees these pages.
    frame._session_panels = lambda: app.MainFrame._session_panels(frame)
    return frame


def test_quitting_still_closes_the_window(monkeypatch):
    monkeypatch.setattr(app, "SessionPanel", _Page)
    frame = _frame_with([_panel(_Worker(delay=0.01))])
    event = _CloseEvent()

    app.MainFrame._on_close(frame, event)

    assert event.skipped, "the close event was swallowed"
