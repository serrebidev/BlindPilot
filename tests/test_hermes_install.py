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
