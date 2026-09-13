"""The off-screen terminal a sign-in runs in when nobody has to read it.

Command Code's login mounts an Ink UI that refuses to start without a real
terminal, and needs nothing else from it -- the sign-in itself is the browser
page the CLI opens. Windows gets a console created hidden; everywhere else the
pseudo-terminal FreeBuff runs in is already off screen. These tests cover both
without starting a terminal on the machine the suite runs on.
"""

from __future__ import annotations

import platform
import subprocess
import time

import pytest

import agent_backends as ab
import blindpilot_app as app


@pytest.mark.skipif(platform.system() == "Windows", reason="Windows uses a console, not a pty")
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


class _Proc:
    """Enough of a Popen for the hidden console's handle."""

    pid = 4_000_000  # not a real pid: nothing here may signal a live process
    returncode = None

    def __init__(self) -> None:
        self.killed = False

    def poll(self):
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


@pytest.mark.skipif(platform.system() != "Windows", reason="CREATE_NEW_CONSOLE is Windows-only")
def test_the_sign_in_console_is_created_hidden_rather_than_hidden_after(monkeypatch):
    """AllocConsole hands back a console that has already appeared, and hiding
    it next frame still shows it. A console created with the show flag already
    set starts hidden and flashes nothing, which is the whole point."""
    seen: dict = {}
    proc = _Proc()

    def fake_popen(args, **kwargs):
        seen["args"] = args
        seen.update(kwargs)
        return proc

    monkeypatch.setattr(ab.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ab, "hide_console_windows", lambda roots=None: 0)

    terminal = ab._spawn_hidden_console(["command-code", "login"], "C:/tmp")

    assert seen["args"] == ["command-code", "login"]
    assert seen["cwd"] == "C:/tmp"
    assert seen["creationflags"] & subprocess.CREATE_NEW_CONSOLE
    startupinfo = seen["startupinfo"]
    assert startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW
    assert startupinfo.wShowWindow == subprocess.SW_HIDE

    # What the wizard asks of it: is it still running, and how did it end.
    assert terminal.isalive() is True
    proc.returncode = 0
    assert terminal.isalive() is False
    assert terminal.exitstatus == 0


@pytest.mark.skipif(platform.system() != "Windows", reason="CREATE_NEW_CONSOLE is Windows-only")
def test_windows_sign_in_goes_through_the_hidden_console(monkeypatch):
    """The pty path is not used on Windows: it is the one that needs
    AllocConsole, and that is the console window the user sees."""
    calls: list[tuple] = []
    monkeypatch.setattr(
        ab, "_spawn_hidden_console", lambda args, cwd: calls.append((args, cwd)) or "handle"
    )
    monkeypatch.setattr(ab, "_spawn_freebuff_pty", lambda *a: pytest.fail("pty path on Windows"))

    assert ab.spawn_hidden_terminal(["command-code", "login"], ".") == "handle"
    assert calls == [(["command-code", "login"], ".")]


@pytest.mark.skipif(platform.system() != "Windows", reason="CREATE_NEW_CONSOLE is Windows-only")
def test_stopping_a_hidden_console_ends_the_process_tree(monkeypatch):
    """The launcher is a batch file with a Node child: killing only the
    launcher would leave the sign-in running with nothing attached to it."""
    ended: list = []
    monkeypatch.setattr(ab, "end_process_group", ended.append)
    proc = _Proc()

    ab._HiddenConsoleProcess(proc).terminate(True)

    assert ended == [proc]


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
