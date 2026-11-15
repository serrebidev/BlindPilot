"""Recovery chooses a new session; it never resumes the source for a prompt."""

import pytest

from session_history import HistoryEntry, HistoryTurn


def test_native_branch_uses_the_completed_turn_boundary():
    from conversation_recovery import RecoveryRequest, prepare_recovery

    entry = HistoryEntry("codex", "source", "Title", "", 0)
    turns = [
        HistoryTurn("one", "answer", turn_id="turn-1"),
        HistoryTurn("two", "later", turn_id="turn-2"),
    ]
    result = prepare_recovery(RecoveryRequest(entry, 0, "branch"), turns)
    assert result.fork_source == "source" and result.fork_at == "turn-1"
    assert len(result.turns) == 1 and result.prompt == ""
    assert result.context == "" and result.session_id is None


def test_retry_stops_before_the_selected_user_message():
    from conversation_recovery import RecoveryRequest, prepare_recovery

    entry = HistoryEntry("claude", "source", "Title", "", 0)
    turns = [HistoryTurn("one", "answer", message_id="assistant-1"), HistoryTurn("two", "later")]
    result = prepare_recovery(RecoveryRequest(entry, 1, "retry"), turns)
    assert result.fork_at == "assistant-1" and result.prompt == "two"
    assert len(result.turns) == 1


def test_first_message_retry_starts_empty_without_a_source_resume():
    from conversation_recovery import RecoveryRequest, prepare_recovery

    entry = HistoryEntry("claude", "source", "Title", "", 0)
    result = prepare_recovery(RecoveryRequest(entry, 0, "retry"), [HistoryTurn("first", "answer")])
    assert not result.fork_source and not result.context
    assert result.turns == () and result.prompt == "first"


def test_missing_native_boundary_is_an_explicit_context_copy():
    from conversation_recovery import RecoveryRequest, prepare_recovery

    entry = HistoryEntry("hermes", "source", "Title", "", 0)
    result = prepare_recovery(RecoveryRequest(entry, 0, "branch"), [HistoryTurn("one", "answer")])
    assert not result.fork_source and result.session_id is None
    assert "one" in result.context and "answer" in result.context
    assert "context copy" in result.label.lower()


def test_stale_file_identity_rejects_recovery(tmp_path):
    from conversation_recovery import RecoveryRequest, prepare_recovery, source_identity

    path = tmp_path / "session.jsonl"
    path.write_text("before")
    entry = HistoryEntry("claude", "source", "Title", str(path), 0)
    request = RecoveryRequest(entry, 0, "branch", source_identity(entry))
    path.write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        prepare_recovery(request, [HistoryTurn("one", "answer")])


def test_codex_rollout_keeps_its_native_turn_id_across_tool_records(tmp_path):
    import json
    from session_history import _CODEX, _jsonl_turns

    path = tmp_path / "rollout.jsonl"
    records = [
        {"type": "turn_context", "payload": {"turn_id": "turn-1"}},
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "question"}],
            },
        },
        {"type": "response_item", "payload": {"type": "function_call", "name": "read"}},
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "answer"}],
            },
        },
    ]
    path.write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")
    turns = _jsonl_turns(_CODEX, HistoryEntry("codex", "source", "Title", str(path), 0))
    assert len(turns) == 1 and turns[0].turn_id == "turn-1"


def test_claude_fork_command_cannot_resume_without_forking():
    import claude_session

    wants = claude_session.Wants(".", "default", fork_source="source", fork_at="boundary")
    command = claude_session.build_command("claude", wants, "")
    assert command[command.index("--resume") + 1] == "source"
    assert "--fork-session" in command
    assert command[command.index("--resume-session-at") + 1] == "boundary"
    assert wants.session_id is None


def test_retry_preserves_attachment_references():
    from conversation_recovery import RecoveryRequest, prepare_recovery

    entry = HistoryEntry("codex", "source", "Title", "", 0)
    result = prepare_recovery(
        RecoveryRequest(entry, 0, "retry"),
        [HistoryTurn("Describe", "answer", attachments=("image.png",))],
    )
    assert result.prompt == "Describe" and result.attachments == ("image.png",)


def test_response_number_gaps_do_not_choose_the_wrong_turn():
    from conversation_recovery import turn_index_for_response
    import blindpilot_app as app

    turns = [
        app.Turn("first", "answer", response_number=1),
        app.Turn("third", "answer", response_number=3),
    ]
    assert turn_index_for_response(turns, 3) == 1
    assert turn_index_for_response(turns, 2) is None


def test_empty_success_cannot_capture_the_next_completed_responses_number():
    from conversation_recovery import turn_index_for_response
    import blindpilot_app as app

    turns = [
        app.Turn("empty", "", response_number=2),
        app.Turn("answered", "answer", response_number=2),
    ]
    assert turn_index_for_response(turns, 2) == 1


@pytest.mark.parametrize("changed", [False, True])
def test_agent_copy_restores_mode_and_attachments_but_discards_stale_completion(
    monkeypatch, changed
):
    from types import SimpleNamespace
    import blindpilot_app as app
    from markdown_rows import Row

    class Prompt:
        value = ""

        def GetValue(self):
            return self.value

        def SetValue(self, value):
            self.value = value

    jobs, callbacks, opened, selections = [], [], [], []
    monkeypatch.setattr(
        app.threading,
        "Thread",
        lambda target, **kw: SimpleNamespace(start=lambda: jobs.append(target)),
    )
    monkeypatch.setattr(app.wx, "CallAfter", lambda cb, *args: callbacks.append((cb, args)))
    import conversation_export
    import session_history

    for holder in (app, conversation_export, session_history):
        if hasattr(holder, "list_history"):
            monkeypatch.setattr(holder, "list_history", lambda **kw: [])
    monkeypatch.setattr(app, "announce", lambda text: None)
    source = SimpleNamespace(
        _run_in_progress=lambda: False,
        _session_id="source",
        _session_backend="claude",
        cwd=".",
        mode=app._MODE_VALUES[-1],
        model="model",
        effort="high",
        tab_title="Title",
        prompt=Prompt(),
        _turns=[app.Turn("Describe", "answer", attachments=("image.png",), response_number=3)],
        _rows=[Row("you", "Describe", "Describe", 3)],
    )
    destination = SimpleNamespace(
        prompt=Prompt(),
        mode_picker=SimpleNamespace(SetSelection=selections.append),
        _refresh_list=lambda: None,
        backend_changed=lambda: None,
        focus_prompt=lambda: None,
    )
    notebook = SimpleNamespace(active=source)
    notebook.GetCurrentPage = lambda: notebook.active
    frame = SimpleNamespace(
        notebook=notebook,
        _add_session=lambda *args, **kwargs: opened.append(destination) or destination,
    )
    app.MainFrame._recover_agent_response(frame, source, source._rows[0], True)
    if changed:
        notebook.active = object()
    jobs.pop()()
    callback, args = callbacks.pop()
    callback(*args)
    if changed:
        assert not opened
    else:
        assert opened == [destination]
        assert destination._pending_recovery.session_id is None
        assert destination._attachments == ["image.png"]
        assert destination.prompt.GetValue() == "Describe"
        assert selections == [app._MODE_VALUES.index(source.mode)]
        assert destination.mode == source.mode
