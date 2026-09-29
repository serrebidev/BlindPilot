"""Muse's past conversations: the session index for the list, the logs for turns.

Muse keeps both shapes at once: ``session-index.db`` carries one row per
conversation (title, workspace, recency), and each session's ``session.jsonl``
carries the run events the replay is folded from. On Windows both live inside
WSL, so every read has a WSL route that is exercised here with fakes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import muse_backend
import session_history
from session_history import HistoryEntry, list_history, load_turns


@pytest.fixture
def muse_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway home holding a Muse store, and nothing of the real one."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    # The WSL route is switched off here: on a machine with a real Muse in
    # WSL it would answer these tests with its own conversations.
    monkeypatch.setattr(muse_backend, "wsl_muse_sqlite_query", lambda _sql: [])
    data = tmp_path / ".local" / "share" / "muse"
    data.mkdir(parents=True)
    return tmp_path


def _write_index(path: Path, rows: list[dict]) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE sessions (session_id TEXT PRIMARY KEY,"
            " title TEXT, first_user_prompt TEXT, workspace_root TEXT,"
            " updated_at_us INTEGER, session_log_path TEXT, prompt_count INTEGER)"
        )
        connection.executemany(
            "INSERT INTO sessions (session_id, title, first_user_prompt,"
            " workspace_root, updated_at_us, session_log_path, prompt_count)"
            " VALUES (:session_id, :title, :first_user_prompt, :workspace_root,"
            " :updated_at_us, :session_log_path, :prompt_count)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()


def _started(prompt: str) -> dict:
    return {
        "payload_type": "runtime.session",
        "payload": {"kind": "run", "event": {"kind": "started", "prompt": prompt}},
    }


def _said(text: str) -> dict:
    return {
        "payload_type": "runtime.session",
        "payload": {
            "kind": "run",
            "event": {"kind": "assistant_message_committed", "text": text},
        },
    }


def _write_log(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


# ----- the listing -----


def test_the_index_lists_conversations_newest_first(muse_home, monkeypatch):
    data = muse_home / ".local" / "share" / "muse"
    _write_index(
        data / "session-index.db",
        [
            {
                "session_id": "old",
                "title": "older work",
                "first_user_prompt": "older work",
                "workspace_root": "/work",
                "updated_at_us": 1_700_000_000_000_000,
                "session_log_path": str(data / "sessions" / "old" / "session.jsonl"),
                "prompt_count": 2,
            },
            {
                "session_id": "new",
                "title": "newer work",
                "first_user_prompt": "newer work",
                "workspace_root": "/work",
                "updated_at_us": 1_800_000_000_000_000,
                "session_log_path": str(data / "sessions" / "new" / "session.jsonl"),
                "prompt_count": 1,
            },
        ],
    )

    entries = list_history("muse")

    assert [entry.session_id for entry in entries] == ["new", "old"]
    assert entries[0].title == "newer work"
    assert entries[0].backend == "muse"
    assert entries[0].folder == "work"


def test_a_conversation_that_never_started_is_not_listed(muse_home):
    data = muse_home / ".local" / "share" / "muse"
    _write_index(
        data / "session-index.db",
        [
            {
                "session_id": "empty",
                "title": "",
                "first_user_prompt": "",
                "workspace_root": "/work",
                "updated_at_us": 1_800_000_000_000_000,
                "session_log_path": str(data / "sessions" / "empty" / "session.jsonl"),
                "prompt_count": 0,
            },
        ],
    )

    assert list_history("muse") == []


def test_the_first_prompt_titles_a_conversation_muse_never_named(muse_home):
    data = muse_home / ".local" / "share" / "muse"
    _write_index(
        data / "session-index.db",
        [
            {
                "session_id": "abc",
                "title": "",
                "first_user_prompt": "fix the crash on startup",
                "workspace_root": "/work",
                "updated_at_us": 1_800_000_000_000_000,
                "session_log_path": str(data / "sessions" / "abc" / "session.jsonl"),
                "prompt_count": 1,
            },
        ],
    )

    (entry,) = list_history("muse")

    assert entry.title == "fix the crash on startup"


def test_listing_filters_by_folder_across_the_wsl_boundary(muse_home):
    # A Muse reached through WSL records /mnt/c/work; the folder picker on
    # the Windows side says C:\work. Both name the same place.
    data = muse_home / ".local" / "share" / "muse"
    log = str(data / "sessions" / "abc" / "session.jsonl")
    _write_index(
        data / "session-index.db",
        [
            {
                "session_id": "abc",
                "title": "here",
                "first_user_prompt": "here",
                "workspace_root": "/mnt/c/work",
                "updated_at_us": 1_800_000_000_000_000,
                "session_log_path": log,
                "prompt_count": 1,
            },
            {
                "session_id": "elsewhere",
                "title": "there",
                "first_user_prompt": "there",
                "workspace_root": "/mnt/c/other",
                "updated_at_us": 1_800_000_000_000_001,
                "session_log_path": log,
                "prompt_count": 1,
            },
        ],
    )

    entries = list_history("muse", r"C:\work")

    assert [entry.session_id for entry in entries] == ["abc"]


def test_without_a_store_there_is_no_history(muse_home):
    assert list_history("muse") == []


def test_muse_is_wired_into_the_shared_listing():
    assert "muse" in session_history._LISTERS
    assert "muse" in session_history._READERS


# ----- the replay -----


def test_turns_come_from_the_run_events(muse_home):
    data = muse_home / ".local" / "share" / "muse"
    log = data / "sessions" / "abc" / "session.jsonl"
    _write_log(
        log,
        [
            _started("fix the bug"),
            _said("On it."),
            _said("Fixed."),
            {"payload_type": "runtime.session.task", "payload": {"kind": "task"}},
            _started("now the tests"),
            _said("Green."),
        ],
    )
    entry = HistoryEntry(backend="muse", session_id="abc", title="t", path=str(log), modified=0.0)

    turns = load_turns(entry)

    assert [(turn.prompt, turn.response) for turn in turns] == [
        ("fix the bug", "On it.\n\nFixed."),
        ("now the tests", "Green."),
    ]


def test_a_steered_prompt_opens_its_own_turn(muse_home):
    data = muse_home / ".local" / "share" / "muse"
    log = data / "sessions" / "abc" / "session.jsonl"
    _write_log(
        log,
        [
            _started("first"),
            {
                "payload_type": "runtime.session",
                "payload": {
                    "kind": "inbox",
                    "event": {
                        "kind": "inbox_item_queued",
                        "source": {"source": "user_steer"},
                        "payload": {"prompt": "actually do it this way"},
                    },
                },
            },
            _said("Steered."),
        ],
    )
    entry = HistoryEntry(backend="muse", session_id="abc", title="t", path=str(log), modified=0.0)

    turns = load_turns(entry)

    assert [turn.prompt for turn in turns] == ["first", "actually do it this way"]


def test_background_deliveries_are_not_quoted_as_the_user(muse_home):
    data = muse_home / ".local" / "share" / "muse"
    log = data / "sessions" / "abc" / "session.jsonl"
    _write_log(
        log,
        [
            _started("first"),
            {
                "payload_type": "runtime.session",
                "payload": {
                    "kind": "inbox",
                    "event": {
                        "kind": "inbox_item_queued",
                        "source": {"source": "background_delivery"},
                        "payload": {"prompt": "a cron result"},
                    },
                },
            },
            _said("Done."),
        ],
    )
    entry = HistoryEntry(backend="muse", session_id="abc", title="t", path=str(log), modified=0.0)

    turns = load_turns(entry)

    assert [(turn.prompt, turn.response) for turn in turns] == [("first", "Done.")]


def test_a_missing_or_empty_log_reads_back_nothing(muse_home):
    entry = HistoryEntry(backend="muse", session_id="abc", title="t", path="", modified=0.0)
    assert load_turns(entry) == []

    missing = muse_home / "no-such-log.jsonl"
    entry = HistoryEntry(
        backend="muse", session_id="abc", title="t", path=str(missing), modified=0.0
    )
    assert load_turns(entry) == []


# ----- the WSL route -----


def test_on_windows_the_index_is_queried_through_wsl(monkeypatch):
    monkeypatch.setattr(muse_backend, "muse_index_db", lambda: None)
    rows = [
        {
            "session_id": "wsl-1",
            "title": "wsl work",
            "first_user_prompt": "wsl work",
            "workspace_root": "/mnt/c/work",
            "updated_at_us": "1800000000000000",
            "session_log_path": "/root/.local/share/muse/sessions/wsl-1/session.jsonl",
            "prompt_count": 3,
        }
    ]
    monkeypatch.setattr(muse_backend, "wsl_muse_sqlite_query", lambda _sql: rows)

    entries = list_history("muse")

    assert [entry.session_id for entry in entries] == ["wsl-1"]
    assert entries[0].title == "wsl work"
    assert entries[0].modified == 1_800_000_000.0


def test_on_windows_the_log_is_fetched_through_wsl(monkeypatch):
    monkeypatch.setattr(
        muse_backend,
        "muse_session_log_text",
        lambda _path: "\n".join(
            [
                json.dumps(_started("from wsl")),
                json.dumps(_said("answered")),
            ]
        ),
    )
    entry = HistoryEntry(
        backend="muse",
        session_id="wsl-1",
        title="t",
        path="/root/.local/share/muse/sessions/wsl-1/session.jsonl",
        modified=0.0,
    )

    turns = load_turns(entry)

    assert [(turn.prompt, turn.response) for turn in turns] == [("from wsl", "answered")]


def test_a_log_this_user_cannot_stat_is_still_read_by_its_own_reader(monkeypatch):
    # The Linux runner is not root, so looking at /root/... raises
    # PermissionError where Windows just says "not a file". The size guard
    # took that for a reason to drop the conversation; the log is fetched
    # through its own reader, which caps itself.
    import pathlib

    real_is_file = pathlib.Path.is_file

    def denied(self, *args, **kwargs):
        if "root" in self.parts[:2]:
            raise PermissionError(13, "Permission denied")
        return real_is_file(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "is_file", denied)
    monkeypatch.setattr(
        muse_backend,
        "muse_session_log_text",
        lambda _path: "\n".join([json.dumps(_started("asked")), json.dumps(_said("told"))]),
    )
    entry = HistoryEntry(
        backend="muse",
        session_id="wsl-2",
        title="t",
        path="/root/.local/share/muse/sessions/wsl-2/session.jsonl",
        modified=0.0,
    )

    assert [(turn.prompt, turn.response) for turn in load_turns(entry)] == [("asked", "told")]
