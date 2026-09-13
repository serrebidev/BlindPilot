"""The off-screen terminal a sign-in runs in when nobody has to read it.

Command Code's login mounts an Ink UI that refuses to start without a real
terminal, and needs nothing else from it -- the sign-in itself is the browser
page the CLI opens. These tests cover the two things that wrapper has to get
right, without starting a terminal on the machine the suite runs on.
"""

from __future__ import annotations

import time

import agent_backends as ab
import blindpilot_app as app


def test_a_hidden_terminal_is_drained_so_its_command_is_never_blocked(monkeypatch):
    """Nobody reads a hidden sign-in's output, and it still has to be read.

    A terminal buffer that fills up stops the command writing into it, which
    would leave the sign-in stuck with nothing on screen to explain it.
    """
    terminal = object()
    reads: list[float] = []

    def spawn(args, cwd, stream_ended):
        assert args == ["command-code", "login"]
        assert cwd == "."

        def read(timeout: float) -> str:
            reads.append(timeout)
            if len(reads) >= 3:
                # Stand in for the command ending: the stream event is what
                # the drain loop watches to know there is nothing left.
                stream_ended.set()
            return ""

        return terminal, read

    monkeypatch.setattr(ab, "_spawn_freebuff_pty", spawn)

    assert ab.spawn_hidden_terminal(["command-code", "login"], ".") is terminal

    for _ in range(200):
        if len(reads) >= 3:
            break
        time.sleep(0.01)
    assert len(reads) >= 3, "a hidden terminal's output must still be read"
    assert all(timeout > 0 for timeout in reads), "each read must be bounded"


def test_a_hidden_terminal_is_stopped_when_the_sign_in_is_abandoned():
    """Backing out of the wizard must not leave the CLI running unseen."""
    calls: list[tuple[str, tuple]] = []

    class _Terminal:
        def terminate(self, force: bool = False) -> None:
            calls.append(("terminate", (force,)))

        def close(self, force: bool = False) -> None:
            calls.append(("close", (force,)))

    ab.end_hidden_terminal(_Terminal())

    # Both, not the first that works: closing is what gives the terminal
    # handle back, and returning after a successful terminate leaked it.
    assert calls == [("terminate", (True,)), ("close", (True,))]


def test_a_terminal_that_cannot_say_whether_it_is_running_is_not_awaited():
    """A handle without `isalive` is treated as finished, so the wizard asks
    the CLI what happened instead of waiting on a terminal it cannot see."""
    assert app._terminal_running(object()) is False
