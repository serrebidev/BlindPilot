"""Muser as a backend: its command line, its events, its sign-in, its history.

The event shapes are the ones `muser exec --json` prints at muser-cli
0.3.6, measured live, and the store layout is the one under
``%LOCALAPPDATA%\\muser`` on the same machine.
"""

from __future__ import annotations

import json

import agent_backends
import muser_backend
import session_history
from task_list import TaskList


def _worker(**overrides):
    said: dict[str, list] = {
        "activity": [],
        "complete": [],
        "failed": [],
        "session": [],
    }
    options = {
        "on_session": said["session"].append,
        "on_started": lambda: None,
        "on_activity": lambda kind, text: said["activity"].append((kind, text)),
        "on_complete": said["complete"].append,
        "on_failed": said["failed"].append,
        "on_done": lambda: None,
    }
    options.update(overrides)
    prompt = options.pop("prompt", "hello")
    session = options.pop("session", None)
    mode = options.pop("mode", "bypassPermissions")
    worker = muser_backend.MuserWorker(prompt, session, "C:/proj", mode, **options)
    return worker, said


def test_the_worker_thread_starts():
    """Regression pin: the worker is a Thread, and Thread owns `_started`.

    A `_started` flag of the worker's own shadows the Event Thread.start()
    calls .is_set() on, and the worker can never be started at all.
    """
    worker, said = _worker()
    worker._do_run = lambda: None
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert said["failed"] == []


def test_a_new_conversation_asks_for_no_session_and_a_resumed_one_names_it():
    worker, _said = _worker()
    command = worker._command("muser")
    assert "--session" not in command
    # The prompt travels on stdin; "-" is exec's word for that.
    assert command[-1] == "-"

    later, _ = _worker(session="abc-123")
    resumed = later._command("muser")
    assert resumed[resumed.index("--session") + 1] == "abc-123"


def test_permission_modes_map_to_musers_modes():
    def mode(permission: str) -> str:
        worker, _ = _worker(mode=permission)
        command = worker._command("muser")
        return command[command.index("--mode") + 1]

    assert mode("plan") == "plan"
    assert mode("acceptEdits") == "auto-edit"
    assert mode("default") == "default"
    # In exec mode an unanswered approval is declined: Don't Ask is Default.
    assert mode("dontAsk") == "default"
    # Muser has no reviewer mode, so Auto is bypass, like Bypass Permissions.
    assert mode("auto") == "bypass"
    assert mode("bypassPermissions") == "bypass"


def test_a_turn_reports_its_steps_and_answers_whole(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    worker, said = _worker()
    worker._handle_frame({"type": "start", "session": "s1", "cwd": "C:/proj", "mode": "bypass"})
    worker._handle_frame(
        {
            "type": "tool",
            "agent": "main",
            "command": "code.read",
            "summary": "README.md",
            "ok": True,
        }
    )
    worker._handle_frame(
        {
            "type": "tool",
            "agent": "main",
            "command": "code.edit",
            "summary": "app.py",
            "ok": False,
            "declined": True,
        }
    )
    worker._handle_frame(
        {
            "type": "tool",
            "agent": "main",
            "command": "system.run",
            "summary": "pytest",
            "ok": False,
            "error": "exit 1\nmore",
        }
    )
    worker._handle_frame({"type": "muse_step", "title": "Searched memory", "detail": "x"})
    worker._handle_frame(
        {
            "type": "result",
            "ok": True,
            "session": "s1",
            "reply": "Done.",
            "sources": [{"label": "Docs", "uri": "https://example.com/docs"}],
            "duration_ms": 5,
        }
    )

    assert said["session"] == ["s1"]
    assert ("tool", "code.read README.md") in said["activity"]
    assert ("tool", "Declined (needs approval in this mode): code.edit app.py") in said["activity"]
    assert ("tool", "Failed: system.run pytest: exit 1") in said["activity"]
    assert ("notice", "Searched memory") in said["activity"]
    assert said["complete"] == ["Done.\n\nSources:\n- Docs: https://example.com/docs"]
    assert said["failed"] == []


def test_a_failed_result_is_not_an_answer(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    worker, said = _worker()
    worker._handle_frame({"type": "start", "session": "s1"})
    worker._handle_frame(
        {"type": "result", "ok": False, "session": "s1", "error": "Error: not logged in"}
    )
    assert said["complete"] == []
    assert worker._errors and "Sign in" in worker._errors[-1]


def test_the_turns_task_list_is_reported_from_the_transcript(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    folder = tmp_path / "muser" / "transcripts"
    folder.mkdir(parents=True)
    record = {
        "v": 1,
        "user": "hello",
        "reply": "Done.",
        "todos": [
            {"content": "Plan", "status": "completed"},
            {"content": "Build", "status": "in_progress"},
        ],
    }
    (folder / "s1.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    worker, said = _worker()
    reported: list = []
    worker.on_tasks = lambda *call: reported.append(call)
    worker._handle_frame({"type": "start", "session": "s1"})
    worker._handle_frame(
        {"type": "result", "ok": True, "session": "s1", "reply": "Done.", "sources": []}
    )
    assert reported == [("muser", record["todos"])]
    assert said["complete"] == ["Done."]

    tasks = TaskList()
    tasks.apply_claude("TodoWrite", {"todos": reported[0][1]})
    assert tasks.lines() == ["1. Done: Plan", "2. In progress: Build"]


_DOCTOR = """web login   : ready
gateway     : connected (18 chats)
pc device   : authenticated (vm ok)
usage today : 6 turns, 79 tool calls
"""


def _fake_cli(monkeypatch, doctor: str, usage: str = ""):
    monkeypatch.setattr(muser_backend, "find_backend_cli", lambda backend: "muser")

    def probe(_binary, args, _timeout):
        if args == ["doctor"]:
            return 0, doctor
        if args == ["usage"]:
            return 0, usage
        return 1, ""

    monkeypatch.setattr(agent_backends, "_probe_backend", probe)


def test_sign_in_needs_the_web_login_and_the_device(monkeypatch):
    _fake_cli(monkeypatch, _DOCTOR)
    assert muser_backend.muser_auth_ok()

    _fake_cli(monkeypatch, _DOCTOR.replace("authenticated (vm ok)", "not registered"))
    assert not muser_backend.muser_auth_ok()

    _fake_cli(monkeypatch, _DOCTOR.replace(": ready", ": signed out"))
    assert not muser_backend.muser_auth_ok()


def test_account_lines_carry_the_weekly_usage(monkeypatch):
    usage = "Muse usage (the same values as muse.ai)\nPower · weekly usage 29% (resets at 11/5, 05:57 PM)\n"
    _fake_cli(monkeypatch, _DOCTOR, usage)
    assert muser_backend.muser_account_lines() == [
        "Signed in: yes",
        "Gateway: connected (18 chats)",
        "Power · weekly usage 29% (resets at 11/5, 05:57 PM)",
    ]

    _fake_cli(monkeypatch, _DOCTOR.replace(": ready", ": signed out"), usage)
    assert muser_backend.muser_account_lines() == ["Signed in: no"]


def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    root = tmp_path / "muser"
    (root / "transcripts").mkdir(parents=True)
    sessions = [
        {
            "id": "s1",
            "cwd": "C:/proj",
            "title": "Fix the build",
            "created": 1793573869721,
            "updated": 1793573891820,
            "turns": 2,
        },
        {
            "id": "agent1",
            "cwd": "C:/proj",
            "title": "Subagent work",
            "created": 1793573869721,
            "updated": 1793573891820,
            "turns": 1,
            "parent": "s1",
        },
        {
            "id": "empty",
            "cwd": "C:/proj",
            "title": "",
            "created": 1793573869721,
            "updated": 1793573891820,
            "turns": 0,
        },
    ]
    (root / "sessions.json").write_text(json.dumps(sessions), encoding="utf-8")
    turns = [
        {
            "v": 1,
            "user": "Fix the build",
            "reply": "On it.",
            "todos": [{"content": "Fix", "status": "in_progress"}],
        },
        {
            "v": 1,
            "user": "Thanks",
            "reply": "Fixed.",
            "todos": [{"content": "Fix", "status": "completed"}],
        },
    ]
    (root / "transcripts" / "s1.jsonl").write_text(
        "\n".join(json.dumps(turn) for turn in turns), encoding="utf-8"
    )
    return root


def test_saved_muser_sessions_are_listed_and_read_back(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)

    entries = session_history.list_history("muser", None)
    assert [(entry.session_id, entry.title, entry.cwd) for entry in entries] == [
        ("s1", "Fix the build", "C:/proj")
    ]
    turns = session_history.load_turns(entries[0])
    assert [(turn.prompt, turn.response) for turn in turns] == [
        ("Fix the build", "On it."),
        ("Thanks", "Fixed."),
    ]


def test_a_reopened_muser_conversation_brings_its_task_list_back(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    entry = session_history.list_history("muser", None)[0]
    tasks = session_history.restore_tasks(entry)
    assert tasks.lines() == ["1. Done: Fix"]
