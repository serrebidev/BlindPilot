"""Reading past conversations back off disk, for every backend.

The transcripts here are trimmed copies of what Claude Code, Codex, FreeBuff,
and opencode actually write, including the context each of them injects into
the user side of the conversation — which is exactly what must never end up as
a title.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

import session_history
from session_history import (
    HistoryEntry,
    clean_user_text,
    describe_age,
    list_history,
    load_turns,
    make_title,
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every history store at a throwaway home directory.

    The Hermes reader also has a second route -- a store belonging to a Hermes
    in WSL -- which is switched off here. Without that a machine with a real
    Hermes in WSL answers these tests with its own hundreds of conversations.
    """
    monkeypatch.setattr(session_history, "_home", lambda: tmp_path)
    import hermes_backend

    monkeypatch.setattr(hermes_backend, "wsl_sqlite_query", lambda _sql, _params=(): [])
    # opencode honours these, and a developer machine may well set them.
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("OPENCODE_DATA", raising=False)
    return tmp_path


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


# ----- Claude Code -----


def _claude_user(text: str, **extra) -> dict:
    record = {"type": "user", "message": {"role": "user", "content": text}}
    record.update(extra)
    return record


def _claude_assistant(text: str, **extra) -> dict:
    record = {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }
    record.update(extra)
    return record


def _write_claude(home: Path, cwd: str, session_id: str, records: list[dict]) -> Path:
    slug = session_history.claude_project_slug(cwd)
    path = home / ".claude" / "projects" / slug / f"{session_id}.jsonl"
    _write_jsonl(path, records)
    return path


CLAUDE_CWD = str(Path("C:/work/demo")) if Path("C:/").drive else "/work/demo"


def test_claude_conversation_is_titled_by_its_first_message(home: Path) -> None:
    _write_claude(
        home,
        CLAUDE_CWD,
        "11111111-1111-1111-1111-111111111111",
        [
            {"type": "mode", "mode": "default"},
            _claude_user("Fix the login redirect", cwd=CLAUDE_CWD),
            _claude_assistant("Found it in auth.py."),
            _claude_user("Now add a test", cwd=CLAUDE_CWD),
            _claude_assistant("Added one."),
        ],
    )

    entries = list_history("claude", CLAUDE_CWD)

    assert len(entries) == 1
    assert entries[0].title == "Fix the login redirect"
    assert entries[0].session_id == "11111111-1111-1111-1111-111111111111"
    assert entries[0].cwd == CLAUDE_CWD


def test_claude_injected_records_never_become_the_title(home: Path) -> None:
    """The wrappers a CLI writes are not what the person asked for."""
    _write_claude(
        home,
        CLAUDE_CWD,
        "22222222-2222-2222-2222-222222222222",
        [
            _claude_user(
                "<local-command-caveat>Ignore this</local-command-caveat>", cwd=CLAUDE_CWD
            ),
            _claude_user(
                "<command-name>/clear</command-name>\n<command-args></command-args>",
                cwd=CLAUDE_CWD,
            ),
            _claude_user("Session start hook fired", cwd=CLAUDE_CWD, isMeta=True),
            _claude_user("A subagent's own prompt", cwd=CLAUDE_CWD, isSidechain=True),
            _claude_user("What does markdown_rows do?", cwd=CLAUDE_CWD),
            _claude_assistant("It segments a response into rows."),
        ],
    )

    entries = list_history("claude", CLAUDE_CWD)

    assert [entry.title for entry in entries] == ["What does markdown_rows do?"]
    assert [turn.prompt for turn in load_turns(entries[0])] == ["What does markdown_rows do?"]


def test_claude_turns_pair_prompts_with_answers(home: Path) -> None:
    _write_claude(
        home,
        CLAUDE_CWD,
        "33333333-3333-3333-3333-333333333333",
        [
            _claude_user("First question", cwd=CLAUDE_CWD),
            _claude_assistant("First half."),
            _claude_assistant("Second half."),
            _claude_user("Second question", cwd=CLAUDE_CWD),
            _claude_assistant("Second answer."),
        ],
    )

    turns = load_turns(list_history("claude", CLAUDE_CWD)[0])

    assert [turn.prompt for turn in turns] == ["First question", "Second question"]
    # Two assistant records in one turn are one answer, in order.
    assert turns[0].response == "First half.\n\nSecond half."
    assert turns[1].response == "Second answer."


def test_claude_reasoning_and_tool_blocks_stay_out_of_the_answer(home: Path) -> None:
    _write_claude(
        home,
        CLAUDE_CWD,
        "44444444-4444-4444-4444-444444444444",
        [
            _claude_user("Read the file", cwd=CLAUDE_CWD),
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "Let me look."},
                        {"type": "tool_use", "name": "Read", "input": {}},
                        {"type": "text", "text": "It defines two functions."},
                    ],
                },
            },
        ],
    )

    turns = load_turns(list_history("claude", CLAUDE_CWD)[0])

    assert turns[0].response == "It defines two functions."


def test_claude_sessions_in_other_folders_are_left_out(home: Path) -> None:
    other = str(Path(CLAUDE_CWD).parent / "elsewhere")
    _write_claude(home, CLAUDE_CWD, "aaaa", [_claude_user("Here", cwd=CLAUDE_CWD)])
    _write_claude(home, other, "bbbb", [_claude_user("There", cwd=other)])

    assert [entry.title for entry in list_history("claude", CLAUDE_CWD)] == ["Here"]
    assert sorted(entry.title for entry in list_history("claude")) == ["Here", "There"]


def test_claude_session_with_no_question_is_not_offered(home: Path) -> None:
    """Nothing was ever asked, so there is nothing to carry on with."""
    _write_claude(home, CLAUDE_CWD, "cccc", [{"type": "mode", "mode": "default"}])

    assert list_history("claude", CLAUDE_CWD) == []


# ----- Codex -----


def _codex_message(role: str, text: str) -> dict:
    kind = "input_text" if role in ("user", "developer") else "output_text"
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": role,
            "content": [{"type": kind, "text": text}],
        },
    }


def _write_codex(home: Path, cwd: str, session_id: str, records: list[dict]) -> Path:
    path = home / ".codex" / "sessions" / "2026" / "08" / "10" / f"rollout-{session_id}.jsonl"
    meta = {
        "type": "session_meta",
        "payload": {"session_id": session_id, "cwd": cwd},
    }
    _write_jsonl(path, [meta, *records])
    return path


CODEX_CWD = CLAUDE_CWD


def test_codex_skips_its_own_context_block(home: Path) -> None:
    """Codex's first "user" message is a wall of injected context."""
    preamble = (
        "<recommended_plugins>\nAirtable\n</recommended_plugins>\n"
        "# AGENTS.md instructions for C:\\work\\demo\n"
        "<INSTRUCTIONS>\nBe brief.\n</INSTRUCTIONS>\n"
        "<environment_context>\n<cwd>C:\\work\\demo</cwd>\n</environment_context>"
    )
    _write_codex(
        home,
        CODEX_CWD,
        "019fe814-a5ee-7f63-a706-53d5f1a2518b",
        [
            _codex_message("developer", "You are Codex."),
            _codex_message("user", preamble),
            _codex_message("user", "what is the weather in seattle"),
            _codex_message("assistant", "Rain, as usual."),
        ],
    )

    entries = list_history("codex", CODEX_CWD)

    assert [entry.title for entry in entries] == ["what is the weather in seattle"]
    assert entries[0].session_id == "019fe814-a5ee-7f63-a706-53d5f1a2518b"
    turns = load_turns(entries[0])
    assert [(turn.prompt, turn.response) for turn in turns] == [
        ("what is the weather in seattle", "Rain, as usual.")
    ]


def test_codex_developer_messages_are_not_the_person(home: Path) -> None:
    _write_codex(
        home,
        CODEX_CWD,
        "019fe814-0000-0000-0000-000000000000",
        [
            _codex_message("developer", "Follow the house style."),
            _codex_message("user", "Rename the module"),
            _codex_message("assistant", "Renamed."),
        ],
    )

    turns = load_turns(list_history("codex", CODEX_CWD)[0])

    assert [turn.prompt for turn in turns] == ["Rename the module"]


def test_codex_sessions_are_scoped_by_working_directory(home: Path) -> None:
    other = str(Path(CODEX_CWD).parent / "elsewhere")
    _write_codex(home, CODEX_CWD, "aaaa-1", [_codex_message("user", "Here")])
    _write_codex(home, other, "bbbb-1", [_codex_message("user", "There")])

    assert [entry.title for entry in list_history("codex", CODEX_CWD)] == ["Here"]
    assert sorted(entry.title for entry in list_history("codex")) == ["Here", "There"]


# ----- FreeBuff -----


def _write_freebuff(
    home: Path,
    project: str,
    chat_id: str,
    messages: list[dict],
    first_prompt: str | None = None,
) -> Path:
    chat = home / ".config" / "manicode" / "projects" / project / "chats" / chat_id
    chat.mkdir(parents=True, exist_ok=True)
    (chat / "chat-messages.json").write_text(json.dumps(messages), encoding="utf-8")
    if first_prompt is not None:
        (chat / "chat-meta.json").write_text(
            json.dumps({"messageCount": len(messages), "firstPrompt": first_prompt}),
            encoding="utf-8",
        )
    return chat


FREEBUFF_CHAT = [
    {"variant": "ai", "blocks": [{"type": "mode-divider"}]},
    {"variant": "user", "content": "testing with a recipe for honey garlic chicken"},
    {
        "variant": "ai",
        "blocks": [
            {"type": "text", "textType": "reasoning", "content": "They want a recipe."},
            {"type": "tool", "toolName": "read_file"},
            {"type": "text", "content": "Here is the recipe."},
        ],
    },
]


def test_freebuff_title_comes_from_its_own_metadata(home: Path) -> None:
    """FreeBuff records the first prompt itself, so listing costs one small read."""
    _write_freebuff(
        home,
        "demo",
        "2026-08-10T02-00-06.557Z",
        FREEBUFF_CHAT,
        first_prompt="testing with a recipe for honey garlic chicken...",
    )

    entries = list_history(
        "freebuff", str(Path("C:/work/demo") if Path("C:/").drive else "/work/demo")
    )

    assert len(entries) == 1
    # FreeBuff's stored preview is elided; the title must not be cut twice.
    assert entries[0].title == "testing with a recipe for honey garlic chicken"
    assert entries[0].session_id == "2026-08-10T02-00-06.557Z"


def test_freebuff_falls_back_to_the_messages_themselves(home: Path) -> None:
    _write_freebuff(home, "demo", "chat-without-meta", FREEBUFF_CHAT)

    entries = list_history("freebuff")

    assert [entry.title for entry in entries] == ["testing with a recipe for honey garlic chicken"]


def test_freebuff_reasoning_is_not_part_of_the_answer(home: Path) -> None:
    _write_freebuff(home, "demo", "chat-1", FREEBUFF_CHAT)

    turns = load_turns(list_history("freebuff")[0])

    assert [(turn.prompt, turn.response) for turn in turns] == [
        ("testing with a recipe for honey garlic chicken", "Here is the recipe.")
    ]


# ----- Across backends -----


def test_every_backend_appears_newest_first(home: Path) -> None:
    claude = _write_claude(home, CLAUDE_CWD, "dddd", [_claude_user("Claude one", cwd=CLAUDE_CWD)])
    codex = _write_codex(home, CODEX_CWD, "cccc-1", [_codex_message("user", "Codex one")])
    freebuff = _write_freebuff(home, "demo", "chat-2", FREEBUFF_CHAT, first_prompt="FreeBuff one")

    now = time.time()
    os.utime(claude, (now - 300, now - 300))
    os.utime(codex, (now - 60, now - 60))
    os.utime(freebuff / "chat-messages.json", (now - 900, now - 900))

    entries = list_history()

    assert [entry.title for entry in entries] == ["Codex one", "Claude one", "FreeBuff one"]
    assert [entry.backend for entry in entries] == ["codex", "claude", "freebuff"]


def test_the_list_is_capped(home: Path) -> None:
    for index in range(5):
        _write_claude(
            home, CLAUDE_CWD, f"session-{index}", [_claude_user(f"Ask {index}", cwd=CLAUDE_CWD)]
        )

    assert len(list_history("claude", CLAUDE_CWD, limit=3)) == 3


def test_a_missing_history_store_is_not_an_error(home: Path) -> None:
    assert list_history() == []
    assert load_turns(HistoryEntry("claude", "nope", "Gone", str(home / "gone.jsonl"), 0.0)) == []


# ----- Hermes -----
#
# Hermes is the odd one out: one SQLite store for every conversation it has
# ever run, rather than a file per conversation. These build a miniature of
# that store rather than a transcript file.

HERMES_CWD = CLAUDE_CWD


def _write_hermes(
    home: Path,
    sessions: list[dict],
    messages: list[dict],
) -> Path:
    """Build a miniature of Hermes' session store."""
    import sqlite3

    path = home / ".hermes" / "state.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, title TEXT, cwd TEXT, source TEXT,
            started_at REAL, last_activity_at REAL, message_count INTEGER,
            archived INTEGER
        )
        """
    )
    db.execute(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
            content TEXT, display_kind TEXT
        )
        """
    )
    for session in sessions:
        db.execute(
            "INSERT INTO sessions (id, title, cwd, source, started_at, "
            "last_activity_at, message_count, archived) VALUES (?,?,?,?,?,?,?,?)",
            (
                session["id"],
                session.get("title", ""),
                session.get("cwd", HERMES_CWD),
                session.get("source", "tui"),
                session.get("started_at", 1_000.0),
                session.get("last_activity_at", session.get("started_at", 1_000.0)),
                session.get("message_count", 2),
                session.get("archived", 0),
            ),
        )
    for message in messages:
        db.execute(
            "INSERT INTO messages (session_id, role, content, display_kind) VALUES (?,?,?,?)",
            (
                message["session_id"],
                message["role"],
                message.get("content", ""),
                message.get("display_kind"),
            ),
        )
    db.commit()
    db.close()
    return path


def test_hermes_conversations_are_listed_with_the_titles_hermes_gave_them(home: Path) -> None:
    """Hermes titles its own conversations, so nothing has to be scanned."""
    _write_hermes(
        home,
        [{"id": "20260816_120000_aaaa", "title": "Fix the deploy script"}],
        [
            {"session_id": "20260816_120000_aaaa", "role": "user", "content": "why does it fail?"},
            {"session_id": "20260816_120000_aaaa", "role": "assistant", "content": "Bad path."},
        ],
    )

    entries = list_history("hermes")

    assert len(entries) == 1
    assert entries[0].title == "Fix the deploy script"
    assert entries[0].session_id == "20260816_120000_aaaa"
    assert entries[0].backend == "hermes"


def test_a_hermes_conversation_is_read_back_as_turns(home: Path) -> None:
    _write_hermes(
        home,
        [{"id": "s1", "title": "Two questions", "message_count": 4}],
        [
            {"session_id": "s1", "role": "user", "content": "first question"},
            {"session_id": "s1", "role": "assistant", "content": "first answer"},
            {"session_id": "s1", "role": "user", "content": "second question"},
            {"session_id": "s1", "role": "assistant", "content": "second answer"},
        ],
    )

    turns = load_turns(list_history("hermes")[0])

    assert [(turn.prompt, turn.response) for turn in turns] == [
        ("first question", "first answer"),
        ("second question", "second answer"),
    ]


def test_hermes_bookkeeping_never_becomes_a_turn(home: Path) -> None:
    """Tool traffic and hidden rows are Hermes talking to itself."""
    _write_hermes(
        home,
        [{"id": "s2", "title": "With tools", "message_count": 6}],
        [
            {"session_id": "s2", "role": "session_meta", "content": "meta"},
            {"session_id": "s2", "role": "user", "content": "run it"},
            {"session_id": "s2", "role": "assistant", "content": ""},
            {"session_id": "s2", "role": "tool", "content": "tool output nobody asked to hear"},
            {"session_id": "s2", "role": "system", "content": "internal"},
            {
                "session_id": "s2",
                "role": "assistant",
                "content": "hidden",
                "display_kind": "hidden",
            },
            {"session_id": "s2", "role": "assistant", "content": "Done."},
        ],
    )

    turns = load_turns(list_history("hermes")[0])

    assert [(turn.prompt, turn.response) for turn in turns] == [("run it", "Done.")]


def test_several_hermes_answers_join_into_one_response(home: Path) -> None:
    """One question can produce many answers around its tool calls."""
    _write_hermes(
        home,
        [{"id": "s3", "title": "Multi", "message_count": 4}],
        [
            {"session_id": "s3", "role": "user", "content": "do the thing"},
            {"session_id": "s3", "role": "assistant", "content": "Looking."},
            {"session_id": "s3", "role": "tool", "content": "{}"},
            {"session_id": "s3", "role": "assistant", "content": "Found it."},
        ],
    )

    turns = load_turns(list_history("hermes")[0])

    assert len(turns) == 1
    assert "Looking." in turns[0].response and "Found it." in turns[0].response


def test_each_hermes_conversation_carries_its_own_activity_time(home: Path) -> None:
    """One shared store means the file's mtime cannot date a conversation.

    Every conversation would report the moment the store was last written, so
    a chat from last week would be announced as "just now" and would sort
    among today's -- including against the other backends, which date theirs
    from their own files.
    """
    three_days_ago = time.time() - 3 * 86400
    _write_hermes(
        home,
        [
            {"id": "old", "title": "Older", "last_activity_at": three_days_ago},
            {"id": "new", "title": "Newer", "last_activity_at": time.time() - 60},
        ],
        [
            {"session_id": "old", "role": "user", "content": "a"},
            {"session_id": "old", "role": "assistant", "content": "b"},
            {"session_id": "new", "role": "user", "content": "c"},
            {"session_id": "new", "role": "assistant", "content": "d"},
        ],
    )

    entries = {entry.title: entry for entry in list_history("hermes")}

    assert [entry.title for entry in list_history("hermes")] == ["Newer", "Older"]
    # The dates are the sessions' own, not the store's single mtime.
    assert entries["Older"].modified == pytest.approx(three_days_ago, abs=2)
    assert entries["Older"].modified != entries["Newer"].modified
    assert describe_age(entries["Older"].modified) == "3 days ago"
    assert describe_age(entries["Newer"].modified) == "1 minute ago"


def test_hermes_conversations_can_be_narrowed_to_one_folder(home: Path) -> None:
    _write_hermes(
        home,
        [
            {"id": "here", "title": "Here", "cwd": HERMES_CWD},
            {"id": "there", "title": "There", "cwd": os.path.join("C:\\", "work", "other")},
        ],
        [
            {"session_id": "here", "role": "user", "content": "a"},
            {"session_id": "here", "role": "assistant", "content": "b"},
            {"session_id": "there", "role": "user", "content": "c"},
            {"session_id": "there", "role": "assistant", "content": "d"},
        ],
    )

    assert [entry.title for entry in list_history("hermes", HERMES_CWD)] == ["Here"]
    assert sorted(entry.title for entry in list_history("hermes")) == ["Here", "There"]


def test_empty_and_archived_hermes_conversations_are_left_out(home: Path) -> None:
    """A session that never went anywhere is noise in a spoken list."""
    _write_hermes(
        home,
        [
            {"id": "empty", "title": "Never asked", "message_count": 0},
            {"id": "gone", "title": "Archived", "message_count": 2, "archived": 1},
            {"id": "real", "title": "Real one", "message_count": 2},
        ],
        [
            {"session_id": "real", "role": "user", "content": "a"},
            {"session_id": "real", "role": "assistant", "content": "b"},
        ],
    )

    assert [entry.title for entry in list_history("hermes")] == ["Real one"]


def test_the_shared_store_is_not_rejected_for_being_large(home: Path, monkeypatch) -> None:
    """The per-transcript size guard must not apply to Hermes.

    Every Hermes conversation lives in one file, which passes any per-file
    limit on a machine that has used Hermes for a while -- 2 GB is ordinary.
    Applying the guard hid all of them, silently.
    """
    _write_hermes(
        home,
        [{"id": "s4", "title": "Big store"}],
        [
            {"session_id": "s4", "role": "user", "content": "still here?"},
            {"session_id": "s4", "role": "assistant", "content": "Yes."},
        ],
    )
    # Any real store is larger than this.
    monkeypatch.setattr(session_history, "_MAX_TRANSCRIPT_BYTES", 10)

    turns = load_turns(list_history("hermes")[0])

    assert [(turn.prompt, turn.response) for turn in turns] == [("still here?", "Yes.")]


def test_a_missing_hermes_store_is_not_an_error(home: Path) -> None:
    assert list_history("hermes") == []
    assert load_turns(HistoryEntry("hermes", "s1", "Gone", str(home / "nope.db"), 0.0)) == []


def test_a_corrupt_hermes_store_is_treated_as_absent(home: Path) -> None:
    """A truncated or foreign database must not take the dialog down."""
    path = home / ".hermes" / "state.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"this is not a database")

    assert list_history("hermes") == []


def test_hermes_opens_its_store_read_only(home: Path) -> None:
    """The store belongs to a running Hermes: this must never be able to write.

    Asserted on the connection itself rather than on a symptom, because a
    read-write handle to someone else's live database is a bug that only shows
    up as corruption much later.
    """
    import sqlite3

    _write_hermes(
        home,
        [{"id": "s5", "title": "Live"}],
        [
            {"session_id": "s5", "role": "user", "content": "hello"},
            {"session_id": "s5", "role": "assistant", "content": "hi"},
        ],
    )
    path = home / ".hermes" / "state.db"
    connection = session_history._hermes_connect(path)
    assert connection is not None
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("UPDATE sessions SET title = 'tampered'")
    finally:
        connection.close()

    # And a writer holding the database must not stop the dialog from reading.
    writer = sqlite3.connect(path)
    writer.execute("BEGIN IMMEDIATE")
    try:
        entries = list_history("hermes")
        assert [entry.title for entry in entries] == ["Live"]
        assert load_turns(entries[0])[0].prompt == "hello"
    finally:
        writer.rollback()
        writer.close()


def test_hermes_honours_its_own_home_override(home: Path, monkeypatch) -> None:
    """Hermes obeys HERMES_HOME, so the history reader has to as well."""
    elsewhere = home / "custom-hermes"
    _write_hermes(
        elsewhere.parent,
        [{"id": "s6", "title": "Default home"}],
        [
            {"session_id": "s6", "role": "user", "content": "a"},
            {"session_id": "s6", "role": "assistant", "content": "b"},
        ],
    )
    (elsewhere / ".hermes").mkdir(parents=True, exist_ok=True)
    moved = elsewhere / ".hermes" / "state.db"
    (home / ".hermes" / "state.db").rename(moved)
    monkeypatch.setenv("HERMES_HOME", str(elsewhere / ".hermes"))

    assert [entry.title for entry in list_history("hermes")] == ["Default home"]


def test_a_conversation_run_through_wsl_is_found_by_the_folder_filter(home: Path) -> None:
    """Hermes in WSL records /mnt/d/work; the picker asks about D:\\work.

    They are one directory, so comparing the strings hid every conversation
    behind "No past conversations found here" -- measured on Windows against a
    store holding three hundred of them.
    """
    _write_hermes(
        home,
        [{"id": "wsl", "title": "Through WSL", "cwd": "/mnt/d/projekty/blindpilot"}],
        [
            {"session_id": "wsl", "role": "user", "content": "a"},
            {"session_id": "wsl", "role": "assistant", "content": "b"},
        ],
    )

    # Asked with the Windows form the picker produces.
    assert [e.title for e in list_history("hermes", r"D:\projekty\blindpilot")] == ["Through WSL"]
    # And the WSL form still works, for a desktop running on Linux.
    assert [e.title for e in list_history("hermes", "/mnt/d/projekty/blindpilot")] == [
        "Through WSL"
    ]
    # A genuinely different folder must still be excluded.
    assert list_history("hermes", r"D:\somewhere\else") == []


def test_a_store_belonging_to_wsl_is_read_through_wsl(home: Path, monkeypatch) -> None:
    """WAL over a network share answers "database is locked" - measured.

    The store is visible to Windows under \\\\wsl.localhost, but Hermes keeps it
    in WAL mode and WAL needs shared memory a share cannot provide, so opening
    it that way lists nothing at all. The query is run inside WSL instead.
    """
    import hermes_backend

    # No store on this side of the machine.
    monkeypatch.setattr(session_history, "_hermes_db_path", lambda: home / "absent.db")
    asked = []

    def _fake_query(sql, params=()):
        asked.append((sql, tuple(params)))
        if "FROM sessions" in sql:
            return [
                {
                    "id": "s1",
                    "title": "From WSL",
                    "cwd": "/mnt/d/work",
                    "started_at": 1000.0,
                    # JSON brings numbers back as text; that must not break it.
                    "last_activity_at": "2000.0",
                    "message_count": 2,
                }
            ]
        return [
            {"role": "user", "content": "hello", "display_kind": None},
            {"role": "assistant", "content": "hi", "display_kind": None},
        ]

    monkeypatch.setattr(hermes_backend, "wsl_sqlite_query", _fake_query)

    entries = list_history("hermes")

    assert [e.title for e in entries] == ["From WSL"]
    assert entries[0].modified == 2000.0
    assert [(t.prompt, t.response) for t in load_turns(entries[0])] == [("hello", "hi")]
    assert asked, "the WSL route has to be used when there is no local store"


# ----- Titles and ages -----


def test_titles_are_one_clean_line() -> None:
    assert make_title("  Fix\nthe   bug  ") == "Fix the bug"
    # Emoji are stripped: the title is read aloud.
    assert make_title("Ship it \U0001f680 now") == "Ship it now"
    long_title = make_title("word " * 40)
    assert len(long_title) <= session_history.TITLE_LIMIT
    assert long_title.endswith("…")


def test_injected_blocks_are_removed_but_real_words_survive() -> None:
    assert clean_user_text("<system-reminder>hush</system-reminder>") == ""
    assert clean_user_text("Real question\n<system-reminder>hush</system-reminder>") == (
        "Real question"
    )
    # A message that merely mentions a tag is still a real message.
    assert clean_user_text("what does <div> mean") == "what does <div> mean"


def test_ages_are_said_the_way_a_person_would() -> None:
    now = 1_000_000.0
    assert describe_age(now - 5, now) == "just now"
    assert describe_age(now - 60, now) == "1 minute ago"
    assert describe_age(now - 1800, now) == "30 minutes ago"
    assert describe_age(now - 3600, now) == "1 hour ago"
    assert describe_age(now - 7200, now) == "2 hours ago"
    assert describe_age(now - 86400, now) == "yesterday"
    assert describe_age(now - 3 * 86400, now) == "3 days ago"
    assert describe_age(0, now) == "unknown"


# ----- opencode -----

_OPENCODE_SCHEMA = (
    "CREATE TABLE session (id TEXT PRIMARY KEY, project_id TEXT, parent_id TEXT, "
    "directory TEXT, title TEXT, time_updated INTEGER, time_archived INTEGER)",
    "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT)",
    "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, "
    "time_created INTEGER, data TEXT)",
)


def _write_opencode(
    home: Path,
    session_id: str,
    directory: str,
    title: str,
    exchanges: list[tuple[str, str]],
    parent_id: str | None = None,
) -> None:
    """Write one conversation into a throwaway copy of opencode's database."""
    database = home / ".local" / "share" / "opencode" / "opencode.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    with connection:
        for statement in _OPENCODE_SCHEMA:
            table = statement.split()[2]
            if not connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone():
                connection.execute(statement)
        connection.execute(
            "INSERT INTO session VALUES (?,?,?,?,?,?,?)",
            (session_id, "global", parent_id, directory, title, 1_787_000_000_000, None),
        )
        clock = 0
        for index, (role, text) in enumerate(exchanges):
            message_id = f"msg_{session_id}_{index}"
            clock += 1
            connection.execute(
                "INSERT INTO message VALUES (?,?,?,?)",
                (message_id, session_id, clock, json.dumps({"role": role})),
            )
            clock += 1
            connection.execute(
                "INSERT INTO part VALUES (?,?,?,?,?)",
                (
                    f"prt_{session_id}_{index}",
                    message_id,
                    session_id,
                    clock,
                    json.dumps({"type": "text", "text": text}),
                ),
            )
            if role == "assistant":
                clock += 1
                connection.execute(
                    "INSERT INTO part VALUES (?,?,?,?,?)",
                    (
                        f"prt_{session_id}_{index}_think",
                        message_id,
                        session_id,
                        clock,
                        json.dumps({"type": "reasoning", "text": "thinking out loud"}),
                    ),
                )
    connection.close()


OPENCODE_CWD = str(Path("C:/work/demo") if Path("C:/").drive else "/work/demo")


def test_opencode_conversations_are_read_out_of_its_database(home: Path) -> None:
    _write_opencode(
        home,
        "ses_1",
        OPENCODE_CWD,
        "Honey garlic chicken",
        [("user", "testing with a recipe"), ("assistant", "Here is the recipe.")],
    )

    entries = list_history("opencode", OPENCODE_CWD)

    assert [(entry.session_id, entry.title) for entry in entries] == [
        ("ses_1", "Honey garlic chicken")
    ]
    assert entries[0].cwd == OPENCODE_CWD
    assert entries[0].folder == "demo"
    # opencode records the time in milliseconds; the picker sorts in seconds.
    assert entries[0].modified == 1_787_000_000.0


def test_opencode_untitled_conversations_are_titled_by_their_first_message(home: Path) -> None:
    """Its own placeholder is a timestamp, which the age column already says."""
    _write_opencode(
        home,
        "ses_1",
        OPENCODE_CWD,
        "New session - 2026-05-24T22:55:37.511Z",
        [("user", "what does markdown_rows do?"), ("assistant", "It parses.")],
    )

    assert [entry.title for entry in list_history("opencode")] == ["what does markdown_rows do?"]


def test_opencode_subagent_conversations_are_not_offered_to_resume(home: Path) -> None:
    """A subagent gets a session of its own, which nobody had with opencode."""
    _write_opencode(home, "ses_1", OPENCODE_CWD, "Real conversation", [("user", "hello")])
    _write_opencode(
        home,
        "ses_2",
        OPENCODE_CWD,
        "Audit the player modules (@general subagent)",
        [("user", "audit")],
        parent_id="ses_1",
    )

    assert [entry.title for entry in list_history("opencode")] == ["Real conversation"]


def test_opencode_reasoning_is_not_part_of_the_answer(home: Path) -> None:
    _write_opencode(
        home,
        "ses_1",
        OPENCODE_CWD,
        "Recipe",
        [("user", "testing with a recipe"), ("assistant", "Here is the recipe.")],
    )

    turns = load_turns(list_history("opencode")[0])

    assert [(turn.prompt, turn.response) for turn in turns] == [
        ("testing with a recipe", "Here is the recipe.")
    ]


def test_opencode_history_is_empty_without_a_database(home: Path) -> None:
    """Listing must never be what creates opencode's database."""
    assert list_history("opencode") == []
    assert not (home / ".local" / "share" / "opencode" / "opencode.db").exists()


def test_opencode_conversations_are_limited_to_one_directory(home: Path) -> None:
    other = str(Path("C:/work/other") if Path("C:/").drive else "/work/other")
    _write_opencode(home, "ses_1", OPENCODE_CWD, "Here", [("user", "here")])
    _write_opencode(home, "ses_2", other, "There", [("user", "there")])

    assert [entry.title for entry in list_history("opencode", OPENCODE_CWD)] == ["Here"]
    assert sorted(entry.title for entry in list_history("opencode")) == ["Here", "There"]
