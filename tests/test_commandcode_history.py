"""Command Code's on-disk transcripts, read back without a Command Code install.

The layout is the one measured at 1.53.1: a per-project folder holding
``<session-id>.jsonl`` with a session header on the first line and message
records after it, beside sidecar files that are not conversations.
"""

from __future__ import annotations

import json

import session_history
from agent_backends import BACKEND_COMMANDCODE


def _write_session(root, slug, session_id, cwd, turns):
    folder = root / ".commandcode" / "projects" / slug
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {
                "type": "session",
                "version": 3,
                "id": session_id,
                "timestamp": "2026-09-13T16:47:18.714Z",
                "cwd": cwd,
            }
        )
    ]
    for prompt, response in turns:
        lines.append(
            json.dumps(
                {
                    "type": "message",
                    "id": "u",
                    "parentId": None,
                    "timestamp": "2026-09-13T16:47:24.755Z",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": prompt}],
                        "meta": {"source": "user"},
                    },
                }
            )
        )
        lines.append(
            json.dumps(
                {
                    "type": "message",
                    "id": "a",
                    "parentId": "u",
                    "timestamp": "2026-09-13T16:47:30.096Z",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "hidden"},
                            {"type": "text", "text": response},
                        ],
                        "meta": {"source": "model"},
                    },
                }
            )
        )
        # A tool result is a user-role record too, and must not read as a prompt.
        lines.append(
            json.dumps(
                {
                    "type": "message",
                    "id": "t",
                    "parentId": "a",
                    "timestamp": "2026-09-13T16:47:31.096Z",
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "content": [{"type": "text", "text": "x"}]}
                        ],
                        "meta": {"source": "tool"},
                    },
                }
            )
        )
    (folder / f"{session_id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (folder / f"{session_id}.meta.json").write_text(
        json.dumps({"entrypoint": "print"}), encoding="utf-8"
    )
    # Sidecars: present beside the transcript, and not conversations.
    (folder / f"{session_id}.checkpoints.jsonl").write_text("{}\n", encoding="utf-8")
    (folder / f"{session_id}.prompts.jsonl").write_text("{}\n", encoding="utf-8")
    return folder / f"{session_id}.jsonl"


def test_a_transcript_is_listed_with_its_own_title(tmp_path, monkeypatch):
    monkeypatch.setattr(session_history, "_home", lambda: tmp_path)
    _write_session(
        tmp_path,
        "c-users-me-proj",
        "abc-123",
        "C:\\Users\\me\\proj",
        [("Fix the login bug", "Done.")],
    )

    entries = session_history.list_history(backend=BACKEND_COMMANDCODE)

    assert len(entries) == 1
    entry = entries[0]
    assert entry.session_id == "abc-123"
    assert entry.title == "Fix the login bug"
    assert entry.cwd == "C:\\Users\\me\\proj"
    assert entry.folder == "proj"


def test_the_sidecar_files_are_not_conversations(tmp_path, monkeypatch):
    monkeypatch.setattr(session_history, "_home", lambda: tmp_path)
    _write_session(tmp_path, "slug", "abc-123", "C:\\work", [("Hello", "Hi")])

    entries = session_history.list_history(backend=BACKEND_COMMANDCODE)

    assert [entry.session_id for entry in entries] == ["abc-123"]


def test_turns_are_read_back_without_tool_traffic(tmp_path, monkeypatch):
    monkeypatch.setattr(session_history, "_home", lambda: tmp_path)
    _write_session(
        tmp_path,
        "slug",
        "abc-123",
        "C:\\work",
        [("First question", "First answer"), ("Second question", "Second answer")],
    )
    entry = session_history.list_history(backend=BACKEND_COMMANDCODE)[0]

    turns = session_history.load_turns(entry)

    assert [(t.prompt, t.response) for t in turns] == [
        ("First question", "First answer"),
        ("Second question", "Second answer"),
    ]


def test_a_directory_filter_excludes_other_projects(tmp_path, monkeypatch):
    monkeypatch.setattr(session_history, "_home", lambda: tmp_path)
    _write_session(tmp_path, "one", "id-one", "C:\\one", [("In one", "yes")])
    _write_session(tmp_path, "two", "id-two", "C:\\two", [("In two", "yes")])

    here = session_history.list_history(backend=BACKEND_COMMANDCODE, cwd="C:\\one")

    assert [entry.session_id for entry in here] == ["id-one"]


def test_a_transcript_with_no_message_is_not_offered(tmp_path, monkeypatch):
    monkeypatch.setattr(session_history, "_home", lambda: tmp_path)
    folder = tmp_path / ".commandcode" / "projects" / "slug"
    folder.mkdir(parents=True)
    (folder / "empty.jsonl").write_text(
        json.dumps({"type": "session", "id": "empty", "cwd": "C:\\work"}) + "\n",
        encoding="utf-8",
    )

    assert session_history.list_history(backend=BACKEND_COMMANDCODE) == []
