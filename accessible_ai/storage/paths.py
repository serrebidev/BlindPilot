from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "BlindPilot"

# The Windows installer drops this file beside the executable. Its presence is
# what tells an installed copy from a portable one: a build that was unpacked
# rather than installed does not have it, and keeps its settings beside itself.
INSTALLED_MARKER = ".installed"

# The folder a portable build keeps its settings in, beside the executable.
PORTABLE_CONFIG_DIR = "config"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def executable_dir() -> Path | None:
    """The folder holding the application, or None when running from source.

    On macOS the executable lives at ``AccessibleAI.app/Contents/MacOS``, and
    the folder that matters to a person is the one holding the bundle, so that
    is what is returned there.
    """
    if not is_frozen():
        return None
    executable = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        for parent in executable.parents:
            if parent.suffix == ".app":
                return parent.parent
    return executable.parent


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


def is_portable() -> bool:
    """True for a build that keeps its settings beside itself.

    Only the Windows installer marks an installation, so an unpacked zip or
    tarball, on any platform, is portable.
    """
    if not is_frozen():
        return False
    directory = bundle_dir()
    if directory is None:
        return False
    if sys.platform == "darwin":
        # A macOS bundle should not be written into, so the marker sits beside it.
        return not (directory.parent / INSTALLED_MARKER).exists()
    return not (directory / INSTALLED_MARKER).exists()


def portable_config_dir() -> Path | None:
    """Where a portable build keeps its settings, beside the executable."""
    directory = executable_dir()
    if directory is None:
        return None
    if sys.platform == "darwin":
        # Beside the bundle rather than inside it, so the app stays replaceable
        # in one piece and its signature is never disturbed.
        return directory / f"{APP_NAME}-{PORTABLE_CONFIG_DIR}"
    return directory / PORTABLE_CONFIG_DIR


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
