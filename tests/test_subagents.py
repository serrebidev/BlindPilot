"""The subagents list: each backend's reports, the window's bookkeeping, and Stop.

Every backend reports the agents it spawns in its own shape. Each test feeds a
worker the shape its provider documents (or, where the documentation is thin,
the shape its installed source emits) and checks what reaches the window as
(agent id, name, status, line). Stop is checked the same way: a turn's own
interrupt does not reach the agents it started, so each worker has to name
them itself.
"""

from types import SimpleNamespace

import agent_backends
from agent_backends import (
    SUBAGENT_COMPLETED,
    SUBAGENT_FAILED,
    SUBAGENT_RUNNING,
    SUBAGENT_STOPPED,
    subagent_line,
    subagent_status,
)


def _callbacks(reports):
    return {
        "on_session": lambda _s: None,
        "on_started": lambda: None,
        "on_activity": lambda _k, _t: None,
        "on_complete": lambda _t: None,
        "on_failed": lambda _m: None,
        "on_done": lambda: None,
        "on_subagent": lambda *report: reports.append(report),
    }


# ----- the shared vocabulary -----


def test_every_providers_state_words_land_on_the_four_the_window_shows():
    assert subagent_status("inProgress") == SUBAGENT_RUNNING
    assert subagent_status("pendingInit") == SUBAGENT_RUNNING
    assert subagent_status("complete") == SUBAGENT_COMPLETED
    assert subagent_status("resultReady") == SUBAGENT_COMPLETED
    assert subagent_status("errored") == SUBAGENT_FAILED
    assert subagent_status("timedOut") == SUBAGENT_FAILED
    assert subagent_status("interrupted") == SUBAGENT_STOPPED
    assert subagent_status("shutdown") == SUBAGENT_STOPPED
    # A word nobody uses yet leaves the agent where it was.
    assert subagent_status("somethingNew") == ""


def test_a_log_line_is_one_line_and_bounded():
    assert subagent_line("  two\nlines  ") == "two lines"
    assert len(subagent_line("x" * 1000)) == 300


# ----- the window -----


def _panel():
    from doubles import panel_stub

    return panel_stub()


def test_the_window_keeps_one_entry_per_agent_and_its_log():
    import blindpilot_app as app

    panel = _panel()
    app.SessionPanel._on_subagent(panel, "a1", "Explore", SUBAGENT_RUNNING, "Task: find it")
    app.SessionPanel._on_subagent(panel, "a1", "", "", "Read main.py")
    app.SessionPanel._on_subagent(panel, "a1", "", "", "Read main.py")

    entry = panel._subagents["a1"]
    assert entry.name == "Explore"
    assert entry.status == SUBAGENT_RUNNING
    # A repeat of the last line is not logged twice.
    assert entry.lines == ["Task: find it", "Read main.py"]
    assert entry.label() == "Explore, running: Read main.py"


def test_agents_a_turn_never_reported_finished_end_with_it():
    import blindpilot_app as app

    panel = _panel()
    app.SessionPanel._on_subagent(panel, "a1", "One", SUBAGENT_RUNNING, "")
    app.SessionPanel._on_subagent(panel, "a2", "Two", SUBAGENT_COMPLETED, "")

    panel._stopping = True
    app.SessionPanel._settle_subagents(panel)
    assert panel._subagents["a1"].status == SUBAGENT_STOPPED
    assert panel._subagents["a2"].status == SUBAGENT_COMPLETED

    # The next turn starts with the finished ones let go.
    app.SessionPanel._prune_subagents(panel)
    assert panel._subagents == {}


def test_agents_that_outlive_their_turn_stay_running_when_it_ends():
    import blindpilot_app as app

    panel = _panel()
    panel._subagents_outlive_turn = True
    app.SessionPanel._on_subagent(panel, "a1", "Background", SUBAGENT_RUNNING, "")
    app.SessionPanel._settle_subagents(panel)
    assert panel._subagents["a1"].status == SUBAGENT_RUNNING


# ----- Claude Code -----


def _claude(reports):
    import blindpilot_app as app

    worker = app.ClaudeWorker("hi", None, ".", "default", **_callbacks(reports))
    session = SimpleNamespace(agent_tasks={}, agent_calls=set(), background_agents=set())
    return worker, session


def test_claude_agents_are_followed_from_the_tool_call_to_the_notification():
    reports = []
    worker, session = _claude(reports)
    track = worker._track_subagents
    track(
        {
            "type": "assistant",
            "parent_tool_use_id": None,
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "Agent",
                        "input": {"description": "Find the bug", "prompt": "Look in src"},
                    }
                ]
            },
        },
        session,
    )
    track(
        {
            "type": "system",
            "subtype": "task_started",
            "task_id": "t1",
            "tool_use_id": "toolu_1",
            "description": "Find the bug",
            "task_type": "local_agent",
        },
        session,
    )
    # A background shell is a task too, but not an agent.
    track(
        {"type": "system", "subtype": "task_started", "task_id": "t2", "task_type": "local_bash"},
        session,
    )
    track(
        {
            "type": "assistant",
            "parent_tool_use_id": "toolu_1",
            "message": {"content": [{"type": "text", "text": "Reading files"}]},
        },
        session,
    )
    track(
        {"type": "system", "subtype": "task_progress", "task_id": "t1", "last_tool_name": "Grep"},
        session,
    )
    track(
        {
            "type": "system",
            "subtype": "task_notification",
            "task_id": "t1",
            "status": "completed",
            "summary": "Found it in parser.py",
        },
        session,
    )

    assert reports[0] == ("toolu_1", "Find the bug", SUBAGENT_RUNNING, "Task: Look in src")
    assert ("toolu_1", "", "", "Reading files") in reports
    assert ("toolu_1", "", "", "Using Grep") in reports
    assert reports[-1] == ("toolu_1", "", SUBAGENT_COMPLETED, "Found it in parser.py")
    assert all(report[0] == "toolu_1" for report in reports)
    assert session.agent_tasks == {}


def test_a_background_agents_launch_is_not_its_answer():
    reports = []
    worker, session = _claude(reports)
    session.agent_calls.add("toolu_9")
    worker._track_subagents(
        {
            "type": "user",
            "parent_tool_use_id": None,
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_9",
                        "content": "Async agent launched successfully. agentId: x",
                    }
                ]
            },
        },
        session,
    )
    assert reports == []
    assert "toolu_9" in session.background_agents


def test_claude_stop_ends_each_running_agent_before_the_turn():
    import claude_session

    asked = []
    session = claude_session.ClaudeSession.__new__(claude_session.ClaudeSession)
    session.agent_tasks = {"t1": "toolu_1", "t2": "toolu_2"}
    session.send_control = lambda subtype, timeout=None, **fields: asked.append((subtype, fields))
    claude_session.ClaudeSession.stop_agents(session)
    assert asked == [("stop_task", {"task_id": "t1"}), ("stop_task", {"task_id": "t2"})]


# ----- Muse Code -----


def _muse(reports):
    from muse_worker import MuseWorker

    worker = MuseWorker("hi", None, ".", "default", **_callbacks(reports))
    worker._live_session = "sess"
    return worker


def test_muse_subagent_items_become_one_agent_with_its_result():
    reports = []
    worker = _muse(reports)
    item = {
        "kind": "subagent",
        "itemId": "i1",
        "subagentId": "sa1",
        "role": "reviewer",
        "objective": "Review the diff",
        "status": "inProgress",
        "controlStatus": "running",
    }
    worker._track_children(item)
    worker._track_children({**item, "controlStatus": "resultReady"})
    worker._track_children(
        {
            **item,
            "status": "completed",
            "controlStatus": "closed",
            "result": {"summary": "Looks good", "artifactRefs": []},
        }
    )
    assert reports[0] == ("sa1", "reviewer", SUBAGENT_RUNNING, "Task: Review the diff")
    assert ("sa1", "reviewer", SUBAGENT_RUNNING, "State: resultReady") in reports
    assert reports[-2:] == [
        ("sa1", "reviewer", "", "State: closed"),
        ("sa1", "", SUBAGENT_COMPLETED, "Looks good"),
    ]


def test_muse_children_are_followed_through_the_subagent_tools():
    # The shape measured live on Muse 1.4.1: no subagent item, only the
    # parent's subagent_spawn and subagent_wait tool calls.
    reports = []
    worker = _muse(reports)
    spawn = {
        "kind": "toolCall",
        "tool": "subagent_spawn",
        "status": "completed",
        "args": '{"objective":"Run the tests","role":"test-runner"}',
        "visibleOutput": '{"status":"accepted","subagent_id":"sa-9","agent_path":"main/test-runner/1"}',
    }
    worker._track_children({**spawn, "status": "inProgress", "visibleOutput": None})
    worker._track_children(spawn)
    worker._track_children(
        {
            "kind": "toolCall",
            "tool": "subagent_wait",
            "status": "completed",
            "args": '{"subagent_id":"sa-9","wait_for":"result_ready"}',
            "visibleOutput": '{"status":"ready","subagent_id":"sa-9","summary":"All 12 passed"}',
        }
    )
    assert reports == [
        ("sa-9", "test-runner", SUBAGENT_RUNNING, "Task: Run the tests"),
        ("sa-9", "", SUBAGENT_COMPLETED, "All 12 passed"),
    ]
    assert worker._subagents == {"sa-9": SUBAGENT_COMPLETED}


def test_muse_stop_stops_running_children_and_workflows():
    reports = []
    worker = _muse(reports)
    fired = []
    worker._fire = lambda method, params=None, retry=True: fired.append((method, params)) or True
    worker._track_children(
        {"kind": "subagent", "subagentId": "sa1", "objective": "x", "status": "inProgress"}
    )
    worker._track_children(
        {"kind": "subagent", "subagentId": "sa2", "objective": "y", "status": "completed"}
    )
    worker._track_children(
        {
            "kind": "workflow",
            "workflowRunId": "run1",
            "children": [{"childId": "c1", "attempt": 1, "status": "running", "label": "Build"}],
        }
    )
    worker._stop_children()
    methods = [
        (method, params.get("subagentId") or params.get("workflowRunId"))
        for method, params in fired
    ]
    assert methods == [("subagent/stop", "sa1"), ("workflow/cancel", "run1")]
    assert all(params["sessionId"] == "sess" and params["commandId"] for _m, params in fired)


# ----- Hermes -----


def test_hermes_relayed_child_events_become_an_agent():
    from hermes_worker import HermesWorker

    reports = []
    worker = HermesWorker("hi", None, ".", "default", **_callbacks(reports))
    payload = {"subagent_id": "sub-1", "goal": "Research A"}
    worker._subagent_event("start", {**payload, "text": "Research A"})
    worker._subagent_event("tool", {**payload, "tool_name": "web_search", "text": "rivers"})
    worker._subagent_event("complete", {**payload, "status": "completed", "summary": "Done"})
    assert reports == [
        ("sub-1", "Research A", SUBAGENT_RUNNING, "Task: Research A"),
        ("sub-1", "Research A", "", "web_search: rivers"),
        ("sub-1", "Research A", SUBAGENT_COMPLETED, "Done"),
    ]


def test_hermes_stop_interrupts_each_running_child_by_name():
    from hermes_worker import HermesWorker

    worker = HermesWorker("hi", None, ".", "default", **_callbacks([]))
    worker._live_session = "live-1"
    worker._subagents = {"sub-1": SUBAGENT_RUNNING, "sub-2": SUBAGENT_COMPLETED}
    sent = []
    worker._request = lambda method, params: sent.append((method, params)) or True
    worker.cancel()
    assert sent == [
        ("subagent.interrupt", {"session_id": "live-1", "subagent_id": "sub-1"}),
        ("session.interrupt", {"session_id": "live-1"}),
    ]


# ----- Codex -----


def test_codex_spawned_agents_are_read_on_this_turns_inbox():
    reports = []
    worker = agent_backends.CodexWorker("hi", "thr_parent", ".", "default", **_callbacks(reports))
    attached = []
    worker._server = SimpleNamespace(attach=lambda thread, inbox: attached.append(thread))
    worker._inbox = object()
    worker._item_completed(
        {
            "type": "collabAgentToolCall",
            "id": "c1",
            "tool": "spawnAgent",
            "senderThreadId": "thr_parent",
            "receiverThreadIds": ["thr_child"],
            "prompt": "Count the tests",
            "agentsStates": {"thr_child": {"status": "running"}},
            "status": "completed",
        }
    )
    assert attached == ["thr_child"]
    worker._child_message("thr_child", "turn/started", {"turn": {"id": "turn_c"}}, {})
    worker._child_message(
        "thr_child",
        "item/started",
        {"item": {"type": "commandExecution", "command": "pytest -q"}},
        {},
    )
    worker._child_message(
        "thr_child",
        "item/completed",
        {"item": {"type": "agentMessage", "text": "There are 12"}},
        {},
    )
    worker._child_message(
        "thr_child", "turn/completed", {"turn": {"id": "turn_c", "status": "completed"}}, {}
    )
    assert reports[0] == ("thr_child", "Count the tests", SUBAGENT_RUNNING, "Task: Count the tests")
    assert ("thr_child", "", "", "Running: pytest -q") in reports
    assert ("thr_child", "", "", "There are 12") in reports
    assert reports[-1] == ("thr_child", "", SUBAGENT_COMPLETED, "Finished: completed")


def test_codex_child_approvals_are_answered_not_declined_unread():
    worker = agent_backends.CodexWorker(
        "hi", "thr_parent", ".", "bypassPermissions", **_callbacks([])
    )
    sent = []
    worker._send = lambda message: sent.append(message) or True
    worker._children = {"thr_child": SUBAGENT_RUNNING}
    worker._child_message(
        "thr_child",
        "item/commandExecution/requestApproval",
        {"threadId": "thr_child"},
        {"id": 7, "method": "item/commandExecution/requestApproval"},
    )
    assert sent == [{"id": 7, "result": {"decision": "accept"}}]


def test_codex_stop_interrupts_each_running_agent():
    worker = agent_backends.CodexWorker("hi", "thr_parent", ".", "default", **_callbacks([]))
    worker._children = {"a": SUBAGENT_RUNNING, "b": SUBAGENT_COMPLETED}
    worker._child_turns = {"a": "turn_a", "b": "turn_b"}
    sent = []
    server = SimpleNamespace(send=lambda message: sent.append(message) or True, next_id=lambda: 42)
    worker._stop_children(server)
    assert sent == [
        {"method": "turn/interrupt", "id": 42, "params": {"threadId": "a", "turnId": "turn_a"}}
    ]


# ----- opencode -----


def test_opencode_task_children_are_followed_and_kept_out_of_the_answer():
    reports = []
    worker = agent_backends.OpencodeWorker(
        "hi", "ses_parent", ".", "default", **_callbacks(reports)
    )
    worker._part(
        {
            "id": "p1",
            "sessionID": "ses_parent",
            "type": "tool",
            "tool": "task",
            "state": {
                "status": "running",
                "input": {"description": "Explore", "prompt": "Map the repo"},
                "metadata": {"sessionId": "ses_child"},
            },
        }
    )
    worker._roles["m_child"] = "assistant"
    worker._part(
        {
            "id": "p2",
            "sessionID": "ses_child",
            "messageID": "m_child",
            "type": "text",
            "text": "Found three packages",
        }
    )
    worker._part(
        {
            "id": "p1",
            "sessionID": "ses_parent",
            "type": "tool",
            "tool": "task",
            "state": {"status": "completed", "metadata": {"sessionId": "ses_child"}},
        }
    )
    assert reports[0] == ("ses_child", "Explore", SUBAGENT_RUNNING, "Task: Map the repo")
    assert ("ses_child", "", "", "Found three packages") in reports
    assert reports[-1][2] == SUBAGENT_COMPLETED
    # The child's words are the child's, not this turn's answer.
    assert worker._answer == []


# ----- Command Code -----


def test_command_code_nested_agent_runs_become_an_agent():
    from commandcode_worker import CommandcodeWorker

    reports = []
    worker = CommandcodeWorker("hi", None, ".", "default", **_callbacks(reports))
    worker._handle_event(
        {
            "type": "tool_queued",
            "toolCallId": "call_1",
            "toolName": "agent",
            "input": {"description": "Search", "prompt": "Find usages"},
        }
    )
    worker._handle_event(
        {"type": "subagent_start", "toolCallId": "call_1", "subagentType": "explore"}
    )
    worker._handle_event(
        {
            "type": "subagent_progress",
            "toolCallId": "call_1",
            "subagentType": "explore",
            "toolName": "grep",
            "toolInput": {"pattern": "foo"},
        }
    )
    worker._handle_event(
        {
            "type": "subagent_stop",
            "toolCallId": "call_1",
            "subagentType": "explore",
            "tokensUsed": 900,
        }
    )
    assert reports[0] == ("call_1", "Search", SUBAGENT_RUNNING, "Task: Find usages")
    assert len(reports) == 3
    assert reports[-1] == ("call_1", "", SUBAGENT_COMPLETED, "Finished, 900 tokens")


# ----- FreeBuff -----


def test_freebuff_agent_blocks_carry_their_task_and_steps():
    log = agent_backends._freebuff_agent_log(
        {
            "type": "agent",
            "agentName": "file-picker",
            "initialPrompt": "Find release files",
            "blocks": [
                {"type": "tool", "toolName": "read_files", "input": {"paths": ["a.py"]}},
                {"type": "text", "content": "Relevant: a.py"},
            ],
        }
    )
    assert log[0] == "Task: Find release files"
    assert log[-1] == "Relevant: a.py"
    assert len(log) == 3
