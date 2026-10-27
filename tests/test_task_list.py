"""The agent's task list, as Claude Code and Codex report it.

A long task's plan was spoken one "Updating the task list" at a time, with no
way to ask where it stood. Task List (Ctrl+Shift+J) keeps the list itself:
each step and whether it is to do, in progress or done, as The Chat Place's
Tasks list does. Claude Code builds it with TaskCreate and TaskUpdate, or the
older TodoWrite; Codex sends its whole plan as ``turn/plan/updated``.
"""

from __future__ import annotations

import blindpilot_app as app
from task_list import TaskList


def test_claude_task_tools_build_and_update_the_list():
    tasks = TaskList()
    tasks.apply_claude("TaskCreate", {"subject": "Write tests"}, "Task #1 created successfully")
    tasks.apply_claude("TaskCreate", {"subject": "Run suite"}, "Task #2 created successfully")

    finished = tasks.apply_claude("TaskUpdate", {"taskId": "1", "status": "completed"})
    tasks.apply_claude("TaskUpdate", {"taskId": "2", "status": "in_progress"})

    assert finished == ["Write tests"]
    assert tasks.lines() == ["1. Done: Write tests", "2. In progress: Run suite"]
    assert tasks.progress() == "Tasks: 1 of 2 done. In progress: Run suite."


def test_a_repeated_task_id_starts_a_new_list():
    tasks = TaskList()
    tasks.apply_claude("TaskCreate", {"subject": "Old"}, "Task #1 created successfully")
    tasks.apply_claude("TaskCreate", {"subject": "New"}, "Task #1 created successfully")

    assert tasks.lines() == ["1. To do: New"]


def test_a_deleted_or_unknown_task_changes_nothing_it_should_not():
    tasks = TaskList()
    tasks.apply_claude("TaskCreate", {"subject": "Keep"}, "Task #1 created successfully")
    tasks.apply_claude("TaskCreate", {"subject": "Drop"}, "Task #2 created successfully")

    tasks.apply_claude("TaskUpdate", {"taskId": "2", "status": "deleted"})
    tasks.apply_claude("TaskUpdate", {"taskId": "9", "status": "completed"})

    assert tasks.lines() == ["1. To do: Keep"]


def test_todowrite_replaces_the_list_and_reports_only_new_completions():
    tasks = TaskList()
    todos = [
        {"content": "Plan", "status": "completed"},
        {"content": "Build", "status": "in_progress"},
    ]
    assert tasks.apply_claude("TodoWrite", {"todos": todos}) == ["Plan"]

    todos[1]["status"] = "completed"
    assert tasks.apply_claude("TodoWrite", {"todos": todos}) == ["Build"]


def test_codex_plan_updates_use_its_own_status_names():
    tasks = TaskList()
    finished = tasks.apply_codex(
        [{"step": "Read code", "status": "completed"}, {"step": "Fix", "status": "inProgress"}]
    )

    assert finished == ["Read code"]
    assert tasks.lines() == ["1. Done: Read code", "2. In progress: Fix"]


def test_a_malformed_plan_is_ignored():
    tasks = TaskList()
    assert tasks.apply_codex("not a list") == []
    assert tasks.apply_codex([{"status": "completed"}, 3]) == []
    assert tasks.progress() == ""


def test_turn_status_includes_the_task_progress():
    stub = type("PanelStub", (), {})()
    spoken: list[str] = []
    stub._announce = lambda text, urgent=False: spoken.append(text)
    stub.task_list = TaskList()
    stub.task_list.apply_codex([{"step": "One", "status": "completed"}])

    app.SessionPanel.turn_status(stub)

    assert spoken == ["No turn is running. Tasks: 1 of 1 done."]


def test_a_finished_step_is_announced_from_the_worker_event():
    stub = type("PanelStub", (), {})()
    spoken: list[str] = []
    stub._announce = lambda text, urgent=False: spoken.append(text)
    stub.task_list = TaskList()
    stub.task_list.apply_claude("TaskCreate", {"subject": "Ship"}, "Task #1 created")

    app.SessionPanel._on_tasks(
        stub, "claude", "TaskUpdate", {"taskId": "1", "status": "completed"}, ""
    )

    assert spoken == ["Task done: Ship"]
