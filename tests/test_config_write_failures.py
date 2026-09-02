"""When the settings file cannot be written.

`_save_config` caught `OSError` and did nothing with it: no return value, no
log line, no announcement. Ten call sites had no way to know, and several of
them speak a sentence that the failed write has just made untrue — "BlindPilot
will check for updates at startup" is a claim about a file that was not saved.

The worst of them is the first-run wizard. Completing it is recorded by setting
`setup_complete` and calling this; startup shows the wizard when that key is
missing. So a profile the settings cannot be written to — a roaming profile on
an unreachable share, a full disk, a directory something else has locked — puts
somebody through the whole wizard, CLI install and browser sign-in included,
every single time they open the application, with nothing anywhere saying why.
The same write carries `backend`, so a Codex or FreeBuff user is also dropped
back to the Claude default and sent through the Claude checks again.

The write was also not atomic: `open(path, "w")` truncates before `json.dump`
fills it, so an interruption partway leaves a half-written file. `_load_config`
catches the `ValueError` that then comes out of `json.load` and returns `{}`,
which resets every setting at once — including `setup_complete`.
"""

from __future__ import annotations

import json
import logging

import pytest

import blindpilot_app as app


@pytest.fixture
def config_at(tmp_path, monkeypatch):
    """Point the settings file somewhere disposable."""

    def place(path):
        monkeypatch.setattr(app, "_config_path", lambda: path)
        monkeypatch.setattr(app, "_config_dir", lambda: path.parent)
        monkeypatch.setattr(app, "_legacy_config_path", lambda: tmp_path / "nothing-here.json")
        return path

    return place


@pytest.fixture
def unwritable(tmp_path, config_at):
    """A settings path that cannot exist: its parent directory is a file.

    `open` raises `NotADirectoryError` on every platform, so this needs no
    permission tricks and behaves the same on the three the tests run on.
    """
    blocker = tmp_path / "in-the-way"
    blocker.write_text("not a directory", encoding="utf-8")
    return config_at(blocker / "config.json")


def test_a_write_that_failed_says_so(unwritable):
    assert app._save_config({"setup_complete": True}) is False


def test_a_write_that_worked_says_so(config_at, tmp_path):
    path = config_at(tmp_path / "config.json")

    assert app._save_config({"backend": "codex"}) is True
    assert json.loads(path.read_text(encoding="utf-8")) == {"backend": "codex"}


def test_the_failure_is_recorded_where_a_bug_report_can_find_it(unwritable, caplog):
    """The user gets a sentence; the log gets the reason."""
    with caplog.at_level(logging.WARNING, logger="blindpilot"):
        app._save_config({"setup_complete": True})

    assert any("settings" in record.getMessage().lower() for record in caplog.records), [
        record.getMessage() for record in caplog.records
    ]


# ----- the wizard loop -----
def test_finishing_the_wizard_when_it_cannot_be_saved_is_reported(unwritable, monkeypatch):
    """Otherwise the only symptom is the whole wizard again next launch, and
    nothing connects that to a settings file nobody can write."""
    said: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, urgent=False: said.append(text))

    app._record_setup_complete({"setup_complete": True})

    assert said, "a wizard that cannot be remembered said nothing about it"
    spoken = " ".join(said).lower()
    assert "settings" in spoken or "saved" in spoken, said


def test_a_saved_wizard_says_nothing(config_at, tmp_path, monkeypatch):
    said: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, urgent=False: said.append(text))
    config_at(tmp_path / "config.json")

    app._record_setup_complete({"setup_complete": True})

    assert said == []


# ----- a half-written file must not reset everything -----
def test_an_interrupted_write_leaves_the_previous_settings_alone(config_at, tmp_path, monkeypatch):
    """Truncate-then-fill means a crash costs every setting, not just the new
    one: `_load_config` cannot read the remains and starts over from empty."""
    path = config_at(tmp_path / "config.json")
    app._save_config({"setup_complete": True, "backend": "codex"})

    def die(*_args, **_kwargs):
        raise OSError("the disk filled up halfway through")

    monkeypatch.setattr(app.json, "dump", die)
    app._save_config({"setup_complete": True, "backend": "freebuff"})

    assert path.exists(), "the settings file was destroyed by a failed write"
    assert app._load_config() == {"setup_complete": True, "backend": "codex"}
