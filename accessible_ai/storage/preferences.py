"""BlindPilot's own settings file: one JSON object, read whole and written whole.

Two halves of the application keep preferences in the same file: the window's
display switches, and the Account the Codex backend is set to use. This module
is the reading, the write-and-move-into-place, and the failure to write, once
for both, so a half-written file cannot cost somebody every setting at once.

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
from pathlib import Path
from typing import Iterable, Mapping


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
    """
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
