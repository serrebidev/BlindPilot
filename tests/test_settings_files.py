"""Finding the files that configure the coding agents.

These are where an agent is actually configured — permissions, MCP servers,
model defaults, hooks — and every one of them is a dotfile in a directory
nothing announces. The only way to reach one was to leave BlindPilot, know
where to look, and find it. `open_log_folder` already exists on exactly this
reasoning: reading a path out loud and leaving somebody to navigate to it is
not a way in.

BlindPilot does not write these. It never has, apart from FreeBuff's model
choice, which FreeBuff itself rewrites after a turn. Listing them cannot create
them either: these files belong to the CLIs, which write their own on first run,
and inventing one at a path BlindPilot guessed would be worse than none — the
person would think they had configured something.

The scopes are not interchangeable and saying which is which is the point.
`.claude/settings.json` is committed to a repository and shared with whoever
else has it; `.claude/settings.local.json` is personal and gitignored. Opening
the wrong one silently is the failure this has to avoid.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import agent_backends
from agent_backends import BACKENDS, settings_files


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    # The Hermes entry honours HERMES_HOME, and its official installer sets it
    # persistently: on a machine with Hermes installed, the inherited value
    # would make the real config.yaml answer "present" instead of the files
    # the test itself created. Same class as the CODEX_HOME deletion above.
    monkeypatch.delenv("HERMES_HOME", raising=False)
    return tmp_path


def _for(backend, entries):
    return [entry for entry in entries if entry.backend == backend]


# ----- what is listed -----
def test_every_backend_blindpilot_drives_has_somewhere_to_look(home):
    entries = settings_files(str(home / "project"))

    for backend in BACKENDS:
        assert _for(backend, entries), f"{backend} has no settings file listed"


def test_the_paths_are_absolute(home):
    for entry in settings_files(str(home / "project")):
        assert entry.path.is_absolute(), entry


def test_claude_offers_the_three_files_it_actually_reads(home):
    paths = [entry.path for entry in _for("claude", settings_files(str(home / "project")))]

    assert home / ".claude" / "settings.json" in paths
    assert home / "project" / ".claude" / "settings.json" in paths
    assert home / "project" / ".claude" / "settings.local.json" in paths


def test_the_shared_and_the_personal_project_files_are_told_apart(home):
    """Committing your own settings to somebody else's repository is the
    mistake this exists to stop."""
    entries = _for("claude", settings_files(str(home / "project")))
    shared = next(e for e in entries if e.path.name == "settings.json" and "project" in str(e.path))
    personal = next(e for e in entries if e.path.name == "settings.local.json")

    assert shared.scope != personal.scope
    assert "personal" in personal.scope.lower() or "personal" in personal.note.lower()
    assert "shar" in shared.note.lower() or "repositor" in shared.note.lower()


def test_codex_is_toml_and_honours_its_own_home(home, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(home / "elsewhere"))

    paths = [entry.path for entry in _for("codex", settings_files(None))]

    assert home / "elsewhere" / "config.toml" in paths


def test_opencode_offers_the_project_file_the_catalog_already_reads(home):
    paths = [entry.path for entry in _for("opencode", settings_files(str(home / "project")))]

    assert home / "project" / "opencode.json" in paths
    assert home / ".config" / "opencode" / "opencode.json" in paths


def test_freebuff_points_at_the_file_blindpilot_already_writes(home):
    """The model choice goes here, so it is certainly the right file."""
    paths = [entry.path for entry in _for("freebuff", settings_files(None))]

    assert home / ".config" / "manicode" / "settings.json" in paths


# ----- with no folder to work from -----
def test_without_a_folder_only_the_global_files_are_offered(home):
    entries = settings_files(None)

    assert entries, "nothing at all was offered"
    for entry in entries:
        assert entry.scope == "global", f"{entry.path} is not global but no folder was given"


# ----- the safety property -----
def test_listing_them_creates_nothing(home):
    """These belong to the CLIs. Inventing one at a guessed path would leave
    somebody believing they had configured something."""
    before = sorted(p for p in home.rglob("*"))

    settings_files(str(home / "project"))

    assert sorted(p for p in home.rglob("*")) == before


def test_it_says_which_ones_are_really_there(home):
    real = home / ".claude"
    real.mkdir(parents=True)
    (real / "settings.json").write_text("{}", encoding="utf-8")

    entries = settings_files(str(home / "project"))
    present = [entry for entry in entries if entry.exists]

    assert [entry.path for entry in present] == [real / "settings.json"]


def test_every_entry_can_be_described_out_loud(home):
    """The dialog reads these; none of them may be blank."""
    for entry in settings_files(str(home / "project")):
        assert entry.scope.strip()
        assert entry.note.strip()
        assert agent_backends.backend_label(entry.backend).strip()
