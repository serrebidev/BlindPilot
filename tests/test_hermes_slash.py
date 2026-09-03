"""Hermes' own slash commands, run instead of being sent to the model.

``prompt.submit`` does not interpret a leading slash -- checked against the
gateway, which has a separate ``slash.exec`` for it. So every Hermes command
BlindPilot did not implement itself used to reach the model as five or six
characters of text and be answered rather than run: "/usage" got a sentence
about usage, not the usage report.

The list of what counts as a command is Hermes', not BlindPilot's. It ships
about 120, plus whatever skills, bundles and plugins are installed, and a copy
compiled into the app would be wrong the first time a skill was added.
"""

from __future__ import annotations

from hermes_worker import HermesWorker


class _ScriptedTransport:
    """Answers the requests the worker makes, in the order it makes them.

    Its stream ends when the script runs out, like a real pipe -- see
    tests/transport_contract.py for why a fake that stays "connected but
    silent for ever" is the shape that hides bugs rather than finding them.
    """

    def __init__(self, replies: list[dict] | None = None) -> None:
        self.replies = list(replies or [])
        self.sent: list[dict] = []
        self.closed = False
        self.alive = True

    def send(self, message: dict) -> bool:
        if not self.connected():
            return False
        self.sent.append(message)
        return True

    def receive(self, timeout: float) -> dict | None:  # noqa: ARG002 - interface
        if self.replies:
            return self.replies.pop(0)
        self.alive = False
        return None

    def close(self) -> None:
        self.closed = True

    def connected(self) -> bool:
        return self.alive and not self.closed

    def failure_detail(self) -> str:
        return "scripted transport ended"


def _worker(prompt, replies, *, attachments=None):
    state: dict = {"activity": [], "complete": [], "failed": []}
    worker = HermesWorker(
        prompt,
        None,
        "C:/Users/admin",
        "default",
        attachments=attachments,
        on_session=lambda _sid: None,
        on_started=lambda: None,
        on_activity=lambda kind, text: state["activity"].append((kind, text)),
        on_complete=lambda text: state["complete"].append(text),
        on_failed=lambda msg: state["failed"].append(msg),
        on_done=lambda: None,
    )
    transport = _ScriptedTransport(replies)
    worker._transport = transport
    worker._live_session = "live-1"
    return worker, transport, state


def _completion(rid: int, names: list[str]) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "result": {"items": [{"text": n, "kind": "command", "meta": ""} for n in names]},
    }


def _methods(transport):
    return [m.get("method") for m in transport.sent]


def _turn_ends(text: str = "answered") -> dict:
    """The completion event, so a fallback turn ends instead of running its
    whole quiet-connection loop (15s before the first liveness check)."""
    return {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {
            "type": "message.complete",
            "session_id": "live-1",
            "payload": {"text": text, "status": "complete"},
        },
    }


# -- a command Hermes knows ----------------------------------------------


def test_a_known_command_is_executed_not_sent_to_the_model():
    worker, transport, state = _worker(
        "/usage",
        [
            _completion(101, ["usage"]),
            {"jsonrpc": "2.0", "id": 102, "result": {"output": "12,300 tokens used today"}},
        ],
    )
    worker._run_turn()

    assert _methods(transport) == ["complete.slash", "slash.exec"]
    assert "prompt.submit" not in _methods(transport)
    exec_params = transport.sent[1]["params"]
    # The parameter is "command"; "text" is answered with 4004 "empty command".
    assert exec_params == {"session_id": "live-1", "command": "/usage"}
    assert state["complete"] == ["12,300 tokens used today"]


def test_arguments_travel_with_the_command():
    worker, transport, _state = _worker(
        "/title R and B caster",
        [
            _completion(101, ["title"]),
            {"jsonrpc": "2.0", "id": 102, "result": {"output": "Session renamed"}},
        ],
    )
    worker._run_turn()
    assert transport.sent[1]["params"]["command"] == "/title R and B caster"


def test_a_silent_command_still_ends_the_turn_out_loud():
    """A turn that finishes saying nothing cannot be told from one that died."""
    worker, _transport, state = _worker(
        "/reload-skills",
        [
            _completion(101, ["reload-skills"]),
            {"jsonrpc": "2.0", "id": 102, "result": {"output": ""}},
        ],
    )
    worker._run_turn()
    assert state["complete"] and state["complete"][0].strip()
    assert state["failed"] == []


def test_the_command_being_run_is_announced():
    worker, _transport, state = _worker(
        "/history",
        [
            _completion(101, ["history"]),
            {"jsonrpc": "2.0", "id": 102, "result": {"output": "3 messages"}},
        ],
    )
    worker._run_turn()
    assert any("/history" in text for _kind, text in state["activity"])


# -- anything Hermes does not know stays a message -----------------------


def test_an_unknown_slash_word_is_sent_as_an_ordinary_message():
    worker, transport, _state = _worker(
        "/nonsense",
        [
            _completion(101, ["new", "next"]),
            {"jsonrpc": "2.0", "id": 102, "result": {"accepted": True}},
            _turn_ends(),
        ],
    )
    worker._run_turn()
    methods = _methods(transport)
    assert "slash.exec" not in methods
    assert methods[-1] == "prompt.submit"


def test_a_sentence_that_merely_opens_with_a_slash_is_not_swallowed():
    worker, transport, _state = _worker(
        "/home/admin is where it lives, put the cache there",
        [
            _completion(101, ["history", "handoff"]),
            {"jsonrpc": "2.0", "id": 102, "result": {"accepted": True}},
            _turn_ends(),
        ],
    )
    worker._run_turn()
    assert "slash.exec" not in _methods(transport)


def test_a_command_with_files_attached_stays_a_message():
    """Attachments are uploaded and named in the prompt text; slash.exec has
    nowhere to put them."""
    worker, transport, _state = _worker("/init", [], attachments=["C:/tmp/notes.txt"])
    assert worker._as_slash_command() is None
    assert transport.sent == []


def test_a_gateway_that_will_not_answer_the_lookup_falls_back_to_a_message():
    """No answer is not a licence to guess: the model reading "/usage" is the
    safer of the two wrong answers."""
    worker, transport, _state = _worker("/usage", [])
    assert worker._as_slash_command() is None


def test_an_error_from_the_lookup_falls_back_to_a_message():
    worker, _transport, _state = _worker(
        "/usage",
        [{"jsonrpc": "2.0", "id": 101, "error": {"code": 4002, "message": "nope"}}],
    )
    assert worker._as_slash_command() is None


# -- when the command itself fails ---------------------------------------


def test_a_refused_command_fails_the_turn_with_the_reason():
    worker, _transport, state = _worker(
        "/snapshot restore",
        [
            _completion(101, ["snapshot"]),
            {
                "jsonrpc": "2.0",
                "id": 102,
                "error": {"code": 4018, "message": "snapshot restore mutates live config"},
            },
        ],
    )
    worker._run_turn()
    assert state["complete"] == []
    assert state["failed"] and "mutates live config" in state["failed"][0]


def test_a_command_matches_case_insensitively():
    worker, transport, _state = _worker(
        "/Usage",
        [
            _completion(101, ["usage"]),
            {"jsonrpc": "2.0", "id": 102, "result": {"output": "ok"}},
        ],
    )
    worker._run_turn()
    assert "slash.exec" in _methods(transport)
