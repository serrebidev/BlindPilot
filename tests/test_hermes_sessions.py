"""Going back to a Hermes conversation, including one that is running right now.

These tests lock down behaviours that were measured against a live gateway
(``hermes serve`` on port 9119) and cannot be guessed from the protocol
documents:

* ``session.list`` is the historical browser (every surface: CLI, TUI,
  messaging) while ``session.active_list`` reports only the sessions with an
  agent inside the gateway process. Only the second kind can be attached to
  live, so the catalog has to return both and the picker has to tell them
  apart.
* ``session.steer`` and ``session.interrupt`` answer to the LIVE session id
  only. Addressed by the stored id -- which is what this adapter did until now
  -- the gateway replies ``4001 session not found``, so steering a remote
  conversation silently did nothing at all.
* A resume that does not omit the transcript both returns the history and
  attaches to the running turn, which is what makes "carry on with the
  conversation that is working right now" possible.
"""

from __future__ import annotations

import hermes_backend
import hermes_worker
from hermes_backend import hermes_session_catalog
from hermes_worker import HermesWorker, _replay_rows


def _callbacks() -> dict:
    return {
        "on_session": lambda _value: None,
        "on_started": lambda: None,
        "on_activity": lambda _kind, _value: None,
        "on_complete": lambda _value: None,
        "on_failed": lambda _value: None,
        "on_done": lambda: None,
    }


class _FakeTransport:
    """Replays scripted frames, so these tests need no Hermes."""

    def __init__(self, frames: list[dict]) -> None:
        self.frames = list(frames)
        self.sent: list[dict] = []
        self.closed = False
        self.alive = True
        self.started = False

    def start(self) -> None:
        self.started = True

    def send(self, message: dict) -> bool:
        self.sent.append(message)
        return True

    def receive(self, timeout: float) -> dict | None:  # noqa: ARG002 - interface
        if self.frames:
            return self.frames.pop(0)
        # A real transport that has run out of frames has ended: the pipe closed
        # or the socket went away, and `connected()` then answers False. Keeping
        # this stand-in "connected" for ever made it a shape no real transport
        # has, and any wait-for-frames loop tested against it spun until its own
        # deadline -- which on Linux is a 60s pytest-timeout failure and on
        # Windows was invisible.
        self.alive = False
        return None

    def close(self) -> None:
        self.closed = True

    def connected(self) -> bool:
        return self.alive and not self.closed

    def failure_detail(self) -> str:
        return "fake transport ended"


def _ready() -> dict:
    return {"jsonrpc": "2.0", "method": "event", "params": {"type": "gateway.ready"}}


def _event(kind: str, payload: dict | None = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {"type": kind, "session_id": "live1", "payload": payload or {}},
    }


def _reply(request_id: int, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _worker(**overrides) -> HermesWorker:
    callbacks = _callbacks()
    callbacks.update(overrides)
    return HermesWorker("", "stored-1", ".", "default", resume_only=True, **callbacks)


# -- the catalog -----------------------------------------------------------


def _catalog_transport(monkeypatch, frames: list[dict]) -> _FakeTransport:
    transport = _FakeTransport(frames)
    monkeypatch.setattr(
        hermes_backend, "WebSocketTransport", lambda *a, **k: transport, raising=True
    )
    return transport


def test_catalog_lists_every_surface_and_marks_the_live_ones(monkeypatch):
    """The list is history; the live set is what can be attached to."""
    transport = _catalog_transport(
        monkeypatch,
        [
            _ready(),
            _reply(
                1,
                {
                    "sessions": [
                        {
                            "id": "20260902_142652_5d15e2",
                            "title": "Working on the gateway",
                            "preview": "start",
                            "message_count": 152,
                            "source": "tui",
                            "started_at": 1788352014.0,
                        },
                        {
                            "id": "20260902_060036_2baac2",
                            "title": "Healthcheck",
                            "preview": "OK",
                            "message_count": 2,
                            "source": "cli",
                            "started_at": 1788318036.0,
                        },
                    ]
                },
            ),
            _reply(
                2,
                {
                    "sessions": [
                        {
                            "id": "31f7cd44",
                            "session_key": "20260902_142652_5d15e2",
                            "status": "working",
                        }
                    ]
                },
            ),
        ],
    )

    sessions, live, error = hermes_session_catalog(remote_url="ws://host:9119/api/ws")

    assert error == ""
    assert [s["id"] for s in sessions] == [
        "20260902_142652_5d15e2",
        "20260902_060036_2baac2",
    ]
    # A CLI conversation is listed as readily as a desktop one: the whole point
    # is reaching a session started somewhere else.
    assert {s["source"] for s in sessions} == {"tui", "cli"}
    # Only the session with an agent in the gateway process is attachable.
    assert live == {"20260902_142652_5d15e2"}
    assert transport.closed is True


def test_catalog_drops_the_gateways_own_bookkeeping_rows(monkeypatch):
    """Sub-agent and kanban rows are machinery, not conversations."""
    _catalog_transport(
        monkeypatch,
        [
            _ready(),
            _reply(
                1,
                {
                    "sessions": [
                        {"id": "real", "title": "Mine", "source": "cli", "message_count": 3},
                        {"id": "sub", "title": "subagent", "source": "tool", "message_count": 9},
                        {"id": "worker", "title": "kanban", "source": "kanban", "message_count": 4},
                    ]
                },
            ),
            _reply(2, {"sessions": []}),
        ],
    )

    sessions, live, error = hermes_session_catalog(remote_url="ws://host:9119/api/ws")

    assert error == ""
    assert [s["id"] for s in sessions] == ["real"]
    assert live == set()


def test_catalog_keeps_the_list_when_the_live_query_fails(monkeypatch):
    """Attaching is a bonus; a refused active_list must not lose the history."""
    _catalog_transport(
        monkeypatch,
        [
            _ready(),
            _reply(1, {"sessions": [{"id": "s1", "title": "One", "source": "cli"}]}),
            {"jsonrpc": "2.0", "id": 2, "error": {"code": 5036, "message": "no"}},
        ],
    )

    sessions, live, error = hermes_session_catalog(remote_url="ws://host:9119/api/ws")

    assert error == ""
    assert [s["id"] for s in sessions] == ["s1"]
    assert live == set()


def test_catalog_reports_a_refused_list_as_an_error(monkeypatch):
    _catalog_transport(
        monkeypatch,
        [_ready(), {"jsonrpc": "2.0", "id": 1, "error": {"message": "database is locked"}}],
    )

    sessions, live, error = hermes_session_catalog(remote_url="ws://host:9119/api/ws")

    assert sessions == []
    assert live == set()
    assert "database is locked" in error


def test_catalog_says_so_when_the_gateway_never_announces_itself(monkeypatch):
    """No gateway.ready means the far end is not a Hermes, or is not answering."""
    _catalog_transport(monkeypatch, [])

    sessions, live, error = hermes_session_catalog(remote_url="ws://host:9119/api/ws")

    assert sessions == []
    assert live == set()
    assert error != ""


# -- the transcript projection --------------------------------------------


def test_replay_rows_carry_users_words_answers_and_tool_steps():
    rows = _replay_rows(
        [
            {"role": "user", "text": "start", "timestamp": 1.0},
            {"role": "tool", "name": "terminal", "context": "pwd + 5 commands"},
            {"role": "assistant", "text": "Done."},
        ]
    )

    assert rows == [
        ("you", "start"),
        ("tool", "terminal: pwd + 5 commands"),
        ("assistant", "Done."),
    ]


def test_replay_rows_skip_empty_messages():
    """An empty row read aloud is a gap the listener has to interpret."""
    rows = _replay_rows(
        [
            {"role": "user", "text": "   "},
            {"role": "assistant", "text": ""},
            {"role": "assistant", "content": "From content instead"},
            {"role": "system", "text": "ignored"},
        ]
    )

    assert rows == [("assistant", "From content instead")]


def test_replay_rows_name_a_tool_with_no_context():
    assert _replay_rows([{"role": "tool", "name": "web_search"}]) == [("tool", "web_search")]


# -- resume-only turns ----------------------------------------------------


def _run_replay(worker: HermesWorker, transport: _FakeTransport) -> None:
    worker._transport = transport
    worker._run_replay()


def test_reopening_a_finished_conversation_replays_it_and_ends():
    rows: list[tuple[str, str]] = []
    completed: list[str] = []
    sessions: list[str] = []
    worker = _worker(
        on_activity=lambda kind, text: rows.append((kind, text)),
        on_complete=completed.append,
        on_session=sessions.append,
    )
    transport = _FakeTransport(
        [
            _reply(
                101,
                {
                    # The shape a real gateway answers with, measured on a live
                    # one: resume returns the per-process handle as session_id,
                    # the durable id as session_key/resumed, and NO
                    # stored_session_id (that field belongs to session.create).
                    # An earlier version of this test invented the missing
                    # field, which froze a bug rather than catching it.
                    "session_id": "live-abc",
                    "session_key": "stored-1",
                    "resumed": "stored-1",
                    "running": False,
                    "status": "idle",
                    "messages": [
                        {"role": "user", "text": "hello"},
                        {"role": "assistant", "text": "hi"},
                    ],
                },
            )
        ]
    )

    _run_replay(worker, transport)

    resume = [m for m in transport.sent if m.get("method") == "session.resume"]
    assert len(resume) == 1
    # The transcript is the point of the request, so it must NOT be omitted.
    assert resume[0]["params"] == {"session_id": "stored-1", "omit_messages": False}
    assert rows == [("you", "hello"), ("assistant", "hi")]
    # Nothing new was said, so the completion carries no text to append.
    assert completed == [""]
    assert sessions == ["stored-1"]


def test_the_window_is_given_the_durable_id_not_the_gateway_handle():
    """Measured against a live gateway: resume answers with session_key.

    The tab stores whatever arrives here and reopens the conversation by it
    later. Handed the per-process handle ("7e76fdca") the tab looks fine for
    the rest of the session and the conversation becomes unreachable the moment
    the gateway restarts -- a loss discovered long after the cause.
    """
    sessions: list[str] = []
    worker = _worker(on_session=sessions.append)
    transport = _FakeTransport(
        [
            _reply(
                101,
                {
                    "session_id": "7e76fdca",
                    "session_key": "20260902_060036_2baac2",
                    "resumed": "20260902_060036_2baac2",
                    "running": False,
                    "messages": [],
                },
            )
        ]
    )

    _run_replay(worker, transport)

    assert sessions == ["20260902_060036_2baac2"]
    assert worker._live_session == "7e76fdca"


def test_a_gateway_that_names_no_durable_id_keeps_the_one_we_asked_for():
    """Never downgrade to the volatile handle: the id we resumed by still works."""
    sessions: list[str] = []
    worker = _worker(on_session=sessions.append)
    transport = _FakeTransport([_reply(101, {"session_id": "live-xyz", "messages": []})])

    _run_replay(worker, transport)

    assert sessions == ["stored-1"]


def test_attaching_to_a_running_conversation_consumes_its_turn():
    """The measured reason this exists: a live session can be joined mid-turn."""
    rows: list[tuple[str, str]] = []
    completed: list[str] = []
    worker = _worker(
        on_activity=lambda kind, text: rows.append((kind, text)),
        on_complete=completed.append,
    )
    transport = _FakeTransport(
        [
            _reply(
                101,
                {
                    "session_id": "live-abc",
                    "stored_session_id": "stored-1",
                    "running": True,
                    "status": "working",
                    "messages": [{"role": "user", "text": "build it"}],
                },
            ),
            _event("tool.start", {"name": "terminal", "context": "make"}),
            _event("message.delta", {"text": "It builds. "}),
            _event("message.complete", {"text": "It builds.", "status": "complete"}),
        ]
    )

    _run_replay(worker, transport)

    assert ("you", "build it") in rows
    assert ("tool", "terminal: make") in rows
    # The answer that arrived while attached is completed like any other turn.
    assert completed == ["It builds."]


def test_a_replay_that_hermes_refuses_is_reported_not_silent():
    failures: list[str] = []
    worker = _worker(on_failed=failures.append)
    transport = _FakeTransport(
        [{"jsonrpc": "2.0", "id": 101, "error": {"code": 4007, "message": "session not found"}}]
    )

    _run_replay(worker, transport)

    assert failures and "session not found" in failures[0]


def test_a_replay_with_no_conversation_id_fails_before_asking():
    failures: list[str] = []
    callbacks = _callbacks()
    callbacks["on_failed"] = failures.append
    worker = HermesWorker("", None, ".", "default", resume_only=True, **callbacks)
    transport = _FakeTransport([])

    _run_replay(worker, transport)

    assert failures
    assert transport.sent == []


# -- steering and interrupting the right session --------------------------


def test_steering_addresses_the_live_session_not_the_stored_one():
    """Measured on a live gateway: the stored id answers 4001 session not found.

    So a steer sent by stored id did nothing, silently, on every remote
    conversation -- the worst shape of bug for a listener, because the window
    reported the steer as accepted.
    """
    worker = _worker()
    transport = _FakeTransport([])
    worker._transport = transport
    worker._gateway_session = "stored-1"
    worker._live_session = "live-abc"
    worker._accepting_input.set()

    assert worker.steer("look at the log") is True
    steers = [m for m in transport.sent if m.get("method") == "session.steer"]
    assert steers[0]["params"]["session_id"] == "live-abc"


def test_interrupting_addresses_the_live_session_too():
    worker = _worker()
    transport = _FakeTransport([])
    worker._transport = transport
    worker._gateway_session = "stored-1"
    worker._live_session = "live-abc"

    worker.cancel()

    interrupts = [m for m in transport.sent if m.get("method") == "session.interrupt"]
    assert interrupts[0]["params"]["session_id"] == "live-abc"


def test_steering_falls_back_to_the_stored_id_before_a_live_one_is_known():
    """The local pipe knows only the stored id until its first reply."""
    worker = _worker()
    transport = _FakeTransport([])
    worker._transport = transport
    worker._gateway_session = "stored-1"
    worker._live_session = ""
    worker._accepting_input.set()

    assert worker.steer("hello") is True
    steers = [m for m in transport.sent if m.get("method") == "session.steer"]
    assert steers[0]["params"]["session_id"] == "stored-1"


def test_a_replay_worker_never_submits_a_prompt(monkeypatch):
    """Reopening a conversation must not add a turn to it.

    The window opens a past conversation without saying anything, so a stray
    prompt.submit here would post an empty message into someone else's live
    session -- including a session running on another machine.
    """
    transport = _FakeTransport(
        [
            _ready(),
            _reply(
                101,
                {
                    "session_id": "live-abc",
                    "stored_session_id": "stored-1",
                    "running": False,
                    "messages": [],
                },
            ),
        ]
    )
    monkeypatch.setattr(
        hermes_worker, "WebSocketTransport", lambda *a, **k: transport, raising=True
    )
    worker = _worker()
    worker._remote_url = "ws://host:9119/api/ws"

    worker._do_run()

    assert [m.get("method") for m in transport.sent] == ["session.resume"]
