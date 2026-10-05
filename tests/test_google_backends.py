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
    monkeypatch.setattr(google_backend, "saved_gemini_key", lambda: "")
    monkeypatch.setattr(google_backend, "chat_account_gemini_key", lambda: "")
    return tmp_path


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
    assert recorder.failed and "Sign in by running agy" in recorder.failed[0]


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
def test_a_whole_gemini_turn_runs_on_the_saved_key(monkeypatch, tmp_path, home):
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
    monkeypatch.setattr(google_backend, "saved_gemini_key", lambda: "saved-key")
    seen_env = {}
    real_turn_env = google_backend.turn_env

    def spy(binary, use_key):
        env = real_turn_env(binary, use_key)
        seen_env.update(env)
        return env

    monkeypatch.setattr(google_backend, "turn_env", spy)
    recorder = Recorder()
    worker = GeminiWorker("Say hi", None, str(tmp_path), "default", **recorder.callbacks())
    worker.start()
    worker.join(timeout=30)
    assert recorder.completed == ["Hello there."]
    assert recorder.failed == []
    assert recorder.sessions == ["s-9"]
    assert recorder.done == 1
    assert (tmp_path / "gemini.stdin").read_text() == "Say hi\n"
    assert seen_env.get("GEMINI_API_KEY") == "saved-key"


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


# -- keys, sign-in and settings ------------------------------------------------


def test_the_environment_key_wins_then_ours_then_chat_modes(monkeypatch, home):
    monkeypatch.setattr(google_backend, "chat_account_gemini_key", lambda: "chat")
    assert google_backend.gemini_api_key() == "chat"
    monkeypatch.setattr(google_backend, "saved_gemini_key", lambda: "saved")
    assert google_backend.gemini_api_key() == "saved"
    monkeypatch.setenv("GEMINI_API_KEY", "env")
    assert google_backend.gemini_api_key() == "env"


def test_turn_env_adds_the_key_only_when_asked(monkeypatch, home):
    monkeypatch.setattr(google_backend, "saved_gemini_key", lambda: "saved")
    monkeypatch.setattr(agent_backends, "subprocess_env", lambda _binary: {"PATH": "/bin"})
    assert google_backend.turn_env("gemini", use_key=True)["GEMINI_API_KEY"] == "saved"
    assert "GEMINI_API_KEY" not in google_backend.turn_env("gemini", use_key=False)


def test_gemini_signs_in_with_a_key_or_its_own_configured_method(home):
    assert not backend_auth_ok(BACKEND_GEMINI)
    settings = home / ".gemini" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps({"security": {"auth": {"selectedType": "vertex-ai"}}}), encoding="utf-8"
    )
    assert google_backend.gemini_configured_auth() == "vertex-ai"
    assert backend_auth_ok(BACKEND_GEMINI)


def test_gemini_does_not_override_its_own_configured_sign_in(monkeypatch, home):
    monkeypatch.setattr(google_backend, "saved_gemini_key", lambda: "saved")
    worker = _worker(GeminiWorker, Recorder())
    monkeypatch.setattr(agent_backends, "subprocess_env", lambda _binary: {})
    assert worker._env("gemini")["GEMINI_API_KEY"] == "saved"
    settings = home / ".gemini" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps({"security": {"auth": {"selectedType": "oauth-personal"}}}), encoding="utf-8"
    )
    assert "GEMINI_API_KEY" not in worker._env("gemini")


def test_switching_agy_to_the_key_keeps_its_other_settings(home):
    path = google_backend.antigravity_settings_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"colorScheme": "dark", "permissions": {"allow": ["command(git)"]}}),
        encoding="utf-8",
    )
    assert not google_backend.antigravity_uses_api_key()
    google_backend.use_api_key_for_antigravity()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == {
        "colorScheme": "dark",
        "permissions": {"allow": ["command(git)"]},
        "modelProvider": "gemini",
    }
    assert google_backend.antigravity_uses_api_key()


def test_agy_on_a_key_is_signed_in_only_with_a_key(monkeypatch, home):
    google_backend.use_api_key_for_antigravity()
    assert not backend_auth_ok(BACKEND_ANTIGRAVITY)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert backend_auth_ok(BACKEND_ANTIGRAVITY)


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


def test_gemini_models_offer_the_cli_aliases_without_a_key(monkeypatch, home):
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
