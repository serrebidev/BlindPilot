# SPDX-License-Identifier: MIT
"""A resumed conversation keeps its own id, however its folder is filed.

A conversation is reopened by the id BlindPilot read out of FreeBuff's history
and handed back with `--continue`. Finding its folder is a separate question:
the chat may have been deleted since the list was drawn, or filed under a
bucket this release no longer uses, and then the turn starts with no chat path
at all -- the same state a brand-new conversation is in.

The rule that learns a new conversation's id is "whatever appeared under
FreeBuff's projects since this turn began". Armed on a resumed turn it answers
with somebody else's conversation, and with another FreeBuff turn running in
another tab that is exactly what appears. The id the tab holds is overwritten,
so the next message resumes a conversation the user never opened. It happens
twice over: once in the watching loop, and once where the turn decides what to
prewarm for the next message.
"""

from __future__ import annotations

import agent_backends
from agent_backends import FreebuffWorker

READY = "Describe your task"
WORKING = "do the work\nHere is the answer.\nEsc to stop"
DONE = "do the work\nHere is the answer.\nDescribe your task"

# FreeBuff repaints: every frame clears the screen, and lines are joined the
# way a terminal receives them, so a bare line feed cannot walk the text one
# column further right until the composer stops being recognised.
_FRAME_PREFIX = "\x1b[2J\x1b[H"


def _frame(text: str) -> str:
    return _FRAME_PREFIX + text.replace("\n", "\r\n") + "\r\n"


class _Turn:
    def __init__(self):
        self.activity: list[tuple[str, str]] = []
        self.sessions: list[str] = []
        self.completed: list[str] = []
        self.failures: list[str] = []


def _worker(turn: _Turn, session_id):
    return FreebuffWorker(
        "do the work",
        session_id,
        ".",
        "default",
        model="z-ai/glm-5.3-flash",
        on_session=turn.sessions.append,
        on_started=lambda: None,
        on_activity=lambda kind, text: turn.activity.append((kind, text)),
        on_complete=turn.completed.append,
        on_failed=turn.failures.append,
        on_done=lambda: None,
    )


def _stub(monkeypatch, worker, dirs, prewarmed):
    state = {"sent": False, "phase": 0}

    def write(text):
        if text == "do the work":
            state["sent"] = True
        return True

    def spawn(_args):
        def read(timeout):
            if timeout == 0:
                return ""
            if not state["sent"]:
                return _frame(READY)
            state["phase"] += 1
            return _frame(WORKING) if state["phase"] <= 5 else _frame(DONE)

        return read

    worker._write = write
    monkeypatch.setattr(agent_backends, "_freebuff_prewarm", None)
    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _backend: "freebuff")
    monkeypatch.setattr(agent_backends, "set_freebuff_model", lambda _model: None)
    monkeypatch.setattr(agent_backends, "_freebuff_chat_dirs", dirs)
    monkeypatch.setattr(
        agent_backends,
        "prewarm_freebuff",
        lambda _cwd, session_id, _model, delay=0.0: prewarmed.append(session_id),
    )
    monkeypatch.setattr(agent_backends, "_FREEBUFF_TURN_SECONDS", 60)
    monkeypatch.setattr(FreebuffWorker, "_spawn_pty", staticmethod(spawn))


def test_a_resumed_turn_does_not_adopt_a_folder_that_appeared_while_it_ran(monkeypatch):
    turn = _Turn()
    worker = _worker(turn, "chat-ours")
    snapshots = [{"chat-ours": 1.0}, {"chat-ours": 1.0, "chat-theirs": 2.0}]
    prewarmed: list[str] = []

    def dirs(_cwd):
        # The first call is the snapshot taken before the turn starts; every
        # later one sees the other tab's conversation as well.
        return snapshots.pop(0) if len(snapshots) > 1 else snapshots[0]

    _stub(monkeypatch, worker, dirs, prewarmed)
    # The conversation being resumed cannot be found, which is the whole point:
    # there is no chat path, and there never will be one this turn.
    monkeypatch.setattr(agent_backends, "_freebuff_chat_path", lambda _cwd, _sid: None)

    worker._do_run()

    assert worker._session_id == "chat-ours", turn.sessions
    assert "chat-theirs" not in turn.sessions
    assert prewarmed == ["chat-ours"], prewarmed


def test_a_new_turn_still_learns_the_conversation_it_created(monkeypatch):
    """The other half of the rule: with no session to begin with, the folder
    that appears during the turn is this turn's, and is what is resumed."""
    turn = _Turn()
    worker = _worker(turn, None)
    snapshots = [{}, {"chat-ours": 2.0}]
    prewarmed: list[str] = []

    def dirs(_cwd):
        return snapshots.pop(0) if len(snapshots) > 1 else snapshots[0]

    _stub(monkeypatch, worker, dirs, prewarmed)
    monkeypatch.setattr(agent_backends, "_freebuff_chat_path", lambda _cwd, _sid: None)

    worker._do_run()

    assert worker._session_id == "chat-ours"
    assert turn.sessions == ["chat-ours"]
    assert prewarmed == ["chat-ours"], prewarmed
