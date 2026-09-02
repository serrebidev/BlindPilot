"""Closing a tab while its turn is still running.

The progress earcon is a looping sound meaning "still working". It is not the
panel's: `Earcons` belongs to the frame and every tab shares one, and on Windows
it is played with `SND_LOOP`, which is process-wide. Exactly one thing plays it
and exactly one thing stops it.

`_on_worker_finished` stops it, and its comment calls itself a safety net that
makes sure the loop is never left running. But it is delivered through the
worker mailbox, and `_drain_worker_events` drops everything the moment the
panel is gone — which is the whole point of that guard, and also the hole in
this one. Close a tab mid-turn and the "done" event is discarded by a panel
that no longer exists, so the sound loops on with nothing left that can stop
it. Starting and finishing a turn in another tab is the only cure.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app


class _Earcons:
    """The frame's one shared earcon player."""

    def __init__(self):
        self.stops = 0

    def stop_progress(self):
        self.stops += 1


class _Worker:
    def __init__(self, alive=True):
        self._alive = alive
        self.cancelled = 0
        self.joined = 0

    def is_alive(self):
        return self._alive

    def cancel(self):
        self.cancelled += 1

    def join(self, timeout=None):
        self.joined += 1


@pytest.fixture
def panel():
    stub = type("PanelStub", (), {})()
    stub._earcons = _Earcons()
    stub._worker = None
    stub._dictation_timer = None
    stub._close_question_dialog = lambda: None
    return stub


def test_closing_a_tab_mid_turn_stops_the_progress_loop(panel):
    """The bug: a looping sound with nothing left alive to stop it."""
    panel._worker = _Worker(alive=True)

    app.SessionPanel.cancel_worker(panel)

    assert panel._earcons.stops == 1, "the progress loop was left running"


def test_the_turn_is_still_cancelled(panel):
    panel._worker = _Worker(alive=True)

    app.SessionPanel.cancel_worker(panel)

    assert panel._worker.cancelled == 1
    assert panel._worker.joined == 1


def test_closing_a_tab_with_no_turn_running_is_still_quiet(panel):
    """A tab closed between turns must not leave a loop either: the sound is
    shared, so another tab may have started one."""
    app.SessionPanel.cancel_worker(panel)

    assert panel._earcons.stops == 1


def test_a_worker_that_already_finished_is_not_joined_again(panel):
    panel._worker = _Worker(alive=False)

    app.SessionPanel.cancel_worker(panel)

    assert panel._worker.cancelled == 0
