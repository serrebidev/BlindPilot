"""Installing Muse and muse.ai on a fresh computer, on every platform.

Three fresh-computer failures, one per layer:

- Windows: the Muse install command was refused unless Muse was
  already installed inside WSL, so a first install could never start.
- macOS and Linux: the Muse installer drops its launcher in
  ~/.local/bin (or $MUSE_INSTALL_DIR) and only fixes the PATH of shells
  started afterwards, so discovery -- which asked PATH alone -- reported
  a successful install as missing, and a GUI process could never see it.
- muse.ai: muse-cli installs with uv or pip, and a fresh computer has
  neither -- a frozen BlindPilot cannot use its own executable as a
  Python, and the python.exe on a fresh Windows PATH is the Microsoft
  Store stub. The install now bootstraps uv with its official installer
  first, and the stub is not mistaken for a Python.
"""

from __future__ import annotations

import sys

import blindpilot_app as app
import muse_backend
import museai_backend


def test_muse_install_argv_windows_needs_only_wsl(monkeypatch):
    """A first install inside WSL must be offered when nothing is installed."""
    monkeypatch.setattr(app.platform, "system", lambda: "Windows")
    monkeypatch.setattr(app, "muse_wsl_exe", lambda: "C:/Windows/System32/wsl.exe")
    monkeypatch.setattr(app, "muse_installed", lambda: False)

    argv = app._muse_install_argv()

    assert argv is not None
    assert argv[0] == "C:/Windows/System32/wsl.exe"
    assert app.MUSE_INSTALL_SH_URL in argv[-1]


def test_muse_install_argv_windows_is_none_without_wsl(monkeypatch):
    monkeypatch.setattr(app.platform, "system", lambda: "Windows")
    monkeypatch.setattr(app, "muse_wsl_exe", lambda: None)

    assert app._muse_install_argv() is None


def test_muse_install_argv_posix_uses_the_official_installer(monkeypatch):
    monkeypatch.setattr(app.platform, "system", lambda: "Linux")
    monkeypatch.setattr(app.shutil, "which", lambda name: f"/usr/bin/{name}")

    argv = app._muse_install_argv()

    assert argv is not None
    assert f"curl -fsSL {app.MUSE_INSTALL_SH_URL} | bash" in argv[-1]


def _posix_muse_backend(monkeypatch, tmp_path):
    """Run muse_backend's POSIX branch with tmp_path as the home folder.

    Forced explicitly: on a Windows runner the real branch is the WSL
    one, and Path.home() there ignores $HOME, so both are patched.
    """
    monkeypatch.setattr(muse_backend.platform, "system", lambda: "Linux")
    monkeypatch.setattr(muse_backend.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("MUSE_INSTALL_DIR", raising=False)


def test_muse_cli_path_finds_the_installers_folder_without_path(monkeypatch, tmp_path):
    """The launcher counts as installed even when PATH never heard of it."""
    launcher = tmp_path / ".local" / "bin" / "muse"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n")
    _posix_muse_backend(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda _name: None)

    assert muse_backend.muse_cli_path() == str(launcher)
    assert muse_backend.muse_installed()


def test_muse_cli_path_honours_muse_install_dir(monkeypatch, tmp_path):
    custom = tmp_path / "elsewhere"
    custom.mkdir()
    launcher = custom / "muse"
    launcher.write_text("#!/bin/sh\n")
    _posix_muse_backend(monkeypatch, tmp_path)
    monkeypatch.setenv("MUSE_INSTALL_DIR", str(custom))
    monkeypatch.setattr("shutil.which", lambda _name: None)

    assert muse_backend.muse_cli_path() == str(launcher)


def test_muse_cli_path_still_prefers_path(monkeypatch, tmp_path):
    _posix_muse_backend(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda _name: "/opt/bin/muse")

    assert muse_backend.muse_cli_path() == "/opt/bin/muse"


def test_museai_argv_uses_uv_from_the_per_user_bin(monkeypatch, tmp_path):
    """uv installed by its own installer counts even off the GUI's PATH."""
    name = "uv.exe" if museai_backend.platform.system() == "Windows" else "uv"
    uv = tmp_path / ".local" / "bin" / name
    uv.parent.mkdir(parents=True)
    uv.write_text("")
    monkeypatch.setattr(museai_backend.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(museai_backend.shutil, "which", lambda _name: None)

    argv = museai_backend.museai_install_argv()

    assert argv == [str(uv), "tool", "install", "muse-cli"]


def test_museai_argv_skips_the_windows_store_stub(monkeypatch, tmp_path):
    monkeypatch.setattr(museai_backend.platform, "system", lambda: "Windows")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    stub = "C:/Users/u/AppData/Local/Microsoft/WindowsApps/python3.exe"
    found = {
        "python3": stub,
        "python": stub.replace("python3", "python"),
        "py": "C:/Windows/py.exe",
    }
    monkeypatch.setattr(museai_backend.shutil, "which", lambda name: found.get(name))
    monkeypatch.setattr(museai_backend.Path, "home", lambda: tmp_path)

    argv = museai_backend.museai_install_argv()

    assert argv is not None
    assert argv[0] == "C:/Windows/py.exe"


def test_museai_argv_is_none_with_no_uv_and_no_real_python(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(museai_backend.shutil, "which", lambda _name: None)
    monkeypatch.setattr(museai_backend.Path, "home", lambda: tmp_path)

    assert museai_backend.museai_install_argv() is None


def test_install_museai_bootstraps_uv_on_a_fresh_computer(monkeypatch):
    """No uv and no Python: uv is installed, then muse-cli through it."""
    calls = {"argv": 0}

    def argv_after_bootstrap(upgrade=False):
        calls["argv"] += 1
        if calls["argv"] == 1:
            return None
        return ["uv", "tool", "upgrade" if upgrade else "install", "muse-cli"]

    monkeypatch.setattr(museai_backend, "museai_install_argv", argv_after_bootstrap)
    monkeypatch.setattr(app, "_install_uv", lambda _log: True)
    monkeypatch.setattr(app, "_run_logged_process", lambda _argv, _log, env=None: 0)
    monkeypatch.setattr(app, "find_backend_cli", lambda _backend: "/home/u/.local/bin/muse-cli")

    log: list[str] = []
    assert app.install_museai(log.append) == "/home/u/.local/bin/muse-cli"


def test_install_museai_reports_the_prereq_when_the_bootstrap_fails(monkeypatch):
    monkeypatch.setattr(museai_backend, "museai_install_argv", lambda upgrade=False: None)
    monkeypatch.setattr(app, "_install_uv", lambda _log: False)

    log: list[str] = []
    assert app.install_museai(log.append) is None
    assert any("uv" in line for line in log)
