# SPDX-License-Identifier: MIT
"""Muse.ai account views read the gateway, and decisions grant only one action."""

import json
import time

import pytest

import agent_backends
import museai_backend as backend
import blindpilot_app as app


def _wait(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        app.wx.GetApp().Yield()
        time.sleep(0.01)
    assert predicate()


def _loaded(dialog):
    _wait(lambda: not dialog._busy)


@pytest.fixture
def gateway(monkeypatch):
    replies, calls = {}, []

    def probe(_binary, args, _timeout):
        calls.append(args)
        return 0, json.dumps({"ok": True, "result": replies[args[1]]})

    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _backend: "muse-cli")
    monkeypatch.setattr(agent_backends, "_probe_backend", probe)
    return replies, calls


def test_feed_reads_complete_items_in_day_and_edition_order(gateway):
    replies, calls = gateway
    replies["feed.list"] = {
        "days": [
            {
                "local_date": "2026-10-08",
                "editions": [
                    {
                        "kind": "morning",
                        "units": [
                            {
                                "unit_id": "f1",
                                "title": "News",
                                "kicker": "Your morning",
                                "body_md": "Full article, with a link: https://example.com",
                            }
                        ],
                    }
                ],
            }
        ]
    }
    items = backend.museai_items("feed")
    assert [item["unit_id"] for item in items] == ["f1"]
    text = backend.museai_item_text("feed", items[0])
    assert "Full article" in text and "https://example.com" in text
    assert "2026-10-08" in text
    assert calls == [["raw", "feed.list"]]


def test_schedules_show_enabled_state_cadence_and_next_run(gateway):
    replies, _calls = gateway
    replies["tasks.list"] = {
        "schedules": [
            {
                "id": "s1",
                "title": "Morning report",
                "schedule_key": "0 9 * * *",
                "enabled": False,
                "next_run_at_utc": "2026-10-09T16:00:00Z",
                "timezone": "America/Vancouver",
            }
        ],
        "invalid": [],
    }
    item = backend.museai_items("schedules")[0]
    text = backend.museai_item_text("schedules", item)
    assert "Disabled" in text and "0 9 * * *" in text
    assert "2026-10-09T16:00:00Z" in text and "America/Vancouver" in text


def test_ideas_keep_the_content_and_grouping_without_executing(gateway):
    replies, calls = gateway
    replies["api.idea-cards.list"] = {
        "sections": [
            {
                "title": "For you",
                "cards": [
                    {
                        "id": "i1",
                        "title": "Make a site",
                        "summary": "A project idea",
                        "content": {"buildSummary": "Complete plan"},
                        "prerequisiteNotes": "Needs a domain",
                    }
                ],
            }
        ]
    }
    item = backend.museai_items("ideas")[0]
    text = backend.museai_item_text("ideas", item)
    assert "For you" in text and "Complete plan" in text and "Needs a domain" in text
    assert calls == [["raw", "api.idea-cards.list"]]


def test_approvals_prefer_current_schema_and_keep_recent_decisions_read_only(gateway):
    replies, _calls = gateway
    replies["egress.approvals"] = {
        "pending": [{"approval_id": "obsolete"}],
        "pending_approvals": [
            {
                "approval_id": "a1",
                "display": {
                    "summary_title": "Send a message",
                    "purpose_summary": "Reply to Sam",
                    "detail_rows": [{"label": "Recipient", "value": "Sam"}],
                },
            }
        ],
        "recent_approvals": [
            {
                "approval_id": "old",
                "decision": "deny",
                "display": {"summary_title": "Earlier action"},
            }
        ],
    }
    items = backend.museai_items("approvals")
    assert [(item["approval_id"], item["_pending"]) for item in items] == [
        ("a1", True),
        ("old", False),
    ]
    assert "Reply to Sam" in backend.museai_item_text("approvals", items[0])
    assert "Recipient: Sam" in backend.museai_item_text("approvals", items[0])


@pytest.mark.parametrize("decision", ["allow_once", "deny"])
def test_decisions_send_verified_sentinel_body_and_never_persistent_grants(gateway, decision):
    replies, calls = gateway
    replies["egress.approval.decide"] = {"status": "resolved"}
    backend.museai_decide("a1", decision)
    assert calls[0][:4] == ["raw", "egress.approval.decide", "--param", "approval_id=a1"]
    assert json.loads(calls[0][5]) == {"approval_id": "a1", "decision": decision}


def test_read_errors_are_not_empty_account_views(monkeypatch):
    monkeypatch.setattr(backend, "_cli_json", lambda _args: None)
    with pytest.raises(ValueError):
        backend.museai_items("approvals")


def test_invalid_or_persistent_decisions_never_reach_the_cli(gateway):
    _replies, calls = gateway
    with pytest.raises(ValueError):
        backend.museai_decide("a1", "allow_always")
    with pytest.raises(ValueError):
        backend.museai_decide("", "allow_once")
    assert calls == []


@pytest.mark.parametrize("view", ["approvals", "schedules", "feed", "ideas"])
def test_changed_gateway_shapes_are_reported_instead_of_silently_empty(gateway, view):
    replies, _calls = gateway
    replies[backend.MUSEAI_VIEWS[view]] = {"unexpected": []}
    with pytest.raises(ValueError):
        backend.museai_items(view)


def test_native_views_read_full_details_and_recent_approvals_cannot_be_decided(
    frame, gateway, monkeypatch
):
    replies, calls = gateway
    replies["egress.approvals"] = {
        "pending_approvals": [],
        "recent_approvals": [
            {
                "approval_id": "old",
                "decision": "deny",
                "display": {"summary_title": "Earlier action"},
            }
        ],
    }
    dialog = app.MuseAiDialog(frame, "approvals")
    _loaded(dialog)
    try:
        assert dialog.list.GetCount() == 1
        assert not dialog.decide.IsEnabled()
        assert "Earlier action" in dialog.list.GetString(0)
    finally:
        dialog.Destroy()
    assert calls == [["raw", "egress.approvals"]]


def test_view_read_errors_are_spoken_and_actions_disabled(frame, monkeypatch):
    spoken = []
    monkeypatch.setattr(app, "announce", spoken.append)
    monkeypatch.setattr(
        backend, "museai_items", lambda _view: (_ for _ in ()).throw(ValueError("Signed out"))
    )
    dialog = app.MuseAiDialog(frame, "approvals")
    _loaded(dialog)
    try:
        assert dialog.list.GetCount() == 0
        assert not dialog.read.IsEnabled() and not dialog.decide.IsEnabled()
        assert spoken and "Signed out" in spoken[-1]
    finally:
        dialog.Destroy()


@pytest.mark.parametrize(
    "details",
    [
        {"display": {"permission_question": ["bad"]}},
        {"payload": ["bad"]},
        {"payload": {"network_payload": ["bad"]}},
        {"display": {"detail_rows": ["bad"]}},
    ],
)
def test_unreadable_approval_details_raise_adapter_error(details):
    with pytest.raises(ValueError, match="approval"):
        backend.museai_item_text("approvals", {"approval_id": "a1", **details})


def test_unreadable_approval_decision_is_spoken_without_sending(frame, gateway, monkeypatch):
    replies, calls = gateway
    replies["egress.approvals"] = {
        "pending_approvals": [{"approval_id": "a1", "payload": ["bad"]}],
        "recent_approvals": [],
    }
    spoken = []
    monkeypatch.setattr(app, "announce", spoken.append)
    dialog = app.MuseAiDialog(frame, "approvals")
    _loaded(dialog)
    try:
        dialog._decide()
        assert "approval" in spoken[-1]
        assert calls == [["raw", "egress.approvals"]]
    finally:
        dialog.Destroy()


def test_titles_and_schedule_times_read_as_words(gateway):
    """Measured on a real account: decisions came back as ids (allow_always,
    timed_out), the heartbeat schedule had no title, a disabled schedule's
    title did not say so, and the next run was seconds since 1970."""
    replies, _calls = gateway
    replies["egress.approvals"] = {
        "pending_approvals": [],
        "recent_approvals": [
            {"approval_id": "a", "decision": "allow_always", "display": {"summary_title": "Fill"}},
            {"approval_id": "b", "decision": "timed_out", "display": {"summary_title": "SSH"}},
        ],
    }
    replies["tasks.list"] = {
        "schedules": [
            {"id": "heartbeat", "title": None, "is_heartbeat": True, "next_run_at_utc": 1795128579},
            {"id": "s2", "title": "Watch", "enabled": False},
        ]
    }
    titles = [backend.museai_item_title("approvals", a) for a in backend.museai_items("approvals")]
    assert titles == ["Allow always: Fill", "Timed out: SSH"]
    beat, off = backend.museai_items("schedules")
    assert backend.museai_item_title("schedules", beat) == "Heartbeat"
    assert backend.museai_item_title("schedules", off) == "Disabled: Watch"
    text = backend.museai_item_text("schedules", beat)
    assert "1795128579" not in text and "Next run: " in text and "2026" in text


def test_refresh_stays_usable_and_keeps_the_selected_item(frame, gateway):
    """Refresh was disabled after every load, and a refresh after Pause put
    the reader back on the first of 82 goals."""
    replies, _calls = gateway
    replies["goals.list"] = {
        "goals": [{"goal_id": f"g{i}", "title": f"Goal {i}", "status": "active"} for i in range(3)]
    }
    dialog = app.MuseAiDialog(frame, "goals")
    _loaded(dialog)
    try:
        assert dialog.refresh_button.IsEnabled()
        dialog.list.SetSelection(2)
        replies["goals.list"]["goals"][2]["status"] = "paused"
        dialog._refresh()
        _loaded(dialog)
        assert dialog.refresh_button.IsEnabled()
        assert dialog.list.GetSelection() == 2
        assert dialog.list.GetString(2) == "paused: Goal 2"
    finally:
        dialog.Destroy()


def test_the_main_chat_id_is_asked_for_once(monkeypatch):
    calls = []

    def threads(args):
        calls.append(args)
        return [{"session_id": "main", "thread": False}, {"session_id": "side", "thread": True}]

    monkeypatch.setattr(backend, "_cli_json", threads)
    assert backend.museai_main_session_id() == "main"
    assert backend.museai_main_session_id() == "main"
    assert len(calls) == 1
