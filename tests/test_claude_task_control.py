"""Stopping one Claude background task, and putting Claude's file edits back."""

from __future__ import annotations

import subprocess
import threading

import claude_session


def _bare_session() -> claude_session.ClaudeSession:
    """A session object without a process: only the task bookkeeping is used."""
    session = claude_session.ClaudeSession.__new__(claude_session.ClaudeSession)
    session._state = threading.Lock()
    session._running_tasks = {}
    return session


def test_the_reader_keeps_every_running_task_until_the_cli_says_it_ended():
    """Agents and background shells both count, so the list stays right
    between turns, when no worker is reading the stream."""
    session = _bare_session()
    session._note_task(
        {"type": "system", "subtype": "task_started", "task_id": "t1", "description": "Run tests"}
    )
    session._note_task(
        {"type": "system", "subtype": "task_started", "task_id": "t2", "task_type": "local_bash"}
    )
    session._note_task({"type": "assistant", "task_id": "t3"})
    assert session.running_tasks() == [("t1", "Run tests"), ("t2", "local_bash")]

    session._note_task({"type": "system", "subtype": "task_notification", "task_id": "t1"})
    assert session.running_tasks() == [("t2", "local_bash")]


def test_stop_task_names_only_the_chosen_task(monkeypatch):
    session = _bare_session()
    asked: list[tuple[str, dict]] = []

    def answer(subtype, timeout=None, **fields):
        asked.append((subtype, fields))
        return {"subtype": "success"}

    monkeypatch.setattr(session, "send_control", answer, raising=False)
    assert session.stop_task("t2")
    assert asked == [("stop_task", {"task_id": "t2"})]

    monkeypatch.setattr(session, "send_control", lambda *a, **k: None, raising=False)
    assert not session.stop_task("t2")


def test_every_claude_process_keeps_file_checkpoints():
    env = claude_session.claude_env("claude")
    assert env[claude_session.CHECKPOINT_VAR] == "1"
    assert env.get("PATH")


def _fake_run(monkeypatch, returncode: int, stdout: str = "", stderr: str = ""):
    calls: list[dict] = []

    def run(argv, **kwargs):
        calls.append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(claude_session.subprocess, "run", run)
    return calls


SESSION = "269bb9c3-ad48-4144-ae26-17e2f2af5508"
MESSAGE = "e6986a05-2a0e-459e-a893-d326cc681aa8"


def test_rewind_runs_the_cli_with_checkpoints_on_and_reports_success(monkeypatch):
    calls = _fake_run(monkeypatch, 0, stdout=f"Files rewound to state at message {MESSAGE}\n")
    ok, said = claude_session.rewind_files("claude", "C:/work", SESSION, MESSAGE)
    assert ok and "put back" in said
    (call,) = calls
    assert call["argv"] == ["claude", "-p", "--resume", SESSION, "--rewind-files", MESSAGE]
    assert call["env"][claude_session.CHECKPOINT_VAR] == "1"
    assert call["cwd"] == "C:/work"


def test_rewind_without_checkpoints_says_so_in_words(monkeypatch):
    _fake_run(
        monkeypatch,
        1,
        stderr="Warning: claude.ai MCP servers blocked\nError: File rewinding is not enabled.\n",
    )
    ok, said = claude_session.rewind_files("claude", "", SESSION, MESSAGE)
    assert not ok
    assert said == "Claude Code kept no file checkpoints for this conversation."


def test_rewind_refuses_ids_that_are_not_ids(monkeypatch):
    calls = _fake_run(monkeypatch, 0)
    ok, said = claude_session.rewind_files("claude", "", SESSION, "--dangerous")
    assert not ok and calls == []
    ok, _said = claude_session.rewind_files("claude", "", "", MESSAGE)
    assert not ok and calls == []
