"""Answering Claude Code's permission prompts instead of refusing them.

Outside Bypass permissions, every tool call Claude Code's mode left to a
person was denied without anybody being asked, and Plan mode could never be
left from here. The request now opens a dialog, as The Chat Place does.
"""

from __future__ import annotations

import blindpilot_app as app


def test_a_request_reads_as_labelled_lines():
    text = app._permission_text(
        "Bash", {"command": "git push", "description": "Push", "_reason": "Not in the allow list"}
    )

    assert text.splitlines() == [
        "Tool: Bash",
        "Why it is asking: Not in the allow list",
        "command: git push",
        "description: Push",
    ]


def test_a_plan_reads_as_the_plan_itself():
    assert app._permission_text("ExitPlanMode", {"plan": "1. Fix it"}) == "1. Fix it"
