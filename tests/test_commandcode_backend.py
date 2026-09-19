"""The Command Code adapter without a Command Code install.

Everything here is answered from fakes -- the CLI probe and binary discovery
are patched in ``agent_backends``, which is exactly how the shared "every
backend" tests fence the other adapters off from the machine the suite runs
on. No Node start-up, no network, no account.
"""

from __future__ import annotations

import json
from pathlib import Path

import agent_backends
import commandcode_backend
import pytest
import session_history
from agent_backends import BACKEND_COMMANDCODE, BACKEND_IDS, BACKENDS
from commandcode_worker import CommandcodeWorker

STATUS_SIGNED_IN = "\u221a Authenticated as person\n  Provider: Command Code\n"

CATALOG = """Available models  \u00b7  4 models

Open Source

deepseek/deepseek-v4-pro               hybrid-attention long-context reasoning
z-ai/glm-5.3-flash                     fast, affordable GLM coding

Anthropic

claude-sonnet-5                        best combo of speed & intelligence (recommended)

Pass the full id, or just the short name after the last "/":
cmdc --model moonshotai/kimi-k2.5

Docs:  https://commandcode.ai/docs/reference/cli/models
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    return tmp_path


def _fake_probe(status=(0, STATUS_SIGNED_IN), models=(0, CATALOG)):
    def probe(_binary, args, _timeout):
        if args == ["--version"]:
            return 0, "1.53.1"
        if args == ["status"]:
            return status
        if args == ["--list-models"]:
            return models
        return 1, ""

    return probe


def _wire(monkeypatch, **kwargs):
    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _backend: "command-code")
    monkeypatch.setattr(agent_backends, "_probe_backend", _fake_probe(**kwargs))


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_the_registry_knows_command_code():
    assert BACKEND_COMMANDCODE in BACKEND_IDS
    assert agent_backends.backend_label("commandcode") == "Command Code"
    for spelling in ("commandcode", "command-code", "Command Code", "cmd"):
        assert agent_backends.normalize_backend(spelling) == BACKEND_COMMANDCODE
    assert agent_backends.worker_class(BACKEND_COMMANDCODE, object) is CommandcodeWorker


def test_the_executable_is_command_code_never_cmd():
    # On Windows `shutil.which("cmd")` finds System32's command interpreter,
    # not the agent, so the launcher name has to be one of the npm bins that
    # does not collide with it.
    info = BACKENDS[BACKEND_COMMANDCODE]
    assert info.executable == "command-code"
    assert info.install_command == "npm install -g command-code"
    assert info.login_args == ("login",)


def test_sign_in_is_run_in_a_terminal_ink_can_use():
    """`cmd login` mounts an Ink UI, which needs a TTY.

    Hidden behind the wizard's pipes, Ink refused to start ("Raw mode is not
    supported on the current process.stdin"), so no browser ever opened and
    the crash's own Ink documentation URL was read out as the address to sign
    in at. It needs a real terminal, and it needs nothing else from it -- the
    sign-in is the browser page the CLI opens -- so the wizard runs it in one
    nobody can see rather than opening a console window.
    """
    info = BACKENDS[BACKEND_COMMANDCODE]
    assert info.login_needs_terminal is True
    assert info.login_terminal_hidden is True


def test_capabilities_match_what_headless_mode_supports():
    info = BACKENDS[BACKEND_COMMANDCODE]
    assert info.supports_model is True
    assert info.supports_effort is True
    assert info.supports_permissions is True
    # BlindPilot supplies summary compaction.
    assert info.supports_compaction is True
    assert info.uploads_attachments is False
    assert agent_backends.compaction_request(BACKEND_COMMANDCODE)[1] == {"compact": True}


def test_history_is_registered():
    assert BACKEND_COMMANDCODE in session_history._LISTERS
    assert BACKEND_COMMANDCODE in session_history._READERS


def test_settings_files_are_offered(home):
    entries = agent_backends.settings_files(str(home / "project"))
    mine = [entry for entry in entries if entry.backend == BACKEND_COMMANDCODE]
    paths = {entry.path for entry in mine}
    assert home / ".commandcode" / "config.json" in paths
    assert home / "project" / ".commandcode" / "settings.json" in paths
    assert home / "project" / ".commandcode" / "settings.local.json" in paths
    scopes = {entry.scope for entry in mine}
    assert "global" in scopes
    assert any("personal" in entry.scope.lower() for entry in mine)
    assert all(entry.note.strip() for entry in mine)


# --------------------------------------------------------------------------
# Model catalog
# --------------------------------------------------------------------------


def test_the_catalog_is_read_from_its_sections():
    models = commandcode_backend._models_from_catalog(CATALOG)
    assert models == ["deepseek/deepseek-v4-pro", "z-ai/glm-5.3-flash", "claude-sonnet-5"]


def test_the_banner_and_the_docs_line_are_not_models():
    models = commandcode_backend._models_from_catalog(CATALOG)
    assert "Available" not in models
    assert "Docs:" not in models


def test_model_options_read_the_current_choice_from_config(home, monkeypatch):
    home.joinpath(".commandcode").mkdir(parents=True)
    home.joinpath(".commandcode", "config.json").write_text(
        json.dumps(
            {
                "model": "z-ai/glm-5.3-flash",
                "reasoningEffort": {"z-ai/glm-5.3-flash": "high"},
            }
        ),
        encoding="utf-8",
    )
    _wire(monkeypatch)

    models, efforts, current, current_effort, error = (
        commandcode_backend.commandcode_model_options()
    )

    assert "claude-sonnet-5" in models
    assert efforts == list(commandcode_backend.COMMANDCODE_EFFORTS)
    assert current == "z-ai/glm-5.3-flash"
    assert current_effort == "high"
    assert error == ""


def test_a_missing_cli_is_reported_not_guessed(home, monkeypatch):
    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _backend: None)
    models, _efforts, _current, _effort, error = commandcode_backend.commandcode_model_options()
    assert models == []
    assert "not found" in error


# --------------------------------------------------------------------------
# Sign-in
# --------------------------------------------------------------------------


def test_signed_in_comes_from_the_status_command(home, monkeypatch):
    _wire(monkeypatch)
    assert commandcode_backend.commandcode_auth_ok() is True
    assert commandcode_backend.commandcode_account_lines()[0] == "Signed in: yes"
    fields = commandcode_backend.commandcode_account_lines()
    assert "Account: person" in fields
    assert "Provider: Command Code" in fields


def test_a_refused_status_falls_back_to_the_stored_credential(home, monkeypatch):
    _wire(monkeypatch, status=(3, "not authenticated"))
    home.joinpath(".commandcode").mkdir(parents=True)
    home.joinpath(".commandcode", "auth.json").write_text(
        json.dumps({"userName": "person"}), encoding="utf-8"
    )
    assert commandcode_backend.commandcode_auth_ok() is True
    assert "Account: person" in commandcode_backend.commandcode_account_lines()


def test_no_credential_and_no_status_means_signed_out(home, monkeypatch):
    _wire(monkeypatch, status=(3, "not authenticated"))
    assert commandcode_backend.commandcode_auth_ok() is False
    assert commandcode_backend.commandcode_account_lines() == ["Signed in: no"]


# --------------------------------------------------------------------------
# The shared report
# --------------------------------------------------------------------------


def test_status_reports_the_account_and_version(home, monkeypatch):
    _wire(monkeypatch)
    report = agent_backends.backend_status(BACKEND_COMMANDCODE)
    fields = {
        caption.strip(): value.strip()
        for caption, _sep, value in (line.partition(":") for line in report.splitlines())
        if caption.strip()
    }
    assert fields["Backend"] == "Command Code"
    assert fields["Version"] == "1.53.1"
    assert fields["Signed in"] == "yes"
    assert fields["Account"] == "person"


def test_usage_is_not_invented(home, monkeypatch):
    # Command Code's usage lives behind the interactive /usage command and the
    # billing site; the headless surface reports none, so nothing is said.
    _wire(monkeypatch)
    assert agent_backends.backend_usage_lines(BACKEND_COMMANDCODE, "command-code") == []
