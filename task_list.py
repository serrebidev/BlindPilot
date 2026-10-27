"""A coding agent's task list, as Claude Code and Codex report it.

Claude Code keeps its list with TaskCreate and TaskUpdate, one task at a time,
or with the older TodoWrite, which writes the whole list on every call. Codex
sends its whole plan as ``turn/plan/updated``. Either way the person gets one
list: each step, and whether it is to do, in progress or done. Modelled on The
Chat Place's Tasks list; implemented independently.

Everything here runs on the GUI thread. Workers only pass the raw calls on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PENDING, IN_PROGRESS, COMPLETED = "pending", "in_progress", "completed"
_SPOKEN = {PENDING: "To do", IN_PROGRESS: "In progress", COMPLETED: "Done"}
# Codex spells in-progress in camel case; the snake form is accepted too.
_CODEX_STATUS = {
    "pending": PENDING,
    "inProgress": IN_PROGRESS,
    "in_progress": IN_PROGRESS,
    "completed": COMPLETED,
}
CLAUDE_TASK_TOOLS = frozenset({"TaskCreate", "TaskUpdate", "TodoWrite"})
_CREATED = re.compile(r"\bTask #(\w+) created", re.IGNORECASE)


@dataclass
class Task:
    task_id: str
    subject: str
    status: str = PENDING
    description: str = ""


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


class TaskList:
    def __init__(self) -> None:
        self.tasks: list[Task] = []

    def apply_claude(self, name: str, params: dict, result: str = "") -> list[str]:
        """Apply one successful Claude Code task call; return steps it finished.

        TaskCreate's id comes from its result ("Task #3 created
        successfully"); TaskUpdate names that id with the fields that change;
        TodoWrite sends the whole list with no ids, so a step's own words stand
        in for one. An unknown shape changes nothing.
        """
        before = {task.task_id: task.status for task in self.tasks}
        if name == "TaskCreate":
            subject = _text(params.get("subject"))
            if not subject:
                return []
            match = _CREATED.search(result)
            task_id = match.group(1) if match else str(len(self.tasks) + 1)
            # Ids only grow within a list, so one already here means Claude
            # started a new list.
            if any(task.task_id == task_id for task in self.tasks):
                self.tasks.clear()
            self.tasks.append(Task(task_id, subject, description=_text(params.get("description"))))
        elif name == "TaskUpdate":
            wanted = _text(params.get("taskId"))
            task = next((task for task in self.tasks if task.task_id == wanted), None)
            if task is None:
                return []
            status = _text(params.get("status"))
            if status == "deleted":
                self.tasks.remove(task)
                return []
            task.subject = _text(params.get("subject")) or task.subject
            task.description = _text(params.get("description")) or task.description
            if status in _SPOKEN:
                task.status = status
        elif name == "TodoWrite":
            todos = params.get("todos")
            if not isinstance(todos, list):
                return []
            self.tasks = self._from_steps(
                (_text(todo.get("content")), _text(todo.get("status")))
                for todo in todos
                if isinstance(todo, dict)
            )
        else:
            return []
        return self._newly_done(before)

    def apply_codex(self, plan: object) -> list[str]:
        """Replace the list with Codex's plan; return steps it finished."""
        if not isinstance(plan, list):
            return []
        before = {task.task_id: task.status for task in self.tasks}
        self.tasks = self._from_steps(
            (_text(step.get("step")), _CODEX_STATUS.get(_text(step.get("status")), PENDING))
            for step in plan
            if isinstance(step, dict)
        )
        return self._newly_done(before)

    @staticmethod
    def _from_steps(steps) -> list[Task]:
        tasks: list[Task] = []
        seen: dict[str, int] = {}
        for subject, status in steps:
            if not subject:
                continue
            seen[subject] = seen.get(subject, 0) + 1
            count = seen[subject]
            task_id = f"step:{subject}" + (f"#{count}" if count > 1 else "")
            tasks.append(Task(task_id, subject, status if status in _SPOKEN else PENDING))
        return tasks

    def _newly_done(self, before: dict[str, str]) -> list[str]:
        return [
            task.subject
            for task in self.tasks
            if task.status == COMPLETED and before.get(task.task_id) != COMPLETED
        ]

    def lines(self) -> list[str]:
        """'1. Done: Write the tests', one per step, in the agent's order."""
        return [
            f"{n}. {_SPOKEN[task.status]}: {task.subject}" for n, task in enumerate(self.tasks, 1)
        ]

    def progress(self) -> str:
        """'Tasks: 2 of 5 done. In progress: Run the suite.' Empty with no list."""
        if not self.tasks:
            return ""
        done = sum(task.status == COMPLETED for task in self.tasks)
        said = f"Tasks: {done} of {len(self.tasks)} done."
        current = next((task for task in self.tasks if task.status == IN_PROGRESS), None)
        return f"{said} In progress: {current.subject}." if current else said
