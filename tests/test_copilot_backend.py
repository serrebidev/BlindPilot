"""GitHub Copilot CLI as a backend: its command line, its events, its history.

The event shapes are the Copilot SDK's (``generated/session-events.d.ts``)
and the ones saved Copilot sessions hold; Copilot 1.0.104 is the measured
release.
"""

from __future__ import annotations

import json

import agent_backends
import copilot_backend
import session_history
from task_list import TaskList


def _worker(**overrides):
    said: dict[str, list] = {
        "activity": [],
        "complete": [],
        "failed": [],
        "session": [],
        "subagents": [],
    }
    options = {
        "on_session": said["session"].append,
        "on_started": lambda: None,
        "on_activity": lambda kind, text: said["activity"].append((kind, text)),
        "on_complete": said["complete"].append,
        "on_failed": said["failed"].append,
        "on_done": lambda: None,
        "on_subagent": lambda *report: said["subagents"].append(report),
    }
    options.update(overrides)
    prompt = options.pop("prompt", "hello")
    session = options.pop("session", None)
    mode = options.pop("mode", "bypassPermissions")
    worker = copilot_backend.CopilotWorker(prompt, session, "C:/proj", mode, **options)
    return worker, said


def _event(kind: str, **data) -> dict:
    return {"type": kind, "data": data, "id": "x", "parentId": None}


def test_a_new_conversation_gets_its_id_from_blindpilot_and_keeps_it():
    worker, _said = _worker()
    command = worker._command("copilot")
    new_id = command[command.index("--session-id") + 1]
    assert len(new_id) == 36

    later, _ = _worker(session=new_id)
    resumed = later._command("copilot")
    assert resumed[resumed.index("--session-id") + 1] == new_id


def test_permission_modes_map_to_copilots_flags():
    def flags(mode: str) -> list[str]:
        worker, _ = _worker(mode=mode, model="gpt-5.5", effort="high")
        return worker._command("copilot")

    bypass = flags("bypassPermissions")
    assert "--allow-all" in bypass and "--allow-all-tools" in bypass
    assert bypass[bypass.index("--model") + 1] == "gpt-5.5"
    assert bypass[bypass.index("--reasoning-effort") + 1] == "high"
    assert flags("plan")[-6:-4] == ["--mode", "plan"]
    assert "--assisted-approval" in flags("auto")
    default = flags("default")
    assert "--allow-all" not in default and "--assisted-approval" not in default
    # Nobody can answer Copilot's ask_user in a headless turn.
    assert "--no-ask-user" in default


def test_the_answer_streams_and_a_new_message_starts_a_paragraph():
    worker, said = _worker()
    worker._handle_frame(_event("assistant.message_delta", messageId="m1", deltaContent="Look"))
    worker._handle_frame(_event("assistant.message_delta", messageId="m1", deltaContent="ing."))
    # The whole message repeats what streamed; it is not added twice.
    worker._handle_frame(_event("assistant.message", messageId="m1", content="Looking."))
    worker._handle_frame(
        _event(
            "tool.execution_start",
            toolCallId="t1",
            toolName="view",
            arguments={"path": "C:/proj/app.py"},
        )
    )
    worker._handle_frame(
        _event(
            "tool.execution_complete",
            toolCallId="t1",
            success=True,
            result={"content": "print(1)"},
        )
    )
    # A message that never streamed (streaming off) is taken whole.
    worker._handle_frame(_event("assistant.message", messageId="m2", content="Done."))
    worker._handle_frame({"type": "result", "exitCode": 0})

    assert said["complete"] == ["Looking.\n\nDone."]
    assert ("tool", "Reading app.py") in said["activity"]
    assert ("result", "print(1)") in said["activity"]


def test_a_subagents_own_messages_stay_out_of_the_answer():
    worker, said = _worker()
    worker._handle_frame(
        _event("subagent.started", toolCallId="t9", agentDisplayName="Explore", agentName="x")
    )
    worker._handle_frame(
        _event(
            "assistant.message_delta",
            messageId="s1",
            deltaContent="inner",
            parentToolCallId="t9",
        )
    )
    worker._handle_frame(_event("subagent.completed", toolCallId="t9", agentDisplayName="Explore"))
    worker._handle_frame(_event("assistant.message", messageId="m1", content="Outer."))
    worker._handle_frame({"type": "result", "exitCode": 0})

    assert said["complete"] == ["Outer."]
    assert [report[2] for report in said["subagents"]] == [
        agent_backends.SUBAGENT_RUNNING,
        agent_backends.SUBAGENT_COMPLETED,
    ]


def test_a_failed_tool_and_a_session_error_are_said():
    worker, said = _worker()
    worker._handle_frame(
        _event("tool.execution_start", toolCallId="t1", toolName="powershell", arguments={})
    )
    worker._handle_frame(
        _event(
            "tool.execution_complete",
            toolCallId="t1",
            success=False,
            error={"message": "exit 1\nmore"},
        )
    )
    worker._handle_frame(
        _event("session.error", errorType="quota", message="402 You have no quota")
    )

    assert ("tool", "Failed: powershell: exit 1") in said["activity"]
    assert ("notice", "402 You have no quota") in said["activity"]


def test_copilots_todo_list_reaches_the_task_list():
    worker, _said = _worker()
    reported: list = []
    worker.on_tasks = lambda *call: reported.append(call)
    todos = "- [x] Plan\n- [~] Build\n  - [ ] Test"
    worker._handle_frame(
        _event(
            "tool.execution_start",
            toolCallId="t1",
            toolName="update_todo",
            arguments={"todos": todos},
        )
    )
    assert reported == [("copilot", todos)]

    tasks = TaskList()
    assert tasks.apply_checklist(todos) == ["Plan"]
    assert tasks.lines() == ["1. Done: Plan", "2. In progress: Build", "3. To do: Test"]


def test_the_policy_refusal_reads_as_what_to_do():
    message = copilot_backend.readable_error(
        "Error: Access denied by policy settings (Request ID: ECA0:3AEF)"
    )
    assert message.startswith("Access denied by policy settings. ")
    assert "Request ID" not in message
    assert "github.com/settings/copilot" in message


def test_sign_in_is_read_from_copilots_config(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path))
    for name in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(copilot_backend, "_gh_signed_in", lambda timeout: False)
    assert not copilot_backend.copilot_auth_ok()

    (tmp_path / "config.json").write_text(
        "// managed\n" + json.dumps({"lastLoggedInUser": {"login": "octo", "host": "x"}}),
        encoding="utf-8",
    )
    assert copilot_backend.copilot_auth_ok()
    assert copilot_backend.copilot_account_lines() == ["Signed in: yes", "Account: octo"]


def test_saved_copilot_sessions_are_listed_and_read_back(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path))
    folder = tmp_path / "session-state" / "abc"
    folder.mkdir(parents=True)
    events = [
        _event("session.start", sessionId="abc", context={"cwd": "C:/proj"}),
        _event("user.message", content="Fix the build", transformedContent="<x>Fix</x>"),
        _event("assistant.message", messageId="1", content="On it.", toolRequests=[]),
        _event("assistant.message", messageId="2", content="inner", parentToolCallId="t"),
        _event("assistant.message", messageId="3", content="Fixed."),
    ]
    (folder / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events), encoding="utf-8"
    )

    entries = session_history.list_history("copilot", None)
    assert [(entry.session_id, entry.title, entry.cwd) for entry in entries] == [
        ("abc", "Fix the build", "C:/proj")
    ]
    turns = session_history.load_turns(entries[0])
    assert [(turn.prompt, turn.response) for turn in turns] == [
        ("Fix the build", "On it.\n\nFixed.")
    ]
