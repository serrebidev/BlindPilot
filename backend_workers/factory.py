"""Lazy selection and construction of one backend worker per turn."""

from __future__ import annotations

from dataclasses import dataclass, fields
from importlib import import_module
from typing import Callable, cast

from .contract import (
    _UNSET,
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
    Worker,
    WorkerCapabilities,
    WorkerRequest,
)


@dataclass(frozen=True)
class _WorkerSpec:
    module: str
    name: str
    options: type
    capabilities: WorkerCapabilities = WorkerCapabilities()


_WORKERS = {
    "claude": _WorkerSpec("claude_worker", "ClaudeWorker", ClaudeOptions, WorkerCapabilities(True)),
    "codex": _WorkerSpec("backend_workers.codex_worker", "CodexWorker", CodexOptions),
    "freebuff": _WorkerSpec("backend_workers.freebuff_worker", "FreebuffWorker", FreebuffOptions),
    "opencode": _WorkerSpec("backend_workers.opencode_worker", "OpencodeWorker", OpencodeOptions),
    "hermes": _WorkerSpec("hermes_worker", "HermesWorker", HermesOptions),
    "muse": _WorkerSpec("muse_worker", "MuseWorker", MuseOptions),
    "museai": _WorkerSpec("museai_worker", "MuseAiWorker", MuseAiOptions),
    "commandcode": _WorkerSpec("commandcode_worker", "CommandcodeWorker", CommandcodeOptions),
    "gemini": _WorkerSpec("google_worker", "GeminiWorker", GeminiOptions),
    "antigravity": _WorkerSpec("google_worker", "AntigravityWorker", AntigravityOptions),
    "copilot": _WorkerSpec("copilot_backend", "CopilotWorker", CopilotOptions),
}


def worker_capabilities(backend: str) -> WorkerCapabilities:
    """Capabilities known without importing a provider implementation."""
    try:
        return _WORKERS[backend].capabilities
    except KeyError:
        raise KeyError(f"no worker for backend {backend!r}") from None


def worker_type(backend: str, *, claude_worker: Callable | None = None) -> Callable:
    """Load only the selected implementation; Claude override keeps old callers."""
    try:
        spec = _WORKERS[backend]
    except KeyError:
        raise KeyError(f"no worker for backend {backend!r}") from None
    if backend == "claude" and claude_worker is not None:
        return claude_worker
    return cast(Callable, getattr(import_module(spec.module), spec.name))


def create_worker(backend: str, request: WorkerRequest) -> Worker:
    """Return a configured, unstarted worker for the exact backend requested."""
    try:
        spec = _WORKERS[backend]
    except KeyError:
        raise KeyError(f"no worker for backend {backend!r}") from None
    if type(request.options) is not spec.options:
        raise TypeError(
            f"{backend} requires {spec.options.__name__}, got {type(request.options).__name__}"
        )
    options = {
        field.name: getattr(request.options, field.name) for field in fields(request.options)
    }
    if backend == "codex" and options["plan_account"] is _UNSET:
        del options["plan_account"]
    if backend == "museai":
        follow_after = options.pop("follow_after")
    callbacks = request.callbacks
    kwargs = {
        "model": request.model,
        "effort": request.effort,
        "on_session": callbacks.on_session,
        "on_started": callbacks.on_started,
        "on_activity": callbacks.on_activity,
        "on_complete": callbacks.on_complete,
        "on_failed": callbacks.on_failed,
        "on_done": callbacks.on_done,
        "on_question": callbacks.on_question,
        "on_permission": callbacks.on_permission,
        "on_subagent": callbacks.on_subagent,
        **options,
    }
    if backend == "claude":
        kwargs["on_unsolicited"] = callbacks.on_unsolicited
    worker = worker_type(backend)(
        request.prompt,
        request.session_id,
        request.cwd,
        request.permission_mode,
        **kwargs,
    )
    if callbacks.on_tasks is not None:
        worker.on_tasks = callbacks.on_tasks
    if callbacks.on_remote is not None:
        worker.on_remote = callbacks.on_remote
    if backend == "museai":
        worker.follow_after = follow_after
    return cast(Worker, worker)
