"""The public worker factory builds each provider without starting a turn."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace

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
    worker_type,
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


@pytest.mark.parametrize(
    ("backend", "options", "constructor_hooks"),
    [
        ("claude", ClaudeOptions(remote_control={"enabled": True}), ("on_tasks", "on_remote")),
        ("copilot", CopilotOptions(additional_dirs=("/extra",)), ("on_tasks",)),
        ("museai", MuseAiOptions(follow_after=42), ("follow_after",)),
    ],
)
def test_factory_finishes_provider_hooks_during_construction(
    monkeypatch, callbacks, backend, options, constructor_hooks
):
    worker_class = worker_type(backend)
    original_init = worker_class.__init__
    original_setattr = worker_class.__setattr__

    def init_and_mark(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._constructor_finished = True

    def reject_late_hook(self, name, value):
        if self.__dict__.get("_constructor_finished") and name in constructor_hooks:
            raise AssertionError(f"{name} was attached after construction")
        original_setattr(self, name, value)

    monkeypatch.setattr(worker_class, "__init__", init_and_mark)
    monkeypatch.setattr(worker_class, "__setattr__", reject_late_hook)

    def task(*_args):
        return None

    def remote(_data):
        return None

    def permission(_tool, _input, _rules):
        return {"behavior": "allow"}

    def unsolicited():
        return None

    configured = replace(
        callbacks,
        on_tasks=task,
        on_remote=remote,
        on_permission=permission,
        on_unsolicited=unsolicited,
    )
    worker = create_worker(
        backend,
        WorkerRequest("hello", None, ".", "default", callbacks=configured, options=options),
    )
    if "on_tasks" in constructor_hooks:
        assert worker.on_tasks is task
    if "on_remote" in constructor_hooks:
        assert worker.on_remote is remote
    if backend == "claude":
        assert worker._on_permission is permission
        assert worker._on_unsolicited is unsolicited
    if "follow_after" in constructor_hooks:
        assert worker.follow_after == 42


@pytest.mark.parametrize(
    ("backend", "outlives_turn"),
    [
        ("claude", True),
        ("codex", False),
        ("freebuff", False),
        ("opencode", False),
        ("hermes", False),
        ("muse", False),
        ("museai", False),
        ("commandcode", False),
        ("gemini", False),
        ("antigravity", False),
        ("copilot", False),
    ],
)
def test_capabilities_are_available_without_starting_a_worker(backend, outlives_turn):
    assert worker_capabilities(backend).subagents_outlive_turn is outlives_turn


@pytest.mark.parametrize(
    ("backend", "options_name", "module"),
    [
        ("claude", "ClaudeOptions", "claude_worker"),
        ("hermes", "HermesOptions", "hermes_worker"),
        ("muse", "MuseOptions", "muse_worker"),
        ("museai", "MuseAiOptions", "museai_worker"),
        ("commandcode", "CommandcodeOptions", "commandcode_worker"),
        ("gemini", "GeminiOptions", "google_worker"),
        ("antigravity", "AntigravityOptions", "google_worker"),
        ("copilot", "CopilotOptions", "copilot_backend"),
    ],
)
def test_remaining_factory_providers_import_only_the_selected_module(backend, options_name, module):
    code = """
import sys
import backend_workers as bw
callbacks = bw.WorkerCallbacks(lambda _: None, lambda: None, lambda *_: None,
                               lambda _: None, lambda _: None, lambda: None)
bw.create_worker(sys.argv[1], bw.WorkerRequest(
    'hello', None, '.', 'default', callbacks=callbacks,
    options=getattr(bw, sys.argv[2])()))
for name in ('claude_worker', 'hermes_worker', 'muse_worker', 'museai_worker',
             'commandcode_worker', 'google_worker', 'copilot_backend'):
    if name in sys.modules:
        print(name)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, backend, options_name],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == [module]


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
