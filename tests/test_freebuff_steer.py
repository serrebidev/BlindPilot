# SPDX-License-Identifier: MIT
"""A steered message is the person's own words, and is not read out as the answer.

FreeBuff has no way to be given a message mid-turn except the composer it already
has: BlindPilot types the steer into it, and FreeBuff writes that into the
transcript exactly as it writes the reply - plain text, with nothing to say
whose words they are. What identifies it is its content, and that is why the
reading cuts the transcript at the echo of what was typed.

Only the prompt was ever looked for, so a steer's echo landed inside the section
that gets spoken and the person heard their own instruction read back as though
the model had said it. The echoes are removed by span now, and the prompt's is
one of them: an echo taller than one line - a message the terminal had to wrap -
had only its first line cut before, so its tail was read as the answer whether
or not the turn was steered.
"""

from __future__ import annotations

import agent_backends
from agent_backends import FreebuffWorker

READY = "Describe your task"
# A turn in progress, before and after a steer, and the same turn finished: the
# composer is back, which is what ends the reading when there is no chat file.
BEFORE = "do the work\nPart one of the answer.\nEsc to stop"
AFTER = "do the work\nPart one of the answer.\n{steer}\nPart two of the answer.\nEsc to stop"
DONE = "do the work\nPart one of the answer.\n{steer}\nPart two of the answer.\nDescribe your task"

_FRAME_PREFIX = "\x1b[2J\x1b[H"


def _frame(text: str) -> str:
    return _FRAME_PREFIX + text.replace("\n", "\r\n") + "\r\n"


class _Turn:
    def __init__(self) -> None:
        self.activity: list[tuple[str, str]] = []
        self.completed: list[str] = []
        self.failures: list[str] = []
        self.written: list[str] = []

    @property
    def spoken(self) -> str:
        return "\n".join(text for _kind, text in self.activity)

    @property
    def answer(self) -> str:
        return self.completed[0] if self.completed else ""


def _run(monkeypatch, steer: str) -> _Turn:
    """One steered turn. `steer` is what the composer is given mid-turn, and it
    is echoed on `echo` lines, which the terminal may have had to wrap."""
    turn = _Turn()
    worker = FreebuffWorker(
        "do the work",
        None,
        ".",
        "default",
        model="z-ai/glm-5.3-flash",
        on_session=lambda _s: None,
        on_started=lambda: None,
        on_activity=lambda kind, text: turn.activity.append((kind, text)),
        on_complete=turn.completed.append,
        on_failed=turn.failures.append,
        on_done=lambda: None,
    )
    state = {"sent": False, "steered": False, "phase": 0}

    def write(text):
        turn.written.append(text)
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
            if not state["steered"]:
                state["steered"] = True
                worker.steer(steer)
            painted = AFTER if state["phase"] <= 5 else DONE
            return _frame(painted.format(steer=steer))

        return read

    worker._write = write
    monkeypatch.setattr(agent_backends, "_freebuff_prewarm", None)
    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _backend: "freebuff")
    monkeypatch.setattr(agent_backends, "set_freebuff_model", lambda _model: None)
    monkeypatch.setattr(agent_backends, "_freebuff_chat_dirs", lambda _cwd: {})
    monkeypatch.setattr(agent_backends, "_FREEBUFF_TURN_SECONDS", 60)
    monkeypatch.setattr(FreebuffWorker, "_spawn_pty", staticmethod(spawn))

    worker._do_run()
    return turn


def test_a_steered_message_is_not_read_out_as_the_answer(monkeypatch):
    turn = _run(monkeypatch, "please run the tests")

    assert "please run the tests" in turn.written, "the steer was never sent"
    assert "please run the tests" not in turn.spoken, turn.activity
    assert "please run the tests" not in turn.answer, turn.answer


def test_the_answer_either_side_of_a_steer_is_kept(monkeypatch):
    """Removing the echo must not remove the answer around it: the text before
    the steer is still this turn's, and the text after it is still the reply."""
    turn = _run(monkeypatch, "please run the tests")

    assert turn.failures == []
    assert "Part one of the answer." in turn.spoken, turn.activity
    assert "Part two of the answer." in turn.spoken, turn.activity
    assert "Part one of the answer." in turn.answer, turn.answer
    assert "Part two of the answer." in turn.answer, turn.answer


def test_a_wrapped_steer_echo_is_dropped_whole(monkeypatch):
    """A message longer than the terminal is wide is echoed over more than one
    line, and the lines after the first are the rest of the echo rather than
    the reply."""
    turn = _run(monkeypatch, "please run the full test suite and\nthen commit it")

    assert any("please run the full test suite and" in text for text in turn.written), turn.written
    assert "please run the full test suite and" not in turn.spoken, turn.activity
    assert "then commit it" not in turn.spoken, turn.activity
    assert "Part two of the answer." in turn.spoken, turn.activity
    assert "Part one of the answer." in turn.answer, turn.answer
    assert "Part two of the answer." in turn.answer, turn.answer


def test_an_unwrapped_prompt_echo_is_still_left_out(monkeypatch):
    """The boundary itself is unchanged: the prompt's own echo is not the
    answer, and a resumed turn above it is not either."""
    turn = _run(monkeypatch, "please run the tests")

    assert "do the work" not in turn.spoken, turn.activity
    assert "do the work" not in turn.answer, turn.answer
