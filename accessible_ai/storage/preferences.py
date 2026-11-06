"""BlindPilot's own settings file: one JSON object, read whole and written whole.

Two halves of the application keep preferences in the same file: the window's
display switches, and the Account the Codex backend is set to use. This module
is the reading, the write-and-move-into-place, the read-modify-write, and the
failure to write, once for both, so a half-written file cannot cost somebody
every setting at once, and neither half can cost the other the settings it
saved in between.

Every function takes the path it works on. Where the file lives is a platform
decision that belongs with the rest of them (see
``agent_backends.blindpilot_config_dir``), not with this.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Iterable, Mapping


# One lock around every write, because one file has two writers. Both halves of
# the application save into it -- the window's display switches and the Account
# the Codex backend uses -- and two of those landing together would otherwise
# write over each other. Re-entrant so `update_config` can do its save through
# `save_config` while holding it.
_WRITE_LOCK = threading.RLock()


def load_config(paths: Iterable[Path]) -> dict:
    """The first of these settings files that can be read, or an empty dict.

    The order is the caller's: the current file first, then any older one it
    has not been migrated from yet.
    """
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            continue
    return {}


def save_config(path: Path, cfg: Mapping) -> bool:
    """Write the settings. False if they did not get written.

    Callers announce what they saved, so they need to know. Written to one
    side and moved into place, so an interruption partway through cannot
    leave a half-written file: `load_config` cannot parse one of those and
    falls back, or starts over from empty.

    The whole file is replaced, so a caller that read it a while ago and saved
    the answer later costs everything written in between. `update_config` is
    the save for one half of a shared file; this is the one for a save that is
    meant to be the whole of it.
    """
    with _WRITE_LOCK:
        temporary = path.with_name(path.name + ".new")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(temporary, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, indent=2)
            os.replace(temporary, path)
            return True
        except (OSError, ValueError, TypeError):
            logging.getLogger("blindpilot").warning("could not write the settings to %s", path)
            try:
                os.unlink(temporary)
            except OSError:
                pass
            return False


def update_config(path: Path, changes: Mapping) -> bool:
    """Change only these settings, read and written under the one write lock.

    A value of ``None`` removes its key. Everything else in the file is left
    exactly as it was found, which is what two halves saving into one file
    need: the read and the write are one step, so a save cannot be built from
    a copy read before the other half's write and then put back over it.

    False if the file refused the write, the way `save_config` says it.
    """
    with _WRITE_LOCK:
        cfg = load_config((path,))
        for key, value in changes.items():
            if value is None:
                cfg.pop(key, None)
            else:
                cfg[key] = value
        return save_config(path, cfg)
