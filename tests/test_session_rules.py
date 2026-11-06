"""How much of what each session was allowed is remembered.

`_remember_session_rules` keeps a record of what "Allow for this session"
granted, keyed by conversation, in the same settings file the window reads
whole at startup. Nothing dropped the oldest keys, so the record grew with
every conversation ever opened. The newest `_SESSION_RULES_KEPT` conversations
are kept, each still with its own line cap; this drives the real save path to
check the bound, which conversations survive and what one of them holds.
"""

from __future__ import annotations

import json

import pytest

import blindpilot_app as app


@pytest.fixture()
def config_at(tmp_path, monkeypatch):
    """Point the settings file somewhere disposable; the write itself is real."""
    path = tmp_path / "config.json"
    monkeypatch.setattr(app, "_config_path", lambda: path)
    monkeypatch.setattr(app, "_config_dir", lambda: path.parent)
    monkeypatch.setattr(app, "_legacy_config_path", lambda: tmp_path / "nothing-here.json")
    return path


def test_only_the_newest_sessions_keep_their_rules(config_at):
    """One conversation past the cap drops the oldest, and no more than that."""
    opened = app._SESSION_RULES_KEPT + 6
    for index in range(opened):
        app._remember_session_rules(f"session-{index}", [f"Allow: Read({index})"])

    stored = app._saved_session_rules()
    assert len(stored) == app._SESSION_RULES_KEPT, "the record is unbounded"
    assert list(stored) == [f"session-{index}" for index in range(6, opened)], (
        "the newest conversations are not the ones kept, in the order they were written"
    )
    assert "session-0" not in stored, "the oldest conversation was kept"

    # The bound is the file's, not this in-memory copy's.
    written = json.loads(config_at.read_text(encoding="utf-8"))["session_permissions"]
    assert list(written) == list(stored)


def test_a_session_written_again_becomes_the_newest(config_at):
    """Allowing more in a conversation that is still open makes it the newest,
    so it cannot be the one the cap drops next."""
    for index in range(app._SESSION_RULES_KEPT):
        app._remember_session_rules(f"session-{index}", [f"Allow: Read({index})"])

    app._remember_session_rules("session-0", ["Allow: Read(again)"])

    stored = app._saved_session_rules()
    assert len(stored) == app._SESSION_RULES_KEPT
    assert list(stored)[-1] == "session-0"
    assert stored["session-0"] == ["Allow: Read(0)", "Allow: Read(again)"]


def test_a_session_still_keeps_its_last_two_hundred_lines(config_at):
    """The per-session cap is unchanged: only the tail of a long grant list."""
    app._remember_session_rules("session-1", [f"Allow: Read({index})" for index in range(250)])

    stored = app._saved_session_rules()["session-1"]
    assert stored == [f"Allow: Read({index})" for index in range(50, 250)]
