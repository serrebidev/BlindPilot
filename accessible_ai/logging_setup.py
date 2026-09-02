from __future__ import annotations

import logging
from pathlib import Path

from accessible_ai.storage.paths import app_data_dir


LOG_PATH: Path = app_data_dir() / "blindpilot-chat.log"


def configure_logging() -> None:
    """Add a chat log, without taking anything else's away.

    This used to call `basicConfig(..., force=True)`, which removes and closes
    every handler already on the root logger. Nothing calls this today, but
    `diagnostics.start_logging` puts its handler there at startup, so the one
    thing this function would do if it were ever wired up is silently destroy
    the log that records why the application stopped working. Adding a handler
    is what was wanted; removing the others was never part of it.
    """
    root = logging.getLogger()
    if any(
        isinstance(existing, logging.FileHandler)
        and Path(getattr(existing, "baseFilename", "")) == LOG_PATH
        for existing in root.handlers
    ):
        return
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
