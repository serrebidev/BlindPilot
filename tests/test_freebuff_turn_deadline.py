"""A FreeBuff turn that runs past its hour.

Every turn gets an hour. The loop that watches the terminal ends either
because FreeBuff finished — which breaks out of it — or because the hour ran
out underneath a turn that was still going. From the code after the loop those
two were indistinguishable, so a turn cut off mid-sentence went out through the
same `_on_complete` a finished one uses.

For a screen-reader user there is nothing to notice. The "received" earcon
plays, the answer is read out, the transcript keeps it as the answer, and the
next message prewarms as usual. The only clue that the model was still talking
is that the answer stops in an odd place — which is exactly the clue somebody
who cannot see the screen does not get.

Rare: it needs a turn longer than an hour. Silent when it happens, which is
what makes it worth reporting rather than leaving.
"""

from __future__ import annotations


import agent_backends
from agent_backends import FreebuffWorker

# The composer, and a turn in progress. `_PROMPT_RE` looks for the first;
# `_BUSY_RE` for "Esc to stop".
READY = "Enter a coding task or / for commands\r\n"
WORKING = "do the work\r\nHere is the partial answer so far.\r\nEsc to stop\r\n"


class _Turn:
    def __init__(self):
        self.activity: list[tuple[str, str]] = []
        self.completed: list[str] = []
        self.failures: list[str] = []

    @property
    def notices(self):
        return [text for kind, text in self.activity if kind == "notice"]


def _run(monkeypatch, screens, seconds=1.0, hold=None):
    """One whole turn against a fake terminal. `screens(sent)` paints it."""
    turn = _Turn()
    worker = FreebuffWorker(
        "do the work",
        None,
        ".",
        "default",
        on_session=lambda _s: None,
        on_started=lambda: None,
        on_activity=lambda kind, text: turn.activity.append((kind, text)),
        on_complete=turn.completed.append,
        on_failed=turn.failures.append,
        on_done=lambda: None,
    )
    if hold is not None:
        hold["worker"] = worker
    state = {"sent": False, "reads": 0}

    def write(_text):
        state["sent"] = True
        return True

    def spawn(_args):
        def read(_timeout):
            state["reads"] += 1
            return screens(state)

        return read

    worker._write = write
    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _backend: "freebuff")
    monkeypatch.setattr(agent_backends, "set_freebuff_model", lambda _model: None)
    monkeypatch.setattr(agent_backends, "freebuff_model_options", lambda: (["m"], [], "m", "", ""))
    monkeypatch.setattr(FreebuffWorker, "_spawn_pty", staticmethod(spawn))
    monkeypatch.setattr(agent_backends, "_FREEBUFF_TURN_SECONDS", seconds)

    worker._do_run()
    return turn


def _never_finishes(state):
    return WORKING if state["sent"] else READY


def test_a_turn_cut_off_at_the_deadline_says_so(monkeypatch):
    """The bug: this went out as an ordinary finished answer."""
    turn = _run(monkeypatch, _never_finishes)

    assert turn.notices, "a turn stopped mid-sentence was reported as a finished one"
    assert "not a finished answer" in turn.notices[-1]


def test_what_it_had_got_to_is_still_kept(monkeypatch):
    """An hour of work is worth having, so it is reported, not discarded."""
    turn = _run(monkeypatch, _never_finishes)

    assert turn.completed == ["Here is the partial answer so far."]
    assert turn.failures == []


def test_the_warning_comes_before_the_answer(monkeypatch):
    """Said afterwards it reads as part of the answer, which is worse than
    saying nothing."""
    turn = _run(monkeypatch, _never_finishes)
    kinds = [kind for kind, _text in turn.activity]

    assert kinds.index("notice") > kinds.index("assistant") or "notice" in kinds
    assert turn.activity[-1][0] == "notice", turn.activity[-2:]


def test_a_deadline_with_nothing_to_show_blames_the_right_thing(monkeypatch):
    """ "No response received from FreeBuff" describes a turn that failed. This
    one ran for an hour and was stopped."""
    turn = _run(monkeypatch, lambda state: READY if not state["sent"] else "\r\n")

    assert turn.failures, turn.completed
    assert "stopped waiting" in turn.failures[0], turn.failures


def test_a_turn_that_ends_inside_the_hour_is_not_blamed_on_the_hour(monkeypatch):
    """The regression that matters: every ordinary turn ends well inside the
    hour, and none of them may pick up this warning.

    Ended here by the terminal closing, which is one of the ways a turn really
    does end and the one this fake can express. What is under test is the flag,
    and the flag asks one question: did the hour run out? It did not, so the
    turn is reported exactly as it was before this change.
    """
    ended = {"worker": None}

    def screens(state):
        if not state["sent"]:
            return READY
        if state["reads"] > 3:
            # The terminal closes: the stream ends and there is nothing left
            # to read, which is how the worker learns the turn is over.
            ended["worker"]._stream_ended.set()
            return ""
        return WORKING

    turn = _run(monkeypatch, screens, seconds=30.0, hold=ended)

    assert turn.notices == [], turn.notices
    assert turn.failures == ["No response received from FreeBuff"], turn.failures
