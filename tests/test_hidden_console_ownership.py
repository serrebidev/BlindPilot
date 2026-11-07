"""A hidden sign-in terminal is an owned process, not only a hidden one.

Without a Job Object, terminating it ends the `.cmd` wrapper and leaves node
running invisibly, holding the sign-in state no window can release.
"""

from __future__ import annotations

import platform
import subprocess

import pytest

import agent_backends

pytestmark = pytest.mark.skipif(
    platform.system() != "Windows", reason="CREATE_NEW_CONSOLE is Windows-only"
)


class _FakeProc:
    pid = 4321

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


def test_the_hidden_console_is_started_through_owned_popen(tmp_path, monkeypatch) -> None:
    seen: dict = {}

    def fake_owned_popen(*args, **kwargs):
        seen["argv"] = args[0]
        seen["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(agent_backends, "owned_popen", fake_owned_popen)

    handle = agent_backends._spawn_hidden_console(["cmd", "/c", "exit"], str(tmp_path))

    assert handle is not None
    assert seen["argv"] == ["cmd", "/c", "exit"]
    # The console is still the hidden one; ownership is what changed.
    assert seen["kwargs"]["creationflags"] & subprocess.CREATE_NEW_CONSOLE
    assert seen["kwargs"]["startupinfo"].wShowWindow == subprocess.SW_HIDE
