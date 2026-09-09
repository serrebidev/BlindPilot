"""The Muse worker driven against a scripted MSP host, frame by frame.

Muse's host protocol is line-delimited JSON-RPC with server notifications for
everything that streams, so the whole turn lifecycle can be replayed from a
script without a Muse install -- and, on Windows, without booting a WSL
distribution, which is where the real CLI lives.

The fake transport here is registered in ``test_transport_contract``: a fake
whose ``receive`` lies about the connection state is precisely the shape that
once hid two cross-platform defects (PR #30), so this one is held to the same
contract the real ``StdioTransport`` answers to.
"""

from __future__ import annotations

import threading

import pytest

import muse_worker
from muse_worker import MuseWorker


class _ScriptedMuseTransport:
    """Replays a canned MSP conversation and records everything sent to it.

    ``receive`` hands the script back one frame at a time and answers ``None``
    once it has run out, with ``connected()`` going False with it -- a finite
    stream, like a real pipe whose child has exited.
    """

    def __init__(self, frames: list[dict] | None = None, stays_open: bool = False) -> None:
        self._frames = list(frames or [])
        # ``stays_open`` models a host that is alive and listening without
        # streaming anything -- the state a live `muse serve` is in while a
        # turn runs. The contract sweep builds the default, finite shape.
        self.stays_open = stays_open
        self.sent: list[dict] = []
        self.closed = False

    def start(self) -> None:
        return None

    def send(self, message: dict) -> bool:
        if self.closed:
            return False
        self.sent.append(message)
        return True

    def receive(self, timeout: float) -> dict | None:  # noqa: ARG002 - interface
        if self._frames:
            return self._frames.pop(0)
        return None

    def connected(self) -> bool:
        return not self.closed and (self.stays_open or bool(self._frames))

    def failure_detail(self) -> str:
        return "scripted transport ended"

    def close(self) -> None:
        self.closed = True

    def sent_with_method(self, method: str) -> list[dict]:
        return [m for m in self.sent if m.get("method") == method]


class _Recorder:
    """One turn's callbacks, the way the window wires them."""

    def __init__(self) -> None:
        self.activity: list[tuple[str, str]] = []
        self.completed: list[str] = []
        self.failures: list[str] = []
        self.sessions: list[str] = []
        self.started = 0
        self.done = 0

    def callbacks(self) -> dict:
        return {
            "on_session": self.sessions.append,
            "on_started": self._started,
            "on_activity": lambda kind, text: self.activity.append((kind, text)),
            "on_complete": self.completed.append,
            "on_failed": self.failures.append,
            "on_done": self._done,
        }

    def _started(self) -> None:
        self.started += 1

    def _done(self) -> None:
        self.done += 1

    def said(self, kind: str) -> list[str]:
        return [text for kind_seen, text in self.activity if kind_seen == kind]


def _init_reply(request_id: int) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": {}}


def _run(worker: MuseWorker, timeout: float = 10.0) -> None:
    """Drive the turn on its own thread, as the window does."""
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), "the turn never ended"


def _with_script(monkeypatch, frames: list[dict]) -> _ScriptedMuseTransport:
    """Point the worker at a scripted host and hand the fake back for asserts."""
    transport = _ScriptedMuseTransport(frames)
    monkeypatch.setattr(muse_worker, "MuseTransport", lambda _cwd: transport)
    return transport


# --------------------------------------------------------------------------
# The turn lifecycle
# --------------------------------------------------------------------------


def test_a_full_turn_streams_the_answer_and_completes(monkeypatch):
    frames = [
        _init_reply(101),
        {"jsonrpc": "2.0", "id": 102, "result": {"session": {"sessionId": "sess-1"}}},
        {"jsonrpc": "2.0", "id": 103, "result": {"turnId": "turn-1"}},
        {
            "jsonrpc": "2.0",
            "method": "item/delta",
            "params": {"itemId": "a1", "field": "text", "delta": "Hello there."},
        },
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"terminal": "completed"}},
    ]
    transport = _with_script(monkeypatch, frames)
    recorder = _Recorder()
    worker = MuseWorker("say hello", None, ".", "bypassPermissions", **recorder.callbacks())

    _run(worker)

    assert recorder.completed == ["Hello there."]
    assert recorder.sessions == ["sess-1"]
    assert recorder.started == 1
    assert recorder.failures == []
    assert recorder.done == 1
    assert "Hello there." in recorder.said("assistant")
    started = transport.sent_with_method("turn/start")
    assert started and started[0]["params"]["input"][0]["text"] == "say hello"


def test_the_client_introduces_itself_with_a_protocol_valid_name(monkeypatch):
    # MSP validates clientInfo.name against ^[a-z0-9_]+$; a mixed-case display
    # name is answered invalidParams and the host never talks to this client.
    transport = _with_script(monkeypatch, [_init_reply(101)])
    recorder = _Recorder()
    worker = MuseWorker("x", None, ".", "default", **recorder.callbacks())

    _run(worker)

    initialize = transport.sent[0]
    assert initialize["method"] == "initialize"
    assert initialize["params"]["clientInfo"]["name"] == "blindpilot"


def test_permission_modes_map_onto_the_protocol_vocabulary(monkeypatch):
    # The window's mode names are what the user picked from; what the wire
    # takes is MSP's own closed enum. Both sides are pinned here.
    cases = {
        "bypassPermissions": "allowAll",
        "default": "promptUnmatched",
        "acceptEdits": "onRequest",
    }
    for window_mode, protocol_mode in cases.items():
        transport = _with_script(
            monkeypatch,
            [
                _init_reply(101),
                {"jsonrpc": "2.0", "id": 102, "result": {"session": {"sessionId": "s"}}},
            ],
        )
        recorder = _Recorder()
        worker = MuseWorker("x", None, ".", window_mode, **recorder.callbacks())
        worker._transport = transport
        # The handshake hands out request id 101; without it the session/start
        # below would be answered by the wrong frame of the script.
        assert worker._handshake(), window_mode
        assert worker._ensure_session(), window_mode
        sent = transport.sent_with_method("session/start")[0]
        assert sent["params"]["approvalMode"] == protocol_mode, window_mode
        assert recorder.sessions == ["s"]


def test_streaming_frames_that_arrive_early_are_not_lost(monkeypatch):
    # An item streamed while the turn/start ack was still in flight is parked
    # by _request and played by the turn loop afterwards. Dropping it would
    # silently clip the start of the answer.
    frames = [
        _init_reply(101),
        {"jsonrpc": "2.0", "id": 102, "result": {"session": {"sessionId": "sess-1"}}},
        # Ahead of the ack on purpose.
        {
            "jsonrpc": "2.0",
            "method": "item/delta",
            "params": {"itemId": "a1", "field": "text", "delta": "Hello there."},
        },
        {"jsonrpc": "2.0", "id": 103, "result": {"turnId": "turn-1"}},
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"terminal": "completed"}},
    ]
    _with_script(monkeypatch, frames)
    recorder = _Recorder()
    worker = MuseWorker("go", None, ".", "bypassPermissions", **recorder.callbacks())

    _run(worker)

    assert "Hello there." in recorder.said("assistant")
    assert recorder.completed == ["Hello there."]


def test_a_failed_turn_reports_what_the_server_said(monkeypatch):
    frames = [
        _init_reply(101),
        {"jsonrpc": "2.0", "id": 102, "result": {"session": {"sessionId": "sess-1"}}},
        {"jsonrpc": "2.0", "id": 103, "result": {"turnId": "turn-1"}},
        {
            "jsonrpc": "2.0",
            "method": "turn/completed",
            "params": {"terminal": "failed", "error": {"message": "quota exhausted"}},
        },
    ]
    _with_script(monkeypatch, frames)
    recorder = _Recorder()
    worker = MuseWorker("go", None, ".", "bypassPermissions", **recorder.callbacks())

    _run(worker)

    assert recorder.failures == ["quota exhausted"]
    assert recorder.completed == []


def test_a_cancelled_turn_says_so_instead_of_passing_for_an_answer(monkeypatch):
    frames = [
        _init_reply(101),
        {"jsonrpc": "2.0", "id": 102, "result": {"session": {"sessionId": "sess-1"}}},
        {"jsonrpc": "2.0", "id": 103, "result": {"turnId": "turn-1"}},
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"terminal": "cancelled"}},
    ]
    _with_script(monkeypatch, frames)
    recorder = _Recorder()
    worker = MuseWorker("go", None, ".", "bypassPermissions", **recorder.callbacks())

    _run(worker)

    assert recorder.completed == ["Stopped"]


def test_an_empty_prompt_is_refused_rather_than_sent():
    recorder = _Recorder()
    worker = MuseWorker("   ", None, ".", "bypassPermissions", **recorder.callbacks())
    worker._live_session = "sess-1"
    worker._transport = _ScriptedMuseTransport(stays_open=True)

    assert worker._start_turn() is False
    assert recorder.failures and "empty" in recorder.failures[0].lower()


def test_a_host_that_refuses_the_session_is_reported(monkeypatch):
    frames = [
        _init_reply(101),
        {"jsonrpc": "2.0", "id": 102, "error": {"code": -32602, "message": "invalid workspace"}},
    ]
    _with_script(monkeypatch, frames)
    recorder = _Recorder()
    worker = MuseWorker("go", None, ".", "bypassPermissions", **recorder.callbacks())

    _run(worker)

    assert recorder.failures == ["invalid workspace"]


# --------------------------------------------------------------------------
# Steering and cancelling
# --------------------------------------------------------------------------


def test_steering_sends_the_guidance_with_the_ids_that_name_the_turn():
    worker = MuseWorker("go", "sess-1", ".", "bypassPermissions", **_Recorder().callbacks())
    transport = _ScriptedMuseTransport(stays_open=True)
    worker._transport = transport
    worker._live_session = "sess-1"
    worker._turn_id = "turn-1"
    worker._accepting_input.set()

    assert worker.steer("also check the tests") is True
    sent = transport.sent_with_method("turn/steer")[0]
    assert sent["params"]["sessionId"] == "sess-1"
    assert sent["params"]["expectedTurnId"] == "turn-1"
    assert sent["params"]["commandId"]
    assert sent["params"]["input"][0]["text"] == "also check the tests"


def test_steering_is_refused_before_a_turn_exists():
    worker = MuseWorker("go", "sess-1", ".", "bypassPermissions", **_Recorder().callbacks())
    worker._transport = _ScriptedMuseTransport(stays_open=True)

    assert worker.steer("too early") is False


def test_cancelling_sends_the_cancel_with_a_command_id():
    worker = MuseWorker("go", "sess-1", ".", "bypassPermissions", **_Recorder().callbacks())
    transport = _ScriptedMuseTransport(stays_open=True)
    worker._transport = transport
    worker._live_session = "sess-1"
    worker._turn_id = "turn-1"
    worker._accepting_input.set()

    worker.cancel()

    sent = transport.sent_with_method("turn/cancel")
    assert sent and sent[0]["params"]["turnId"] == "turn-1"
    assert sent[0]["params"]["commandId"]
    assert worker.accepting_input() is False


# --------------------------------------------------------------------------
# Approvals and mid-run questions
# --------------------------------------------------------------------------


def test_an_approval_is_answered_with_the_choice_and_the_requirement_carried_through(monkeypatch):
    # currentRequirementId is the multi-stage race guard: a decision aimed at
    # one stage must never satisfy another, so it is passed through verbatim.
    requirement = {"stage": 2, "nonce": "abc"}
    frames = [
        _init_reply(101),
        {"jsonrpc": "2.0", "id": 102, "result": {"session": {"sessionId": "sess-1"}}},
        {"jsonrpc": "2.0", "id": 103, "result": {"turnId": "turn-1"}},
        {
            "jsonrpc": "2.0",
            "method": "approval/requested",
            "params": {
                "approvalId": "ap-1",
                "sessionId": "sess-1",
                "toolName": "shell",
                "subject": {"command": "rm -rf /"},
                "currentRequirementId": requirement,
            },
        },
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"terminal": "completed"}},
    ]
    transport = _with_script(monkeypatch, frames)
    recorder = _Recorder()
    answers = [["Allow once"]]
    worker = MuseWorker(
        "go",
        None,
        ".",
        "bypassPermissions",
        on_question=lambda questions: answers,
        **recorder.callbacks(),
    )

    _run(worker)

    decide = transport.sent_with_method("approval/decide")
    assert decide and decide[0]["params"]["choiceId"] == "approved"
    assert decide[0]["params"]["approvalId"] == "ap-1"
    assert decide[0]["params"]["requirementId"] == requirement


def test_an_approval_nobody_is_here_to_answer_is_denied(monkeypatch):
    # Leaving the run wedged on an approval nobody can see is the one outcome
    # worse than refusing it.
    frames = [
        _init_reply(101),
        {"jsonrpc": "2.0", "id": 102, "result": {"session": {"sessionId": "sess-1"}}},
        {"jsonrpc": "2.0", "id": 103, "result": {"turnId": "turn-1"}},
        {
            "jsonrpc": "2.0",
            "method": "approval/requested",
            "params": {"approvalId": "ap-2", "sessionId": "sess-1", "toolName": "shell"},
        },
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"terminal": "completed"}},
    ]
    transport = _with_script(monkeypatch, frames)
    worker = MuseWorker("go", None, ".", "bypassPermissions", **_Recorder().callbacks())

    _run(worker)

    decide = transport.sent_with_method("approval/decide")
    assert decide and decide[0]["params"]["choiceId"] == "denied"


def test_an_approval_for_the_session_maps_to_the_session_choice(monkeypatch):
    frames = [
        _init_reply(101),
        {"jsonrpc": "2.0", "id": 102, "result": {"session": {"sessionId": "sess-1"}}},
        {"jsonrpc": "2.0", "id": 103, "result": {"turnId": "turn-1"}},
        {
            "jsonrpc": "2.0",
            "method": "approval/requested",
            "params": {"approvalId": "ap-3", "sessionId": "sess-1", "toolName": "edit"},
        },
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"terminal": "completed"}},
    ]
    transport = _with_script(monkeypatch, frames)
    worker = MuseWorker(
        "go",
        None,
        ".",
        "bypassPermissions",
        on_question=lambda questions: [["Allow for this conversation"]],
        **_Recorder().callbacks(),
    )

    _run(worker)

    decide = transport.sent_with_method("approval/decide")
    assert decide and decide[0]["params"]["choiceId"] == "approvedForSession"


def test_a_mid_run_question_is_answered_with_the_picked_label(monkeypatch):
    frames = [
        _init_reply(101),
        {"jsonrpc": "2.0", "id": 102, "result": {"session": {"sessionId": "sess-1"}}},
        {"jsonrpc": "2.0", "id": 103, "result": {"turnId": "turn-1"}},
        {
            "jsonrpc": "2.0",
            "method": "userInput/requested",
            "params": {
                "userInputId": "ui-1",
                "sessionId": "sess-1",
                "questions": [
                    {
                        "id": "q1",
                        "question": "Which database?",
                        "options": [{"label": "Postgres"}, {"label": "SQLite"}],
                        "selection": {"mode": "single"},
                    }
                ],
            },
        },
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"terminal": "completed"}},
    ]
    transport = _with_script(monkeypatch, frames)
    worker = MuseWorker(
        "go",
        None,
        ".",
        "bypassPermissions",
        on_question=lambda questions: [["SQLite"]],
        **_Recorder().callbacks(),
    )

    _run(worker)

    answer = transport.sent_with_method("userInput/answer")
    assert answer and answer[0]["params"]["answers"] == [
        {"questionId": "q1", "selectedLabel": "SQLite"}
    ]


def test_a_question_nobody_is_here_to_answer_is_cancelled_not_dropped(monkeypatch):
    frames = [
        _init_reply(101),
        {"jsonrpc": "2.0", "id": 102, "result": {"session": {"sessionId": "sess-1"}}},
        {"jsonrpc": "2.0", "id": 103, "result": {"turnId": "turn-1"}},
        {
            "jsonrpc": "2.0",
            "method": "userInput/requested",
            "params": {
                "userInputId": "ui-2",
                "sessionId": "sess-1",
                "questions": [{"id": "q1", "question": "Which?", "options": [{"label": "A"}]}],
            },
        },
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"terminal": "completed"}},
    ]
    transport = _with_script(monkeypatch, frames)
    worker = MuseWorker("go", None, ".", "bypassPermissions", **_Recorder().callbacks())

    _run(worker)

    assert transport.sent_with_method("userInput/cancel")


# --------------------------------------------------------------------------
# Compaction and replay
# --------------------------------------------------------------------------


def test_compaction_waits_for_the_summary_item(monkeypatch):
    # The ack is admission only; the summary lands as a compaction item on the
    # view stream afterwards.
    frames = [
        _init_reply(101),
        {"jsonrpc": "2.0", "id": 102, "result": {"session": {"sessionId": "sess-1"}}},
        {"jsonrpc": "2.0", "id": 103, "result": {}},
        {
            "jsonrpc": "2.0",
            "method": "item/completed",
            "params": {"item": {"kind": "compaction", "summary": "The conversation so far"}},
        },
    ]
    transport = _with_script(monkeypatch, frames)
    recorder = _Recorder()
    worker = MuseWorker("", None, ".", "bypassPermissions", compact=True, **recorder.callbacks())

    _run(worker)

    assert recorder.completed == ["Conversation compacted"]
    assert transport.sent_with_method("session/compact")


def test_a_resume_only_turn_replays_the_stored_transcript(monkeypatch):
    frames = [
        _init_reply(101),
        {
            "jsonrpc": "2.0",
            "id": 102,
            "result": {
                "session": {"sessionId": "sess-9"},
                "history": {
                    "items": [
                        {"kind": "userMessage", "text": "fix the bug"},
                        {"kind": "agentMessage", "text": "Fixed."},
                    ]
                },
            },
        },
    ]
    _with_script(monkeypatch, frames)
    recorder = _Recorder()
    worker = MuseWorker(
        "", "sess-9", ".", "bypassPermissions", resume_only=True, **recorder.callbacks()
    )

    _run(worker)

    assert ("you", "fix the bug") in recorder.activity
    assert ("assistant", "Fixed.") in recorder.activity
    assert recorder.completed == [""]
    assert recorder.sessions == ["sess-9"]


def test_the_window_can_hold_the_muse_worker_like_any_other():
    from agent_backends import worker_class

    assert worker_class("muse", None).__name__ == "MuseWorker"


# The contract harness sweeps this module for transport-shaped fakes; this one
# is registered in test_transport_contract.py with stream_ends=True.
def test_the_scripted_transport_ends_like_a_pipe():
    transport = _ScriptedMuseTransport([])
    transport.receive(0.001)
    assert transport.connected() is False


@pytest.mark.parametrize(
    ("worker_args", "expected"),
    [
        ({"compact": True}, True),
        ({"resume_only": True}, True),
        ({}, False),
    ],
    ids=["compact", "resume", "plain-turn"],
)
def test_turn_kinds_are_declared_up_front(worker_args, expected):
    """The flags the window passes reach the turn loop as its own vocabulary."""
    recorder = _Recorder()
    worker = MuseWorker("", None, ".", "bypassPermissions", **worker_args, **recorder.callbacks())
    if "compact" in worker_args:
        assert worker._compact is expected
    elif "resume_only" in worker_args:
        assert worker._resume_only is expected
    else:
        assert worker._compact is False and worker._resume_only is False
