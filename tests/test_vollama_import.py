"""VoLlama JSON import: another app's saves, made into Chat conversations.

VoLlama writes either a bare list of messages or an object holding that
list under ``messages``. BlindPilot's own interchange exports also end in
``.json``, so the two must never be confused: an interchange file carries
its ``format`` key, and everything else that claims to be JSON is tried
as VoLlama -- and refused, creating nothing, when it is not.
"""

from __future__ import annotations

import json

import pytest

import history_backup as backup
from accessible_ai.storage.database import Database


def _write(tmp_path, name, payload):
    path = tmp_path / name
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _conversation_count(database):
    return len(database.list_conversations())


def test_vollama_array_form_imports_in_order(tmp_path):
    """A bare message list: the leading system message becomes the prompt,
    the rest keep their order, and the title is the file stem."""
    path = _write(
        tmp_path,
        "My chat.json",
        [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": None, "extra": {"reasoning": "Thinking"}},
            {"role": "assistant", "content": "Done"},
        ],
    )
    database = Database(tmp_path / "chat.sqlite3")
    report = backup.import_vollama(path, database)
    assert report.title == "My chat"
    assert report.messages == 3
    conversation = database.get_conversation(report.conversation_id)
    assert conversation is not None and conversation.system_prompt_snapshot == "Be helpful."
    messages = database.list_messages(report.conversation_id)
    assert [(m.role, m.content) for m in messages] == [
        ("user", "hello"),
        ("assistant", ""),
        ("assistant", "Done"),
    ]


def test_vollama_object_form_imports_messages_and_tool_results(tmp_path):
    """An object with a ``messages`` array, tool calls in ``extra`` and a
    tool result naming its call: the messages travel, the tool metadata is
    reported as not carried over rather than silently dropped."""
    path = _write(
        tmp_path,
        "Saved.json",
        {
            "messages": [
                {"role": "user", "content": "read x"},
                {
                    "role": "assistant",
                    "content": None,
                    "additional_kwargs": {
                        "tool_calls": [
                            {
                                "id": "call1",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": "{}"},
                            }
                        ]
                    },
                },
                {
                    "role": "tool",
                    "content": "file contents",
                    "extra": {"tool_call_id": "call1", "name": "read_file"},
                },
                {"role": "assistant", "content": "Done"},
            ],
            "summary": "Earlier context",
        },
    )
    database = Database(tmp_path / "chat.sqlite3")
    report = backup.import_vollama(path, database)
    assert report.title == "Saved"
    assert report.messages == 4
    messages = database.list_messages(report.conversation_id)
    assert [m.role for m in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[2].content == "file contents"
    assert report.unsupported


def test_vollama_without_a_system_message_keeps_the_default_prompt(tmp_path):
    """No system message: no prompt of its own is set, so the conversation
    keeps the default system prompt instead of an empty one."""
    path = _write(tmp_path, "Plain.json", [{"role": "user", "content": "hi"}])
    database = Database(tmp_path / "chat.sqlite3")
    report = backup.import_vollama(path, database)
    conversation = database.get_conversation(report.conversation_id)
    assert conversation is not None and conversation.system_prompt_snapshot == ""


def test_vollama_invalid_json_is_refused_and_creates_nothing(tmp_path):
    """Broken JSON is not a conversation: it is refused, and the database
    gains no conversation from the attempt."""
    path = _write(tmp_path, "Broken.json", "{not json")
    database = Database(tmp_path / "chat.sqlite3")
    with pytest.raises(backup.HistoryBackupError):
        backup.import_vollama(path, database)
    assert _conversation_count(database) == 0


def test_vollama_unrelated_json_is_refused_and_creates_nothing(tmp_path):
    """Valid JSON that is not a VoLlama save -- a bad role, non-string
    content, or no message list at all -- is refused before anything is
    created, however far into the list the fault sits."""
    database = Database(tmp_path / "chat.sqlite3")
    for payload in (
        {},
        {"messages": 42},
        [42],
        [{"role": "unknown", "content": "x"}],
        [{"role": "user", "content": []}],
        [
            {"role": "user", "content": "fine"},
            {"role": "assistant", "extra": {"tool_calls": [{}]}},
        ],
    ):
        with pytest.raises(backup.HistoryBackupError):
            backup.import_vollama(payload, database)
    assert _conversation_count(database) == 0
