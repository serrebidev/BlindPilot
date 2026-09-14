"""Read-only CLI probes must never spawn Command Code's detached updater."""

import io

import agent_backends
import blindpilot_app as app


def test_every_backend_child_disables_commandcode_self_updates(monkeypatch):
    monkeypatch.setattr(agent_backends, "login_shell_path_dirs", list)
    monkeypatch.setenv("COMMANDCODE_SKIP_UPDATES", "")
    for binary in ("command-code.cmd", "commandcode", "npm.cmd", "claude"):
        assert agent_backends.subprocess_env(binary)["COMMANDCODE_SKIP_UPDATES"] == "1"


def test_status_model_and_version_probes_are_hidden_and_disable_updates(monkeypatch):
    calls = []
    monkeypatch.setattr(agent_backends, "login_shell_path_dirs", list)

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    monkeypatch.setattr(agent_backends.subprocess, "run", run)
    for argument in ("status", "--list-models", "--version"):
        agent_backends._probe_backend("command-code.cmd", [argument], 30)
    for _argv, kwargs in calls:
        assert kwargs["stdin"] == agent_backends.subprocess.DEVNULL
        assert kwargs["env"]["COMMANDCODE_SKIP_UPDATES"] == "1"
        for key, value in agent_backends.no_window_kwargs().items():
            assert kwargs[key] == value


def test_npm_update_is_noninteractive_hidden_and_logged(monkeypatch):
    calls = []
    monkeypatch.setattr(agent_backends, "login_shell_path_dirs", list)
    output = io.StringIO("updated successfully\n")

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return type("Process", (), {"stdout": output, "wait": lambda _self: 0})()

    monkeypatch.setattr(app.subprocess, "Popen", popen)
    logs = []
    env = app._npm_environment("npm.cmd")
    assert (
        app._run_logged_process(["npm.cmd", "install", "command-code@latest"], logs.append, env)
        == 0
    )
    assert logs == ["updated successfully"]
    kwargs = calls[0][1]
    assert kwargs["stdin"] == app.subprocess.DEVNULL
    assert kwargs["env"]["CI"] == "1"
    assert kwargs["env"]["npm_config_yes"] == "true"
    assert kwargs["env"]["COMMANDCODE_SKIP_UPDATES"] == "1"
    for key, value in app._no_window_kwargs().items():
        assert kwargs[key] == value
