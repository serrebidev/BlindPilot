"""Past conversations: find them on disk, title them, and read them back.

Every backend BlindPilot drives already keeps its own transcript of each
conversation, and every one of them can be resumed by id — Claude Code with
``--resume``, Codex with the app-server's ``thread/resume``, FreeBuff with
``--continue``, opencode by asking its server for the session again, Hermes with
its gateway's ``session.resume``. What was missing was a way to *find* one
again, which is what this module provides: one list of past conversations across
every backend, each titled by its first message, plus a reader that turns any of
them back into prompt-and-response turns the GUI can put in its rows.

Where each backend keeps its history:

* Claude Code — ``~/.claude/projects/<slug>/<session-id>.jsonl``, one JSON
  record per line, where ``<slug>`` is the working directory with every
  non-alphanumeric character replaced by a dash.
* Codex — ``~/.codex/sessions/<year>/<month>/<day>/rollout-<stamp>-<id>.jsonl``,
  one JSON record per line, the first of which carries the session id and the
  working directory.
* FreeBuff — ``~/.config/manicode/projects/<project>/chats/<chat-id>/``, a
  directory per conversation holding ``chat-messages.json`` and a
  ``chat-meta.json`` that already stores the first prompt.
* opencode — ``~/.local/share/opencode/opencode.db``, one SQLite database for
  every conversation, with a row per session, message, and message part.
* Hermes — ``~/.hermes/state.db``, one SQLite database holding every session it
  has ever run, opened read-only. It is the odd one out even among these: there
  is no file per conversation, and Hermes titles its own, so nothing has to be
  scanned to build the list.

Listing is deliberately cheap: it reads only as far into a transcript as it
takes to find the first real user message, because the newest conversations
have to appear the moment the dialog opens. Reading a conversation back
(:func:`load_turns`) parses the whole file, and only happens for the one the
user actually chose.

Deliberately GUI-agnostic and free of any wx dependency, like
``markdown_rows``, so it can be unit-tested on its own (see
``tests/test_session_history.py``).

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Sequence

from agent_backends import (
    BACKEND_CLAUDE,
    BACKEND_CODEX,
    BACKEND_FREEBUFF,
    BACKEND_HERMES,
    BACKEND_IDS,
    BACKEND_OPENCODE,
    normalize_backend,
)
from markdown_rows import strip_noise

# How long a title may be before it is cut. Long enough to tell two similar
# conversations apart when they are read out, short enough that arrowing
# through the list is not a chore.
TITLE_LIMIT = 90

# A conversation's first message is normally in the first few records. This
# caps how far the listing scan reads before giving up on a file, so one
# enormous transcript cannot stall the dialog.
_MAX_HEAD_LINES = 400

# Transcripts this large are not read back into rows. Nothing legitimate comes
# close; the guard is against a corrupt or runaway file freezing the GUI.
_MAX_TRANSCRIPT_BYTES = 64 * 1024 * 1024

# The same guard for a backend that keeps every conversation in one store: how
# many message parts of one conversation are read back before stopping. A
# shared database's size says nothing about the size of any one conversation
# in it, so it is counted in rows rather than in bytes.
_MAX_TRANSCRIPT_PARTS = 20_000

# How many conversations one backend contributes to the picker. Comfortably
# more than :func:`list_history` returns, so the cap that decides what is shown
# stays that one rather than this.
_MAX_HISTORY_ENTRIES = 1_000


@dataclass(frozen=True)
class HistoryEntry:
    """One past conversation, as far as the picker needs to know about it.

    ``session_id`` is what the backend is asked to resume. ``path`` is the
    transcript :func:`load_turns` reads back. ``cwd`` is the directory the
    conversation ran in where the backend records it — FreeBuff does not, so
    ``folder`` (which is always populated) is what the picker displays.
    """

    backend: str
    session_id: str
    title: str
    path: str
    modified: float
    cwd: str = ""
    folder: str = ""


@dataclass
class HistoryTurn:
    """One exchange: what was asked, and the answer text it produced."""

    prompt: str = ""
    response: str = ""


# Every CLI stuffs context of its own into the user side of the transcript —
# environment blocks, skills manifests, system reminders, plugin listings,
# slash-command wrappers — all of it wrapped in an XML-ish element. Removing
# whole elements leaves exactly what the person typed, which is what a title
# has to be and what a replayed conversation should show.
# One opening or closing tag. Elements are matched with a stack in a single
# pass over these (see `_without_injected_blocks`); a backreference pattern
# rescanned to the end of the text for every tag that was never closed.
_TAG = re.compile(r"<(/?)([A-Za-z][\w.:-]*)>")

# Injected preamble that is not wrapped in an element of its own. Codex writes
# this heading above the instructions it loaded from AGENTS.md.
_INJECTED_MARKERS = (re.compile(r"^#\s*AGENTS\.md instructions.*$", re.IGNORECASE | re.MULTILINE),)


def _home() -> Path:
    """The home directory the history stores hang off.

    A function rather than a constant so tests can point the whole module at a
    temporary directory.
    """
    return Path.home()


def _flat(text: str) -> str:
    return " ".join((text or "").split())


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def make_title(text: str, limit: int = TITLE_LIMIT) -> str:
    """Turn a first message into the one line that names the conversation.

    Flattened to a single line and stripped of emoji, because this is read
    aloud: a title is only useful if it is the words the person typed.
    """
    flat = _flat(strip_noise(text or ""))
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"


def clean_user_text(text: str) -> str:
    """What the person actually typed, with the CLI's own additions removed.

    Returns "" for a message that was entirely injected — a plugin listing, an
    environment block, the wrapper around a slash command — which is how such
    records are recognised and skipped.
    """
    remainder = (text or "").strip()
    if not remainder:
        return ""
    remainder = _without_injected_blocks(remainder)
    for marker in _INJECTED_MARKERS:
        remainder = marker.sub(" ", remainder)
    return remainder.strip()


def _without_injected_blocks(text: str) -> str:
    """``text`` with every ``<name>...</name>`` element replaced by a space.

    One pass with a stack of open tags: a closing tag removes everything back
    to its nearest matching opener, nested elements included, and an opener
    that is never closed is left as the words it is ("what does <div> mean").
    Linear in the number of tags, so a pasted file full of generics or an
    HTML fragment costs milliseconds rather than seconds.
    """
    open_tags: List[tuple] = []  # (name, start offset), innermost last
    open_counts: dict = {}
    spans: List[tuple] = []  # (start, end) of elements to remove, in order
    for match in _TAG.finditer(text):
        closing, name = match.group(1), match.group(2)
        if not closing:
            open_tags.append((name, match.start()))
            open_counts[name] = open_counts.get(name, 0) + 1
            continue
        if not open_counts.get(name):
            continue
        depth = len(open_tags) - 1
        while open_tags[depth][0] != name:
            depth -= 1
        start = open_tags[depth][1]
        for popped, _offset in open_tags[depth:]:
            open_counts[popped] -= 1
        del open_tags[depth:]
        # An element that started inside this one is covered by it.
        while spans and spans[-1][0] >= start:
            spans.pop()
        spans.append((start, match.end()))
    if not spans:
        return text
    pieces: List[str] = []
    last = 0
    for start, end in spans:
        pieces.append(text[last:start])
        pieces.append(" ")
        last = end
    pieces.append(text[last:])
    return "".join(pieces)


def _content_text(content: object, kinds: Sequence[str]) -> str:
    """Pull the plain text out of one message's content.

    Content is either a bare string or a list of typed blocks; only the block
    types named in ``kinds`` contribute, so reasoning, tool calls and tool
    results stay out of the transcript the user reads back.
    """
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: List[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in kinds:
            continue
        piece = str(block.get("text") or "").strip()
        if piece:
            parts.append(piece)
    return "\n\n".join(parts)


def _iter_jsonl(path: Path, limit: Optional[int] = None) -> Iterator[dict]:
    """Yield the JSON objects in a JSONL transcript, skipping unreadable lines.

    A transcript being written to right now can end in a half-flushed line, and
    a crashed run can leave one in the middle, so a bad line is stepped over
    rather than allowed to lose the whole conversation.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for count, line in enumerate(handle):
                if limit is not None and count >= limit:
                    return
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def _append(existing: str, addition: str) -> str:
    if not existing:
        return addition
    return f"{existing}\n\n{addition}"


def _folder_name(cwd: str) -> str:
    name = os.path.basename(os.path.normpath(cwd or ""))
    return name or cwd


def _same_dir(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _same_dir_across_wsl(recorded: str, wanted: str) -> bool:
    """Whether two working directories are the same place, across WSL.

    A Hermes running in WSL records ``/mnt/d/work``, while the folder picker in
    a Windows desktop produces ``D:\\work``. They are one directory, so
    comparing the strings hid every conversation from the folder filter -- the
    dialog said "No past conversations found here" beside three hundred of
    them. Both forms are normalised to the WSL one before comparing.
    """
    if _same_dir(recorded, wanted):
        return True
    from hermes_backend import windows_path_to_wsl

    left = windows_path_to_wsl(recorded)
    right = windows_path_to_wsl(wanted)
    if not left or not right:
        return False
    return left.rstrip("/").lower() == right.rstrip("/").lower()


# ----- Claude Code -----


def claude_project_slug(cwd: str) -> str:
    """Claude Code's directory name for a working directory."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd or "")


def _claude_project_dirs(cwd: Optional[str]) -> List[Path]:
    root = _home() / ".claude" / "projects"
    if cwd:
        exact = root / claude_project_slug(cwd)
        return [exact] if exact.is_dir() else []
    try:
        return sorted(path for path in root.iterdir() if path.is_dir())
    except OSError:
        return []


def _claude_user_text(record: dict) -> str:
    """The typed text of a user record, or "" if it is not one."""
    if record.get("type") != "user" or record.get("isMeta") or record.get("isSidechain"):
        return ""
    if record.get("isCompactSummary"):
        # The "this session is being continued" summary Claude Code writes
        # after compaction. It is a user record, but nobody typed it.
        return ""
    message = record.get("message")
    if not isinstance(message, dict):
        return ""
    return clean_user_text(_content_text(message.get("content"), ("text",)))


def _claude_assistant_text(record: dict) -> str:
    if record.get("type") != "assistant" or record.get("isSidechain"):
        return ""
    message = record.get("message")
    if not isinstance(message, dict):
        return ""
    return _content_text(message.get("content"), ("text",))


def _claude_entry(path: Path, fallback_cwd: Optional[str]) -> Optional[HistoryEntry]:
    title = ""
    cwd = ""
    for record in _iter_jsonl(path, _MAX_HEAD_LINES):
        if not cwd and record.get("cwd"):
            cwd = str(record.get("cwd") or "")
        text = _claude_user_text(record)
        if text:
            title = make_title(text)
            break
    if not title:
        # Nothing was ever asked in this session, so there is nothing to resume.
        return None
    cwd = cwd or (fallback_cwd or "")
    return HistoryEntry(
        backend=BACKEND_CLAUDE,
        session_id=path.stem,
        title=title,
        path=str(path),
        modified=_mtime(path),
        cwd=cwd,
        folder=_folder_name(cwd),
    )


def _claude_entries(cwd: Optional[str]) -> List[HistoryEntry]:
    entries: List[HistoryEntry] = []
    for folder in _claude_project_dirs(cwd):
        try:
            paths = list(folder.glob("*.jsonl"))
        except OSError:
            continue
        for path in paths:
            entry = _claude_entry(path, cwd)
            if entry is not None:
                entries.append(entry)
    return entries


def _claude_turns(entry: HistoryEntry) -> List[HistoryTurn]:
    path = Path(entry.path)
    turns: List[HistoryTurn] = []
    for record in _iter_jsonl(path):
        prompt = _claude_user_text(record)
        if prompt:
            turns.append(HistoryTurn(prompt=prompt))
            continue
        answer = _claude_assistant_text(record)
        if not answer:
            continue
        if not turns:
            turns.append(HistoryTurn())
        turns[-1].response = _append(turns[-1].response, answer)
    return turns


# ----- Codex -----


def _codex_message(record: dict) -> tuple[str, str]:
    """(role, text) for a Codex transcript record, or ("", "")."""
    if record.get("type") != "response_item":
        return "", ""
    payload = record.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "message":
        return "", ""
    role = str(payload.get("role") or "")
    if role not in ("user", "assistant"):
        # "developer" is Codex's own instruction channel, never the person.
        return "", ""
    text = _content_text(payload.get("content"), ("input_text", "output_text", "text"))
    if role == "user":
        text = clean_user_text(text)
    if not text:
        return "", ""
    return role, text


def _codex_entry(path: Path, cwd: Optional[str]) -> Optional[HistoryEntry]:
    session_id = ""
    session_cwd = ""
    title = ""
    for record in _iter_jsonl(path, _MAX_HEAD_LINES):
        if record.get("type") == "session_meta":
            payload = record.get("payload")
            if isinstance(payload, dict):
                session_id = str(payload.get("session_id") or payload.get("id") or "")
                session_cwd = str(payload.get("cwd") or "")
            continue
        role, text = _codex_message(record)
        if role == "user":
            title = make_title(text)
            break
    if not session_id or not title:
        return None
    if cwd and not _same_dir(session_cwd, cwd):
        return None
    return HistoryEntry(
        backend=BACKEND_CODEX,
        session_id=session_id,
        title=title,
        path=str(path),
        modified=_mtime(path),
        cwd=session_cwd,
        folder=_folder_name(session_cwd),
    )


def _codex_entries(cwd: Optional[str]) -> List[HistoryEntry]:
    root = _home() / ".codex" / "sessions"
    try:
        paths = list(root.glob("**/*.jsonl"))
    except OSError:
        return []
    entries: List[HistoryEntry] = []
    for path in paths:
        entry = _codex_entry(path, cwd)
        if entry is not None:
            entries.append(entry)
    return entries


def _codex_turns(entry: HistoryEntry) -> List[HistoryTurn]:
    path = Path(entry.path)
    turns: List[HistoryTurn] = []
    for record in _iter_jsonl(path):
        role, text = _codex_message(record)
        if role == "user":
            turns.append(HistoryTurn(prompt=text))
        elif role == "assistant":
            if not turns:
                turns.append(HistoryTurn())
            turns[-1].response = _append(turns[-1].response, text)
    return turns


# ----- Hermes -----
#
# Hermes is the one backend that does not keep a file per conversation: every
# session it has ever run lives in one SQLite database. Two consequences shape
# the code below.
#
# First, the database is opened read-only and queried, rather than parsed. It
# is Hermes' own live store — the file a running Hermes is writing to — so this
# never opens it for writing and never runs anything that could block a writer.
#
# Second, ``path`` on a Hermes entry is that shared database, so the size guard
# in ``load_turns`` cannot apply to it: a busy Hermes' store passes tens of
# megabytes quickly (2 GB is ordinary on a machine that has run it for months),
# and applying a per-transcript limit to a shared store would silently hide
# every Hermes conversation. The equivalent protection here is a row limit on
# the query, which bounds the work regardless of how large the store has grown.

# Rows read back for one conversation. Comfortably more than any conversation a
# person scrolls through, and it keeps a runaway session from freezing the GUI.
_HERMES_MAX_ROWS = 4000

# Hermes writes its own bookkeeping into the transcript alongside the
# conversation. Only "user" and "assistant" rows are ever turned into turns
# below, so tool traffic is already excluded by that; these are the roles worth
# skipping before the work of decoding a row happens at all.
_HERMES_SKIP_ROLES = ("system", "session_meta", "tool")
# Rows Hermes marks as not for replay.
_HERMES_HIDDEN_KIND = "hidden"


def _hermes_db_path() -> Path:
    """Hermes' session store on this side of the machine, if there is one."""
    override = os.environ.get("HERMES_HOME", "").strip()
    root = Path(override).expanduser() if override else _home() / ".hermes"
    return root / "state.db"


def _hermes_query(sql: str, params: Sequence = ()) -> List[dict]:
    """Run one read-only query against Hermes' store, wherever it lives.

    Two routes, because Hermes keeps its store in WAL mode and that decides
    which one works:

    * a store on this machine is opened directly, read-only;
    * a store belonging to a Hermes in WSL is read *through* WSL. It is also
      visible to Windows under \\\\wsl.localhost, but WAL needs shared memory
      that a network share cannot provide, so SQLite refuses with "database is
      locked" -- measured, and the reason the dialog reported no conversations
      at all. Asking WSL to run the query sidesteps that entirely.

    Returns a list of plain dicts so the caller never holds a connection.
    """
    path = _hermes_db_path()
    if path.is_file():
        connection = _hermes_connect(path)
        if connection is None:
            return []
        try:
            return [dict(row) for row in connection.execute(sql, tuple(params)).fetchall()]
        except Exception:  # noqa: BLE001 - an older schema is not a crash
            return []
        finally:
            connection.close()

    if os.environ.get("HERMES_HOME", "").strip():
        # An explicit home names one store. If it is not there, that is the
        # answer -- reaching into WSL would return a different Hermes' history
        # than the one the user pointed at.
        return []

    from hermes_backend import wsl_sqlite_query

    return wsl_sqlite_query(sql, params)


def _hermes_connect(path: Path):
    """Open Hermes' store read-only, or return None if it cannot be read.

    Read-only matters: this is the database a running Hermes owns. The URI form
    fails outright rather than creating an empty file when the path is wrong,
    which is what a plain connect() would do. `as_uri` escapes the path: a
    `#`, `?` or `%` in it would otherwise be read as URI syntax, dropping
    both the rest of the path and the read-only flag.
    """
    try:
        uri = f"{path.absolute().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection
    except Exception:  # noqa: BLE001 - a locked or corrupt store is just absent
        return None


def _hermes_entries(cwd: Optional[str]) -> List[HistoryEntry]:
    path = _hermes_db_path()
    modified = _mtime(path) if path.is_file() else 0.0
    # Hermes titles its own conversations, so unlike the other backends there is
    # no transcript to scan for a first message. Sessions with no messages are
    # skipped: they are starts that never went anywhere.
    rows = _hermes_query(
        """
        SELECT id, title, cwd, started_at, last_activity_at, message_count
        FROM sessions
        WHERE COALESCE(archived, 0) = 0 AND COALESCE(message_count, 0) > 0
        ORDER BY COALESCE(last_activity_at, started_at) DESC
        LIMIT 500
        """
    )
    entries: List[HistoryEntry] = []

    for row in rows:
        session_id = str(row.get("id") or "")
        if not session_id:
            continue
        session_cwd = str(row.get("cwd") or "")
        if cwd and not _same_dir_across_wsl(session_cwd, cwd):
            continue
        title = make_title(clean_user_text(str(row.get("title") or "")))
        if not title:
            title = session_id
        # Prefer the session's own last-activity time over the file's mtime:
        # one shared store means every conversation would otherwise appear to
        # have been touched at the same moment, and the list is sorted by this.
        stamp = row.get("last_activity_at") or row.get("started_at") or modified
        try:
            # A query answered through WSL arrives as JSON, where a timestamp
            # can come back as text.
            stamp = float(stamp)
        except (TypeError, ValueError):
            stamp = modified
        entries.append(
            HistoryEntry(
                backend=BACKEND_HERMES,
                session_id=session_id,
                title=title,
                path=str(path),
                modified=stamp,
                cwd=session_cwd,
                folder=_folder_name(session_cwd),
            )
        )
    return entries


def _hermes_turns_for(session_id: str) -> List[HistoryTurn]:
    """Read one Hermes conversation back as prompt-and-response turns.

    Which store is consulted is decided by :func:`_hermes_query`, so this does
    not need to know whether Hermes runs here or in WSL.
    """
    rows = _hermes_query(
        """
        SELECT role, content, display_kind
        FROM messages
        WHERE session_id = ?
        ORDER BY id
        LIMIT ?
        """,
        (session_id, _HERMES_MAX_ROWS),
    )

    turns: List[HistoryTurn] = []
    for row in rows:
        role = str(row.get("role") or "")
        if role in _HERMES_SKIP_ROLES:
            continue
        if str(row.get("display_kind") or "") == _HERMES_HIDDEN_KIND:
            continue
        text = str(row.get("content") or "").strip()
        if not text:
            # A turn that only called tools has no text of its own; the tool
            # rows themselves are Hermes' bookkeeping, not the conversation.
            continue
        if role == "user":
            turns.append(HistoryTurn(prompt=clean_user_text(text)))
        elif role == "assistant":
            if not turns:
                turns.append(HistoryTurn())
            turns[-1].response = _append(turns[-1].response, text)
    return turns


def _hermes_turns(entry: HistoryEntry) -> List[HistoryTurn]:
    """Reader signature the public API uses. See :func:`load_turns`.

    Hermes' entries all share one store, so the session id has to come from the
    entry rather than the path -- the same shape opencode needs.
    """
    return _hermes_turns_for(entry.session_id)


# ----- FreeBuff -----


def _git_root_name(cwd: str) -> str:
    """The name FreeBuff files a directory's chats under.

    FreeBuff buckets a conversation by the *repository* it was started in, not
    by the directory given to ``--cwd``, so a session started in a subfolder is
    filed under the repository's name.
    """
    try:
        current = Path(cwd).resolve()
    except (OSError, ValueError):
        return ""
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate.name
    return ""


def _freebuff_chat_buckets(cwd: Optional[str]) -> List[Path]:
    root = _home() / ".config" / "manicode" / "projects"
    if cwd:
        names = [name for name in (Path(cwd).name, _git_root_name(cwd)) if name]
        buckets = [root / name / "chats" for name in names]
        buckets.append(root / "chats")
        return [path for path in buckets if path.is_dir()]
    try:
        return sorted(path for path in root.glob("*/chats") if path.is_dir())
    except OSError:
        return []


def _freebuff_messages(chat: Path) -> List[dict]:
    try:
        payload = json.loads((chat / "chat-messages.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _freebuff_first_prompt(chat: Path) -> str:
    """The chat's first typed message, from its metadata where possible.

    FreeBuff writes the first prompt into ``chat-meta.json`` itself, so the
    common case costs one small read instead of parsing the whole conversation.
    """
    try:
        meta = json.loads((chat / "chat-meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    if isinstance(meta, dict):
        first = str(meta.get("firstPrompt") or "").strip()
        if first:
            # FreeBuff's own preview is already elided; drop its ellipsis so the
            # title is not cut twice.
            return first[:-3].rstrip() if first.endswith("...") else first
    for message in _freebuff_messages(chat):
        if message.get("variant") == "user":
            text = str(message.get("content") or "").strip()
            if text:
                return text
    return ""


def _freebuff_entry(chat: Path, bucket_folder: str) -> Optional[HistoryEntry]:
    first = _freebuff_first_prompt(chat)
    if not first:
        return None
    return HistoryEntry(
        backend=BACKEND_FREEBUFF,
        session_id=chat.name,
        title=make_title(first),
        path=str(chat),
        modified=_mtime(chat / "chat-messages.json") or _mtime(chat),
        cwd="",
        folder=bucket_folder,
    )


def _freebuff_entries(cwd: Optional[str]) -> List[HistoryEntry]:
    entries: List[HistoryEntry] = []
    seen: set[str] = set()
    for bucket in _freebuff_chat_buckets(cwd):
        # ".../projects/<project>/chats" — the project is what to display.
        bucket_folder = bucket.parent.name if bucket.parent.name != "manicode" else ""
        try:
            chats = list(bucket.iterdir())
        except OSError:
            continue
        for chat in chats:
            if not chat.is_dir() or chat.name in seen:
                continue
            entry = _freebuff_entry(chat, bucket_folder)
            if entry is not None:
                seen.add(chat.name)
                entries.append(entry)
    return entries


def _freebuff_turns(entry: HistoryEntry) -> List[HistoryTurn]:
    chat = Path(entry.path)
    turns: List[HistoryTurn] = []
    for message in _freebuff_messages(chat):
        variant = message.get("variant")
        if variant == "user":
            text = str(message.get("content") or "").strip()
            if text:
                turns.append(HistoryTurn(prompt=text))
            continue
        if variant != "ai":
            continue
        blocks = message.get("blocks")
        if not isinstance(blocks, list):
            continue
        answer: List[str] = []
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            # Reasoning is FreeBuff thinking aloud, not its answer.
            if block.get("textType") == "reasoning":
                continue
            content = str(block.get("content") or "").strip()
            if content:
                answer.append(content)
        if not answer:
            continue
        if not turns:
            turns.append(HistoryTurn())
        turns[-1].response = _append(turns[-1].response, "\n\n".join(answer))
    return turns


# ----- opencode -----


def _opencode_db() -> Path:
    """opencode's database file.

    opencode keeps every conversation in one SQLite database rather than in a
    file per conversation, so this is both the listing and the transcript.
    """
    override = os.environ.get("OPENCODE_DATA")
    if override:
        return Path(override) / "opencode.db"
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else _home() / ".local" / "share"
    return root / "opencode" / "opencode.db"


def _opencode_connect() -> Optional["sqlite3.Connection"]:
    """A read-only connection, or None when there is no database to read.

    Opened by URI so that opening it can never create one, and so a database
    the running opencode is writing to is never written to from here.
    """
    path = _opencode_db()
    if not path.is_file():
        return None
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    return connection


# The name opencode gives a conversation nobody has titled yet. It is a
# timestamp, which tells the user nothing the age column does not already say,
# so the first message is used instead.
_OPENCODE_UNTITLED = re.compile(r"^(?:new session\b|untitled\b)", re.IGNORECASE)


def _opencode_first_prompt(connection: "sqlite3.Connection", session_id: str) -> str:
    """The first thing typed into a conversation, for titling it.

    Whose message a part belongs to is decided from the message record rather
    than by matching on the JSON it is stored as: a role is a field, not a
    substring, and reading it as one is what survives opencode reformatting
    what it writes.
    """
    try:
        rows = connection.execute(
            "SELECT m.data, p.data FROM part p JOIN message m ON m.id = p.message_id "
            "WHERE p.session_id = ? ORDER BY p.time_created LIMIT 20",
            (session_id,),
        ).fetchall()
    except sqlite3.Error:
        return ""
    for message_data, part_data in rows:
        try:
            message = json.loads(message_data)
            part = json.loads(part_data)
        except ValueError:
            continue
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        if isinstance(part, dict) and part.get("type") == "text":
            text = clean_user_text(str(part.get("text") or ""))
            if text:
                return text
    return ""


def _opencode_entries(cwd: Optional[str]) -> List[HistoryEntry]:
    connection = _opencode_connect()
    if connection is None:
        return []
    entries: List[HistoryEntry] = []
    try:
        # Subagents get sessions of their own, hung off the one that started
        # them. Only the conversations a person actually had belong in the
        # picker, which is what a null parent means here.
        #
        # Read one row at a time, newest first, and stop once there are enough
        # to fill the picker. A limit in the query would be a limit on rows
        # *scanned*, and the conversations for one directory can sit anywhere
        # in a database shared by every directory — so asking for the newest
        # few hundred rows would quietly hide older ones from this project.
        rows = connection.execute(
            "SELECT id, title, directory, time_updated FROM session "
            "WHERE parent_id IS NULL AND time_archived IS NULL "
            "ORDER BY time_updated DESC"
        )
        for session_id, title, directory, updated in rows:
            if len(entries) >= _MAX_HISTORY_ENTRIES:
                break
            directory = str(directory or "")
            if cwd and not _same_dir(directory, cwd):
                continue
            title = str(title or "").strip()
            if not title or _OPENCODE_UNTITLED.match(title):
                title = _opencode_first_prompt(connection, str(session_id))
            if not title:
                continue
            try:
                modified = float(updated or 0) / 1000.0
            except (TypeError, ValueError):
                modified = 0.0
            entries.append(
                HistoryEntry(
                    backend=BACKEND_OPENCODE,
                    session_id=str(session_id),
                    title=make_title(title),
                    # opencode's transcript is a row set, not a file, so the
                    # session id is what identifies it and the path is only
                    # here so the picker has something to show.
                    path=str(_opencode_db()),
                    modified=modified,
                    cwd=directory,
                    folder=_folder_name(directory),
                )
            )
    except sqlite3.Error:
        return entries
    finally:
        connection.close()
    return entries


def _opencode_turns(entry: HistoryEntry) -> List[HistoryTurn]:
    connection = _opencode_connect()
    if connection is None:
        return []
    turns: List[HistoryTurn] = []
    try:
        roles = {}
        for message_id, data in connection.execute(
            "SELECT id, data FROM message WHERE session_id = ? ORDER BY time_created",
            (entry.session_id,),
        ):
            try:
                record = json.loads(data)
            except ValueError:
                continue
            if isinstance(record, dict):
                roles[str(message_id)] = str(record.get("role") or "")
        for message_id, data in connection.execute(
            "SELECT message_id, data FROM part WHERE session_id = ? ORDER BY time_created LIMIT ?",
            (entry.session_id, _MAX_TRANSCRIPT_PARTS),
        ):
            role = roles.get(str(message_id))
            if role not in ("user", "assistant"):
                continue
            try:
                part = json.loads(data)
            except ValueError:
                continue
            # Reasoning is opencode thinking aloud, and tool calls are its
            # working; neither is part of the conversation being replayed.
            if not isinstance(part, dict) or part.get("type") != "text":
                continue
            text = str(part.get("text") or "").strip()
            if not text:
                continue
            if role == "user":
                cleaned = clean_user_text(text)
                if cleaned:
                    turns.append(HistoryTurn(prompt=cleaned))
                continue
            if not turns:
                turns.append(HistoryTurn())
            turns[-1].response = _append(turns[-1].response, text)
    except sqlite3.Error:
        return turns
    finally:
        connection.close()
    return turns


# ----- Public API -----

_LISTERS = {
    BACKEND_CLAUDE: _claude_entries,
    BACKEND_CODEX: _codex_entries,
    BACKEND_FREEBUFF: _freebuff_entries,
    BACKEND_OPENCODE: _opencode_entries,
    BACKEND_HERMES: _hermes_entries,
}

# Readers are given the whole entry rather than its path, because opencode and
# Hermes each keep every conversation in one database: what identifies their
# transcript is the session id, not a file of its own.
_READERS = {
    BACKEND_CLAUDE: _claude_turns,
    BACKEND_CODEX: _codex_turns,
    BACKEND_FREEBUFF: _freebuff_turns,
    BACKEND_OPENCODE: _opencode_turns,
    BACKEND_HERMES: _hermes_turns,
}


def list_history(
    backend: Optional[str] = None,
    cwd: Optional[str] = None,
    limit: int = 300,
) -> List[HistoryEntry]:
    """Past conversations, newest first.

    ``backend`` limits the list to one provider; ``None`` returns them all.
    ``cwd`` limits it to conversations that ran in that directory; ``None``
    returns every directory. ``limit`` caps how many are returned, after
    sorting, so a machine with years of history still opens the picker fast.
    """
    if backend is None:
        backends = list(BACKEND_IDS)
    else:
        backends = [normalize_backend(backend)]

    entries: List[HistoryEntry] = []
    for name in backends:
        lister = _LISTERS.get(name)
        if lister is None:
            continue
        try:
            entries.extend(lister(cwd))
        except OSError:
            continue
    entries.sort(key=lambda entry: entry.modified, reverse=True)
    return entries[: max(0, limit)]


def load_turns(entry: HistoryEntry) -> List[HistoryTurn]:
    """Read one past conversation back as prompt-and-response turns.

    Turns with neither a prompt nor an answer are dropped, so an aborted run
    does not leave an empty response in the list.
    """
    backend = normalize_backend(entry.backend)
    reader = _READERS.get(backend)
    if reader is None:
        return []
    # opencode's and Hermes' path is the database every conversation shares, so
    # its size is no measure of this one -- and on a machine that has used
    # Hermes for a while the shared store passes this limit, which would hide
    # every conversation at once. Both cap themselves, in rows, while reading.
    if backend not in (BACKEND_OPENCODE, BACKEND_HERMES):
        try:
            path = Path(entry.path)
            if path.is_file() and path.stat().st_size > _MAX_TRANSCRIPT_BYTES:
                return []
        except OSError:
            return []
    try:
        turns = reader(entry)
    except OSError:
        return []
    return [turn for turn in turns if turn.prompt.strip() or turn.response.strip()]


def describe_age(modified: float, now: Optional[float] = None) -> str:
    """When a conversation was last touched, said the way a person would.

    Spoken by a screen reader, so it avoids clock arithmetic the listener would
    have to do: "12 minutes ago" rather than a timestamp, and a real date only
    once the relative form stops being useful.
    """
    if not modified:
        return "unknown"
    now = time.time() if now is None else now
    seconds = now - modified
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return "1 minute ago" if minutes == 1 else f"{minutes} minutes ago"
    hours = int(seconds // 3600)
    if hours < 24:
        return "1 hour ago" if hours == 1 else f"{hours} hours ago"
    days = int(seconds // 86400)
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    return time.strftime("%d %B %Y", time.localtime(modified))
