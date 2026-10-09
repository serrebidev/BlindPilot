# SPDX-License-Identifier: MIT
"""A muse.ai turn opens a side chat once, sends to it, and reports everything
the agent says there: its status updates while it works, then its answer.

The worker drives `muse-cli`; these tests stand in for it at the one place the
worker runs it, so nothing reaches the user's real cloud agent.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

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


def _worker(monkeypatch, send, histories=(), start=None, session_id=None, approvals=()):
    """`histories` are successive answers to `history`; the last one repeats."""
    calls, events = [], []
    pending = list(histories) or [[]]
    monkeypatch.setattr(museai_worker, "find_backend_cli", lambda _backend: "muse-cli")
    monkeypatch.setattr(museai_worker, "STATUS_POLL_SECONDS", 0.01)
    monkeypatch.setattr(museai_worker, "QUIET_SECONDS", 0)

    def fake_run(self, binary, args, timeout, track=False):
        calls.append(args)
        if args[0] == "session-start":
            return start
        if args[0] == "history":
            batch = pending.pop(0) if len(pending) > 1 else pending[0]
            return 0, json.dumps({"chat_events": batch}), ""
        if args[:2] == ["raw", "egress.approvals"]:
            return 0, json.dumps({"ok": True, "result": {"pending_approvals": list(approvals)}}), ""
        if args[:2] == ["raw", "egress.approval.decide"]:
            return 0, json.dumps({"ok": True, "result": {"status": "resolved"}}), ""
        return send

    monkeypatch.setattr(MuseAiWorker, "_run", fake_run)
    monkeypatch.setattr(MuseAiWorker, "_start_status", lambda self, binary, session: None)
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


def test_action_answer_already_arrived_when_follow_starts_is_still_read(monkeypatch):
    monkeypatch.setattr(museai_worker, "REPLY_WAIT_SECONDS", 1)
    worker, calls, events = _worker(
        monkeypatch,
        send=None,
        histories=[[_event(10, "Earlier reply"), _event(11, "Action finished")]],
        session_id="main",
    )
    worker._prompt = None
    worker.follow_after = 10
    worker.run()
    assert ("complete", "Action finished") in events
    assert not any(call[0] in {"send", "session-start"} for call in calls)


def test_sentinel_approvals_ask_even_in_bypass_mode_and_are_answered_only_once(monkeypatch):
    approval = {
        "approval_id": "a1",
        "display": {"summary_title": "Send mail", "purpose_summary": "Reply to Sam"},
    }
    worker, calls, _events = _worker(
        monkeypatch,
        send=(0, json.dumps({"sent": True, "reply": {"text": "Done"}}), ""),
        session_id="main",
        approvals=[approval],
    )
    asked = []
    worker._permission_mode = "bypassPermissions"
    worker._on_permission = lambda tool, payload, rules: (
        asked.append(payload) or {"behavior": "allow"}
    )
    worker.run()
    assert len(asked) == 1 and "Reply to Sam" in asked[0]["Request"]
    decisions = [c for c in calls if c[:2] == ["raw", "egress.approval.decide"]]
    assert len(decisions) == 1
    assert json.loads(decisions[0][-1]) == {"approval_id": "a1", "decision": "allow_once"}


def test_stopping_at_an_approval_does_not_send_a_decision(monkeypatch):
    worker, calls, _events = _worker(
        monkeypatch,
        send=(0, json.dumps({"sent": True, "reply": {"text": "Done"}}), ""),
        session_id="main",
        approvals=[{"approval_id": "a1"}],
    )
    worker._on_permission = lambda *_args: worker.cancel() or {"behavior": "deny"}
    worker.run()
    assert not [c for c in calls if c[:2] == ["raw", "egress.approval.decide"]]
    assert worker._cancelled


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


def test_progress_goes_to_the_status_line_not_the_conversation(monkeypatch):
    """Working steps carry no text; their count is reported as a quiet step."""
    old = [_event(5, "Earlier answer")]
    working = old + [_event(6, ""), _event(7, "")]
    done = working + [_event(8, "All done.")]
    worker, _calls, events = _worker(
        monkeypatch, send=None, histories=[old, working, working, done], session_id="chat-9"
    )
    worker._prompt = None
    worker.run()

    steps = [e[2] for e in events if e[0] == "activity" and e[1] == "step"]
    assert steps and steps[0].startswith("muse.ai is working: 2 steps so far")
    assert _said(events) == ["All done."]


def _status(code, text, session="chat-9", ts_ms=None):
    payload = {"activity_code": code, "activity_text": text, "session_id": session}
    return json.dumps({"event": "agent.status", "payload": payload, "ts_ms": ts_ms}) + "\n"


def test_live_activity_goes_to_the_status_line_in_the_web_clients_words(monkeypatch):
    """What muse.ai says it is doing ("Fetching release") replaces the step
    count on the status line, and real work also gets a row in the
    conversation; "is working"/"is responding" stay on the status line, and
    going back to the same work after one of them adds no second row. Only a
    status stamped since the watcher started gets a row: the previous turn's
    last step, a few seconds old, may reach the status line but never the
    conversation. Coming online, repeats, other chats, statuses replayed from
    long before, and anything after the turn has settled say nothing."""
    worker, _calls, events = _worker(monkeypatch, send=None, session_id="chat-9")
    now = time.time() * 1000
    lines = [
        _status("online", "online", ts_ms=now + 1),
        _status("working", "Last turn's step", ts_ms=now - 5_000),
        _status("working", "is working", ts_ms=now + 1),
        json.dumps({"event": "task.status", "payload": {"session_id": "chat-9"}}) + "\n",
        _status("working", "Fetching release", ts_ms=now + 2),
        _status("working", "Fetching release", ts_ms=now + 3),
        _status("working", "Reading mail", session="other-chat", ts_ms=now + 4),
        _status("working", "Yesterday's work", ts_ms=1_000),
        _status("working", "Just now", ts_ms=now + 5),
        _status("responding", "is responding", ts_ms=now + 6),
        _status("working", "Just now", ts_ms=now + 7),
    ]
    seen = []

    def on_activity(kind, text):
        seen.append((kind, text))
        assert worker._live_status

    worker._on_activity = on_activity
    worker._read_status(SimpleNamespace(stdout=iter(lines)), "chat-9")
    assert seen == [
        ("step", "muse.ai: Last turn's step"),
        ("step", "muse.ai is working"),
        ("step", "muse.ai: Fetching release"),
        ("tool", "muse.ai: Fetching release"),
        ("step", "muse.ai: Just now"),
        ("tool", "muse.ai: Just now"),
        ("step", "muse.ai is responding"),
        ("step", "muse.ai: Just now"),
    ]
    assert not worker._live_status

    worker._settled.set()
    late = _status("working", "Late", ts_ms=time.time() * 1000 + 1_000)
    worker._read_status(SimpleNamespace(stdout=iter([late])), "chat-9")
    assert len(seen) == 8
