"""macOS keeps its files where a Mac user looks.

The Linux-style ~/.config and ~/.local/share folders were never a Mac's own
convention: settings now live in ~/Library/Application Support/BlindPilot and
managed runtimes in .../BlindPilot/data, and an install that predates the move
is relocated once, without losing anything or overwriting the new home.

These tests run anywhere (the platform is faked), because the migration must
not depend on a real Mac to be verified.
"""

from __future__ import annotations

import sys

import pytest

import agent_backends


@pytest.fixture
def darwin_home(monkeypatch, tmp_path):
    """A fake user with a fake macOS platform."""
    monkeypatch.setattr(agent_backends.platform, "system", lambda: "Darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    # The macOS path never consults XDG_* (that is the Linux layout), but the
    # other tests in this file switch to Linux, so the variables must be
    # gone there rather than inherited from the host. Set, then delete -- the
    # `raising` keyword is not available on every pytest version.
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    return tmp_path


@pytest.mark.skipif(sys.platform == "win32", reason="Path.home() follows HOME only on POSIX")
def test_config_and_data_live_under_application_support(darwin_home):
    base = darwin_home / "Library" / "Application Support" / "BlindPilot"

    assert agent_backends.blindpilot_config_dir() == base
    assert agent_backends.blindpilot_data_dir() == base / "data"


@pytest.mark.skipif(sys.platform == "win32", reason="Path.home() follows HOME only on POSIX")
def test_legacy_folders_are_moved_once(darwin_home):
    legacy_config = darwin_home / ".config" / "blindpilot"
    legacy_config.mkdir(parents=True)
    legacy_data = darwin_home / ".local" / "share" / "blindpilot"
    (legacy_config / "config.json").write_text("{}", encoding="utf-8")
    (legacy_data / "npm" / "bin").mkdir(parents=True)

    agent_backends.migrate_macos_legacy_dirs()
    agent_backends.migrate_macos_legacy_dirs()  # idempotent

    base = darwin_home / "Library" / "Application Support" / "BlindPilot"
    assert (base / "config.json").read_text(encoding="utf-8") == "{}"
    assert (base / "data" / "npm" / "bin").is_dir()
    assert not legacy_config.exists()
    assert not legacy_data.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="Path.home() follows HOME only on POSIX")
def test_nothing_already_in_the_new_home_is_overwritten(darwin_home):
    base = darwin_home / "Library" / "Application Support" / "BlindPilot"
    base.mkdir(parents=True)
    (base / "config.json").write_text('{"new": true}', encoding="utf-8")
    legacy = darwin_home / ".config" / "blindpilot"
    legacy.mkdir(parents=True)
    (legacy / "config.json").write_text("{}", encoding="utf-8")
    (legacy / "freebuff-model.json").write_text("{}", encoding="utf-8")

    agent_backends.migrate_macos_legacy_dirs()

    assert (base / "config.json").read_text(encoding="utf-8") == '{"new": true}'
    assert (base / "freebuff-model.json").read_text(encoding="utf-8") == "{}"


@pytest.mark.skipif(sys.platform == "win32", reason="Path.home() follows HOME only on POSIX")
def test_no_migration_outside_macos(darwin_home, monkeypatch):
    monkeypatch.setattr(agent_backends.platform, "system", lambda: "Linux")
    legacy = darwin_home / ".config" / "blindpilot"
    legacy.mkdir(parents=True)
    (legacy / "config.json").write_text("{}", encoding="utf-8")

    agent_backends.migrate_macos_legacy_dirs()

    assert (legacy / "config.json").read_text(encoding="utf-8") == "{}"
    assert agent_backends.blindpilot_config_dir() == darwin_home / ".config" / "blindpilot"


@pytest.mark.skipif(sys.platform == "win32", reason="Path.home() follows HOME only on POSIX")
def test_a_partial_failure_leaves_everything_else_in_place(darwin_home, monkeypatch):
    legacy = darwin_home / ".config" / "blindpilot"
    legacy.mkdir(parents=True)
    (legacy / "config.json").write_text("{}", encoding="utf-8")
    (legacy / "freebuff-model.json").write_text("{}", encoding="utf-8")

    def fail_on_second(path: str, _dest: str):
        if path.endswith("freebuff-model.json"):
            raise OSError("simulated failure")
        return shutil_move(path, _dest)

    shutil_move = agent_backends.shutil.move
    monkeypatch.setattr(agent_backends.shutil, "move", fail_on_second)
    agent_backends.migrate_macos_legacy_dirs()

    base = darwin_home / "Library" / "Application Support" / "BlindPilot"
    assert (base / "config.json").read_text(encoding="utf-8") == "{}"
    # The entry that failed to move stayed where it was, not half-moved.
    assert (legacy / "freebuff-model.json").read_text(encoding="utf-8") == "{}"
    assert not (base / "freebuff-model.json").exists()
