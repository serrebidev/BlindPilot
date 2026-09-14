"""Queue and steering use the GUI's drained turn boundary, including late Enter."""

from types import MethodType

import pytest

import blindpilot_app as app
from test_send_race import _DeadWorker, _panel


def panel():
    tab = _panel()
    tab._session_backend = app.BACKEND_COMMANDCODE
    tab.selected_backend = lambda: app.BACKEND_COMMANDCODE
    tab._pending_messages = []
    tab._queue_paused = False
    tab._cli_effort = "high"
    tab.sent = []
    for name in (
        "_on_send",
        "_on_steer",
        "_queue_message",
        "_send_queued_message",
        "_commandcode_command",
    ):
        setattr(tab, name, MethodType(getattr(app.SessionPanel, name), tab))
    tab._backend_uploads_attachments = lambda: False
    tab._build_send_text = MethodType(app.SessionPanel._build_send_text, tab)

    def launch(text, backend, extra):
        tab.sent.append((text, tab._session_id, backend, extra))
        tab._worker = _DeadWorker()

    tab._launch_turn = launch
    return tab


def finish(tab):
    app.SessionPanel._on_worker_finished(tab)


def test_enter_in_completion_gap_queues_instead_of_overlapping():
    tab = panel()
    first = tab._worker
    tab._on_send()
    assert tab._worker is first
    assert tab.sent == []
    assert tab.prompt.GetValue() == ""
    assert len(tab._pending_messages) == 1
    finish(tab)
    assert tab.sent[0][:2] == ("the second question", "session-1")


def test_multiple_followups_keep_order_attachments_and_unsent_draft():
    tab = panel()
    tab._attachments = ["file one.txt"]
    tab._on_send()
    tab.prompt.SetValue("third question")
    tab._on_send()
    tab.prompt.SetValue("draft still being written")
    tab._attachments = ["draft.txt"]
    finish(tab)
    assert "file one.txt" in tab.sent[0][0]
    assert "draft.txt" not in tab.sent[0][0]
    assert tab.prompt.GetValue() == "draft still being written"
    assert tab._attachments == ["draft.txt"]
    assert len(tab._pending_messages) == 1
    finish(tab)
    assert tab.sent[1][0] == "third question"
    assert tab._pending_messages == []


def test_steering_stops_once_and_waits_for_done_before_resuming():
    tab = panel()
    tab._worker.is_alive = lambda: True
    stopped = []
    tab._on_stop = lambda: (stopped.append(True), setattr(tab, "_stopping", True))
    tab._finish_stopped_turn = lambda: None
    tab.prompt.SetValue("ordinary followup")
    tab._on_send()
    tab.prompt.SetValue("use the other approach")
    tab._on_steer()
    assert stopped == [True]
    assert not tab.sent
    finish(tab)
    assert tab.sent[0][:2] == ("use the other approach", "session-1")
    assert tab._pending_messages[0][0] == "ordinary followup"


def test_steering_before_session_arrives_keeps_original_task_and_files():
    tab = panel()
    tab._session_id = None
    tab._active_send_text = "Original task with attached input.txt"
    tab.prompt.SetValue("changed instruction")
    tab._on_steer()
    finish(tab)
    assert "Original task with attached input.txt" in tab.sent[0][0]
    assert "changed instruction" in tab.sent[0][0]


def test_stop_in_completion_gap_pauses_queue_until_explicit_resume():
    tab = panel()
    tab._on_send()
    app.SessionPanel._on_stop(tab)
    finish(tab)
    assert not tab.sent
    tab.prompt.SetValue("/queue resume")
    tab._on_send()
    assert len(tab.sent) == 1


def test_failure_pauses_remaining_messages():
    tab = panel()
    tab._earcons.play_error = lambda: None
    tab._announce = lambda text, **_kwargs: tab.announced.append(text)
    tab._on_send()
    app.SessionPanel._on_failed(tab, "Network failed")
    finish(tab)
    assert tab._queue_paused
    assert len(tab._pending_messages) == 1
    assert not tab.sent


def test_backend_change_holds_queue():
    tab = panel()
    tab._on_send()
    tab.selected_backend = lambda: app.BACKEND_CLAUDE
    finish(tab)
    assert not tab.sent
    assert tab._queue_paused
    assert len(tab._pending_messages) == 1


def test_launch_failure_keeps_queued_message():
    tab = panel()
    tab._on_send()
    tab._launch_turn = lambda *_args: None
    finish(tab)
    assert tab._queue_paused
    assert tab._pending_messages[0][0] == "the second question"


@pytest.mark.parametrize(
    "command,mode",
    [
        ("/plan", "plan"),
        ("/mode auto-accept", "acceptEdits"),
        ("/mode:default", "default"),
        ("/mode dont-ask", "dontAsk"),
    ],
)
def test_mode_commands_apply_without_launching_model(command, mode):
    tab = panel()
    modes = []
    tab._set_mode = modes.append
    tab.prompt.SetValue(command)
    tab._on_send()
    assert modes == [mode]
    assert not tab.sent and not tab._pending_messages


def test_effort_is_applied_and_invalid_effort_preserves_input():
    tab = panel()
    choices = []
    tab.set_model = lambda model, effort: choices.append((model, effort))
    tab.prompt.SetValue("/effort xhigh")
    tab._on_send()
    assert choices == [("", "xhigh")]
    tab.prompt.SetValue("/effort nonsense")
    tab._on_send()
    assert tab.prompt.GetValue() == "/effort nonsense"
    assert len(choices) == 1


def test_add_dir_is_passed_to_next_turn_without_splitting_spaces(tmp_path):
    tab = panel()
    directory = tmp_path / "shared project"
    directory.mkdir()
    tab.prompt.SetValue(f'/add-dir "{directory}"')
    tab._on_send()
    tab.prompt.SetValue("read shared files")
    tab._on_send()
    finish(tab)
    assert tab.sent[0][3]["additional_dirs"] == (str(directory.resolve()),)


@pytest.mark.parametrize("command", ["/context", "/worktree", "/logout", "/mcp", "/unknown"])
def test_terminal_only_commands_are_explained_without_becoming_prompts(command):
    tab = panel()
    tab.prompt.SetValue(command)
    tab._on_send()
    assert not tab.sent and not tab._pending_messages
    assert "no headless command" in tab.announced[-1]
    assert tab.prompt.GetValue() == command


def test_plan_task_queues_a_real_prompt_and_changes_mode():
    tab = panel()
    modes = []
    tab._set_mode = modes.append
    tab.prompt.SetValue("/plan add a search feature")
    tab._on_send()
    assert modes == ["plan"]
    assert (
        tab._pending_messages[0][0]
        == "Plan this task without changing files:\nadd a search feature"
    )


def test_session_alias_uses_frontend_history(monkeypatch):
    tab = panel()
    opened = []
    frame = type("Frame", (), {"_open_history": lambda _self: opened.append(True)})()
    monkeypatch.setattr(app.wx, "GetTopLevelParent", lambda _tab: frame)
    monkeypatch.setattr(app.wx, "CallAfter", lambda fn: fn())
    tab.prompt.SetValue("/sessions")
    tab._on_send()
    assert opened == [True]
    assert not tab._pending_messages
