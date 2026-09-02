"""Finding Hermes after its official installer has run.

Measured on the machine the installer actually ran on: it installs to
``%LOCALAPPDATA%\\hermes\\hermes-agent``, puts the launcher in
``%LOCALAPPDATA%\\hermes\\bin``, sets ``HERMES_HOME`` persistently, and prints
"[OK] hermes command ready". A BlindPilot process that was already running
when the installer ran has none of that in its environment, so discovery has
to know the layout from the installer itself — which it did not: the first
end-to-end run reported "hermes was not found afterwards" against a complete
install.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import hermes_backend


@pytest.fixture
def fake_profile(monkeypatch, tmp_path):
    """A Windows user profile the installer has written its layout into.

    The layout under test is the Windows installer's, so the platform is
    forced with it — these tests run in CI on macOS and Linux too, where the
    unforced discovery code would rightly take its POSIX branch and find
    nothing. (The same technique the installer-argv tests use.)"""
    home = tmp_path / "home"
    local = home / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("HERMES_HOME", "")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(hermes_backend.platform, "system", lambda: "Windows")
    return home, local


def test_the_launcher_in_hermes_bin_is_found(fake_profile):
    """The installer's bin directory: %LOCALAPPDATA%\\hermes\\bin."""
    _home, local = fake_profile
    launcher = local / "hermes" / "bin" / "hermes.exe"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("")

    assert hermes_backend.find_hermes_cli() == str(launcher)


def test_the_venv_launcher_is_found_through_scripts_on_windows(fake_profile):
    """The fallback had `venv/bin/hermes` even on Windows, where the venv
    layout is `venv\\Scripts\\hermes.exe`."""
    home, _local = fake_profile
    launcher = home / ".hermes" / "hermes-agent" / "venv" / "Scripts" / "hermes.exe"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("")

    assert hermes_backend.find_hermes_cli() == str(launcher)


def test_the_source_tree_under_localappdata_is_found_when_env_is_stale(fake_profile):
    """A process started before the installer ran has no HERMES_HOME, but the
    installer's default location can still be found on disk — and with it the
    interpreter that can drive the gateway."""
    home, local = fake_profile
    root = local / "hermes" / "hermes-agent"
    (root / "tui_gateway").mkdir(parents=True)
    python = root / "venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_text("")

    assert hermes_backend.hermes_source_root() == root
    assert hermes_backend.hermes_python() == str(python)


def test_the_installer_bin_dir_goes_on_the_search_path(monkeypatch, tmp_path):
    """`_hermes_binary_after_install` adds %LOCALAPPDATA%\\hermes\\bin to this
    process' PATH before searching, so `shutil.which` can see a launcher the
    installer put there after BlindPilot started."""
    added: list[Path] = []
    local = tmp_path / "local"
    hermes_bin = local / "hermes" / "bin"
    hermes_bin.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.setattr(hermes_backend.platform, "system", lambda: "Windows")
    import blindpilot_app as app

    monkeypatch.setattr(app.platform, "system", lambda: "Windows")
    monkeypatch.setattr(hermes_backend, "find_hermes_cli", lambda: "C:/l/hermes/bin/hermes.exe")
    monkeypatch.setattr(app, "_add_to_process_path", lambda p: added.append(Path(p)))

    assert app._hermes_binary_after_install() == "C:/l/hermes/bin/hermes.exe"
    assert hermes_bin in added
