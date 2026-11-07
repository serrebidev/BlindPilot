"""The panel hands complete requests to the typed worker factory."""

from __future__ import annotations

import blindpilot_app as app
from backend_workers import ClaudeOptions, HermesOptions, WorkerRequest
from doubles import panel_stub


class _Worker:
    __slots__ = ("started",)

    def __init__(self):
        self.started = False

    def start(self):
        self.started = True


def test_normal_turn_reaches_factory_with_callbacks_and_claude_options(monkeypatch):
    requests = []
    worker = _Worker()
    monkeypatch.setattr(
        app.backend_workers,
        "create_worker",
        lambda backend, request: requests.append((backend, request)) or worker,
    )
    queued = []

    def wake():
        pass

    def permission(*_args):
        return {"behavior": "allow"}

    panel = panel_stub(
        _session_id="session-1",
        cwd="/work",
        mode="plan",
        model="opus",
        effort="high",
        _ask_questions=lambda _questions: None,
        _queue_worker_event=lambda *event: queued.append(event),
    )

    app.SessionPanel._launch_turn(
        panel,
        "hello",
        app.BACKEND_CLAUDE,
        {
            "held_for": panel,
            "remote_control": {"enabled": True},
            "on_unsolicited": wake,
            "on_permission": permission,
        },
    )

    backend, request = requests[0]
    assert backend == app.BACKEND_CLAUDE
    assert isinstance(request, WorkerRequest)
    assert request.prompt == "hello" and request.session_id == "session-1"
    assert (request.cwd, request.permission_mode, request.model, request.effort) == (
        "/work",
        "plan",
        "opus",
        "high",
    )
    assert isinstance(request.options, ClaudeOptions)
    assert request.options.held_for is panel
    assert request.options.remote_control == {"enabled": True}
    assert request.callbacks.on_unsolicited is wake
    assert request.callbacks.on_permission is permission
    request.callbacks.on_tasks("task")
    request.callbacks.on_remote({"enabled": True})
    assert queued == [("tasks", "task"), ("remote", {"enabled": True})]
    assert worker.started


def test_hermes_reopen_reaches_factory_as_resume_only(monkeypatch):
    requests = []
    worker = _Worker()
    monkeypatch.setattr(
        app.backend_workers,
        "create_worker",
        lambda backend, request: requests.append((backend, request)) or worker,
    )
    held = object()
    panel = panel_stub(
        cwd="/work",
        mode="default",
        model="model",
        effort="low",
        _ask_questions=lambda _questions: None,
        _queue_worker_event=lambda *_event: None,
        _drop_held_backends=lambda: None,
        _hermes_worker_extra=lambda _files: {"remote_url": "ws://example", "held": held},
        backend_changed=lambda: None,
        _on_title=lambda *_args: None,
    )

    app.SessionPanel.open_hermes_session(panel, "stored-1", "Existing", attaching=True)

    backend, request = requests[0]
    assert backend == app.BACKEND_HERMES
    assert isinstance(request, WorkerRequest)
    assert request.prompt == "" and request.session_id == "stored-1"
    assert isinstance(request.options, HermesOptions)
    assert request.options.resume_only
    assert request.options.remote_url == "ws://example"
    assert request.options.held is held
    assert worker.started and panel._turn_attached
    assert panel.stop_btn.enabled and panel.steer_btn.enabled
