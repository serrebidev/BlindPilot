"""Help, Report a Bug.

Filing a bug meant finding the repository, then being asked for versions
nobody knows how to look up. The dialog asks what happened, shows exactly
what else goes with it, and opens GitHub's new-issue page filled in, as The
Chat Place's does. Nothing from a conversation is ever part of it.
"""

from __future__ import annotations

import os

import blindpilot_app as app


def test_the_facts_carry_versions_and_never_a_folder():
    facts = app.bug_report_facts(app.BACKEND_IDS[0], "agent")

    assert f"BlindPilot: {app.APP_VERSION}" in facts
    assert "wxPython:" in facts
    assert os.getcwd() not in facts


def test_the_body_reads_in_the_order_a_maintainer_wants():
    body = app.bug_report_body("Crash", "It crashed", "", "1. Open it", "BlindPilot: 1")

    assert body.index("What happened") < body.index("What I expected") < body.index("Steps")
    assert "Not given." in body
    assert body.endswith("```\nBlindPilot: 1\n```")
