"""What a Hermes turn says while it is waiting.

Three ways a Hermes turn goes quiet, and the worker's job is to keep saying
something useful in each. Measured against a live gateway before these tests:
a provider that is rate-limited or out of credits makes Hermes grind through
backoff and fallbacks that the gateway mostly does not narrate, and the one
explanation the gateway DOES send (``notification.show``, "Still starting the
agent...") was dropped on the floor by the worker -- so a slow start sounded
like nothing, and a long rate-limit grind sounded like "still working" with no
reason attached. Each test below pins the feedback that used to be missing.
"""

from __future__ import annotations

import threading
import time

import hermes_worker
from hermes_worker import HermesWorker


def _worker(activities, failures):
    return HermesWorker(
        "hi",
        None,
        "C:/Users/admin",
        "default",
        on_session=lambda _sid: None,
        on_started=lambda: None,
        on_activity=lambda kind, text: activities.append((kind, text)),
        on_complete=lambda _txt: None,
        on_failed=lambda msg: failures.append(msg),
        on_done=lambda: None,
    )


def _frame(event: str, payload: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {"type": event, "session_id": "s", "payload": payload},
    }


# -- the gateway's own "still starting" notice becomes a row -------------


def test_notification_show_becomes_a_row_and_names_the_wait():
    activities = []
    w = _worker(activities, [])
    text = (
        "Still starting the agent (tool discovery / model setup) \u2014 "
        "your message will be sent as soon as it's ready."
    )
    w._handle_event(_frame("notification.show", {"text": text, "level": "info", "kind": "agent"}))
    assert activities == [("tool", text)]
    # The still-working notice says what it is waiting on.
    assert w._last_step == text


def test_notification_clear_produces_no_row():
    activities = []
    w = _worker(activities, [])
    result = w._handle_event(_frame("notification.clear", {"key": "agent-build-slow"}))
    assert result is None
    assert activities == []


# -- status lines lose the warning symbols a screen reader reads as noise -


def test_status_update_strips_leading_symbols():
    activities = []
    w = _worker(activities, [])
    w._handle_event(
        _frame(
            "status.update",
            {
                "text": (
                    "\u26a0\ufe0f Model fallback: claude-opus-5 via anthropic "
                    "unavailable (rate limit); using gpt-5.6-sol via openai-codex"
                ),
                "kind": "",
            },
        )
    )
    assert len(activities) == 1
    kind, text = activities[0]
    assert kind == "tool"
    assert text.startswith("Model fallback: claude-opus-5")
    assert "\u26a0" not in text


# -- a turn that produces nothing gets a diagnosis, not just "still working" --


class _SilentTransport:
    """A transport that never delivers a frame and never dies."""

    def __init__(self, clock) -> None:
        self._clock = clock

    def receive(self, _timeout):
        self._clock.advance(1.0)

    def connected(self) -> bool:
        return True

    def failure_detail(self) -> str:
        return ""


class _Clock:
    def __init__(self) -> None:
        self.value = time.monotonic()

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_prolonged_silence_reports_a_diagnostic(monkeypatch):
    activities = []
    failures = []
    w = _worker(activities, failures)
    clock = _Clock()
    monkeypatch.setattr(hermes_worker, "_now", clock)
    # Keep the loop's only exit the cancellation below; the diagnostic is the
    # point of the test, not the idle-limit failure.
    monkeypatch.setattr(hermes_worker, "_IDLE_LIMIT", 10**6)
    w._transport = _SilentTransport(clock)
    w._live_session = "live"
    w._accepting_input.set()

    thread = threading.Thread(target=w._consume_turn, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if any("silent" in text for _kind, text in activities):
            break
        time.sleep(0.01)
    w._cancelled = True
    thread.join(timeout=5)

    spoken = [text for _kind, text in activities if "silent" in text]
    assert spoken, "no silence diagnosis was ever spoken"
    diagnosis = spoken[0]
    assert "2 minutes" in diagnosis
    assert "rate-limited" in diagnosis or "credits" in diagnosis
    # The generic notices still arrive alongside the diagnosis.
    assert any("Still working" in text for _kind, text in activities)
    # A silent-but-connected turn must NOT be reported as a failure.
    assert failures == []
