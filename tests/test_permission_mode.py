"""Unit tests for where a session's permission mode comes from.

BlindPilot runs its backends hands-off. A new tab starts at the mode you last
chose in this app; if you have never chosen one, it starts fully automatic,
because a run that stops to ask a question nobody is watching for is a run that
never finishes.

Run from the project root:

    python -m pytest tests/ -q
    # or, with no pytest installed:
    python tests/test_permission_mode.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import blindpilot_app  # noqa: E402
from blindpilot_app import (  # noqa: E402
    DEFAULT_PERMISSION_MODE,
    _default_permission_mode,
    _remember_permission_mode,
    adopt_full_auto_default,
)


class _Sandbox:
    """Temp project directory, with the app's own config stubbed."""

    def __init__(self, saved=None):
        self._saved = saved
        self._tmp = tempfile.TemporaryDirectory()

    def __enter__(self):
        root = Path(self._tmp.name)
        self.project = root / "project"
        self.project.mkdir()

        # The reader's own config.json is stubbed so tests never touch the
        # real one, and never write to it.
        self._writes = []
        self._old_load = blindpilot_app._load_config
        self._old_save = blindpilot_app._save_config
        cfg = {"permission_mode": self._saved} if self._saved else {}
        blindpilot_app._load_config = lambda: dict(cfg)
        blindpilot_app._save_config = lambda c: self._writes.append(dict(c))
        return self

    def __exit__(self, *exc):
        blindpilot_app._load_config = self._old_load
        blindpilot_app._save_config = self._old_save
        self._tmp.cleanup()
        return False

    @property
    def writes(self):
        return self._writes


def test_full_auto_is_where_a_new_tab_starts():
    with _Sandbox() as box:
        assert DEFAULT_PERMISSION_MODE == "bypassPermissions"
        assert _default_permission_mode(str(box.project)) == "bypassPermissions"


def test_every_backend_starts_full_auto():
    with _Sandbox() as box:
        for backend in ("claude", "codex", "freebuff", "opencode"):
            assert _default_permission_mode(str(box.project), backend) == "bypassPermissions"


def test_your_saved_choice_wins():
    with _Sandbox(saved="plan") as box:
        assert _default_permission_mode(str(box.project)) == "plan"


def test_a_stale_saved_choice_is_ignored():
    with _Sandbox(saved="noSuchMode") as box:
        assert _default_permission_mode(str(box.project)) == "bypassPermissions"


def test_changing_the_mode_is_written_to_config():
    with _Sandbox() as box:
        _remember_permission_mode("plan")
        assert box.writes == [{"permission_mode": "plan"}]


def test_an_unchanged_mode_is_not_rewritten():
    with _Sandbox(saved="plan") as box:
        _remember_permission_mode("plan")
        assert box.writes == []


def test_a_bogus_mode_is_never_saved():
    with _Sandbox() as box:
        _remember_permission_mode("noSuchMode")
        assert box.writes == []


def test_an_old_config_is_moved_onto_full_auto():
    cfg = {"permission_mode": "default", "backend": "codex"}
    assert adopt_full_auto_default(cfg) is True
    assert cfg["permission_mode"] == "bypassPermissions"
    # Everything else in the config is left alone.
    assert cfg["backend"] == "codex"


def test_a_config_with_no_mode_at_all_is_moved_too():
    cfg = {}
    assert adopt_full_auto_default(cfg) is True
    assert cfg["permission_mode"] == "bypassPermissions"


def test_the_move_happens_once_and_then_leaves_your_choice_alone():
    cfg = {}
    adopt_full_auto_default(cfg)
    cfg["permission_mode"] = "plan"  # chosen in the picker afterwards
    assert adopt_full_auto_default(cfg) is False
    assert cfg["permission_mode"] == "plan"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if failures else 0)
