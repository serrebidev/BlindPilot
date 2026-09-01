"""Where BlindPilot writes an account of itself when something goes wrong.

WHAT IS NEVER WRITTEN HERE, at any level, for any reason: the text of a
prompt, the text of an answer, the contents of a file, or a credential. This
application's content is somebody's source code and the questions they asked
about it, so the line is drawn deliberately and in one place: what BlindPilot
*did* is recorded, what the person said is not. `log_unfinished_turn` takes a
fixed list of fields and refuses anything else, so widening it is a decision
somebody has to make on purpose rather than a `logger.debug(prompt)` that
looked convenient one afternoon.

The packaged build is windowed, which on Windows means it has no stderr at
all. An uncaught exception there currently goes nowhere: no console, no
message, no file. That is what the hooks below are for.
"""

from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import os
import platform
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

LOG_NAME = "blindpilot.log"
CRASH_NAME = "blindpilot-crash.log"

# Small enough that the whole thing can be read, large enough to hold a session
# worth of turns. Four files at a megabyte is the most this can ever occupy.
MAX_BYTES = 1024 * 1024
KEEP = 3

# The only fields a turn may record. Every one of them describes how the turn
# was run, not what it was about.
TURN_FIELDS = (
    "exit_code",
    "completed",
    "session_id",
    "permission_mode",
    "model",
    "cancelled",
    "detail",
)

_started = False
_crash_file = None


def log_dir() -> Path:
    """The platform's directory for logs, which is not the one for settings.

    Settings roam between machines on a domain profile and should; a log is
    about one machine, grows, and should not. Windows separates the two as
    Roaming and Local, macOS keeps logs in ~/Library/Logs, and the XDG spec
    calls this state rather than data or config.
    """
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "BlindPilot" / "Logs"
    if system == "Darwin":
        return Path.home() / "Library" / "Logs" / "BlindPilot"
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "state") / "blindpilot"


def log_path() -> Path:
    return log_dir() / LOG_NAME


def _level() -> int:
    """INFO unless asked for more, the conventional way, through the environment.

    A name that is not a level is somebody's typo, and refusing to start over a
    typo would be worse than the wrong verbosity.
    """
    wanted = os.environ.get("BLINDPILOT_LOG_LEVEL", "").strip().upper()
    resolved = logging.getLevelNamesMapping().get(wanted) if wanted else None
    return resolved if isinstance(resolved, int) else logging.INFO


def start_logging() -> Optional[Path]:
    """Begin writing to the log, and route what would otherwise vanish into it.

    Returns where it is writing, or None if it could not. Losing the log is
    never worth losing the application over, so every failure here is silent
    and the program carries on without one.
    """
    global _started, _crash_file
    if _started:
        # Called twice, a second handler would write every line twice.
        return log_path()

    try:
        directory = log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            directory / LOG_NAME,
            maxBytes=MAX_BYTES,
            backupCount=KEEP,
            encoding="utf-8",
            errors="replace",
            delay=True,
        )
    except OSError:
        return None

    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(_level())
    root.addHandler(handler)

    sys.excepthook = log_uncaught
    # Workers are threads, and an exception escaping one is exactly the failure
    # that used to end a turn with nothing said anywhere.
    threading.excepthook = _log_uncaught_in_thread

    try:
        # A separate file, not this one: faulthandler holds a raw descriptor,
        # and rotation would leave it writing into a file nobody reads again.
        _crash_file = open(directory / CRASH_NAME, "a", encoding="utf-8", errors="replace")
        faulthandler.enable(file=_crash_file)
    except (OSError, ValueError, RuntimeError):
        # A native crash going unrecorded is a smaller problem than refusing
        # to start. wxPython, pywinpty and ConPTY are the reason it is tried.
        _crash_file = None

    _started = True
    logging.getLogger("blindpilot").info("logging started at %s", logging.getLevelName(root.level))
    return directory / LOG_NAME


def stop_logging() -> None:
    """Release the log files. Mainly so tests can delete what they wrote."""
    global _started, _crash_file
    faulthandler.disable()
    if _crash_file is not None:
        try:
            _crash_file.close()
        except OSError:
            pass
        _crash_file = None
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            handler.close()
            root.removeHandler(handler)
    _started = False


def _reveal(path: Path) -> bool:
    """Show a folder in whatever this platform calls its file manager."""
    system = platform.system()
    if system == "Windows":
        os.startfile(str(path))  # noqa: S606 - a directory this module chose
        return True
    opener = "open" if system == "Darwin" else "xdg-open"
    return subprocess.Popen([opener, str(path)]) is not None


def open_log_folder() -> bool:
    """Put the log folder in front of somebody. False if nothing happened.

    Reading a path out loud and leaving somebody to navigate to it is not a
    way in, which is the whole reason this exists rather than an announcement.
    """
    try:
        directory = log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        return bool(_reveal(directory))
    except (OSError, ValueError):
        return False


def log_uncaught(exc_type, exc, tb) -> None:
    """An exception nothing caught, on its way to a stderr that may not exist."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    logging.getLogger("blindpilot").critical("uncaught exception", exc_info=(exc_type, exc, tb))


def _log_uncaught_in_thread(args) -> None:
    if args.exc_type is SystemExit:
        return
    logging.getLogger("blindpilot").critical(
        "uncaught exception in thread %s",
        getattr(args.thread, "name", "?"),
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def log_unfinished_turn(backend: str, **fields) -> None:
    """Record a turn that did not finish, in fields chosen on purpose.

    Anything not named in TURN_FIELDS raises rather than being written. That is
    the whole safety mechanism: a caller cannot pass the prompt through here by
    reaching for a keyword that seemed reasonable at the time.
    """
    unknown = sorted(set(fields) - set(TURN_FIELDS))
    if unknown:
        raise TypeError(
            f"log_unfinished_turn does not record {', '.join(unknown)}. "
            "Only how a turn ran is recorded, never what it was about."
        )
    described = " ".join(f"{name}={fields[name]!r}" for name in TURN_FIELDS if name in fields)
    logging.getLogger(f"blindpilot.{backend}").warning("turn ended early: %s", described)
