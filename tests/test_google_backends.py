# SPDX-License-Identifier: MIT
"""Gemini CLI and Antigravity CLI: commands, streams, keys and settings.

The stream shapes below are the ones the real CLIs wrote (Gemini CLI 0.62.0,
agy 1.2.17), copied from runs against them, so a test here fails when this
adapter stops reading what the CLIs actually say.
"""

from __future__ import annotations

import json
import stat
import sys
import textwrap
from pathlib import Path

import pytest

import agent_backends
import google_backend
import google_worker
from accessible_ai.providers.chat_completions import is_gemini_chat_model
from agent_backends import (
    BACKEND_ANTIGRAVITY,
    BACKEND_GEMINI,
    backend_auth_ok,
    normalize_backend,
    worker_class,
)
from google_worker import AntigravityWorker, GeminiWorker


class Recorder:
    def __init__(self) -> None:
        self.sessions: list[str] = []
        self.activity: list[tuple[str, str]] = []
        self.completed: list[str] = []
        self.failed: list[str] = []
        self.subagents: list[tuple] = []
        self.started = 0
        self.done = 0

    def callbacks(self) -> dict:
        return {
            "on_session": self.sessions.append,
            "on_started": self._started,
            "on_activity": lambda kind, text: self.activity.append((kind, text)),
            "on_complete": self.completed.append,
            "on_failed": self.failed.append,
            "on_done": self._done,
            "on_subagent": lambda *report: self.subagents.append(report),
        }

    def _started(self) -> None:
        self.started += 1

    def _done(self) -> None:
        self.done += 1

    def said(self) -> str:
        return "".join(text for kind, text in self.activity if kind == "assistant")


def _worker(cls, recorder: Recorder, *, mode="bypassPermissions", session=None, **kwargs):
    return cls("Fix the bug", session, "/project", mode, **kwargs, **recorder.callbacks())


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_backends.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return tmp_path


def _gemini_signed_in(home: Path, account: str = "") -> None:
    gemini = home / ".gemini"
    gemini.mkdir(exist_ok=True)
    (gemini / "settings.json").write_text(
        json.dumps({"security": {"auth": {"selectedType": "oauth-personal"}}}), encoding="utf-8"
    )
    (gemini / "oauth_creds.json").write_text(json.dumps({"refresh_token": "r"}), encoding="utf-8")
    if account:
        (gemini / "google_accounts.json").write_text(
            json.dumps({"active": account}), encoding="utf-8"
        )


# -- registration ------------------------------------------------------------


def test_names_resolve_to_the_new_backends():
    assert normalize_backend("Gemini CLI") == BACKEND_GEMINI
    assert normalize_backend("agy") == BACKEND_ANTIGRAVITY
    assert normalize_backend("Google Antigravity") == BACKEND_ANTIGRAVITY
    assert worker_class(BACKEND_GEMINI, object) is GeminiWorker
    assert worker_class(BACKEND_ANTIGRAVITY, object) is AntigravityWorker


def test_agy_is_found_where_its_windows_installer_puts_it(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_backends.platform, "system", lambda: "Windows")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert tmp_path / "agy" / "bin" / "agy.exe" in agent_backends._fallback_cli_paths("agy")


# -- commands ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("bypassPermissions", "yolo"),
        ("acceptEdits", "auto_edit"),
        ("plan", "plan"),
        ("default", "default"),
    ],
)
def test_gemini_command_maps_the_permission_mode(mode, expected):
    worker = _worker(GeminiWorker, Recorder(), mode=mode)
    command = worker._command("gemini")
    assert command[:5] == [
        "gemini",
        "--output-format",
        "stream-json",
        "--skip-trust",
        "--approval-mode",
    ]
    assert command[5] == expected


def test_gemini_command_carries_model_and_session():
    worker = _worker(GeminiWorker, Recorder(), session="abc-123", model="flash")
    command = worker._command("gemini")
    assert command[-4:] == ["--model", "flash", "--resume", "abc-123"]
    # The prompt is stdin, never an argument.
    assert "Fix the bug" not in command
    assert worker._stdin_text() == "Fix the bug\n"


@pytest.mark.parametrize(
    ("mode", "flags"),
    [
        ("bypassPermissions", ["--dangerously-skip-permissions"]),
        ("acceptEdits", ["--mode", "accept-edits"]),
        ("plan", ["--mode", "plan"]),
        ("default", []),
    ],
)
def test_antigravity_command_maps_the_permission_mode(mode, flags):
    command = _worker(AntigravityWorker, Recorder(), mode=mode)._command("agy")
    base = [
        "agy",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--disable-slash-commands",
    ]
    assert command == base + flags


def test_antigravity_command_carries_model_effort_session_and_folders():
    worker = _worker(
        AntigravityWorker,
        Recorder(),
        mode="default",
        session="conv-1",
        model="gemini-3.8-flash-high",
        effort="xhigh",
        additional_dirs=("/other",),
    )
    assert worker._command("agy")[6:] == [
        "--model",
        "gemini-3.8-flash-high",
        "--effort",
        "xhigh",
        "--conversation",
        "conv-1",
        "--add-dir",
        "/other",
    ]
    assert json.loads(worker._stdin_text()) == {
        "event": "user",
        "message": {"content": "Fix the bug"},
    }


# -- Gemini CLI stream ---------------------------------------------------------


def test_gemini_stream_becomes_a_spoken_turn():
    recorder = Recorder()
    worker = _worker(GeminiWorker, recorder)
    frames = [
        {"type": "init", "session_id": "s-1", "model": "auto"},
        {"type": "message", "role": "user", "content": "Fix the bug\n"},
        {"type": "message", "role": "assistant", "content": "Reading it now. ", "delta": True},
        {
            "type": "tool_use",
            "tool_name": "read_file",
            "tool_id": "t1",
            "parameters": {"file_path": "/project/app.py"},
        },
        {"type": "tool_result", "tool_id": "t1", "status": "success", "output": "print(1)"},
        {
            "type": "tool_use",
            "tool_name": "run_shell_command",
            "tool_id": "t2",
            "parameters": {"command": "pytest"},
        },
        {
            "type": "tool_result",
            "tool_id": "t2",
            "status": "error",
            "error": {"type": "TOOL_EXECUTION_ERROR", "message": "exit 1"},
        },
        {"type": "message", "role": "assistant", "content": "Fixed.", "delta": True},
        {"type": "result", "status": "success", "stats": {}},
    ]
    for frame in frames:
        worker._handle(frame)
    assert recorder.sessions == ["s-1"]
    assert recorder.started == 1
    tools = [text for kind, text in recorder.activity if kind == "tool"]
    assert tools == ["Reading app.py", "Running: pytest", "Failed: run_shell_command: exit 1"]
    assert ("result", "print(1)") in recorder.activity
    assert recorder.completed == ["Reading it now. \n\nFixed."]
    assert "Fix the bug" not in recorder.said()


def test_gemini_error_result_says_the_one_sentence_that_matters():
    recorder = Recorder()
    worker = _worker(GeminiWorker, recorder)
    message = (
        '[API Error: {"error":{"message":"{\\n  \\"error\\": {\\n    \\"code\\": 400,\\n'
        '    \\"message\\": \\"API key not valid. Please pass a valid API key.\\",\\n'
        '    \\"status\\": \\"INVALID_ARGUMENT\\"\\n  }\\n}\\n","code":400}}]'
    )
    worker._handle({"type": "init", "session_id": "s"})
    worker._handle(
        {"type": "result", "status": "error", "error": {"type": "unknown", "message": message}}
    )
    assert recorder.failed == ["Gemini CLI: API key not valid. Please pass a valid API key."]
    assert recorder.completed == []


# -- Antigravity stream --------------------------------------------------------


def test_antigravity_stream_becomes_a_spoken_turn():
    recorder = Recorder()
    worker = _worker(AntigravityWorker, recorder)
    conv = "1b4f806e"
    frames = [
        {"event": "init", "conversation_id": conv, "init": {"permission_mode": "always-proceed"}},
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": conv,
                "step_index": 0,
                "state": "DONE",
                "step_type": "user_input",
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": conv,
                "step_index": 1,
                "state": "ACTIVE",
                "step_type": "agent_response",
                "text_delta": "Looking. ",
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": conv,
                "step_index": 2,
                "state": "ACTIVE",
                "step_type": "tool",
                "tool_name": "view_file",
                "tool_info": {
                    "name": "view_file",
                    "parameters": {"AbsolutePath": "/project/main.go"},
                },
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": conv,
                "step_index": 2,
                "state": "DONE",
                "step_type": "tool",
                "tool_name": "view_file",
                "tool_info": {
                    "name": "view_file",
                    "parameters": {"AbsolutePath": "/project/main.go"},
                    "output": "package main",
                },
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": conv,
                "step_index": 3,
                "state": "ACTIVE",
                "step_type": "tool",
                "tool_info": {
                    "name": "run_command",
                    "parameters": {"CommandLine": "go test ./..."},
                },
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": conv,
                "step_index": 3,
                "state": "DONE",
                "step_type": "tool",
                "tool_info": {
                    "name": "run_command",
                    "error": {"type": "x", "message": "exit status 1"},
                },
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": conv,
                "step_index": 4,
                "state": "ACTIVE",
                "step_type": "agent_response",
                "text_delta": "Done.",
            },
        },
        {
            "event": "result",
            "result": {"conversation_id": conv, "status": "SUCCESS", "response": "Done."},
        },
    ]
    for frame in frames:
        worker._handle(frame)
    assert recorder.sessions == [conv]
    tools = [text for kind, text in recorder.activity if kind == "tool"]
    assert tools == [
        "Reading main.go",
        "Running: go test ./...",
        "Failed: run_command: exit status 1",
    ]
    assert ("result", "package main") in recorder.activity
    assert recorder.completed == ["Looking. \n\nDone."]


def test_antigravity_reply_without_deltas_comes_from_the_result():
    recorder = Recorder()
    worker = _worker(AntigravityWorker, recorder)
    worker._handle({"event": "init", "conversation_id": "c"})
    worker._handle(
        {
            "event": "result",
            "result": {"conversation_id": "c", "status": "SUCCESS", "response": "All good."},
        }
    )
    assert recorder.completed == ["All good."]


def test_antigravity_failures_are_readable():
    recorder = Recorder()
    worker = _worker(AntigravityWorker, recorder)
    # Copied from agy 1.2.17 run with an invalid key.
    error = (
        "agent executor error: generating and executing: Error 400, Message: API key not valid. "
        "Please pass a valid API key., Status: INVALID_ARGUMENT, Details: [map[@type:"
        "type.googleapis.com/google.rpc.LocalizedMessage locale:en-US message:API key not valid. "
        "Please pass a valid API key.]]"
    )
    worker._handle(
        {"event": "result", "result": {"conversation_id": "c", "status": "ERROR", "error": error}}
    )
    assert recorder.failed == ["Antigravity CLI: API key not valid. Please pass a valid API key."]


def test_antigravity_signed_out_says_how_to_sign_in():
    recorder = Recorder()
    worker = _worker(AntigravityWorker, recorder)
    worker._handle(
        {
            "event": "result",
            "result": {
                "conversation_id": "",
                "status": "ERROR",
                "error": "authentication failed or timed out",
            },
        }
    )
    assert recorder.failed and "Sign in with your Google account" in recorder.failed[0]


# -- whole turns against stand-in CLIs ----------------------------------------


def _fake_cli(tmp_path: Path, name: str, lines: list[dict], exit_code: int = 0) -> str:
    """A stand-in CLI that records its argv and stdin, then prints *lines*."""
    script = tmp_path / name
    payload = "\n".join(json.dumps(line) for line in lines)
    script.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import json, sys
            from pathlib import Path
            Path({str(tmp_path / (name + ".args"))!r}).write_text(json.dumps(sys.argv[1:]))
            Path({str(tmp_path / (name + ".stdin"))!r}).write_text(sys.stdin.read())
            print({payload!r})
            sys.exit({exit_code})
            """
        ),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in CLI is a shebang script")
def test_a_whole_gemini_turn_runs_on_its_own_sign_in(monkeypatch, tmp_path, home):
    cli = _fake_cli(
        tmp_path,
        "gemini",
        [
            {"type": "init", "session_id": "s-9", "model": "auto"},
            {"type": "message", "role": "assistant", "content": "Hello there.", "delta": True},
            {"type": "result", "status": "success"},
        ],
    )
    monkeypatch.setattr(google_worker, "find_backend_cli", lambda _backend: cli)
    recorder = Recorder()
    worker = GeminiWorker("Say hi", None, str(tmp_path), "default", **recorder.callbacks())
    # An agent turn never carries a key BlindPilot chose; the CLI signs itself in.
    assert "GEMINI_API_KEY" not in worker._env(cli)
    worker.start()
    worker.join(timeout=30)
    assert recorder.completed == ["Hello there."]
    assert recorder.failed == []
    assert recorder.sessions == ["s-9"]
    assert recorder.done == 1
    assert (tmp_path / "gemini.stdin").read_text() == "Say hi\n"


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in CLI is a shebang script")
def test_a_signed_out_gemini_turn_says_how_to_sign_in(monkeypatch, tmp_path, home):
    cli = _fake_cli(tmp_path, "gemini", [], exit_code=41)
    monkeypatch.setattr(google_worker, "find_backend_cli", lambda _backend: cli)
    recorder = Recorder()
    worker = GeminiWorker("Say hi", None, str(tmp_path), "default", **recorder.callbacks())
    worker.start()
    worker.join(timeout=30)
    assert recorder.failed and "Sign in with your Google account" in recorder.failed[0]


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in CLI is a shebang script")
def test_a_gemini_crash_is_reported_with_its_reason(monkeypatch, tmp_path, home):
    cli = _fake_cli(tmp_path, "gemini", [{"type": "init", "session_id": "s"}], exit_code=1)
    monkeypatch.setattr(google_worker, "find_backend_cli", lambda _backend: cli)
    recorder = Recorder()
    worker = GeminiWorker("Say hi", None, str(tmp_path), "default", **recorder.callbacks())
    worker.start()
    worker.join(timeout=30)
    assert recorder.completed == []
    assert recorder.failed == ["Gemini CLI stopped before the turn completed (exit code 1)."]


def test_gemini_shut_off_for_personal_accounts_points_to_antigravity():
    # Real stderr at 0.62.0, June 2026 on: the last "error" line is a stack frame.
    class Exited:
        def wait(self, timeout=None):
            return 1

    recorder = Recorder()
    worker = _worker(GeminiWorker, recorder)
    worker._error_lines = [
        "An unexpected critical error occurred:IneligibleTierError: This client is no "
        "longer supported for Gemini Code Assist for individuals. To continue using "
        "Gemini, please migrate to the Antigravity suite of products",
        "at throwIneligibleOrProjectIdError (file:///gemini-cli/bundle/chunk.js:1:1)",
    ]
    worker._finish(Exited())
    assert recorder.failed and "Antigravity CLI" in recorder.failed[0]
    assert "throwIneligible" not in recorder.failed[0]


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in CLI is a shebang script")
def test_a_whole_antigravity_turn_sends_the_prompt_as_ndjson(monkeypatch, tmp_path, home):
    cli = _fake_cli(
        tmp_path,
        "agy",
        [
            {"event": "init", "conversation_id": "c-1"},
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 1,
                    "state": "ACTIVE",
                    "step_type": "agent_response",
                    "text_delta": "Hi.",
                },
            },
            {
                "event": "result",
                "result": {"conversation_id": "c-1", "status": "SUCCESS", "response": "Hi."},
            },
        ],
    )
    monkeypatch.setattr(google_worker, "find_backend_cli", lambda _backend: cli)
    recorder = Recorder()
    worker = AntigravityWorker("Say hi", "c-0", str(tmp_path), "plan", **recorder.callbacks())
    worker.start()
    worker.join(timeout=30)
    assert recorder.completed == ["Hi."]
    stdin = json.loads((tmp_path / "agy.stdin").read_text())
    assert stdin == {"event": "user", "message": {"content": "Say hi"}}
    args = json.loads((tmp_path / "agy.args").read_text())
    assert args[-4:] == ["--mode", "plan", "--conversation", "c-0"]


def test_a_missing_cli_says_how_to_install_it(monkeypatch):
    monkeypatch.setattr(google_worker, "find_backend_cli", lambda _backend: None)
    for cls, words in (
        (GeminiWorker, "npm install -g @google/gemini-cli"),
        (AntigravityWorker, "antigravity.google"),
    ):
        recorder = Recorder()
        worker = cls("hi", None, ".", "default", **recorder.callbacks())
        worker.start()
        worker.join(timeout=10)
        assert recorder.failed and words in recorder.failed[0]


# -- sign-in and settings ------------------------------------------------------


def test_gemini_is_signed_in_once_its_google_sign_in_is_cached(home):
    assert not backend_auth_ok(BACKEND_GEMINI)
    settings = home / ".gemini" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps({"security": {"auth": {"selectedType": "oauth-personal"}}}), encoding="utf-8"
    )
    # Chosen but not finished: no cached credentials yet.
    assert not backend_auth_ok(BACKEND_GEMINI)
    _gemini_signed_in(home, "someone@example.com")
    assert backend_auth_ok(BACKEND_GEMINI)
    assert google_backend.gemini_account_lines() == [
        "Signed in: yes",
        "Sign-in method: Google account",
        "Account: someone@example.com",
    ]


def test_a_sign_in_somebody_set_up_in_gemini_cli_themselves_counts(home):
    settings = home / ".gemini" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps({"security": {"auth": {"selectedType": "vertex-ai"}}}), encoding="utf-8"
    )
    assert backend_auth_ok(BACKEND_GEMINI)


def test_an_api_key_in_the_environment_does_not_sign_in_agent_mode(monkeypatch, home):
    monkeypatch.setenv("GEMINI_API_KEY", "chat-key")
    assert not backend_auth_ok(BACKEND_GEMINI)


def test_both_backends_sign_in_in_a_terminal_and_are_watched():
    for backend in (BACKEND_GEMINI, BACKEND_ANTIGRAVITY):
        info = agent_backends.BACKENDS[backend]
        assert info.login_needs_terminal
        assert not info.login_terminal_hidden
        assert info.login_watch_until_signed_in
    assert agent_backends.BACKENDS[BACKEND_GEMINI].login_args == ("--screen-reader",)
    assert agent_backends.BACKENDS[BACKEND_ANTIGRAVITY].login_args == ()


def test_agy_is_signed_in_when_it_lists_models(monkeypatch, home):
    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _backend: "agy")
    monkeypatch.setattr(
        agent_backends,
        "_probe_backend",
        lambda *_a: (0, "Fetching available models...\nm-1\tModel One\n"),
    )
    assert backend_auth_ok(BACKEND_ANTIGRAVITY)
    assert google_backend.antigravity_account_lines() == [
        "Signed in: yes",
        "Sign-in method: Google account",
    ]


def test_agy_model_list_is_read_from_its_output(monkeypatch, home):
    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _backend: "agy")
    output = (
        "Fetching available models...\n"
        "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
        "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n"
    )
    monkeypatch.setattr(agent_backends, "_probe_backend", lambda *_a: (0, output))
    models, efforts, current, effort, error = google_backend.antigravity_model_options()
    assert models == ["gemini-3.8-flash-high", "gemini-3.8-flash-low"]
    assert efforts == ["low", "medium", "high", "xhigh", "max"]
    assert error == ""
    # Signed out it still exits 0, and the list is empty.
    signed_out = "Fetching available models...\nError: Please sign in to view available models. Launch the CLI without arguments to sign in.\n"
    monkeypatch.setattr(agent_backends, "_probe_backend", lambda *_a: (0, signed_out))
    models, _efforts, _current, _effort, error = google_backend.antigravity_model_options()
    assert models == []
    assert error.startswith("Please sign in")
    assert not google_backend.antigravity_auth_ok()


def test_gemini_models_offer_the_cli_aliases(monkeypatch, home):
    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _backend: "gemini")
    models, efforts, current, _effort, error = google_backend.gemini_model_options()
    assert models == ["auto", "pro", "flash", "flash-lite"]
    assert efforts == []
    assert current == "auto"
    assert error == ""


def test_only_conversation_models_are_offered():
    assert is_gemini_chat_model("models/gemini-3.8-flash")
    assert is_gemini_chat_model("gemini-3.1-pro-preview")
    assert is_gemini_chat_model("gemma-4-27b-it")
    for name in (
        "gemini-3.8-flash-tts",
        "gemini-3.1-flash-image",
        "gemini-embedding-2-preview",
        "veo-3.1-generate-preview",
        "gemini-3.8-live",
        "gemini-3.5-transcribe",
        "gemini-omni-1.1-flash",
        "gemini-2.5-flash-native-audio-latest",
    ):
        assert not is_gemini_chat_model(name), name


def test_settings_files_name_both_google_clis(tmp_path, home):
    files = {
        (entry.backend, str(entry.path)) for entry in agent_backends.settings_files(str(tmp_path))
    }
    assert (BACKEND_GEMINI, str(home / ".gemini" / "settings.json")) in files
    assert (
        BACKEND_ANTIGRAVITY,
        str(home / ".gemini" / "antigravity-cli" / "settings.json"),
    ) in files


# -- the wizard watching a terminal sign-in -----------------------------------


class _WizardStub:
    def __init__(self, backend: str, step: int) -> None:
        self.backend = backend
        self._step = step
        self.landed: list[tuple] = []

    def __bool__(self) -> bool:
        return True

    def _on_watched_sign_in(self, ticket, backend, step) -> None:
        self.landed.append((ticket, backend, step))


def test_the_wizard_moves_on_when_the_terminal_sign_in_lands(monkeypatch):
    import blindpilot_app

    answers = iter([False, False, True])
    monkeypatch.setattr(blindpilot_app, "backend_auth_ok", lambda _backend: next(answers))
    monkeypatch.setattr(blindpilot_app, "_SIGN_IN_WATCH_INTERVAL", 0.0)
    monkeypatch.setattr(blindpilot_app.wx, "CallAfter", lambda fn, *args: fn(*args))
    wizard = _WizardStub(BACKEND_ANTIGRAVITY, 2)
    blindpilot_app.SetupWizard._watch_for_sign_in(wizard, BACKEND_ANTIGRAVITY, 2)
    assert wizard.landed == [(1, BACKEND_ANTIGRAVITY, 2)]


def test_the_watch_stops_when_the_wizard_leaves_the_step(monkeypatch):
    import blindpilot_app

    wizard = _WizardStub(BACKEND_GEMINI, 2)

    def check(_backend):
        wizard._step = 3  # the person chose Already Signed In meanwhile
        return False

    monkeypatch.setattr(blindpilot_app, "backend_auth_ok", check)
    monkeypatch.setattr(blindpilot_app, "_SIGN_IN_WATCH_INTERVAL", 0.0)
    monkeypatch.setattr(blindpilot_app.wx, "CallAfter", lambda fn, *args: fn(*args))
    blindpilot_app.SetupWizard._watch_for_sign_in(wizard, BACKEND_GEMINI, 2)
    assert wizard.landed == []
