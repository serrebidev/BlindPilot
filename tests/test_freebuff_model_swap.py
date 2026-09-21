# SPDX-License-Identifier: MIT
"""A model the picker no longer offers is swapped, and the swap is heard.

FreeBuff drops models between releases. When the chosen one is gone from the
cards there is no row to walk to, so after a few seconds BlindPilot presses
Enter on whatever the picker has highlighted and runs the turn on that -- with
a row saying so, because a silent substitution is the one thing a listener
cannot discover for themselves.

That row was filed as agent activity, and Narration, Keep up drops every kind
but "assistant" and "notice" (`blindpilot_app._ALWAYS_SPOKEN`). So the one mode
where an answer unlike the chosen model's is least likely to be noticed was the
mode that never heard why. The row is a "notice" now, which is what the sibling
sentences in this worker already are: the boot hold, the mid-turn drop, the
hourly cut-off.
"""

from __future__ import annotations

import agent_backends
from agent_backends import FreebuffWorker

# An already-expanded picker, as 0.0.168 opens on: there is no "See all models"
# entry to walk through first, so the swap is reached on the first frame that
# shows it. The only card is a model that is not the one asked for.
PICKER = (
    "Start coding for free\n"
    "┌──────────────────────────────────────┐\n"
    "│   GLM 5.3 Flash                      │\n"
    "└──────────────────────────────────────┘\n"
    "↑  Show fewer\n"
)

READY = "Describe your task"
WORKING = "do the work\nHere is the answer.\nEsc to stop"
DONE = "do the work\nHere is the answer.\nDescribe your task"

# FreeBuff's TUI repaints: every frame clears what was on screen before it, so
# the frames do not pile up and the composer is seen to arrive.
_FRAME_PREFIX = "\x1b[2J\x1b[H"


def _frame(text: str) -> str:
    return _FRAME_PREFIX + text.replace("\n", "\r\n") + "\r\n"


class _Turn:
    def __init__(self):
        self.activity: list[tuple[str, str]] = []
        self.completed: list[str] = []
        self.failures: list[str] = []

    def kinds_for(self, needle: str) -> list[str]:
        return [kind for kind, text in self.activity if needle in text]


def test_a_model_the_picker_dropped_is_announced_as_a_notice(monkeypatch):
    turn = _Turn()
    worker = FreebuffWorker(
        "do the work",
        None,
        ".",
        "default",
        model="gone/model-that-freebuff-dropped",
        on_session=lambda _s: None,
        on_started=lambda: None,
        on_activity=lambda kind, text: turn.activity.append((kind, text)),
        on_complete=turn.completed.append,
        on_failed=turn.failures.append,
        on_done=lambda: None,
    )
    state = {"sent": False, "enters": 0, "phase": 0}

    def write(text):
        if text == "do the work":
            state["sent"] = True
        if text == "\r":
            state["enters"] += 1
        return True

    def spawn(_args):
        def read(timeout):
            if timeout == 0:
                return ""  # nothing is pending between frames
            if state["sent"]:
                state["phase"] += 1
                return _frame(WORKING) if state["phase"] <= 5 else _frame(DONE)
            return _frame(PICKER) if state["enters"] == 0 else _frame(READY)

        return read

    monkeypatch.setattr(agent_backends, "_freebuff_prewarm", None)
    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _backend: "freebuff")
    monkeypatch.setattr(agent_backends, "set_freebuff_model", lambda _model: None)
    monkeypatch.setattr(
        agent_backends,
        "freebuff_model_options",
        lambda: (["z-ai/glm-5.3-flash"], "z-ai/glm-5.3-flash", ""),
    )
    monkeypatch.setattr(agent_backends, "_freebuff_chat_dirs", lambda _cwd: {})
    monkeypatch.setattr(agent_backends, "_FREEBUFF_PICKER_PATIENCE_SECONDS", 0.05)
    monkeypatch.setattr(agent_backends, "_FREEBUFF_TURN_SECONDS", 60)
    worker._write = write
    monkeypatch.setattr(FreebuffWorker, "_spawn_pty", staticmethod(spawn))

    worker._do_run()

    assert turn.completed == ["Here is the answer."], turn.failures
    assert turn.kinds_for("no longer offers") == ["notice"], turn.activity
