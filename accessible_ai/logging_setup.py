from __future__ import annotations

import logging
from pathlib import Path

from accessible_ai.storage.paths import app_data_dir


LOG_PATH: Path = app_data_dir() / "blindpilot-chat.log"


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8")],
        force=True,
    )
