"""Back up, restore, keep and import conversation history.

One module beside ``conversation_export.py`` for everything that protects or
moves history: a dated backup archive of the Chat database and the settings
file, a preview-before-restore, read-only copies of external agent transcripts,
Basilisk ``.bskc`` import, and a versioned JSON interchange format.

Backups never contain API keys, sign-in tokens, cookies or secret-bearing
backend configuration: keys live in the OS credential store / keyring
(``accessible_ai.storage.credentials``), outside the files copied here, and the
manifest lists the exclusions so the absence is checkable. Restoring never
overwrites live state in place: the live database and settings move aside to
a timestamped copy before the backup installs, and credentials are never
touched. Kept transcripts never expire automatically and deleting a copy never
deletes the provider original.

Archive readers reject traversal entries, symlinks and decompression bombs,
work in temporary files, and publish completed outputs atomically. A cancelled
run never leaves a plausible final file behind.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from accessible_ai.models import Conversation, Message, MessageAttachment
from accessible_ai.storage.database import Database

BACKUP_FORMAT_VERSION = 1
INTERCHANGE_FORMAT = "blindpilot.interchange"
INTERCHANGE_VERSION = 1
BSKC_FORMAT_VERSION = 3

# Bomb guardrails for archive readers. A single conversation body or a backup
# of a single machine's state is far below every one of these; anything past
# them is not a conversation file at all.
_MAX_ENTRIES = 2000
_MAX_TOTAL_BYTES = 512 * 1024 * 1024
_MAX_ENTRY_BYTES = 128 * 1024 * 1024


class HistoryBackupError(RuntimeError):
    """Raised when an archive or import is unusable; the message says why."""


@dataclass
class BackupPreview:
    """What a backup contains, read before anything is restored."""

    created_at: str = ""
    app_version: str = ""
    accounts: int = 0
    profiles: int = 0
    conversations: int = 0
    messages: int = 0
    includes_kept_transcripts: bool = False
    total_bytes: int = 0
    exclusions: tuple[str, ...] = ()


@dataclass
class ImportReport:
    """What an import made, and every field it could not carry over."""

    conversation_id: int = 0
    title: str = ""
    messages: int = 0
    attachments: int = 0
    unsupported: tuple[str, ...] = ()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _check_cancelled(cancel: Any | None) -> None:
    if cancel is not None and hasattr(cancel, "is_set") and cancel.is_set():
        raise HistoryBackupError("Cancelled.")


def _safe_entry_name(name: str) -> bool:
    """True when a zip entry name cannot escape the archive root."""
    if not name or name.startswith(("/", "\\")):
        return False
    parts = name.replace("\\", "/").split("/")
    return all(part not in ("", ".", "..") for part in parts[:-1]) and ".." not in parts


def _check_archive_limits(infos: list[zipfile.ZipInfo]) -> None:
    if len(infos) > _MAX_ENTRIES:
        raise HistoryBackupError(f"Archive has too many entries ({len(infos)}).")
    total = 0
    for info in infos:
        if not _safe_entry_name(info.filename):
            raise HistoryBackupError(f"Archive entry {info.filename!r} is not a safe name.")
        is_symlink = (info.external_attr >> 16) & 0o170000 == 0o120000
        if is_symlink:
            raise HistoryBackupError(f"Archive entry {info.filename!r} is a link.")
        if info.file_size > _MAX_ENTRY_BYTES:
            raise HistoryBackupError(f"Archive entry {info.filename!r} is too large.")
        total += info.file_size
        if total > _MAX_TOTAL_BYTES:
            raise HistoryBackupError("Archive expands beyond the allowed size.")


def _read_manifest(archive: zipfile.ZipFile) -> dict[str, Any]:
    try:
        raw = archive.read("manifest.json")
    except KeyError:
        raise HistoryBackupError("This archive has no manifest.json.") from None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise HistoryBackupError("The backup manifest is not readable JSON.") from None
    if not isinstance(data, dict) or data.get("format") != "blindpilot.backup":
        raise HistoryBackupError("This is not a BlindPilot backup archive.")
    if int(data.get("version") or 0) != BACKUP_FORMAT_VERSION:
        raise HistoryBackupError(f"Backup format version {data.get('version')!r} is not supported.")
    return data


def _copy_db_consistent(db_path: Path, out_path: Path) -> None:
    """Copy the live database without corrupting it under a writer.

    ``Connection.backup`` reads a consistent snapshot, so copying the file
    bytes would be the wrong way; anything open at copy time stays readable.
    """
    source = sqlite3.connect(str(db_path))
    try:
        target = sqlite3.connect(str(out_path))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _db_counts(db_path: Path) -> dict[str, int]:
    """Row counts, proving the database opens. Raises when it does not."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            raise HistoryBackupError("The backup database failed its integrity check.")
        counts = {}
        for table in ("accounts", "profiles", "conversations", "messages"):
            counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return counts
    finally:
        conn.close()


def create_backup(
    archive_path: Path,
    db_path: Path,
    settings_path: Path | None,
    kept_dir: Path | None,
    include_kept: bool,
    cancel: Any | None = None,
) -> None:
    """Create a backup archive. Atomic: the destination appears whole or not at all."""
    archive_path = Path(archive_path)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    exclusions = (
        "API keys and sign-in tokens (OS credential store / keyring, never in this file)",
        "Cookies",
        "Secret-bearing backend configuration",
    )
    entries: list[tuple[str, bytes]] = []
    with tempfile.TemporaryDirectory(prefix="blindpilot-backup-") as tmp:
        temp = Path(tmp)
        counts = _db_counts(db_path)
        _copy_db_consistent(db_path, temp / "chat.sqlite3")
        _check_cancelled(cancel)
        if _db_counts(temp / "chat.sqlite3") != counts:
            raise HistoryBackupError("The database copy did not match the live database.")
        db_bytes = (temp / "chat.sqlite3").read_bytes()
        entries.append(("chat.sqlite3", db_bytes))
        if settings_path is not None and Path(settings_path).is_file():
            entries.append(("settings.json", Path(settings_path).read_bytes()))
        kept_entries = []
        if include_kept and kept_dir is not None and Path(kept_dir).is_dir():
            for path in sorted(Path(kept_dir).rglob("*")):
                if path.is_file():
                    data = path.read_bytes()
                    kept_entries.append((f"kept_transcripts/{path.name}", data))
        manifest = {
            "format": "blindpilot.backup",
            "version": BACKUP_FORMAT_VERSION,
            "app_version": _app_version(),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "includes_kept_transcripts": bool(kept_entries),
            "exclusions": list(exclusions),
            "counts": counts,
            "entries": [
                {"name": name, "size": len(data), "sha256": _sha256(data)}
                for name, data in entries + kept_entries
            ],
        }
        all_entries = [("manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))]
        all_entries += entries + kept_entries
        tmp_archive = archive_path.with_name(f".{archive_path.name}.tmp-{os.getpid()}")
        try:
            with zipfile.ZipFile(tmp_archive, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, data in all_entries:
                    _check_cancelled(cancel)
                    archive.writestr(name, data)
            os.replace(tmp_archive, archive_path)
        finally:
            if tmp_archive.exists():
                tmp_archive.unlink()


def _app_version() -> str:
    try:
        from blindpilot_app import APP_VERSION

        return str(APP_VERSION)
    except Exception:
        return "unknown"


def preview_backup(archive_path: Path) -> BackupPreview:
    """Validate an archive and report what restoring it would install."""
    archive_path = Path(archive_path)
    if not zipfile.is_zipfile(archive_path):
        raise HistoryBackupError("This file is not a BlindPilot backup archive.")
    with zipfile.ZipFile(archive_path) as archive:
        _check_archive_limits(archive.infolist())
        manifest = _read_manifest(archive)
        names = {info.filename for info in archive.infolist()}
        if "chat.sqlite3" not in names:
            raise HistoryBackupError("The backup has no chat database.")
        for entry in manifest.get("entries", []):
            name = entry.get("name", "")
            if name not in names:
                raise HistoryBackupError(f"The backup is missing entry {name!r}.")
            data = archive.read(name)
            if _sha256(data) != entry.get("sha256", ""):
                raise HistoryBackupError(f"Entry {name!r} does not match the manifest.")
        db_bytes = archive.read("chat.sqlite3")
    with tempfile.TemporaryDirectory(prefix="blindpilot-preview-") as tmp:
        temp_db = Path(tmp) / "chat.sqlite3"
        temp_db.write_bytes(db_bytes)
        counts = _db_counts(temp_db)
    total = sum(int(e.get("size") or 0) for e in manifest.get("entries", []))
    return BackupPreview(
        created_at=str(manifest.get("created_at") or ""),
        app_version=str(manifest.get("app_version") or ""),
        accounts=counts.get("accounts", 0),
        profiles=counts.get("profiles", 0),
        conversations=counts.get("conversations", 0),
        messages=counts.get("messages", 0),
        includes_kept_transcripts=bool(manifest.get("includes_kept_transcripts")),
        total_bytes=total,
        exclusions=tuple(manifest.get("exclusions") or ()),
    )


def _move_aside(path: Path) -> Path | None:
    """Move a live file to a timestamped ``.before-restore-<stamp>`` copy."""
    if not path.is_file():
        return None
    stamp = _now_stamp()
    target = path.with_name(f"{path.name}.before-restore-{stamp}")
    counter = 1
    while target.exists():
        target = path.with_name(f"{path.name}.before-restore-{stamp}-{counter}")
        counter += 1
    os.replace(path, target)
    return target


def restore_backup(
    archive_path: Path,
    db_path: Path,
    settings_path: Path | None,
    kept_dir: Path | None,
    cancel: Any | None = None,
) -> BackupPreview:
    """Install a backup. Live state moves aside first; credentials are untouched.

    The preview runs first, so an invalid archive changes nothing. The live
    database and settings file move to timestamped copies, then the backup's
    database installs and re-opens through ``Database`` as proof. Kept
    transcripts install only entries the destination does not already hold at
    the same hash. Returns the preview that was installed.
    """
    archive_path = Path(archive_path)
    db_path = Path(db_path)
    preview = preview_backup(archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        db_bytes = archive.read("chat.sqlite3")
        settings_bytes = (
            archive.read("settings.json") if "settings.json" in archive.namelist() else None
        )
        kept = [n for n in archive.namelist() if n.startswith("kept_transcripts/")]
        kept_data = {n: archive.read(n) for n in kept}
    _check_cancelled(cancel)
    moved_db = _move_aside(db_path)
    moved_settings = _move_aside(Path(settings_path)) if settings_path else None
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=".chat-", suffix=".sqlite3", dir=str(db_path.parent), delete=False
        ) as handle:
            handle.write(db_bytes)
            staged = Path(handle.name)
        os.replace(staged, db_path)
        Database(db_path)  # Re-open: proves the restored database works.
        if settings_bytes is not None and settings_path is not None:
            staged_settings = Path(str(settings_path) + f".tmp-{os.getpid()}")
            staged_settings.write_bytes(settings_bytes)
            os.replace(staged_settings, settings_path)
        if kept_dir is not None and kept_data:
            kept_target = Path(kept_dir)
            kept_target.mkdir(parents=True, exist_ok=True)
            for name, data in kept_data.items():
                destination = kept_target / Path(name).name
                if destination.is_file() and _sha256(destination.read_bytes()) == _sha256(data):
                    continue
                staged_kept = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
                staged_kept.write_bytes(data)
                os.replace(staged_kept, destination)
    except (OSError, sqlite3.Error) as exc:
        # Put the live state back: a failed restore leaves the old state whole.
        if db_path.exists():
            db_path.unlink()
        if moved_db is not None:
            os.replace(moved_db, db_path)
        if settings_path is not None and moved_settings is not None:
            if Path(settings_path).exists():
                Path(settings_path).unlink()
            os.replace(moved_settings, settings_path)
        raise HistoryBackupError(f"Restore failed and live state was kept: {exc}") from None
    return preview


# ----- Kept transcripts -----


@dataclass
class KeptEntry:
    """One read-only copy, with the source it protects."""

    source_path: str = ""
    copy_name: str = ""
    sha256: str = ""
    size: int = 0
    kept_at: str = ""


class KeptTranscripts:
    """Read-only copies of external agent transcripts.

    Copies live in ``<kept_dir>/copies/`` with an ``index.json`` mapping each
    kept source path to its copy. Copies never expire automatically; deleting
    one never deletes the provider original. All mutations hold one lock.
    """

    def __init__(self, kept_dir: Path):
        self.dir = Path(kept_dir)
        self.copies = self.dir / "copies"
        self.index_path = self.dir / "index.json"
        self._lock = threading.Lock()

    def _load_index(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save_index(self, entries: list[dict[str, Any]]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        staged = self.dir / f".index.json.tmp-{os.getpid()}"
        staged.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        os.replace(staged, self.index_path)

    def entries(self) -> list[KeptEntry]:
        return [
            KeptEntry(
                source_path=str(item.get("source_path") or ""),
                copy_name=str(item.get("copy_name") or ""),
                sha256=str(item.get("sha256") or ""),
                size=int(item.get("size") or 0),
                kept_at=str(item.get("kept_at") or ""),
            )
            for item in self._load_index()
            if isinstance(item, dict)
        ]

    def storage_report(self) -> tuple[int, int]:
        """(copies, bytes) currently kept."""
        total = 0
        count = 0
        for entry in self.entries():
            path = self.copies / entry.copy_name
            if path.is_file():
                total += path.stat().st_size
                count += 1
        return count, total

    def keep(self, source_path: Path, cancel: Any | None = None) -> bool:
        """Copy one transcript. False when it cannot be kept."""
        source = Path(source_path)
        if not source.is_file():
            return False
        try:
            data = source.read_bytes()
        except OSError:
            return False
        digest = _sha256(data)
        with self._lock:
            entries = self._load_index()
            if any(
                item.get("source_path") == str(source) and item.get("sha256") == digest
                for item in entries
            ):
                return True
            _check_cancelled(cancel)
            self.copies.mkdir(parents=True, exist_ok=True)
            stamp = _now_stamp()
            copy_name = f"{source.stem}-{stamp}{source.suffix}"
            staged = self.copies / f".{copy_name}.tmp-{os.getpid()}"
            staged.write_bytes(data)
            os.replace(staged, self.copies / copy_name)
            entries.append(
                {
                    "source_path": str(source),
                    "copy_name": copy_name,
                    "sha256": digest,
                    "size": len(data),
                    "kept_at": stamp,
                }
            )
            self._save_index(entries)
        return True

    def delete(self, copy_names: list[str]) -> int:
        """Remove the named copies. Returns how many were removed.

        The provider original is never touched: only files inside this
        directory's ``copies`` folder, named in the index, are removed.
        """
        removed = 0
        with self._lock:
            entries = self._load_index()
            wanted = {name for name in copy_names if name in {e["copy_name"] for e in entries}}
            kept = []
            for item in entries:
                if item.get("copy_name") in wanted:
                    path = self.copies / str(item.get("copy_name") or "")
                    if path.is_file():
                        path.unlink()
                        removed += 1
                else:
                    kept.append(item)
            if wanted:
                self._save_index(kept)
        return removed


# ----- Basilisk .bskc import -----


def _bskc_read(archive_path: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Read a .bskc zip: (conversation JSON, {attachment name: bytes})."""
    if not zipfile.is_zipfile(archive_path):
        raise HistoryBackupError("This file is not a Basilisk conversation (.bskc).")
    with zipfile.ZipFile(archive_path) as archive:
        _check_archive_limits(archive.infolist())
        names = set(archive.namelist())
        if "conversation.json" not in names:
            raise HistoryBackupError("The .bskc archive has no conversation.json.")
        try:
            data = json.loads(archive.read("conversation.json").decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise HistoryBackupError("The .bskc conversation.json is not valid JSON.") from None
        attachments = {
            Path(name).name: archive.read(name) for name in names if name.startswith("attachments/")
        }
    if not isinstance(data, dict):
        raise HistoryBackupError("The .bskc conversation.json is not an object.")
    return data, attachments


def _message_from_block(block: Any, role: str) -> tuple[str, list[Any]]:
    if not isinstance(block, dict):
        return "", []
    content = str(block.get("content") or "")
    return content, list(block.get("attachments") or [])


def import_bskc(archive_path: Path, database: Database, cancel: Any | None = None) -> ImportReport:
    """Import a Basilisk conversation into a new Chat conversation."""
    data, attachments = _bskc_read(Path(archive_path))
    unsupported: list[str] = []
    systems = data.get("systems") or []
    system_text = ""
    if systems:
        system_text = "\n\n".join(
            str(item.get("content") or "") for item in systems if isinstance(item, dict)
        )
        if len(systems) > 1:
            unsupported.append("multiple system messages combined into one prompt")
    messages_data = data.get("messages") or []
    title = str(data.get("title") or "Imported Basilisk conversation")
    conversation_id = database.create_conversation(
        Conversation(
            title=title,
            system_prompt_snapshot=system_text,
        )
    )
    message_count = 0
    attachment_count = 0
    for block in messages_data:
        _check_cancelled(cancel)
        if not isinstance(block, dict):
            continue
        request, request_attachments = _message_from_block(block.get("request"), "user")
        built = Message(conversation_id=conversation_id, role="user", content=request)
        for attachment in request_attachments:
            if not isinstance(attachment, dict):
                continue
            location = str(attachment.get("location") or "")
            attachment_type = str(attachment.get("type") or "")
            if attachment_type == "url" or location.startswith(("http://", "https://")):
                unsupported.append("URL attachments are not imported; only file bytes travel")
                continue
            name = Path(location).name
            payload = attachments.get(name)
            if payload is None:
                unsupported.append(f"attachment {name!r} missing from the archive")
                continue
            built.attachments.append(
                MessageAttachment(
                    filename=name or "attachment",
                    mime_type=_guess_mime(name or "attachment"),
                    data=payload,
                )
            )
            attachment_count += 1
        database.add_message(built)
        message_count += 1
        response, _ = _message_from_block(block.get("response"), "assistant")
        if response:
            database.add_message(
                Message(conversation_id=conversation_id, role="assistant", content=response)
            )
            message_count += 1
        model = block.get("model")
        if isinstance(model, dict):
            unsupported.append(
                f"per-message model {model.get('model_id') or model.get('provider_id') or '?'} "
                "kept as a note, not applied"
            )
        for key in ("temperature", "max_tokens", "top_p", "stream"):
            if key in block:
                unsupported.append(f"message setting {key!r} is not applied on import")
    return ImportReport(
        conversation_id=conversation_id,
        title=title,
        messages=message_count,
        attachments=attachment_count,
        unsupported=tuple(dict.fromkeys(unsupported)),
    )


def _guess_mime(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".md": "text/markdown",
    }.get(suffix, "application/octet-stream")


# ----- VoLlama JSON import -----

_VOLLAMA_ROLES = ("system", "user", "assistant", "tool")


def import_vollama(
    path_or_data: Any, database: Database, cancel: Any | None = None
) -> ImportReport:
    """Import a VoLlama JSON save as a new Chat conversation.

    ``path_or_data`` is a path to the save, the save's JSON text, or the
    already-parsed save. A save is either a JSON array of messages or an
    object with a ``messages`` array. Each message has a ``role`` in
    system/user/assistant/tool, a string-or-null ``content``, and may carry
    an ``extra`` or ``additional_kwargs`` object holding reasoning,
    ``tool_call_id``, ``name`` and ``tool_calls``.

    Everything is validated before anything is created, so unrelated JSON
    is refused and leaves no conversation behind. The title is the file
    stem. Leading system messages become the conversation's system prompt;
    with none, ``system_prompt_snapshot`` is left unset so the default
    prompt applies. Message order is kept.
    """
    title = "Imported VoLlama conversation"
    data: Any = path_or_data
    if isinstance(path_or_data, (str, os.PathLike)):
        candidate = Path(path_or_data)
        is_path = isinstance(path_or_data, os.PathLike) or (
            candidate.is_file() and not str(path_or_data).lstrip().startswith(("[", "{"))
        )
        if is_path:
            title = candidate.stem or title
            try:
                raw_text = candidate.read_text(encoding="utf-8")
            except OSError as exc:
                raise HistoryBackupError(f"The VoLlama save could not be read: {exc}") from None
            try:
                data = json.loads(raw_text)
            except ValueError:
                raise HistoryBackupError("The VoLlama save is not valid JSON.") from None
        else:
            try:
                data = json.loads(str(path_or_data))
            except ValueError:
                raise HistoryBackupError("The VoLlama save is not valid JSON.") from None
    elif isinstance(path_or_data, (bytes, bytearray)):
        try:
            data = json.loads(bytes(path_or_data).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise HistoryBackupError("The VoLlama save is not valid JSON.") from None

    if isinstance(data, list):
        messages_data = data
    elif isinstance(data, dict) and isinstance(data.get("messages"), list):
        messages_data = data["messages"]
        if isinstance(data.get("title"), str) and data["title"]:
            # A parsed object handed in directly has no file stem to use.
            if not isinstance(path_or_data, (str, os.PathLike)):
                title = data["title"]
    else:
        raise HistoryBackupError(
            "The VoLlama save could not be read: "
            "expected a JSON message list or an object containing one."
        )

    # Validate everything before creating anything.
    parsed: list[tuple[str, str, dict[str, Any]]] = []
    for item in messages_data:
        if not isinstance(item, dict):
            raise HistoryBackupError(
                "The VoLlama save contains a message with an invalid role or content."
            )
        role = item.get("role")
        content = item.get("content")
        if role not in _VOLLAMA_ROLES or not (content is None or isinstance(content, str)):
            raise HistoryBackupError(
                "The VoLlama save contains a message with an invalid role or content."
            )
        extra: Any = {}
        for key in ("extra", "additional_kwargs"):
            if key in item and item[key] is not None and not isinstance(item[key], dict):
                raise HistoryBackupError("The VoLlama save contains invalid message metadata.")
        raw_extra = item.get("extra")
        if isinstance(raw_extra, dict):
            extra = raw_extra
        elif isinstance(item.get("additional_kwargs"), dict):
            extra = item["additional_kwargs"]
        for key in ("reasoning", "tool_call_id", "name"):
            value = extra.get(key)
            if value is not None and not isinstance(value, str):
                raise HistoryBackupError("The VoLlama save contains invalid message metadata.")
        tool_calls = extra.get("tool_calls")
        if tool_calls is not None:
            if not isinstance(tool_calls, list):
                raise HistoryBackupError("The VoLlama save contains an invalid tool call list.")
            for call in tool_calls:
                function = call.get("function") if isinstance(call, dict) else None
                if (
                    not isinstance(call, dict)
                    or not isinstance(function, dict)
                    or not isinstance(call.get("id"), str)
                    or not call.get("id")
                    or not isinstance(function.get("name"), str)
                    or not function.get("name")
                ):
                    raise HistoryBackupError("The VoLlama save contains an invalid tool call.")
                arguments = function.get("arguments")
                if arguments is not None and not isinstance(
                    arguments, (str, dict, list, int, float, bool)
                ):
                    raise HistoryBackupError("The VoLlama save contains an invalid tool call.")
        parsed.append((str(role), content or "", dict(extra)))

    _check_cancelled(cancel)

    system_parts: list[str] = []
    index = 0
    while index < len(parsed) and parsed[index][0] == "system":
        system_parts.append(parsed[index][1])
        index += 1

    unsupported: list[str] = []
    for _role, _content, extra in parsed:
        if extra.get("reasoning"):
            unsupported.append("reasoning is not carried over")
        if extra.get("tool_calls"):
            unsupported.append("tool calls are not carried over")
        if extra.get("tool_call_id") or extra.get("name"):
            unsupported.append("tool result names are not carried over")

    if system_parts:
        conversation_id = database.create_conversation(
            Conversation(title=title, system_prompt_snapshot="\n\n".join(system_parts))
        )
    else:
        # Leave system_prompt_snapshot unset: the default prompt applies.
        conversation_id = database.create_conversation(Conversation(title=title))
    count = 0
    for role, content, _extra in parsed[index:]:
        _check_cancelled(cancel)
        database.add_message(Message(conversation_id=conversation_id, role=role, content=content))
        count += 1
    return ImportReport(
        conversation_id=conversation_id,
        title=title,
        messages=count,
        attachments=0,
        unsupported=tuple(dict.fromkeys(unsupported)),
    )


# ----- Interchange export/import -----


def export_interchange(conversation_id: int, database: Database) -> dict[str, Any]:
    """A versioned JSON snapshot of one conversation, for round trips."""
    conversation = database.get_conversation(conversation_id)
    if conversation is None:
        raise HistoryBackupError("That conversation no longer exists.")
    messages = database.list_messages(conversation_id)
    return {
        "format": INTERCHANGE_FORMAT,
        "version": INTERCHANGE_VERSION,
        "title": conversation.title,
        "system_prompt": conversation.system_prompt_snapshot,
        "messages": [
            {
                "role": message.role,
                "content": message.content,
                "attachments": [
                    {
                        "filename": attachment.filename,
                        "mime_type": attachment.mime_type,
                        "data_b64": base64.b64encode(attachment.data).decode("ascii"),
                    }
                    for attachment in message.attachments
                ],
            }
            for message in messages
        ],
    }


def import_interchange(
    data: dict[str, Any], database: Database, cancel: Any | None = None
) -> ImportReport:
    """Install an interchange snapshot as a new conversation."""
    if not isinstance(data, dict) or data.get("format") != INTERCHANGE_FORMAT:
        raise HistoryBackupError("This file is not a BlindPilot interchange export.")
    if int(data.get("version") or 0) != INTERCHANGE_VERSION:
        raise HistoryBackupError(f"Interchange version {data.get('version')!r} is not supported.")
    title = str(data.get("title") or "Imported conversation")
    conversation_id = database.create_conversation(
        Conversation(
            title=title,
            system_prompt_snapshot=str(data.get("system_prompt") or ""),
        )
    )
    count = 0
    seen_attachments = 0
    for item in data.get("messages") or []:
        _check_cancelled(cancel)
        if not isinstance(item, dict):
            continue
        message = Message(
            conversation_id=conversation_id,
            role=str(item.get("role") or "user"),
            content=str(item.get("content") or ""),
        )
        for attachment in item.get("attachments") or []:
            if not isinstance(attachment, dict):
                continue
            try:
                payload = base64.b64decode(str(attachment.get("data_b64") or ""), validate=True)
            except ValueError:
                continue
            message.attachments.append(
                MessageAttachment(
                    filename=str(attachment.get("filename") or "attachment"),
                    mime_type=str(attachment.get("mime_type") or "application/octet-stream"),
                    data=payload,
                )
            )
            seen_attachments += 1
        database.add_message(message)
        count += 1
    return ImportReport(
        conversation_id=conversation_id,
        title=title,
        messages=count,
        attachments=seen_attachments,
        unsupported=(),
    )
