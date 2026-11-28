"""Run as agent: a Claude Code tab runs as one of the folder's agents."""

from __future__ import annotations

import pytest

import blindpilot_app
import claude_session as cs

INIT = {
    "agents": [
        {"name": "Explore", "description": "Fast read-only search.\nUse it to find files."},
        {"name": "reviewer", "description": "Reviews diffs.", "model": "haiku"},
        {"name": "plain", "model": "inherit"},
        {"name": "--dangerous"},
        {"name": "Explore", "description": "a duplicate"},
        "not an agent",
    ]
}


def test_the_agent_list_keeps_what_can_be_passed_back_once_each() -> None:
    agents = cs.agents_from_init(INIT)
    assert agents is not None
    assert [agent.label() for agent in agents] == [
        "Explore",
        "reviewer (uses its own model, haiku)",
        "plain",
    ]
    assert cs.agents_from_init({"commands": []}) is None


def test_the_agent_is_a_start_flag_and_a_change_starts_a_new_process() -> None:
    wants = cs.Wants(cwd="C:/work", permission_mode="", agent="Explore")
    assert cs.build_command("claude", wants, "")[-2:] == ["--agent", "Explore"]
    flag = cs.Wants(cwd="C:/work", permission_mode="", agent="--resume")
    assert "--agent" not in cs.build_command("claude", flag, "")


def test_the_description_box_says_the_whole_description_or_why_there_is_none() -> None:
    agents = cs.agents_from_init(INIT) or []
    names = [agent.name for agent in agents]
    about = blindpilot_app.agent_about
    assert about(agents[0], True, names) == "Fast read-only search.\nUse it to find files."
    assert about(agents[2], True, names) == "plain has no description."
    assert about(None, True, names) == "The conversation runs as Claude Code's own agent."
    gone = cs.Agent("old-agent")
    assert "doesn't list this agent" in about(gone, True, names)
    assert "could not be read" in about(gone, False, [])


def test_the_model_dialog_offers_the_agents_and_keeps_the_tabs_choice() -> None:
    wx = pytest.importorskip("wx")
    owns_app = wx.GetApp() is None
    app = wx.GetApp() or wx.App(False)
    frame = wx.Frame(None)
    try:
        options = blindpilot_app.ModelOptions(["opus"], ["high"], agents=cs.agents_from_init(INIT))
        dlg = blindpilot_app.ModelDialog(
            frame, options, "", "", "Claude Code", selected_agent="reviewer"
        )
        try:
            assert dlg.agent_box is not None
            assert dlg.agent_box.GetStrings()[0] == blindpilot_app.AGENT_NONE_LABEL
            assert dlg.agent() == "reviewer"
            assert dlg.agent_about.GetValue() == "Reviews diffs."
            dlg.agent_box.SetSelection(1)
            dlg._show_agent_about()
            assert dlg.agent() == "Explore"
            assert dlg.agent_about.GetValue().startswith("Fast read-only search.")
        finally:
            dlg.Destroy()
        other = blindpilot_app.ModelDialog(frame, options, "", "", "Codex")
        try:
            assert other.agent_box is None and other.agent() == ""
        finally:
            other.Destroy()
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()
        if owns_app:
            app.Destroy()
