"""FreeBuff must never put a terminal window on screen.

A pseudoconsole creates no hosting window (Microsoft Learn, "Pseudoconsoles":
"With a pseudoconsole, the hosting window is not created"), so the ConPTY
FreeBuff runs in needs no console of BlindPilot's own. AllocConsole, by
contrast, hands back a console that has already appeared -- one visible frame
titled with BlindPilot's own executable, at app start, at backend switch, and
again at the first message. These tests pin the launch to the pseudoconsole
alone, with the console-hiding watcher kept only as a net for consoles
FreeBuff's own tools raise.
"""

from __future__ import annotations

import inspect
import json
import os
import platform
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import agent_backends as ab


def test_spawning_freebuff_allocates_no_console():
    """The PTY launch path contains no console allocation at all."""
    source = inspect.getsource(ab._spawn_freebuff_pty)
    assert "AllocConsole(" not in source
    assert "reserve_hidden_console(" not in source
    assert not hasattr(ab, "reserve_hidden_console")


def test_the_windows_terminal_is_a_pseudoconsole_only(monkeypatch):
    """Spawned through a fake _ConPtyTerminal, with no console API touched."""
    spawned: dict = {}

    class _Pty:
        pid = 0

        def read_chunk(self):
            return None

    def spawn(args, cwd, env, rows, cols):
        spawned.update(args=args, cwd=cwd, env=env, rows=rows, cols=cols)
        return _Pty()

    monkeypatch.setattr(ab, "_ConPtyTerminal", spawn)
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(ab, "subprocess_env", lambda _binary: {})
    monkeypatch.setattr(ab, "hide_console_windows", lambda roots=None: 0)

    ended = threading.Event()
    _pty, read = ab._spawn_freebuff_pty(["freebuff", "--cwd", "."], ".", ended)

    assert spawned["args"] == ["freebuff", "--cwd", "."]
    assert (spawned["rows"], spawned["cols"]) == (60, 180)
    assert ended.wait(3), "the drained stream must report its own end"
    assert read(0.1) == ""
    assert "winpty" not in inspect.getsource(ab._spawn_freebuff_pty)


@pytest.mark.skipif(platform.system() != "Windows", reason="ConPTY is Windows-only")
def test_a_windowless_process_gains_no_console_from_the_terminal(tmp_path):
    """Spawned as BlindPilot runs -- no console -- it must stay that way.

    pywinpty's native module calls AllocConsole in its host process before
    CreatePseudoConsole (the import is in the shipped _winpty binary, and a
    windowless probe measured a conhost child plus a ConsoleWindowClass owned
    by the host, titled with the host's own executable). The console-hiding
    watcher hides that window again, but only after it has appeared and taken
    screen-reader focus -- NVDA announces it as "terminal". Measuring windows
    afterwards therefore proves nothing; the host's own console handle before
    and after the spawn is the test.
    """
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        pytest.skip("no windowless interpreter beside this one")
    script = tmp_path / "probe.cmd"
    script.write_text("@echo probe-hello\r\n@ping -n 10 127.0.0.1 >nul\r\n", encoding="utf-8")
    helper = tmp_path / "spawn_probe.py"
    helper.write_text(
        "import ctypes, json, os, sys, threading, time\n"
        f"sys.path.insert(0, {os.getcwd()!r})\n"
        "import agent_backends as ab\n"
        "k = ctypes.windll.kernel32\n"
        "before = k.GetConsoleWindow()\n"
        "ended = threading.Event()\n"
        f"pty, read = ab._spawn_freebuff_pty([{str(script)!r}], os.getcwd(), ended)\n"
        'text = ""\n'
        "for _ in range(50):\n"
        "    text += read(3)\n"
        '    if "probe-hello" in text:\n'
        "        break\n"
        # The child is still alive (ping), which is when the console matters:
        # after the terminal closes, the allocator frees it again and the
        # window is gone either way.
        "during = k.GetConsoleWindow()\n"
        "ab.end_hidden_terminal(pty)\n"
        'print(json.dumps({"before": before, "during": during, "text": text}))\n',
        encoding="utf-8",
    )
    proc = subprocess.run(
        [str(pythonw), str(helper)],
        capture_output=True,
        text=True,
        timeout=55,
        cwd=os.getcwd(),
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert "probe-hello" in result["text"]
    assert result["before"] == 0, "the probe itself started with a console"
    assert result["during"] == 0, "spawning the terminal gave the windowless host a console window"


def test_freebuff_launches_trust_the_workspace_agents(monkeypatch):
    """The hidden terminal cannot answer FreeBuff's .agents trust prompt.

    ``--trust-agents`` is upstream's opt-in for unattended runs ("for CI" in
    its own help). Without it a project carrying ``.agents`` or ``mcp.json``
    stops on a prompt nobody can see, and the turn dies on the startup-silence
    timeout instead of answering.
    """
    launched: dict = {}
    monkeypatch.setattr(ab, "find_backend_cli", lambda _backend: "freebuff")
    monkeypatch.setattr(ab, "set_freebuff_model", lambda _model: None)
    monkeypatch.setattr(ab, "_freebuff_chat_dirs", lambda _cwd: {})

    def spawn(args, cwd, ended):
        launched["args"] = args
        return object(), lambda _timeout: ""

    monkeypatch.setattr(ab, "_spawn_freebuff_pty", spawn)
    ab.discard_freebuff_prewarm()
    try:
        ab.prewarm_freebuff(".", None, "some-model")
        for _ in range(200):
            if "args" in launched:
                break
            threading.Event().wait(0.01)
    finally:
        ab.discard_freebuff_prewarm()
    assert "--trust-agents" in launched["args"]
