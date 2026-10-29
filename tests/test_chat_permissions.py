"""Chat Permissions: the record of what a session may do without asking.

The grant lines are built from Claude Code's own permission updates and
from its settings files, both plain data, so they are checked here without
a window: what "Allow for this session" granted has to read back as what
was actually handed over, and a settings file has to name its rules with
the file they came from.
"""

from __future__ import annotations

import json

import pytest

import blindpilot_app as app


@pytest.fixture()
def config_at(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(app, "_config_path", lambda: path)
    monkeypatch.setattr(app, "_config_dir", lambda: path.parent)
    monkeypatch.setattr(app, "_legacy_config_path", lambda: tmp_path / "nothing-here.json")
    return path


def test_update_lines_read_rules_folders_and_modes():
    lines = app._permission_update_lines(
        [
            {
                "type": "addRules",
                "behavior": "allow",
                "rules": [
                    {"toolName": "Bash", "ruleContent": "git push:*"},
                    {"toolName": "Read"},
                ],
            },
            {
                "type": "addRules",
                "behavior": "deny",
                "rules": [{"toolName": "Bash", "ruleContent": "rm:*"}],
            },
            {"type": "addDirectories", "directories": ["/tmp/extra"]},
            {"type": "setMode", "mode": "acceptEdits"},
            {"type": "somethingElse"},
            "not a dict",
        ]
    )
    assert lines == [
        "Allow: Bash(git push:*)",
        "Allow: Read",
        "Never allow: Bash(rm:*)",
        "Work in the folder /tmp/extra",
        "Permission mode set to acceptEdits",
    ]


def test_remembered_rules_dedupe_and_come_back(config_at):
    app._remember_session_rules("s1", ["Allow: Bash(git push:*)", "Allow: Read"])
    app._remember_session_rules("s1", ["Allow: Read", "Allow: Edit"])
    app._remember_session_rules("", ["Allow: Bash(rm:*)"])
    assert app._saved_session_rules() == {
        "s1": ["Allow: Bash(git push:*)", "Allow: Read", "Allow: Edit"]
    }


def test_settings_rule_lines_name_their_files(tmp_path, monkeypatch):
    claude_home = tmp_path / "claude"
    claude_home.mkdir()
    (claude_home / "settings.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(git status:*)"], "deny": ["Bash(rm -rf:*)"]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    cwd = tmp_path / "project"
    (cwd / ".claude").mkdir(parents=True)
    (cwd / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"ask": ["Bash(npm publish:*)"]}}), encoding="utf-8"
    )
    (cwd / ".claude" / "settings.local.json").write_text("{ not json", encoding="utf-8")
    lines = app._claude_settings_rule_lines(str(cwd))
    assert lines == [
        f"Allow: Bash(git status:*) (from {claude_home / 'settings.json'})",
        f"Never allow: Bash(rm -rf:*) (from {claude_home / 'settings.json'})",
        f"Ask about: Bash(npm publish:*) (from {cwd / '.claude' / 'settings.json'})",
    ]


def test_settings_rule_lines_empty_without_files(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "missing"))
    assert app._claude_settings_rule_lines(str(tmp_path)) == []
