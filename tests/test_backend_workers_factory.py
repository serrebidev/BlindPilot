"""The public worker factory builds each provider without starting a turn."""

from __future__ import annotations

import subprocess
import sys

import pytest

from agent_backends import BACKENDS
from backend_workers import (
    AntigravityOptions,
    ClaudeOptions,
    CodexOptions,
    CommandcodeOptions,
    CopilotOptions,
    FreebuffOptions,
    GeminiOptions,
    HermesOptions,
    MuseAiOptions,
    MuseOptions,
    OpencodeOptions,
    WorkerCallbacks,
    WorkerRequest,
    create_worker,
    worker_capabilities,
)


@pytest.fixture
def callbacks():
    return WorkerCallbacks(
        on_session=lambda _session: None,
        on_started=lambda: None,
        on_activity=lambda _kind, _text: None,
        on_complete=lambda _text: None,
        on_failed=lambda _message: None,
        on_done=lambda: None,
    )


@pytest.mark.parametrize(
    ("backend", "options", "class_name"),
    [
        ("claude", ClaudeOptions(), "ClaudeWorker"),
        ("codex", CodexOptions(), "CodexWorker"),
        ("freebuff", FreebuffOptions(), "FreebuffWorker"),
        ("opencode", OpencodeOptions(), "OpencodeWorker"),
        ("hermes", HermesOptions(), "HermesWorker"),
        ("muse", MuseOptions(), "MuseWorker"),
        ("museai", MuseAiOptions(), "MuseAiWorker"),
        ("commandcode", CommandcodeOptions(), "CommandcodeWorker"),
        ("gemini", GeminiOptions(), "GeminiWorker"),
        ("antigravity", AntigravityOptions(), "AntigravityWorker"),
        ("copilot", CopilotOptions(), "CopilotWorker"),
    ],
)
def test_factory_returns_ready_worker_for_each_backend(backend, options, class_name, callbacks):
    assert backend in BACKENDS
    worker = create_worker(
        backend,
        WorkerRequest("hello", None, ".", "default", callbacks=callbacks, options=options),
    )
    assert type(worker).__name__ == class_name
    assert not worker.is_alive()
    assert all(hasattr(worker, name) for name in ("start", "join", "cancel", "steer"))


def test_factory_rejects_options_for_another_backend(callbacks):
    request = WorkerRequest(
        "hello", None, ".", "default", callbacks=callbacks, options=HermesOptions()
    )
    with pytest.raises(TypeError, match="codex.*CodexOptions.*HermesOptions"):
        create_worker("codex", request)


def test_factory_rejects_unknown_backend_without_falling_back(callbacks):
    request = WorkerRequest(
        "hello", None, ".", "default", callbacks=callbacks, options=ClaudeOptions()
    )
    with pytest.raises(KeyError, match="unknown"):
        create_worker("unknown", request)


def test_factory_preserves_callbacks_and_provider_options(callbacks):
    events = []
    configured = WorkerCallbacks(
        callbacks.on_session,
        callbacks.on_started,
        callbacks.on_activity,
        callbacks.on_complete,
        callbacks.on_failed,
        callbacks.on_done,
        on_tasks=lambda *args: events.append(("tasks", args)),
        on_remote=lambda data: events.append(("remote", data)),
    )
    worker = create_worker(
        "codex",
        WorkerRequest(
            "hello",
            "thread-1",
            ".",
            "plan",
            callbacks=configured,
            options=CodexOptions(compact=True, plan_account=None),
        ),
    )
    assert worker._compact is True
    assert worker._plan_account is None
    assert worker._on_session is configured.on_session
    worker.on_tasks("one")
    worker.on_remote({"enabled": True})
    assert events == [("tasks", ("one",)), ("remote", {"enabled": True})]


@pytest.mark.parametrize(
    ("backend", "options", "module"),
    [
        ("codex", CodexOptions(compact=True), "backend_workers.codex_worker"),
        ("freebuff", FreebuffOptions(compact=True), "backend_workers.freebuff_worker"),
        ("opencode", OpencodeOptions(compact=True), "backend_workers.opencode_worker"),
    ],
)
def test_protocol_workers_live_behind_the_factory(backend, options, module, callbacks):
    import agent_backends

    worker = create_worker(
        backend,
        WorkerRequest("hello", None, ".", "default", callbacks=callbacks, options=options),
    )
    assert type(worker).__module__ == module
    assert isinstance(worker, agent_backends._TurnWorker)
    assert type(worker) is getattr(agent_backends, type(worker).__name__)
    assert worker._compact is True


def test_factory_preserves_museai_follow_cursor(callbacks):
    worker = create_worker(
        "museai",
        WorkerRequest(
            None,
            "session-1",
            ".",
            "default",
            callbacks=callbacks,
            options=MuseAiOptions(follow_after=42),
        ),
    )
    assert worker.follow_after == 42


def test_capabilities_are_available_without_starting_a_worker():
    assert worker_capabilities("claude").subagents_outlive_turn
    assert not worker_capabilities("hermes").subagents_outlive_turn


def test_factory_only_imports_the_selected_provider():
    code = """
import sys
from backend_workers import ClaudeOptions, WorkerCallbacks, WorkerRequest, create_worker
callbacks = WorkerCallbacks(lambda _: None, lambda: None, lambda *_: None,
                            lambda _: None, lambda _: None, lambda: None)
create_worker('claude', WorkerRequest('hello', None, '.', 'default',
                                     callbacks=callbacks, options=ClaudeOptions()))
for name in ('claude_worker', 'hermes_worker', 'muse_worker', 'museai_worker',
             'commandcode_worker', 'google_worker', 'copilot_backend'):
    if name in sys.modules:
        print(name)
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.splitlines() == ["claude_worker"]
