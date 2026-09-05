from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "BlindPilot"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path | None:
    """The folder or bundle that an update replaces, or None when run from source."""
    if not is_frozen():
        return None
    executable = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        for parent in executable.parents:
            if parent.suffix == ".app":
                return parent
    return executable.parent


def system_config_dir() -> Path:
    """The platform's usual per-user settings folder for this application."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        # APPDATA is always set in a normal session; the home folder is only a
        # fallback for a stripped environment such as a service account.
        return Path(base) / APP_NAME if base else Path.home() / f".{APP_NAME.lower()}"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME")
    return Path(base) / APP_NAME if base else Path.home() / ".config" / APP_NAME


def app_data_dir() -> Path:
    """BlindPilot's existing per-user settings folder."""
    path = system_config_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path() -> Path:
    return app_data_dir() / "chat.sqlite3"
