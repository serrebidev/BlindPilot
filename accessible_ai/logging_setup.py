from __future__ import annotations

from pathlib import Path

from accessible_ai.storage.paths import app_data_dir


def log_path() -> Path:
    """Where Chat mode's own log lives.

    A function rather than a module constant so importing the package neither
    decides nor creates the data folder. Tests redirect that folder, and a path
    fixed at import time would have ignored the redirect.
    """
    return app_data_dir() / "blindpilot-chat.log"
