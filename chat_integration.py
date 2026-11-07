"""Construct BlindPilot's embedded provider-chat mode.

The provider and dialog code is maintained in the local ``accessible_ai``
package.  This module owns the BlindPilot-specific data location and the
one-time import of an existing AccessibleAI database.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Callable

import wx

from accessible_ai.services.generation_service import GenerationService
from accessible_ai.services.model_service import ModelService
from accessible_ai.storage.credentials import CredentialStore
from accessible_ai.storage.database import Database
from accessible_ai.storage.paths import database_path
from accessible_ai.ui.chat_panel import ChatPanel
from accessible_ai.logging_setup import log_path


logger = logging.getLogger(__name__)


def _configure_chat_logging() -> None:
    chat_logger = logging.getLogger("accessible_ai")
    path = log_path()
    if any(getattr(handler, "baseFilename", None) == str(path) for handler in chat_logger.handlers):
        return
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    chat_logger.addHandler(handler)
    chat_logger.setLevel(logging.INFO)


def _legacy_database_candidates() -> tuple[Path, ...]:
    """Locations used by installed and source copies of AccessibleAI."""
    appdata = os.environ.get("APPDATA")
    candidates: list[Path] = []
    if appdata:
        candidates.append(Path(appdata) / "AccessibleAI" / "accessible_ai.sqlite3")
    candidates.append(Path.home() / ".accessibleai" / "accessible_ai.sqlite3")
    candidates.append(Path.home() / ".config" / "AccessibleAI" / "accessible_ai.sqlite3")
    return tuple(candidates)


def import_existing_accessible_ai_data(target: Path) -> Path | None:
    """Seed Chat mode from AccessibleAI once, without changing its database."""
    if target.exists():
        return None
    for source in _legacy_database_candidates():
        if not source.is_file():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(source)) as source_db:
                with closing(sqlite3.connect(target)) as target_db:
                    source_db.backup(target_db)
            logger.info("Imported AccessibleAI chat data from %s", source)
            return source
        except (OSError, sqlite3.Error):
            # A corrupt or locked source raises sqlite3's own errors. A copy
            # that stopped part-way must not be left behind: it would pass the
            # `target.exists()` check above and open as a truncated database.
            logger.exception("Could not import AccessibleAI chat data from %s", source)
            target.unlink(missing_ok=True)
    return None


_IMPORT_LOCK = threading.Lock()
_IMPORT_STARTED = False
_IMPORT_DONE = threading.Event()
_IMPORTED: Path | None = None


def ensure_legacy_import() -> Path | None:
    """Copy an older AccessibleAI database once, or return what was copied.

    Safe from any thread and any number of times: the first caller does the
    copy and later ones take its answer, waiting for it if it is still running.
    Waiting is the lesser evil -- opening the database while sqlite is writing
    it would show a half-copied set of conversations.

    Startup calls this off the window's thread, so the first switch to Chat
    mode no longer freezes while a large database is copied.
    """
    global _IMPORT_STARTED, _IMPORTED
    with _IMPORT_LOCK:
        if not _IMPORT_STARTED:
            _IMPORT_STARTED = True
            try:
                _IMPORTED = import_existing_accessible_ai_data(database_path())
            finally:
                _IMPORT_DONE.set()
            return _IMPORTED
    _IMPORT_DONE.wait()
    return _IMPORTED


def create_chat_panel(
    parent: wx.Window,
    set_status: Callable[[str], None],
    speak: Callable[[str], None],
    model_order: str = "newest",
) -> ChatPanel:
    _configure_chat_logging()
    path = database_path()
    imported = ensure_legacy_import()
    database = Database(path)
    credentials = CredentialStore()
    panel = ChatPanel(
        parent,
        database,
        credentials,
        ModelService(database, credentials),
        GenerationService(credentials),
        set_status,
        speak,
        model_order=model_order,
    )
    if imported is not None:
        set_status("AccessibleAI accounts, profiles, and conversations were imported.")
    return panel
