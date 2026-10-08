"""Reading what Claude Code keeps about you.

Claude Code stores your instructions, the memories it saves as you work, your
skills and your settings as plain files, scattered across its config folder
and the project. What Claude Knows (Ctrl+Shift+I) lists them by kind and reads
one on Enter, as The Chat Place does. A memory is labelled by its own name and
description, so the list reads like an index rather than a column of paths.
"""

from __future__ import annotations

import blindpilot_app as app


def _write(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_files_are_found_by_kind_and_labelled_from_their_front_matter(tmp_path, monkeypatch):
    home = tmp_path / "config"
    project = tmp_path / "work" / "app"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    _write(home / "CLAUDE.md", "be blunt")
    _write(home / "rules" / "cli.md")
    _write(
        home / "projects" / "c--" / "memory" / "ship-all.md",
        '---\nname: ship-all\ndescription: "Build for every platform"\nmetadata:\n  type: project\n---\nbody',
    )
    _write(home / "projects" / "c--" / "memory" / "MEMORY.md", "# index")
    _write(home / "skills" / "deploy" / "SKILL.md", "---\ndescription: >\n  folded\n---\n")
    _write(home / "settings.json", "{}")
    _write(project / "CLAUDE.md")
    _write(project / ".claude" / "settings.local.json", "{}")
    _write(project / ".mcp.json", "{}")

    found = app.claude_files(str(project))

    assert [label for _path, label in found["Memories"]] == [
        "MEMORY.md",
        "ship-all: Build for every platform",
    ]
    assert found["Skills"] == [
        (str(home / "skills" / "deploy" / "SKILL.md"), f"deploy, in {home / 'skills' / 'deploy'}")
    ]
    instructions = {path for path, _label in found["Instructions"]}
    assert {
        str(home / "CLAUDE.md"),
        str(home / "rules" / "cli.md"),
        str(project / "CLAUDE.md"),
    } <= instructions
    assert len(found["Settings"]) == 3
    assert found["Agents"] == found["Commands"] == []


def test_memories_from_several_projects_say_which_project(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    _write(tmp_path / "projects" / "one" / "memory" / "a.md")
    _write(tmp_path / "projects" / "two" / "memory" / "b.md")

    labels = [label for _path, label in app.claude_files(None)["Memories"]]

    assert labels == ["a.md, one", "b.md, two"]


def test_a_nested_folder_finds_the_repository_roots_skills_and_agents_md(tmp_path, monkeypatch):
    """Claude Code walks up from a subfolder for skills, agents and AGENTS.md."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    _write(repo / "AGENTS.md")
    _write(repo / ".claude" / "skills" / "lint" / "SKILL.md")
    _write(repo / ".claude" / "settings.json", "{}")

    found = app.claude_files(str(repo / "packages" / "api"))

    assert str(repo / "AGENTS.md") in [path for path, _label in found["Instructions"]]
    assert [path for path, _label in found["Skills"]] == [
        str(repo / ".claude" / "skills" / "lint" / "SKILL.md")
    ]
    # Settings come from the working folder alone, not the folders above it.
    assert str(repo / ".claude" / "settings.json") not in [
        path for path, _label in found["Settings"]
    ]
