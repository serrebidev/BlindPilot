"""Commands, Decisions and Properties: three more ways to read a conversation.

Commands lists what the agent ran with whether it finished, Decisions what
it asked and how it was answered, Properties the session itself, one
"Property: value" line each. Modelled on The Chat Place's Lists and
Properties; read from the rows BlindPilot already keeps.
"""

from __future__ import annotations

import blindpilot_app as app
from markdown_rows import Row
from task_list import TaskList


def _row(kind: str, text: str, number: int = 1, source: str | None = None) -> Row:
    return Row(kind=kind, label=text, payload=text, response_number=number, source=source)


def _panel(rows: list[Row]):
    stub = type("PanelStub", (), {})()
    stub._rows = rows
    stub.decisions = []
    stub.task_list = TaskList()
    shown: list = []
    stub._show_items = lambda items, title, empty: shown.append((title, items, empty))
    stub.command_items = lambda: app.SessionPanel.command_items(stub)
    return stub, shown


def test_commands_are_listed_newest_first_with_their_outcome():
    stub, _shown = _panel(
        [
            _row("tool", "Running: git status"),
            _row("result", "On branch main"),
            _row("tool", "Reading app.py"),
            _row("tool", "Running: pytest"),
            _row("tool", "Failed: Bash: exit 1"),
            _row("tool", "Running: npm run build"),
        ]
    )
    items = app.SessionPanel.command_items(stub)

    assert [item.label for item in items] == [
        "npm run build, no result yet",
        "pytest, failed",
        "git status, done",
    ]
    assert items[2].payload == "git status\n\nOn branch main"


def test_decisions_list_the_newest_answer_first():
    stub, shown = _panel([])
    stub.decisions = [
        ("Claude Code asked: Which version?", "You answered: 2.0"),
        ("Codex asked to use Bash", "You denied it"),
    ]
    app.SessionPanel.open_decisions(stub)

    title, items, _empty = shown[0]
    assert title == "Decisions, 2"
    assert [item.label for item in items] == [
        "Codex asked to use Bash. You denied it",
        "Claude Code asked: Which version?. You answered: 2.0",
    ]


def test_properties_describe_the_session_one_line_each():
    header = "Done. See [docs](https://example.com).\n\n```python\nprint(1)\n```"
    stub, _shown = _panel(
        [
            _row("you", "You: fix it"),
            _row("header", "Claude: Done.", source=header),
            _row("tool", "Running: git status"),
            _row("result", "clean"),
        ]
    )
    stub._session_backend = app.BACKEND_COPILOT
    stub.tab_title = "Build fix"
    stub.cwd = "C:/proj"
    stub.model = ""
    stub.effort = "high"
    stub.mode = "bypassPermissions"
    stub._session_id = "abc"
    stub._turns = []
    stub._response_count = 1
    stub.task_list.apply_codex([{"step": "One", "status": "completed"}])

    lines = app.SessionPanel.property_lines(stub)

    assert lines == [
        "Title: Build fix",
        "Backend: GitHub Copilot CLI",
        "Folder: C:/proj",
        "Model: the backend default",
        "Effort: high",
        "Permission mode: Bypass permissions",
        "Session ID: abc",
        "Your messages: 1",
        "Responses: 1",
        "Tool steps: 1",
        "Commands: 1",
        "Code blocks: 1",
        "Links: 1",
        "Decisions: 0",
        "Tasks: 1 of 1 done.",
    ]
