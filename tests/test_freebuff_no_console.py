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
import platform
import sys
import threading
import types

import agent_backends as ab


def test_spawning_freebuff_allocates_no_console():
    """The PTY launch path contains no console allocation at all."""
    source = inspect.getsource(ab._spawn_freebuff_pty)
    assert "AllocConsole(" not in source
    assert "reserve_hidden_console(" not in source
    assert not hasattr(ab, "reserve_hidden_console")


def test_the_windows_terminal_is_a_pseudoconsole_only(monkeypatch):
    """Spawned through a fake pywinpty, with no console API touched."""
    spawned: dict = {}

    class _Pty:
        pid = 0

        def read(self, _size: int) -> str:
            raise EOFError("closed")

    def spawn(args, **kwargs):
        spawned["args"] = args
        spawned.update(kwargs)
        return _Pty()

    fake_winpty = types.SimpleNamespace(PtyProcess=types.SimpleNamespace(spawn=spawn))
    monkeypatch.setitem(sys.modules, "winpty", fake_winpty)
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(ab, "subprocess_env", lambda _binary: {})
    monkeypatch.setattr(ab, "hide_console_windows", lambda roots=None: 0)

    ended = threading.Event()
    _pty, read = ab._spawn_freebuff_pty(["freebuff", "--cwd", "."], ".", ended)

    assert spawned["args"] == ["freebuff", "--cwd", "."]
    assert spawned["dimensions"] == (60, 180)
    assert ended.wait(3), "the drained stream must report its own end"
    assert read(0.1) == ""


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
