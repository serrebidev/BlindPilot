# SPDX-License-Identifier: MIT
"""Recent Conversations reads the stores the desktop apps and newer CLIs write.

Codex moved its conversations from ~/.codex/sessions/*.jsonl into SQLite (the
Codex desktop app writes the same databases), and FreeBuff Desktop keeps one
SQLite store per project. Each test builds a minimal copy of the real schema,
measured 2026-10-08.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

import session_history


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(session_history, "_home", lambda: tmp_path)
    return tmp_path


def _codex(home):
    codex = home / ".codex"
    codex.mkdir()
    state = sqlite3.connect(codex / "state_5.sqlite")
    state.execute(
        "CREATE TABLE threads (id TEXT, title TEXT, first_user_message TEXT, cwd TEXT, "
        "updated_at INTEGER, rollout_path TEXT, source TEXT, archived INTEGER)"
    )
    rows = [
        ("t-cli", "Fix the build", "", "\\\\?\\C:\\work", 200, "gone.jsonl", "cli", 0),
        ("t-app", "", "From the desktop app", "C:\\work", 300, "", "vscode", 0),
        ("t-sub", "helper", "", "C:\\work", 400, "", '{"subagent":"review"}', 0),
        ("t-old", "archived", "", "C:\\work", 100, "", "cli", 1),
    ]
    state.executemany("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)", rows)
    state.commit()
    state.close()
    history = sqlite3.connect(codex / "thread_history_1.sqlite")
    history.execute(
        "CREATE TABLE thread_items (thread_id TEXT, turn_id TEXT, item_id TEXT, "
        "rollout_ordinal INTEGER, item_json TEXT, item_type TEXT)"
    )
    items = [
        (
            "t-cli",
            "u1",
            "a",
            1,
            {"content": [{"type": "text", "text": "Fix the build"}]},
            "userMessage",
        ),
        ("t-cli", "u1", "b", 2, {"text": "Looking."}, "agentMessage"),
        ("t-cli", "u1", "c", 3, {"command": "pytest"}, "commandExecution"),
        ("t-cli", "u1", "d", 4, {"text": "Fixed."}, "agentMessage"),
    ]
    history.executemany(
        "INSERT INTO thread_items VALUES (?,?,?,?,?,?)",
        [(t, u, i, o, json.dumps(j), k) for t, u, i, o, j, k in items],
    )
    history.commit()
    history.close()


def test_codex_conversations_come_from_its_databases(home):
    _codex(home)

    entries = session_history.list_history("codex")

    # Newest first; subagent helpers and archived threads are left out, and the
    # desktop app's thread (source "vscode") is there beside the CLI's.
    assert [e.session_id for e in entries] == ["t-app", "t-cli"]
    assert entries[0].title == "From the desktop app"
    assert entries[1].cwd == "C:\\work"

    turns = session_history.load_turns(entries[1])
    assert [(t.prompt, t.response) for t in turns] == [("Fix the build", "Looking.\n\nFixed.")]


def test_freebuff_desktop_threads_are_listed_and_read(home):
    project = home / ".config" / "freebuff-desktop" / "projects" / "app-1234"
    project.mkdir(parents=True)
    db = sqlite3.connect(project / "desktop-v2.db")
    # An older store, before the archive columns existed.
    db.execute("CREATE TABLE threads (id TEXT, title TEXT, project_path TEXT, updated_at INTEGER)")
    db.execute("INSERT INTO threads VALUES ('th1', 'Tidy the README', 'C:\\app', 5000)")
    db.execute("CREATE TABLE messages (seq INTEGER, thread_id TEXT, role TEXT, parts_json TEXT)")
    db.executemany(
        "INSERT INTO messages VALUES (?,?,?,?)",
        [
            (1, "th1", "user", json.dumps([{"kind": "text", "text": "Tidy the README"}])),
            (
                2,
                "th1",
                "assistant",
                json.dumps(
                    [
                        {"kind": "reasoning", "text": "thinking"},
                        {"kind": "tool", "text": "read README"},
                        {"kind": "text", "text": "Done."},
                    ]
                ),
            ),
        ],
    )
    db.commit()
    db.close()

    entries = [e for e in session_history.list_history("freebuff") if e.session_id == "th1"]

    assert len(entries) == 1 and entries[0].title == "Tidy the README"
    turns = session_history.load_turns(entries[0])
    assert [(t.prompt, t.response) for t in turns] == [("Tidy the README", "Done.")]
