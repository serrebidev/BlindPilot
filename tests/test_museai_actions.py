# SPDX-License-Identifier: MIT
"""Muse.ai actions preserve item identity and only run after explicit selection."""

import json
import threading
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


def test_museai_view_opens_before_gateway_answers(frame, monkeypatch):
    started, release = threading.Event(), threading.Event()
    item = {"id": "idea-1", "title": "An idea"}

    def items(_view):
        started.set()
        assert release.wait(5)
        return [item]

    monkeypatch.setattr(backend, "museai_items", items)
    began = time.monotonic()
    dialog = app.MuseAiDialog(frame, "ideas")
    try:
        assert time.monotonic() - began < 1
        assert started.wait(1)
        assert dialog.list.GetCount() == 0
        assert "Loading muse.ai" in dialog.status.GetLabel()
        release.set()
        _wait(lambda: dialog.list.GetCount() == 1)
    finally:
        release.set()
        dialog.Destroy()


def test_read_action_returns_before_gateway_answers(frame, monkeypatch):
    item = {"id": "idea-1", "title": "An idea"}
    monkeypatch.setattr(backend, "museai_items", lambda _view: [item])
    started, release = threading.Event(), threading.Event()

    def read(_view, _item):
        started.set()
        assert release.wait(5)
        return item

    class ReadView:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def ShowModal(self):
            return app.wx.ID_OK

    monkeypatch.setattr(backend, "museai_read_item", read)
    monkeypatch.setattr(app, "ReadView", ReadView)
    dialog = app.MuseAiDialog(frame, "ideas")
    _loaded(dialog)
    try:
        began = time.monotonic()
        dialog._read()
        assert time.monotonic() - began < 1
        assert started.wait(1)
        assert "Reading item" in dialog.status.GetLabel()
        release.set()
        _wait(lambda: not dialog._busy)
    finally:
        release.set()
        dialog.Destroy()


def test_failed_remote_action_is_spoken_and_keeps_dialog_open(frame, monkeypatch):
    item = {"id": "idea-1", "title": "An idea"}
    monkeypatch.setattr(backend, "museai_items", lambda _view: [item])
    monkeypatch.setattr(
        backend,
        "museai_read_item",
        lambda _view, _item: (_ for _ in ()).throw(ValueError("Gateway unavailable")),
    )
    spoken = []
    monkeypatch.setattr(app, "announce", spoken.append)
    dialog = app.MuseAiDialog(frame, "ideas")
    _loaded(dialog)
    try:
        dialog._read()
        _wait(lambda: not dialog._busy)
        assert "Gateway unavailable" in dialog.status.GetLabel()
        assert any("Gateway unavailable" in message for message in spoken)
        assert dialog and dialog.list.IsEnabled()
    finally:
        dialog.Destroy()


@pytest.fixture
def gateway(monkeypatch):
    calls = []
    reply = {"status": "queued", "chat": {"session_id": "chat-1", "is_thread": True}}

    def probe(_binary, args, _timeout):
        calls.append(args)
        return 0, json.dumps({"ok": True, "result": reply})

    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _backend: "muse-cli")
    monkeypatch.setattr(agent_backends, "_probe_backend", probe)
    return calls, reply


def test_run_idea_sends_selected_items_and_opens_returned_chat(gateway):
    calls, _reply = gateway
    result = backend.museai_run_idea({"id": "idea-1"}, ["part-2"])
    assert backend.museai_action_session(result) == "chat-1"
    assert calls[0][:4] == ["raw", "api.idea-cards.execute", "--param", "ideaCardId=idea-1"]
    assert json.loads(calls[0][5]) == {
        "ideaCardId": "idea-1",
        "mode": "selectedItems",
        "itemIds": ["part-2"],
    }


def test_feed_idea_uses_its_idea_id_not_its_feed_unit_id(gateway):
    calls, _reply = gateway
    backend.museai_run_idea({"unit_id": "post-1", "idea_action": {"idea_id": "idea-1"}})
    assert json.loads(calls[0][5]) == {"ideaCardId": "idea-1", "mode": "full"}


def test_invalid_idea_never_executes(gateway):
    calls, _reply = gateway
    with pytest.raises(ValueError):
        backend.museai_run_idea({"unit_id": "post-1"})
    assert calls == []


def test_schedule_run_sends_job_id_and_history_filters_same_job(gateway):
    calls, reply = gateway
    backend.museai_run_schedule({"id": "job-1"})
    assert calls[0][:4] == ["raw", "tasks.run", "--param", "job_id=job-1"]
    assert json.loads(calls[0][5]) == {}
    reply.clear()
    reply["runs"] = [{"job_id": "job-1", "status": "success", "result_summary": "Finished"}]
    assert "Finished" in backend.museai_schedule_history({"id": "job-1"})
    assert json.loads(calls[1][-1]) == {"job_id": "job-1", "limit": 100}


def test_links_exclude_local_files_and_unsafe_schemes():
    item = {
        "body_md": "[Article](https://example.com/story?a=1) [bad](file:///secret) javascript:bad"
    }
    assert backend.museai_item_links(item) == ["https://example.com/story?a=1"]


def test_goal_status_and_suggestion_decisions_use_verified_values(gateway):
    calls, _reply = gateway
    backend.museai_goal_status({"id": "goal-1"}, "paused")
    backend.museai_goal_decide("goal-1", {"id": "suggestion-1"}, "accepted")
    assert json.loads(calls[0][-1]) == {"status": "paused"}
    assert calls[1][:6] == [
        "raw",
        "goals.suggestions.decide",
        "--param",
        "goal_id=goal-1",
        "--param",
        "suggestion_id=suggestion-1",
    ]
    assert json.loads(calls[1][-1]) == {"decision": "accepted"}
    with pytest.raises(ValueError):
        backend.museai_goal_status({"id": "goal-1"}, "unknown")
    with pytest.raises(ValueError):
        backend.museai_goal_decide("goal-1", {"id": "suggestion-1"}, "allow_always")
    assert len(calls) == 2


def test_child_goals_are_available_with_their_own_ids_and_parent_context(gateway):
    _calls, reply = gateway
    reply.clear()
    reply["goals"] = [
        {
            "goal_id": "parent",
            "title": "Parent",
            "sub_goals": [{"goal_id": "child", "title": "Child", "status": "active"}],
        }
    ]
    goals = backend.museai_items("goals")
    assert [goal["goal_id"] for goal in goals] == ["parent", "child"]
    assert "under Parent" in backend.museai_item_title("goals", goals[1])


def test_view_discussion_is_a_draft_and_opening_view_executes_nothing(frame, monkeypatch):
    item = {"unit_id": "post-1", "title": "Article", "body_md": "Article details"}
    monkeypatch.setattr(backend, "museai_items", lambda _view: [item])
    monkeypatch.setattr(backend, "_cli_json", lambda _args: pytest.fail("Unexpected execution"))
    dialog = app.MuseAiDialog(frame, "feed")
    _loaded(dialog)
    monkeypatch.setattr(dialog, "EndModal", lambda _result: None)
    try:
        assert not dialog.run.IsEnabled()
        dialog._discuss()
        _wait(lambda: dialog.chat_request is not None)
        assert dialog.chat_request[0] == "draft"
        assert "Article details" in dialog.chat_request[1]
    finally:
        dialog.Destroy()


def test_explicit_run_opens_exact_returned_session_without_sending_a_second_message(
    frame, gateway, monkeypatch
):
    calls, _reply = gateway
    item = {"id": "idea-1", "title": "An idea"}
    monkeypatch.setattr(backend, "museai_items", lambda _view: [item])
    monkeypatch.setattr(backend, "museai_read_item", lambda _view, _item: item)
    dialog = app.MuseAiDialog(frame, "ideas")
    _loaded(dialog)
    monkeypatch.setattr(dialog, "EndModal", lambda _result: None)
    try:
        assert calls == []
        dialog._run()
        _wait(lambda: dialog.chat_request is not None)
        assert dialog.chat_request == ("follow", "chat-1", "An idea")
        assert len(calls) == 1 and calls[0][1] == "api.idea-cards.execute"
    finally:
        dialog.Destroy()


def test_cancelled_run_choice_never_executes(frame, gateway, monkeypatch):
    calls, _reply = gateway
    item = {"id": "idea-1", "title": "An idea", "items": [{"id": "part-1"}]}
    monkeypatch.setattr(backend, "museai_items", lambda _view: [item])
    monkeypatch.setattr(backend, "museai_read_item", lambda _view, _item: item)

    class Cancelled:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def ShowModal(self):
            return app.wx.ID_CANCEL

    monkeypatch.setattr(app.wx, "SingleChoiceDialog", Cancelled)
    dialog = app.MuseAiDialog(frame, "ideas")
    _loaded(dialog)
    try:
        dialog._run()
        assert calls == [] and dialog.chat_request is None
    finally:
        dialog.Destroy()


def test_schedules_without_an_enabled_flag_follow_muse_default(frame, monkeypatch):
    monkeypatch.setattr(backend, "museai_items", lambda _view: [{"id": "job-1", "title": "Task"}])
    dialog = app.MuseAiDialog(frame, "schedules")
    _loaded(dialog)
    try:
        assert dialog.run.IsEnabled()
        assert "Enabled" in backend.museai_item_text("schedules", dialog.items[0])
    finally:
        dialog.Destroy()
