from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from accessible_ai.models import Account, Conversation, Message, MessageAttachment, Profile


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL,
    base_url TEXT NOT NULL,
    models_endpoint TEXT NOT NULL,
    chat_endpoint TEXT NOT NULL,
    responses_endpoint TEXT NOT NULL,
    messages_endpoint TEXT NOT NULL,
    api_mode TEXT NOT NULL,
    default_model TEXT NOT NULL DEFAULT '',
    timeout_seconds REAL NOT NULL DEFAULT 120,
    streaming INTEGER NOT NULL DEFAULT 1,
    custom_headers_json TEXT NOT NULL DEFAULT '{}',
    custom_body_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    system_prompt TEXT NOT NULL DEFAULT '',
    default_account_id INTEGER NULL REFERENCES accounts(id) ON DELETE SET NULL,
    default_model TEXT NOT NULL DEFAULT '',
    temperature REAL NULL,
    max_output_tokens INTEGER NULL,
    streaming INTEGER NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    profile_id INTEGER NULL REFERENCES profiles(id) ON DELETE SET NULL,
    account_id INTEGER NULL REFERENCES accounts(id) ON DELETE SET NULL,
    model TEXT NOT NULL DEFAULT '',
    system_prompt_snapshot TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS message_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    data BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS model_cache (
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    model_id TEXT NOT NULL,
    PRIMARY KEY (account_id, model_id)
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def list_accounts(self) -> list[Account]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM accounts ORDER BY name COLLATE NOCASE").fetchall()
        return [self._account_from_row(row) for row in rows]

    def get_account(self, account_id: int) -> Account | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return self._account_from_row(row) if row else None

    def save_account(self, account: Account) -> int:
        values = (
            account.name.strip(),
            account.provider,
            account.base_url.strip().rstrip("/"),
            account.models_endpoint.strip(),
            account.chat_endpoint.strip(),
            account.responses_endpoint.strip(),
            account.messages_endpoint.strip(),
            account.api_mode,
            account.default_model.strip(),
            float(account.timeout_seconds),
            1 if account.streaming else 0,
            json.dumps(account.custom_headers, ensure_ascii=False),
            json.dumps(account.custom_body, ensure_ascii=False),
        )
        with self.connect() as conn:
            if account.id is None:
                cur = conn.execute(
                    """
                    INSERT INTO accounts (
                        name, provider, base_url, models_endpoint, chat_endpoint,
                        responses_endpoint, messages_endpoint, api_mode,
                        default_model, timeout_seconds, streaming,
                        custom_headers_json, custom_body_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                account.id = int(cur.lastrowid)
            else:
                conn.execute(
                    """
                    UPDATE accounts SET
                        name = ?, provider = ?, base_url = ?, models_endpoint = ?,
                        chat_endpoint = ?, responses_endpoint = ?, messages_endpoint = ?,
                        api_mode = ?, default_model = ?, timeout_seconds = ?, streaming = ?,
                        custom_headers_json = ?, custom_body_json = ?
                    WHERE id = ?
                    """,
                    values + (account.id,),
                )
        return int(account.id)

    def delete_account(self, account_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

    def _account_from_row(self, row: sqlite3.Row) -> Account:
        return Account(
            id=row["id"],
            name=row["name"],
            provider=row["provider"],
            base_url=row["base_url"],
            models_endpoint=row["models_endpoint"],
            chat_endpoint=row["chat_endpoint"],
            responses_endpoint=row["responses_endpoint"],
            messages_endpoint=row["messages_endpoint"],
            api_mode=row["api_mode"],
            default_model=row["default_model"],
            timeout_seconds=float(row["timeout_seconds"]),
            streaming=bool(row["streaming"]),
            custom_headers=json.loads(row["custom_headers_json"] or "{}"),
            custom_body=json.loads(row["custom_body_json"] or "{}"),
        )

    def list_profiles(self) -> list[Profile]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM profiles ORDER BY name COLLATE NOCASE").fetchall()
        return [self._profile_from_row(row) for row in rows]

    def get_profile(self, profile_id: int) -> Profile | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        return self._profile_from_row(row) if row else None

    def save_profile(self, profile: Profile) -> int:
        streaming_value = None if profile.streaming is None else (1 if profile.streaming else 0)
        values = (
            profile.name.strip(),
            profile.system_prompt,
            profile.default_account_id,
            profile.default_model.strip(),
            profile.temperature,
            profile.max_output_tokens,
            streaming_value,
        )
        with self.connect() as conn:
            if profile.id is None:
                cur = conn.execute(
                    """
                    INSERT INTO profiles (
                        name, system_prompt, default_account_id, default_model,
                        temperature, max_output_tokens, streaming
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                profile.id = int(cur.lastrowid)
            else:
                conn.execute(
                    """
                    UPDATE profiles SET
                        name = ?, system_prompt = ?, default_account_id = ?,
                        default_model = ?, temperature = ?, max_output_tokens = ?, streaming = ?
                    WHERE id = ?
                    """,
                    values + (profile.id,),
                )
        return int(profile.id)

    def delete_profile(self, profile_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))

    def _profile_from_row(self, row: sqlite3.Row) -> Profile:
        streaming_raw = row["streaming"]
        streaming = None if streaming_raw is None else bool(streaming_raw)
        return Profile(
            id=row["id"],
            name=row["name"],
            system_prompt=row["system_prompt"],
            default_account_id=row["default_account_id"],
            default_model=row["default_model"],
            temperature=row["temperature"],
            max_output_tokens=row["max_output_tokens"],
            streaming=streaming,
        )

    def create_conversation(self, conversation: Conversation) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO conversations (
                    title, profile_id, account_id, model, system_prompt_snapshot
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    conversation.title,
                    conversation.profile_id,
                    conversation.account_id,
                    conversation.model,
                    conversation.system_prompt_snapshot,
                ),
            )
            conversation.id = int(cur.lastrowid)
        return int(conversation.id)

    def touch_conversation(self, conversation_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (conversation_id,),
            )

    def add_message(self, message: Message) -> int:
        if message.conversation_id is None:
            raise ValueError("conversation_id is required")
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
                (message.conversation_id, message.role, message.content),
            )
            message.id = int(cur.lastrowid)
            conn.executemany(
                "INSERT INTO message_attachments (message_id, filename, mime_type, data) VALUES (?, ?, ?, ?)",
                [
                    (message.id, attachment.filename, attachment.mime_type, attachment.data)
                    for attachment in message.attachments
                ],
            )
            conn.execute(
                "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (message.conversation_id,),
            )
        return int(message.id)

    def update_message_content(self, message_id: int, content: str) -> None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT conversation_id FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Message {message_id} does not exist")
            conn.execute("UPDATE messages SET content = ? WHERE id = ?", (content, message_id))
            conn.execute(
                "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (row["conversation_id"],),
            )

    def list_messages(self, conversation_id: int) -> list[Message]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        messages = [
            Message(
                id=row["id"],
                conversation_id=row["conversation_id"],
                role=row["role"],
                content=row["content"],
            )
            for row in rows
        ]
        if not messages:
            return messages

        message_ids = [int(message.id) for message in messages if message.id is not None]
        placeholders = ",".join("?" for _ in message_ids)
        with self.connect() as conn:
            attachment_rows = conn.execute(
                f"SELECT * FROM message_attachments WHERE message_id IN ({placeholders}) ORDER BY id",
                message_ids,
            ).fetchall()
        by_message: dict[int, list[MessageAttachment]] = {
            message_id: [] for message_id in message_ids
        }
        for row in attachment_rows:
            by_message[int(row["message_id"])].append(
                MessageAttachment(
                    id=row["id"],
                    message_id=row["message_id"],
                    filename=row["filename"],
                    mime_type=row["mime_type"],
                    data=bytes(row["data"]),
                )
            )
        for message in messages:
            if message.id is not None:
                message.attachments = by_message[int(message.id)]
        return messages

    def replace_model_cache(self, account_id: int, model_ids: list[str]) -> None:
        unique_models = sorted({m.strip() for m in model_ids if m.strip()}, key=str.casefold)
        with self.connect() as conn:
            conn.execute("DELETE FROM model_cache WHERE account_id = ?", (account_id,))
            conn.executemany(
                "INSERT INTO model_cache (account_id, model_id) VALUES (?, ?)",
                [(account_id, model_id) for model_id in unique_models],
            )

    def get_cached_models(self, account_id: int) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT model_id FROM model_cache WHERE account_id = ? ORDER BY model_id COLLATE NOCASE",
                (account_id,),
            ).fetchall()
        return [row["model_id"] for row in rows]

    def get_setting(self, key: str, default: str = "") -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
