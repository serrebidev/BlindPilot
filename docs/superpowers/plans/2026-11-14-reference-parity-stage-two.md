# Reference Parity Stage Two Implementation Plan

**Goal:** Back up and restore BlindPilot state, keep read-only transcript copies, and import conversations from Basilisk (.bskc) and a documented interchange format, without ever silently overwriting live history or credentials.

**Architecture:** New `history_backup.py` next to `conversation_export.py`: pure path/config operations plus thin `Database` use. Archive work runs on workers; GUI handlers only select files and announce results. Keep `blindpilot_app.py` for menu routing and dialogs only.

**Spec:** `docs/superpowers/specs/2026-11-14-reference-app-parity-design.md`, staged release 2.

## Global Constraints

- GUI callbacks stay under the ADR 0001 50 ms budget; zip/database-copy/transcript work runs on workers.
- Backups exclude API keys, sign-in tokens, cookies and secret-bearing backend config; the manifest lists exclusions.
- Restore never overwrites live state in place: the live database and settings file move aside to a timestamped copy first, then the backup installs. Credentials are never touched.
- Kept transcripts never expire automatically; deletion is explicit, confirmed, and never deletes the provider original. A preference disables future keeping.
- Archives reject traversal, symlinks and decompression bombs; work in temp files and atomically publish completed outputs. Cancellation never leaves a plausible final file.
- Imports preserve messages, attachments and supported settings; every unsupported field is reported, never silently dropped.
- Menu additions go to the Recent Conversations submenu (top-level File/Conversation menus are at the ten-item ceiling).
- No new dependencies; stdlib (`zipfile`, `sqlite3`, `json`, `hashlib`) plus existing seams only.
- Before commit: Ruff check, Ruff format check, full `pytest -q -W error`, bare `mypy`, startup smokes.

## Task 1: Backup archive with preview

- [ ] `history_backup.py`: `create_backup(archive_path, db_path, settings_paths, kept_dir, include_kept, cancel)`. Manifest JSON with format version, app version, timestamp, entry list (name, size, sha256), exclusions. DB copied via `sqlite3` backup API to a temp file so a live writer cannot corrupt it. Settings copied verbatim (they hold no API keys; keys live in the OS credential store / keyring). Temp archive + atomic rename; zip entry limits (count, total size, single-file size) reject bombs.
- [ ] `preview_backup(archive_path) -> BackupPreview`: validate zip, manifest, entry layout, db integrity (`PRAGMA integrity_check` on a temp copy), and return counts (accounts, profiles, conversations, messages) plus total size. Reject traversal/`..`/absolute entries and symlinks.
- [ ] Tests: round-trip backup→preview; counts match; exclusion list has no key material; traversal entry rejected; oversized entry rejected; cancelled creation leaves no final file.

## Task 2: Restore with live state preserved

- [ ] `restore_backup(archive_path, db_path, settings_paths, kept_dir, cancel)`: preview first, then move live db/settings aside (`<name>.before-restore-<stamp>`), verify the backup db again, install, and re-open `Database` to prove it. Kept transcripts restore only when the archive includes them and the destination has no entry with the same source+hash. Never calls any credential function.
- [ ] Tests: restore preserves live state under a stamp; restored counts match preview; credentials untouched (fake store untouched); re-open works.

## Task 3: Kept transcripts

- [ ] `KeptTranscripts` helper over `<app_data>/kept_transcripts/`: `index.json` entries `{source_path, copy_name, sha256, size, mtime}`. `keep(source_path, cancel) -> bool` copies atomically and updates the index under a lock. `storage_report() -> (copies, bytes)`. `delete_selected(indices, confirm)` removes copies only, never the source. `enabled` preference read through the existing preferences config.
- [ ] Hook keeping into history opening and successful local turns via a worker; a failed first copy is reported, never claimed as protection.
- [ ] Tests: copy+hash match, dedupe on same hash, delete never touches source, disabled preference skips, failed copy returns False.

## Task 4: Basilisk .bskc import

- [ ] `import_bskc(archive_path, database, cancel) -> ImportReport`: read zip `conversation.json` (version 3), map `messages[].request/response`, `systems`, `title` into a new Chat conversation; attachments under `attachments/` become `message_attachments`; report per-block model info and unsupported fields (`temperature`, `top_p`, per-message stream flags) in the report instead of applying them. Reject non-zip, missing `conversation.json`, and bomb entries.
- [ ] Tests: round-trip fixture with messages/systems/title/attachment; unsupported fields reported; bad zip rejected; source conversation rows unchanged.

## Task 5: Interchange export/import

- [ ] `export_interchange(conversation_id, database) -> dict` and `import_interchange(data, database, cancel) -> ImportReport` using a versioned JSON schema (`blindpilot.interchange`, version 1) with base64 attachment payloads and the same unsupported-field report path.
- [ ] Tests: export→import round trip preserves title, order, roles, attachment bytes; unknown schema version rejected.

## Task 6: UI wiring

- [ ] Recent Conversations submenu: "Back Up BlindPilot…", "Restore from Backup…", "Import Conversation…", "Kept Transcripts…". Backup dialog asks for destination + an include-kept-transcripts choice (default off: external transcripts are sensitive). Restore shows the preview and requires confirmation. Import accepts `.bskc` and `.blindpilot.json`. Kept Transcripts dialog lists copies, storage use, disable-keeping check, and delete-selected with confirmation. All heavy work on workers; results announced without moving focus unexpectedly.
- [ ] Wire keeping into history-open and turn-complete callbacks.
- [ ] Menu/layout and dialog tests with existing stand-in patterns. Commit gates.
