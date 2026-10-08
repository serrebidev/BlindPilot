# SPDX-License-Identifier: MIT
"""A muse.ai turn opens a side chat once, sends to it, and reports everything
the agent says there: its status updates while it works, then its answer.

The worker drives `muse-cli`; these tests stand in for it at the one place the
worker runs it, so nothing reaches the user's real cloud agent.
"""

from __future__ import annotations

import json

import museai_worker
from museai_worker import MuseAiWorker


def _event(seq, text, message_id=None, status="completed"):
    return {
        "event_name": "message.assistant",
        "payload": {
            "seq": seq,
            "message_id": message_id or f"m{seq}",
            "content": text,
            "status": status,
        },
    }


def _worker(monkeypatch, send, histories=(), start=None, session_id=None):
    """`histories` are successive answers to `history`; the last one repeats."""
    calls, events = [], []
    pending = list(histories) or [[]]
    monkeypatch.setattr(museai_worker, "find_backend_cli", lambda _backend: "muse-cli")
    monkeypatch.setattr(museai_worker, "STATUS_POLL_SECONDS", 0.01)

    def fake_run(self, binary, args, timeout, track=False):
        calls.append(args)
        if args[0] == "session-start":
            return start
        if args[0] == "history":
            batch = pending.pop(0) if len(pending) > 1 else pending[0]
            return 0, json.dumps({"chat_events": batch}), ""
        return send

    monkeypatch.setattr(MuseAiWorker, "_run", fake_run)
    worker = MuseAiWorker(
        "Check the weather in Vancouver",
        session_id,
        "C:/work",
        "default",
        on_session=lambda s: events.append(("session", s)),
        on_started=lambda: events.append(("started",)),
        on_activity=lambda kind, text: events.append(("activity", kind, text)),
        on_complete=lambda text: events.append(("complete", text)),
        on_failed=lambda text: events.append(("failed", text)),
        on_done=lambda: events.append(("done",)),
    )
    return worker, calls, events


def _said(events):
    return [e[2] for e in events if e[0] == "activity" and e[1] == "assistant"]


def test_a_new_conversation_opens_a_side_chat_and_reports_the_reply(monkeypatch):
    worker, calls, events = _worker(
        monkeypatch,
        send=(0, json.dumps({"sent": True, "reply": {"text": "Rain, 12 degrees."}}), ""),
        start=(0, json.dumps({"created": True, "session_id": "chat-1"}), ""),
    )
    worker.run()

    sends = [c for c in calls if c[0] == "send"]
    assert calls[0][:2] == ["session-start", "--title"]
    assert sends[0][:3] == ["send", "--thread", "chat-1"]
    # Never --wait 0: muse-cli drops a message it does not wait for.
    assert int(sends[0][sends[0].index("--wait") + 1]) > 0
    assert ("session", "chat-1") in events
    assert _said(events) == ["Rain, 12 degrees."]
    assert ("complete", "Rain, 12 degrees.") in events
    assert events[-1] == ("done",)


def test_status_updates_go_into_the_conversation_before_the_answer(monkeypatch):
    old = [_event(5, "An answer from an earlier turn")]
    later = old + [
        _event(7, "Connected"),
        _event(8, "## Status\n\nCloning the repository."),
        _event(9, "Half-writ", status=""),
        _event(10, "## Done\n\nAll merged.", message_id="final"),
    ]
    worker, _calls, events = _worker(
        monkeypatch,
        send=(
            0,
            json.dumps(
                {"sent": True, "reply": {"text": "## Done\n\nAll merged.", "message_id": "final"}}
            ),
            "",
        ),
        histories=[old, later],
        session_id="chat-9",
    )
    worker.run()

    # Earlier turns, connection notices and half-written messages stay out;
    # the answer is said once, after the status update.
    assert _said(events) == ["## Status\n\nCloning the repository.", "## Done\n\nAll merged."]
    assert ("complete", "## Done\n\nAll merged.") in events


def test_no_reply_within_the_wait_says_where_the_answer_will_be(monkeypatch):
    worker, _calls, events = _worker(
        monkeypatch,
        send=(0, json.dumps({"sent": True, "note": "no assistant reply within 3600s"}), ""),
        session_id="chat-9",
    )
    worker.run()

    failed = [e for e in events if e[0] == "failed"]
    assert failed and "muse.ai chat" in failed[0][1]


def test_a_signed_out_cli_fails_the_turn_with_its_own_words(monkeypatch):
    worker, _calls, events = _worker(
        monkeypatch, send=(1, "", "gateway error: not signed in"), session_id="chat-9"
    )
    worker.run()

    failed = [e for e in events if e[0] == "failed"]
    assert failed and "not signed in" in failed[0][1]


def test_a_reopened_chat_still_running_is_followed_until_its_answer(monkeypatch):
    """Nothing is sent; updates the agent posts on its own are relayed, and its
    next direct answer ends the turn."""
    old = [_event(5, "Earlier answer")]
    update = _event(6, "## Status\n\nTests running.")
    update["payload"]["reply_to_message_id"] = "root"
    later = old + [update, _event(7, "## Done\n\nReleased v1.2.")]
    worker, calls, events = _worker(
        monkeypatch, send=None, histories=[old, later], session_id="chat-9"
    )
    worker._prompt = None
    worker.run()

    assert not [c for c in calls if c[0] == "send"]
    assert _said(events) == ["## Status\n\nTests running.", "## Done\n\nReleased v1.2."]
    assert ("complete", "## Done\n\nReleased v1.2.") in events
