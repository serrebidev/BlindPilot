"""Hermes has to satisfy the contract the window applies to every backend.

These are the checks that a merge with upstream cannot pass by accident. The
rest of the Hermes tests exercise the adapter against a fake gateway; nothing
there notices when the *window* starts handing every worker something new, or
when a table Hermes has to appear in gains a backend and loses him.

The defect this file exists for was real and silent: upstream began passing
``on_question`` to every worker it starts, Hermes' worker did not accept it, and
every single Hermes turn would have died with a TypeError before reaching the
gateway. All 491 other tests passed, because each one constructs the worker
itself with the arguments it knows about.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import inspect

import agent_backends
import session_history
from agent_backends import BACKEND_HERMES, BACKEND_IDS, BACKENDS, normalize_backend
from hermes_worker import HermesWorker


def _noop(*_args, **_kwargs) -> None:
    return None


_WINDOW_CALLBACKS = (
    "on_session",
    "on_started",
    "on_activity",
    "on_complete",
    "on_failed",
    "on_done",
    "on_question",
)


def test_hermes_worker_accepts_every_callback_the_window_passes() -> None:
    """The window passes these to whichever worker it started, unconditionally.

    Constructed here the way ``_send`` constructs it, so a callback added to
    that call is caught here rather than on the user's first turn.
    """
    worker = HermesWorker(
        "hello",
        None,
        ".",
        "auto",
        on_session=_noop,
        on_started=_noop,
        on_activity=_noop,
        on_complete=_noop,
        on_failed=_noop,
        on_done=_noop,
        on_question=_noop,
    )
    accepted = set(inspect.signature(HermesWorker.__init__).parameters)
    assert worker is not None
    for name in _WINDOW_CALLBACKS:
        assert name in accepted, f"HermesWorker does not accept {name}"


def test_every_worker_accepts_the_same_window_callbacks() -> None:
    """Hermes is held to the same contract as the backends upstream maintains.

    This is the check that keeps the fix above from being Hermes-specific
    knowledge: if upstream adds another callback to its own workers, the
    difference shows up as a named parameter Hermes is missing.
    """
    reference = agent_backends.CodexWorker
    expected = {
        name for name in inspect.signature(reference.__init__).parameters if name.startswith("on_")
    }
    accepted = set(inspect.signature(HermesWorker.__init__).parameters)
    missing = sorted(expected - accepted)
    assert not missing, f"HermesWorker does not accept: {missing}"


def test_hermes_never_calls_the_question_callback() -> None:
    """Accepting it is not the same as pretending to support it.

    Hermes' gateway protocol has no mid-turn question, and BackendInfo says so.
    Storing the callback is how the worker stays constructible; calling it would
    mean inventing a question nobody asked.
    """
    calls: list[object] = []
    worker = HermesWorker(
        "hello",
        None,
        ".",
        "auto",
        on_session=_noop,
        on_started=_noop,
        on_activity=_noop,
        on_complete=_noop,
        on_failed=_noop,
        on_done=_noop,
        on_question=lambda *a, **k: calls.append(a),
    )
    # Nothing has run the turn, and nothing should have asked anything either.
    assert calls == []
    assert worker._on_question is not None


def test_hermes_is_registered_everywhere_a_backend_has_to_be() -> None:
    """A merge that resolves a conflict by choosing one side drops Hermes here.

    Each of these is a place where two sides of a merge both add a row, which is
    exactly where "take theirs" quietly removes a backend: the app would start,
    the tests would pass, and Hermes would simply not be in the menu.
    """
    assert BACKEND_HERMES in BACKEND_IDS
    assert BACKEND_HERMES in BACKENDS
    assert BACKEND_HERMES in agent_backends.BACKEND_LABELS
    assert BACKEND_HERMES in session_history._LISTERS
    assert BACKEND_HERMES in session_history._READERS
    assert agent_backends.compaction_request(BACKEND_HERMES) is not None
    assert agent_backends.worker_class(BACKEND_HERMES, object) is HermesWorker
    # And the aliases the settings file may still hold from an older version.
    for spelling in ("hermes", "Hermes", "hermes-agent", "Hermes Agent", "nous"):
        assert normalize_backend(spelling) == BACKEND_HERMES


def test_no_backend_was_lost_from_the_pickers() -> None:
    """Negative control for the test above: upstream's backends survive too.

    Resolving those same conflicts the other way -- keeping only our side --
    would drop opencode, and a test suite that only ever checks for Hermes
    would call that a success.
    """
    for backend in ("claude", "codex", "freebuff", "opencode", "hermes"):
        assert backend in BACKEND_IDS
        assert backend in session_history._LISTERS
        assert backend in session_history._READERS


def test_hermes_reader_reads_the_conversation_it_was_given() -> None:
    """Upstream changed every reader to take the entry, not a path.

    Hermes keeps all conversations in one store, so a reader that ignores the
    entry returns the wrong conversation -- or nothing -- while still passing
    any test that only checks the list of conversations.
    """
    seen: list[str] = []
    original = session_history._hermes_turns_for
    try:
        session_history._hermes_turns_for = lambda sid: seen.append(sid) or []
        entry = session_history.HistoryEntry(
            backend=BACKEND_HERMES,
            session_id="20260901_120000_abcdef",
            path=str(session_history._home() / ".hermes" / "state.db"),
            title="whatever",
            modified=0.0,
            cwd="",
            folder="",
        )
        session_history.load_turns(entry)
    finally:
        session_history._hermes_turns_for = original
    assert seen == ["20260901_120000_abcdef"]
