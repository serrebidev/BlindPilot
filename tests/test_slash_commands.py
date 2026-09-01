"""What the slash-command picker offers, and for which backend.

The picker is the only place the commands are written down, so a command that
works everywhere has to be listed everywhere — a blind user cannot discover one
by noticing it in a menu they were not looking at.

Run from the project root:

    python -m pytest tests/ -q
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_backends import BACKEND_CLAUDE, BACKEND_CODEX, BACKEND_FREEBUFF, BACKEND_IDS  # noqa: E402
from blindpilot_app import SessionPanel, _slash_commands_for_backend  # noqa: E402


def _names(backend: str) -> list[str]:
    return [command.split()[0] for command, _description in _slash_commands_for_backend(backend)]


def _tab(backend: str, **overrides):
    """A stand-in for a session tab, for the parts of /status it reports.

    The report is read off a live panel, and building one needs a window; these
    are the fields it reads, and nothing else about the tab is involved.
    """
    fields = {
        "model": "",
        "effort": "",
        "_cli_model": "sonnet",
        "_cli_effort": "",
        "mode": "bypassPermissions",
        "cwd": "/work",
        "_session_id": None,
        "_session_backend": backend,
    }
    fields.update(overrides)
    state = SimpleNamespace(**fields)
    state.selected_backend = lambda: backend
    return state


def test_status_is_offered_for_every_backend():
    """/status is BlindPilot's own, and every backend can answer it.

    None of them answers it themselves in the headless mode they are driven in
    — Claude Code's /status is interactive-only, and the other three have no
    status command at all — so BlindPilot asks each one the way it can answer
    and reports all four the same way.
    """
    for backend in BACKEND_IDS:
        assert "/status" in _names(backend), backend


def test_status_is_listed_once_for_claude():
    """It used to be Claude's alone; moving it must not leave a second copy."""
    assert _names(BACKEND_CLAUDE).count("/status") == 1


def test_status_is_marked_as_handled_by_blindpilot():
    """The tag is how the picker says which commands never reach the provider."""
    for backend in BACKEND_IDS:
        descriptions = [
            description
            for command, description in _slash_commands_for_backend(backend)
            if command == "/status"
        ]
        assert descriptions and all(text.endswith("[BlindPilot]") for text in descriptions)


def test_status_reports_what_the_next_message_will_do():
    """The provider block says who is signed in; this says what the tab is set to."""
    lines = SessionPanel._session_status_lines(_tab(BACKEND_CLAUDE))
    assert "Model: sonnet" in lines
    assert "Effort: CLI default" in lines
    assert "Permission mode: Bypass permissions" in lines
    assert "Folder: /work" in lines
    assert "Conversation: new, nothing sent yet" in lines


def test_status_says_a_permission_mode_the_backend_does_not_take_is_not_in_use():
    """FreeBuff's picker is disabled, so naming the remembered mode would mislead."""
    lines = SessionPanel._session_status_lines(_tab(BACKEND_FREEBUFF))
    assert "Permission mode: not offered by this backend" in lines


def test_status_says_a_conversation_the_backend_switch_has_already_ended():
    """Switching backend starts a new conversation on the next message."""
    tab = _tab(BACKEND_CODEX, _session_id="abc")
    tab._session_backend = BACKEND_CLAUDE
    lines = SessionPanel._session_status_lines(tab)
    assert "Conversation: new, the backend changed since the last message" in lines

    tab._session_backend = BACKEND_CODEX
    assert "Conversation: continuing" in SessionPanel._session_status_lines(tab)
