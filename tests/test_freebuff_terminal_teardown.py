"""Ending a FreeBuff terminal has to give the pseudo-terminal back as well.

Both teardown paths were written as a fallback chain - terminate, and only
`close` if terminate raised - so a terminate that worked, which is every
ordinary one, meant `close` never ran. The process ends either way, so nothing
looks wrong; what is left behind is the pseudo-terminal handle itself, one per
turn and one per prewarmed terminal that is thrown away.
"""

from __future__ import annotations

import threading

import agent_backends
from agent_backends import FreebuffWorker


class _FakeTerminal:
    """A terminal that ends when asked, as a healthy one does."""

    def __init__(self):
        self.calls: list[str] = []

    def terminate(self, force=False):
        self.calls.append("terminate")
        return True

    def close(self, force=False):
        self.calls.append("close")


def test_killing_a_terminal_closes_it_as_well_as_ending_it():
    terminal = _FakeTerminal()

    agent_backends._kill_pty(terminal)

    assert terminal.calls == ["terminate", "close"], (
        "a terminate that worked left the pseudo-terminal open"
    )


def test_a_terminal_that_will_not_terminate_is_still_closed():
    """The fallback the old shape was reaching for still has to work."""

    class _Stuck(_FakeTerminal):
        def terminate(self, force=False):
            self.calls.append("terminate")
            raise OSError("the terminal would not stop")

    terminal = _Stuck()

    agent_backends._kill_pty(terminal)

    assert terminal.calls == ["terminate", "close"]


def test_ending_a_turn_gives_back_the_terminal_it_ran_in():
    """Every FreeBuff turn ends through `cancel`, so every one of them leaked."""
    terminal = _FakeTerminal()
    callbacks = {
        "on_session": lambda _value: None,
        "on_started": lambda: None,
        "on_activity": lambda _kind, _value: None,
        "on_complete": lambda _value: None,
        "on_failed": lambda _value: None,
        "on_done": lambda: None,
    }
    worker = FreebuffWorker("do the work", None, ".", "default", **callbacks)

    def turn():
        worker._pty = terminal

    worker._do_run = turn
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    thread.join(10)

    assert not thread.is_alive()
    assert terminal.calls == ["terminate", "close"], (
        "the turn ended but its pseudo-terminal was never given back"
    )
