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


def test_a_memory_added_here_is_one_claude_code_can_read_and_is_indexed(tmp_path):
    """Same front matter Claude Code writes, and a line in MEMORY.md."""
    folder = tmp_path / "memory"

    path = app.add_memory(folder, "Ship All", 'Build "every" platform', "feedback", "Body.\n")

    assert path == folder / "ship-all.md"
    assert app._front_matter(path) == {
        "name": "ship-all",
        "description": 'Build "every" platform',
    }
    assert "\n  type: feedback\n" in path.read_text(encoding="utf-8")
    assert path.read_text(encoding="utf-8").endswith("---\n\nBody.\n")
    assert (folder / "MEMORY.md").read_text(encoding="utf-8").splitlines()[-1] == (
        '- [ship-all](ship-all.md) — Build "every" platform'
    )


def test_adding_never_replaces_an_existing_memory(tmp_path):
    app.add_memory(tmp_path, "a", "one", "user", "first")

    try:
        app.add_memory(tmp_path, "A", "two", "user", "second")
    except FileExistsError:
        pass
    else:
        raise AssertionError("an existing memory was replaced")
    assert "first" in (tmp_path / "a.md").read_text(encoding="utf-8")


def test_deleting_a_memory_drops_its_index_lines_and_nothing_else(tmp_path):
    _write(tmp_path / "old.md", "x")
    _write(
        tmp_path / "MEMORY.md",
        "# Memory index\n- [Old](old.md) — gone\n- old: also gone\n- [Keep](keep.md) — stays\n",
    )

    app.delete_memory(tmp_path / "old.md")

    assert not (tmp_path / "old.md").exists()
    assert (tmp_path / "MEMORY.md").read_text(encoding="utf-8") == (
        "# Memory index\n- [Keep](keep.md) — stays\n"
    )


def test_the_tabs_own_memory_folder_is_offered_first_even_before_it_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    _write(tmp_path / "projects" / "other" / "memory" / "a.md")

    folders = app.memory_folders(r"C:\work\app")

    assert folders == [
        tmp_path / "projects" / "C--work-app" / "memory",
        tmp_path / "projects" / "other" / "memory",
    ]
    assert app.memory_folders(None) == [tmp_path / "projects" / "other" / "memory"]


def test_command_code_tastes_are_listed_added_and_deleted_one_line_at_a_time(tmp_path):
    """A taste is one bullet of taste/<category>/taste.md, so it is handled as one."""
    _write(
        tmp_path / "taste" / "debugging" / "taste.md",
        "# Debugging taste\n\n- Probe first.\n- Read logs.\n",
    )

    app.add_taste(tmp_path, "debugging", "Bound every curl.")
    app.add_taste(tmp_path, "", "Be  brief.")
    listed = app.tastes(tmp_path)

    assert [label for _key, label in listed] == [
        "debugging: Bound every curl.",
        "debugging: Probe first.",
        "debugging: Read logs.",
        "general: Be brief.",
    ]
    assert app.entry_text(listed[1][0], "taste")[1] == "Probe first."
    app.delete_entry(listed[1][0], "taste")
    assert (tmp_path / "taste" / "debugging" / "taste.md").read_text(encoding="utf-8") == (
        "# Debugging taste\n\n- Read logs.\n- Bound every curl.\n"
    )
    assert (tmp_path / "taste" / "taste.md").read_text(
        encoding="utf-8"
    ) == "# General taste\n\n- Be brief.\n"


def test_hermes_memory_entries_are_split_on_the_section_sign(tmp_path):
    memory = tmp_path / "memories" / "MEMORY.md"
    _write(memory, "first\nline two\n§\nsecond")

    app.add_hermes_memory(memory, "third")
    listed = app.hermes_memories(tmp_path)

    assert [label for _key, label in listed] == [
        "MEMORY: first line two",
        "MEMORY: second",
        "MEMORY: third",
    ]
    app.delete_entry(listed[1][0], "hermes")
    assert memory.read_text(encoding="utf-8") == "first\nline two\n§\nthird"


def test_every_backend_is_listed_in_backend_order_with_only_what_it_has(tmp_path, monkeypatch):
    monkeypatch.setattr(app.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    _write(tmp_path / ".commandcode" / "AGENTS.md")
    (tmp_path / "hermes").mkdir()
    _write(tmp_path / ".gemini" / "GEMINI.md")

    names = [f"{g.backend} {g.kind}" for g in app.agent_files(None)]

    claude, cc, hermes, gemini = (
        app.BACKEND_LABELS[b] for b in ("claude", "commandcode", "hermes", "gemini")
    )
    # Claude Code memories, Command Code tastes and Hermes memories are offered
    # empty, so the first can be added; nothing else is listed when empty.
    # In the backends' own order: Hermes is listed before Command Code.
    assert names == [
        f"{claude} Memories",
        f"{hermes} Memories",
        f"{cc} Tastes",
        f"{cc} Instructions",
        f"{gemini} Instructions",
    ]
