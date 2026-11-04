# SPDX-License-Identifier: MIT
"""The GUI-thread budget, and the guard that keeps work inside it.

The budget is the number the rest of this work is measured against, so it is
pinned here rather than left to drift. The guard is checked against the
primitives it forbids and, just as importantly, against the things it must not
forbid — reading a file, waiting with a deadline, and starting the worker that
does the slow work — because a guard that fails the fix is worse than none.
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

import blindpilot_app as app
from blocking_contract import BlockedTheGuiThread, drive, gui_thread_may_not_block, offences

wx = pytest.importorskip("wx")


@pytest.fixture(autouse=True)
def forget_what_ran_long():
    """Each test starts with an empty record of over-budget work."""
    app._over_budget_work.clear()
    yield
    app._over_budget_work.clear()


def test_the_budget_is_fifty_milliseconds():
    """The number the design settled on. Changing it is a decision, not a tweak."""
    assert app.GUI_BUDGET_MS == 50.0


def test_work_over_budget_is_recorded_and_named():
    app._gui_work("Slow item", lambda: time.sleep(0.06))()

    recorded = app.over_budget_work()
    assert [name for name, _spent in recorded] == ["Slow item"]
    assert recorded[0][1] >= app.GUI_BUDGET_MS


def test_work_inside_the_budget_is_not_recorded():
    app._gui_work("Quick item", lambda: None)()

    assert app.over_budget_work() == []


def test_the_record_cannot_grow_without_limit(monkeypatch):
    """A stalled window files one entry per keystroke; the record is bounded."""
    monkeypatch.setattr(app, "GUI_BUDGET_MS", -1.0)
    for _ in range(app._over_budget_work.maxlen + 25):
        app._gui_work("item", lambda: None)()

    assert len(app.over_budget_work()) == app._over_budget_work.maxlen


def test_a_menu_item_is_bound_through_the_budget_wrapper(wx_app, monkeypatch):
    """The wiring, not just the wrapper.

    Asserted on the binding rather than by dispatching a menu event: a
    synthetic event's dispatch depends on wx's own state, which other tests in
    a full run have been through, and a guard that goes red by itself teaches
    people to ignore it.
    """
    measured: list[tuple[str, object]] = []

    def spy(name, action):
        measured.append((name, action))
        return action

    monkeypatch.setattr(app, "_gui_work", spy)
    frame = wx.Frame(None)
    try:
        menu = wx.Menu()
        action = lambda: None  # noqa: E731 - the identity is what is asserted
        app.MainFrame._menu_item(frame, menu, "&Changed files", "help", action)
    finally:
        frame.Destroy()

    assert [(name, entry is action) for name, entry in measured] == [("Changed files", True)]


def test_an_accelerator_is_bound_through_the_budget_wrapper(wx_app, monkeypatch):
    """Ctrl+Tab and the rest are measured by the same wrapper as the menus."""
    measured: list[tuple[str, object]] = []

    def spy(name, action):
        measured.append((name, action))
        return action

    monkeypatch.setattr(app, "_gui_work", spy)
    frame = wx.Frame(None)
    try:
        action = lambda: None  # noqa: E731 - the identity is what is asserted
        app.MainFrame._bind_shortcut(frame, wx.NewIdRef(), "Ctrl+Tab, next tab", action)
    finally:
        frame.Destroy()

    assert [(name, entry is action) for name, entry in measured] == [("Ctrl+Tab, next tab", True)]


# ----- the guard itself -----


def test_starting_a_process_is_caught():
    with gui_thread_may_not_block():
        error = drive(lambda: subprocess.run(["git", "status"]))

    assert isinstance(error, BlockedTheGuiThread)
    assert "process" in str(error)


def test_waiting_for_a_process_is_caught():
    """The shape of the worst offender: a process started earlier, waited on now."""
    running = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        with gui_thread_may_not_block():
            error = drive(running.wait)
    finally:
        running.wait()

    assert isinstance(error, BlockedTheGuiThread)
    assert "process" in str(error)


def test_joining_a_thread_is_caught():
    thread = threading.Thread(target=lambda: None)
    thread.start()
    try:
        with gui_thread_may_not_block():
            error = drive(thread.join)
    finally:
        thread.join()

    assert isinstance(error, BlockedTheGuiThread)


def test_an_unbounded_wait_is_caught():
    event = threading.Event()

    with gui_thread_may_not_block():
        error = drive(event.wait)

    assert isinstance(error, BlockedTheGuiThread)


def test_a_wait_with_a_deadline_is_allowed():
    """A deadline is a delay, not a stall."""
    event = threading.Event()

    with gui_thread_may_not_block():
        error = drive(event.wait, 0.001)

    assert error is None


def test_waiting_on_a_queue_is_caught():
    with gui_thread_may_not_block():
        error = drive(queue.Queue().get)

    assert isinstance(error, BlockedTheGuiThread)


def test_reaching_the_network_is_caught():
    with gui_thread_may_not_block():
        error = drive(urllib.request.urlopen, "https://example.invalid/")

    assert isinstance(error, BlockedTheGuiThread)


def test_starting_the_worker_that_does_the_work_is_allowed():
    """Moving work off the GUI thread is the fix; the guard must not fail it."""
    done: list[int] = []

    with gui_thread_may_not_block():
        error = drive(threading.Thread(target=lambda: done.append(1)).start)

    assert error is None
    deadline = time.monotonic() + 5
    while not done and time.monotonic() < deadline:
        time.sleep(0.01)
    assert done == [1]


def test_reading_a_file_is_allowed(tmp_path):
    """A settings read is well inside the budget, so it stays legal."""
    settings = tmp_path / "config.json"
    settings.write_text("{}", encoding="utf-8")

    with gui_thread_may_not_block():
        error = drive(lambda: settings.read_text(encoding="utf-8"))

    assert error is None


def test_another_failing_handler_is_not_counted_as_an_offence():
    """The guard reports blocking, not every handler that cannot run here."""

    def selects_nothing():
        raise LookupError("no session is open")

    with gui_thread_may_not_block():
        error = drive(selects_nothing)

    assert isinstance(error, LookupError)
    assert offences([("Some item", error)]) == []


def test_the_guard_names_only_the_handlers_that_blocked():
    with gui_thread_may_not_block():
        driven = [
            ("Quick item", drive(lambda: None)),
            ("Slow item", drive(lambda: subprocess.run(["git"]))),
        ]

    assert offences(driven) == ["Slow item"]
