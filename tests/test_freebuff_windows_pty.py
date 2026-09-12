"""FreeBuff's Windows pseudo-terminal: what it is started with, and what is read."""

from __future__ import annotations

import platform
import threading
import ctypes

import pytest

import agent_backends as ab

pytestmark = pytest.mark.skipif(platform.system() != "Windows", reason="pywinpty is Windows-only")
winpty = pytest.importorskip("winpty")


@pytest.fixture
def quiet_console(monkeypatch):
    """Keep the test process's own console out of it."""
    monkeypatch.setattr(ab, "reserve_hidden_console", lambda: False)
    monkeypatch.setattr(ab, "hide_console_windows", lambda roots=None: 0)


class _Pty:
    """A terminal that says its piece, exits, and is then closed."""

    pid = 4321

    def __init__(self, frames: list[str]) -> None:
        self.frames = list(frames)

    def isalive(self) -> bool:
        return False

    def read(self, size: int) -> str:
        if self.frames:
            return self.frames.pop(0)
        raise EOFError("Pty is closed")


def test_a_freebuff_console_cannot_be_activated_or_shown(monkeypatch):
    """A ConPTY console is only transport, never an interactive app window."""

    calls: list[tuple] = []

    class User32:
        def GetWindowLongW(self, handle, index):
            calls.append(("get_style", handle, index))
            return 0x00040000  # WS_EX_APPWINDOW

        def SetWindowLongW(self, handle, index, style):
            calls.append(("set_style", handle, index, style))

        def EnableWindow(self, handle, enabled):
            calls.append(("enable", handle, enabled))

        def ShowWindow(self, handle, command):
            calls.append(("show", handle, command))

        def SetWindowPos(self, *args):
            calls.append(("position", *args))

    class Windll:
        user32 = User32()

    monkeypatch.setattr(ctypes, "windll", Windll())

    ab._banish_window(42)

    assert ("set_style", 42, -20, 0x08000080) in calls
    assert ("enable", 42, False) in calls
    assert ("show", 42, 0) in calls
    position = next(call for call in calls if call[0] == "position")
    assert position[3:7] == (-32000, -32000, 0, 0)
    assert position[-1] & 0x0080  # SWP_HIDEWINDOW


def test_the_terminal_is_started_with_the_environment_every_cli_gets(monkeypatch, quiet_console):
    # Without it NoDefaultCurrentDirectoryInExePath is unset for FreeBuff and
    # everything it runs, so a git.exe committed to the project folder is what
    # runs when the agent asks for git.
    seen: dict = {}

    def spawn(args, **kwargs):
        seen.update(kwargs)
        return _Pty([])

    monkeypatch.setattr(winpty.PtyProcess, "spawn", spawn)
    ended = threading.Event()

    ab._spawn_freebuff_pty(["freebuff", "--cwd", "."], ".", ended)

    assert seen["cwd"] == "."
    assert seen["env"]["NoDefaultCurrentDirectoryInExePath"] == "1"
    assert ended.wait(3)


def test_a_spawn_that_fails_does_not_leave_the_console_watcher_running(monkeypatch, quiet_console):
    def spawn(*_args, **_kwargs):
        raise FileNotFoundError("freebuff was removed after discovery")

    monkeypatch.setattr(winpty.PtyProcess, "spawn", spawn)
    ended = threading.Event()

    with pytest.raises(FileNotFoundError):
        ab._spawn_freebuff_pty(["freebuff"], ".", ended)

    assert ended.is_set(), "hide_terminal polls EnumWindows four times a second until this is set"


def test_what_the_terminal_said_on_its_way_out_is_still_read(monkeypatch, quiet_console):
    # The pump looped on isalive(), so a terminal that died at startup had its
    # last line, the one that says why, left unread.
    monkeypatch.setattr(
        winpty.PtyProcess, "spawn", lambda *_a, **_k: _Pty(["env: node: not found\r\n"])
    )
    ended = threading.Event()

    _pty, read = ab._spawn_freebuff_pty(["freebuff"], ".", ended)

    assert ended.wait(3)
    assert read(1) == "env: node: not found\r\n"
