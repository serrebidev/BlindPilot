"""Installing Hermes from the setup wizard, on every platform.

Hermes ships official script installers rather than an npm package —
`irm .../install.ps1 | iex` through PowerShell on native Windows, and
`curl -fsSL .../install.sh | bash` through curl on macOS, Linux and WSL2 —
the same shape Claude's installer already uses in this codebase. The wizard
used to refuse to offer Install for Hermes at all, because the only install
machinery was npm's.

Success is measured the way `install_claude` measures it: not by the
installer's exit code, but by whether `hermes` can be found afterwards.
"""

from __future__ import annotations

from pathlib import Path

import blindpilot_app as app
from agent_backends import BACKEND_HERMES


class _Patch:
    """The `with _Patch(...)` helper test_cli_install.py uses, local here."""

    def __init__(self, **patches):
        self._patches = patches
        self._saved: list = []

    def __enter__(self):
        for name, value in self._patches.items():
            self._saved.append((name, getattr(app, name)))
            setattr(app, name, value)
        return self

    def __exit__(self, *_exc):
        for name, value in reversed(self._saved):
            setattr(app, name, value)
        return False


HERMES_PS1 = "https://hermes-agent.nousresearch.com/install.ps1"
HERMES_SH = "https://hermes-agent.nousresearch.com/install.sh"


def test_hermes_install_argv_uses_the_official_powershell_one_liner(monkeypatch):
    monkeypatch.setattr(app.platform, "system", lambda: "Windows")
    monkeypatch.setattr(app, "_powershell_exe", lambda: "C:/Windows/powershell.exe")

    argv = app._hermes_install_argv()

    assert argv is not None
    assert argv[0] == "C:/Windows/powershell.exe"
    assert HERMES_PS1 in argv[-1]
    assert "iex" in argv[-1]


def test_hermes_install_argv_uses_the_official_shell_one_liner(monkeypatch):
    monkeypatch.setattr(app.platform, "system", lambda: "Linux")

    original_which = app.shutil.which

    def which(name):
        if name in ("curl", "bash"):
            return f"/usr/bin/{name}"
        return original_which(name)

    monkeypatch.setattr(app.shutil, "which", which)

    argv = app._hermes_install_argv()

    assert argv is not None
    assert argv[0] == "/usr/bin/bash"
    assert f"curl -fsSL {HERMES_SH} | bash" in argv[-1]


def test_hermes_install_argv_is_none_without_prerequisites(monkeypatch):
    monkeypatch.setattr(app.platform, "system", lambda: "Windows")
    monkeypatch.setattr(app, "_powershell_exe", lambda: None)
    assert app._hermes_install_argv() is None

    monkeypatch.setattr(app.platform, "system", lambda: "Linux")
    monkeypatch.setattr(app.shutil, "which", lambda _name: None)
    assert app._hermes_install_argv() is None


def test_hermes_install_missing_prereq_message_names_the_missing_tool():
    monkeypatch_message = app._hermes_missing_prereq_message()
    assert "PowerShell" in monkeypatch_message or "curl" in monkeypatch_message


def test_install_hermes_runs_the_installer_and_reports_the_found_binary():
    log: list[str] = []
    runs: list[list[str]] = []
    found = str(Path("C:/Users/u/.local/bin/hermes.exe"))

    with _Patch(
        _hermes_install_argv=lambda: ["powershell.exe", "-Command", "install"],
        _run_logged_process=lambda argv, _log, env=None: runs.append(list(argv)) or 0,
        _add_to_process_path=lambda _path: None,
        _hermes_binary_after_install=lambda: found,
    ):
        result = app.install_hermes(log.append)

    assert result == found
    assert runs == [["powershell.exe", "-Command", "install"]]
    assert any("Hermes" in line for line in log)


def test_install_hermes_measures_success_by_the_binary_not_the_exit_code():
    """The installer's exit code is advisory; a working `hermes` is the fact."""
    found = "/home/u/.local/bin/hermes"

    with _Patch(
        _hermes_install_argv=lambda: ["bash", "-c", "install"],
        _run_logged_process=lambda _argv, _log, env=None: 3,
        _add_to_process_path=lambda _path: None,
        _hermes_binary_after_install=lambda: found,
    ):
        assert app.install_hermes(lambda _line: None) == found


def test_install_hermes_says_what_is_missing_when_prerequisites_are_absent():
    log: list[str] = []

    with _Patch(
        _hermes_install_argv=lambda: None,
        _run_logged_process=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("ran")),
    ):
        assert app.install_hermes(log.append) is None

    assert log and ("PowerShell" in log[0] or "curl" in log[0])


def test_install_hermes_reports_failure_when_nothing_is_found_afterwards():
    log: list[str] = []

    with _Patch(
        _hermes_install_argv=lambda: ["bash", "-c", "install"],
        _run_logged_process=lambda _argv, _log, env=None: 0,
        _add_to_process_path=lambda _path: None,
        _hermes_binary_after_install=lambda: None,
    ):
        assert app.install_hermes(log.append) is None

    assert any("not found afterwards" in line for line in log)


# --- the Python that Hermes' installer bootstraps with uv -----------------
#
# A user on a machine whose AppData is cloud-synced reported the installer
# dying at "Downloading Python 3.14" with
# "Failed to create Python minor version link directory ... (os error 448)" --
# Windows refusing to place a program under a folder it does not trust -- and
# BlindPilot then reporting only "exit code 0 but hermes was not found". uv
# puts its managed Pythons under %APPDATA% by default; the fix is to ask for
# the local app-data folder, which no sync client owns.


def _clean_windows(monkeypatch, tmp_path):
    """A Windows box with nothing configured and nothing synced."""
    monkeypatch.setattr(app.platform, "system", lambda: "Windows")
    monkeypatch.delenv(app.UV_PYTHON_DIR_ENV, raising=False)
    monkeypatch.delenv("OneDrive", raising=False)
    monkeypatch.delenv("OneDriveConsumer", raising=False)
    monkeypatch.delenv("OneDriveCommercial", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))


def test_uv_python_folder_default_is_the_roaming_one_that_breaks(tmp_path, monkeypatch):
    """The reason this exists: uv's own answer is the roaming folder."""
    _clean_windows(monkeypatch, tmp_path)
    assert app._uv_roaming_python_dir() == tmp_path / "Roaming" / "uv" / "python"
    assert app._uv_local_python_dir() == tmp_path / "Local" / "uv" / "python"


def test_an_ordinary_folder_is_not_reported_as_synced(tmp_path):
    """The walk must not fire on a machine that never had the problem."""
    nested = tmp_path / "uv" / "python"
    assert app._folder_is_cloud_synced(tmp_path) is False
    assert app._folder_is_cloud_synced(nested) is False


def test_a_folder_inside_one_drive_is_reported_as_synced(tmp_path, monkeypatch):
    """OneDrive names its own root, which catches it before placeholders do."""
    monkeypatch.setenv("OneDrive", str(tmp_path))
    assert app._folder_is_cloud_synced(tmp_path / "AppData" / "Roaming" / "uv") is True


def test_a_reparse_point_on_the_way_makes_a_folder_unusable(tmp_path, monkeypatch):
    """The other signal: a step on the path that Windows will not traverse."""
    target = tmp_path / "Roaming" / "uv" / "python"
    monkeypatch.setattr(
        app,
        "_windows_file_attributes",
        lambda path: 0x400 if "Roaming" in str(path) else 0,
    )
    assert app._folder_is_cloud_synced(target) is True


def test_hermes_installer_env_moves_uvs_python_off_the_roaming_folder(monkeypatch, tmp_path):
    _clean_windows(monkeypatch, tmp_path)
    log: list[str] = []

    env = app._hermes_installer_env(log.append)

    assert env == {app.UV_PYTHON_DIR_ENV: str(tmp_path / "Local" / "uv" / "python")}
    assert log == []  # a healthy machine has nothing to hear


def test_hermes_installer_env_leaves_a_chosen_folder_alone(monkeypatch, tmp_path):
    """Someone who already set the variable knows where they want it."""
    _clean_windows(monkeypatch, tmp_path)
    monkeypatch.setenv(app.UV_PYTHON_DIR_ENV, str(tmp_path / "mine"))

    assert app._hermes_installer_env(lambda _line: None) is None


def test_hermes_installer_env_does_nothing_off_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(app.platform, "system", lambda: "Linux")
    monkeypatch.delenv(app.UV_PYTHON_DIR_ENV, raising=False)

    assert app._hermes_installer_env(lambda _line: None) is None


def test_hermes_installer_env_is_remembered_when_roaming_is_synced(monkeypatch, tmp_path):
    """A terminal-launched `hermes update` bootstraps again with no help from us.

    So when the roaming folder is known to be the problem, the variable is
    written to the user environment as well as handed to this one installer.
    """
    _clean_windows(monkeypatch, tmp_path)
    remembered: list[tuple[str, str]] = []
    monkeypatch.setattr(app, "_folder_is_cloud_synced", lambda path: "Roaming" in str(path))
    monkeypatch.setattr(
        app, "_remember_windows_env_var", lambda name, value: remembered.append((name, value))
    )
    log: list[str] = []

    env = app._hermes_installer_env(log.append)

    local = str(tmp_path / "Local" / "uv" / "python")
    assert env == {app.UV_PYTHON_DIR_ENV: local}
    assert remembered == [(app.UV_PYTHON_DIR_ENV, local)]
    assert any("synced" in line and "local" in line.lower() for line in log)


def test_hermes_installer_env_says_so_when_both_folders_are_synced(monkeypatch, tmp_path):
    """No automatic answer exists, so name the setting to change instead."""
    _clean_windows(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "_folder_is_cloud_synced", lambda _path: True)
    log: list[str] = []

    assert app._hermes_installer_env(log.append) is None

    assert any("UV_PYTHON_INSTALL_DIR" in line for line in log)


def test_hermes_bootstrap_failure_names_the_refused_folder_from_the_real_output():
    """The user's own log, with its Polish sentence left in it.

    The message around the failure is localised and the installer exits 0, so
    the error number is the only thing here that survives translation.
    """
    heard = [
        "-> Downloading Python 3.14",
        "error: Failed to create Python minor version link directory",
        "  Caused by: Nie można przejść do tej ścieżki, ponieważ zawiera ona "
        "niezaufany punkt instalacji. (os error 448)",
        "[X] bootstrap Python installation failed",
    ]

    message = app._hermes_bootstrap_failure(heard)

    assert "cloud-sync" in message
    assert "UV_PYTHON_INSTALL_DIR" in message


def test_hermes_bootstrap_failure_stays_quiet_about_other_output():
    assert app._hermes_bootstrap_failure(["everything is fine"]) == ""


def test_install_hermes_tells_the_user_what_to_do_when_windows_refused_the_folder():
    """The report this whole path answers to: exit code 0, no hermes, no clue."""
    log: list[str] = []

    def installer(_argv, log_line, env=None):
        log_line("error: Failed to create Python minor version link directory (os error 448)")
        return 0

    with _Patch(
        _hermes_install_argv=lambda: ["powershell.exe", "-Command", "install"],
        _run_logged_process=installer,
        _hermes_installer_env=lambda _log: None,
        _add_to_process_path=lambda _path: None,
        _hermes_binary_after_install=lambda: None,
    ):
        assert app.install_hermes(log.append) is None

    assert any("UV_PYTHON_INSTALL_DIR" in line for line in log)


def test_install_hermes_passes_the_python_folder_to_the_installer(monkeypatch, tmp_path):
    _clean_windows(monkeypatch, tmp_path)
    seen: dict = {}

    def installer(_argv, _log, env=None):
        seen["env"] = env
        return 0

    with _Patch(
        _hermes_install_argv=lambda: ["powershell.exe", "-Command", "install"],
        _run_logged_process=installer,
        _add_to_process_path=lambda _path: None,
        _hermes_binary_after_install=lambda: (
            "C:/Users/u/.hermes/hermes-agent/venv/Scripts/hermes.exe"
        ),
    ):
        app.install_hermes(lambda _line: None)

    assert seen["env"] == {app.UV_PYTHON_DIR_ENV: str(tmp_path / "Local" / "uv" / "python")}


def test_update_backend_updates_hermes_in_the_same_environment(monkeypatch, tmp_path):
    """An update bootstraps Python the same way an install does, so it needs
    the same answer or it fails on the machine that already failed once."""
    _clean_windows(monkeypatch, tmp_path)
    seen: dict = {}

    def installer(_argv, _log, env=None):
        seen["env"] = env
        return 0

    with _Patch(
        _find_claude=lambda: None,
        find_backend_cli=lambda _backend: "C:/Users/u/.hermes/hermes-agent/venv/Scripts/hermes.exe",
        _hermes_install_argv=lambda: ["powershell.exe", "-Command", "install"],
        _run_logged_process=installer,
        _add_to_process_path=lambda _path: None,
        _hermes_binary_after_install=lambda: (
            "C:/Users/u/.hermes/hermes-agent/venv/Scripts/hermes.exe"
        ),
    ):
        assert app.update_backend(BACKEND_HERMES, lambda _line: None) is True

    assert seen["env"] == {app.UV_PYTHON_DIR_ENV: str(tmp_path / "Local" / "uv" / "python")}


def test_install_backend_installs_hermes_through_its_own_installer(monkeypatch):
    seen: dict = {}

    def fake_install_hermes(log):
        seen["called"] = True
        return "C:/Users/u/.local/bin/hermes.exe"

    monkeypatch.setattr(app, "install_hermes", fake_install_hermes)

    assert app.install_backend(BACKEND_HERMES, lambda _line: None) == (
        "C:/Users/u/.local/bin/hermes.exe"
    )
    assert seen["called"]


def test_update_backend_updates_hermes_with_the_official_installer(monkeypatch):
    """The Update path had the same hole as Install: it reported that npm
    could not be found for a backend that has no npm package."""
    log: list[str] = []
    binary = "C:/Users/u/.local/bin/hermes.exe"

    with _Patch(
        _find_claude=lambda: None,
        find_backend_cli=lambda _backend: binary,
        _hermes_install_argv=lambda: ["powershell.exe", "-Command", "install"],
        _run_logged_process=lambda _argv, _log, env=None: 0,
        _add_to_process_path=lambda _path: None,
        _hermes_binary_after_install=lambda: binary,
    ):
        assert app.update_backend(BACKEND_HERMES, log.append) is True

    assert not any("npm" in line for line in log)
    assert any("up to date" in line for line in log)
