"""Command Code's background commands must not open console windows.

Command Code starts builds and dev servers with Node's ``detached: true``,
which on Windows leaves the shell without a console, so every console program
it runs pops up a window. The worker's NODE_OPTIONS preload keeps those
spawns on the hidden console. This runs real Node, the same way Command Code
spawns, and asks the grandchild whether it has a console window.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys

import pytest

import commandcode_worker

pytestmark = pytest.mark.skipif(
    platform.system() != "Windows" or shutil.which("node") is None,
    reason="Windows console behaviour, needs Node",
)

# Written to a file: a grandchild that got a console of its own also gets that
# console's handles, so anything it printed would never reach the pipe.
_PROBE = (
    "import ctypes, sys; h = ctypes.windll.kernel32.GetConsoleWindow(); "
    "open(sys.argv[1], 'w').write("
    "'visible' if h and ctypes.windll.user32.IsWindowVisible(h) else 'hidden')"
)
# Command Code's own background spawn: shell, detached, asked to be hidden.
_SPAWN = (
    "const {spawn} = await import('node:child_process');"
    "const c = spawn(process.argv[1], [], {shell: true, detached: true, windowsHide: true,"
    " stdio: ['ignore', 'pipe', 'pipe']});"
    "c.on('close', () => process.exit(0));"
)


def _grandchild_console(env: dict, tmp_path) -> str:
    report = tmp_path / "console.txt"
    probe = f'"{sys.executable}" -c "{_PROBE}" "{report}"'
    subprocess.run(
        ["node", "--input-type=module", "-e", _SPAWN, probe],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return report.read_text(encoding="utf-8")


def test_a_detached_command_code_spawn_opens_no_window(tmp_path):
    env = commandcode_worker._commandcode_env(shutil.which("node") or "node")
    assert _grandchild_console(env, tmp_path) == "hidden"


def test_without_the_preload_the_same_spawn_does_open_a_window(tmp_path, monkeypatch):
    """Proves the probe can see the bug, so the test above means something."""
    monkeypatch.delenv("NODE_OPTIONS", raising=False)
    assert _grandchild_console(dict(os.environ), tmp_path) == "visible"


def test_the_preload_keeps_the_users_own_node_options(monkeypatch):
    monkeypatch.setenv("NODE_OPTIONS", "--max-old-space-size=4096")
    env = commandcode_worker._commandcode_env(shutil.which("node") or "node")
    assert env["NODE_OPTIONS"].startswith("--max-old-space-size=4096 --import=data:")
