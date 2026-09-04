"""A tab that stops being its conversation must let go of its process.

The failure this guards is not a crash. It is the next message going into the
previous conversation, which nobody would see until they read the transcript.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import backend_pool

app = pytest.importorskip("blindpilot_app")


# Every place the tab stops being the conversation its process was started
# for. Taken from the design doc; a seventh added without a drop is a bug
# this list is here to catch.
ABANDONMENT_SITES = [
    "clear_conversation",
    "restore_history",
    "open_hermes_session",
]


@pytest.mark.parametrize("method_name", ABANDONMENT_SITES)
def test_every_abandonment_site_lets_go_of_the_held_process(method_name):
    source = inspect.getsource(getattr(app.SessionPanel, method_name))
    assert "_drop_held_backends" in source, (
        f"{method_name} changes which conversation this tab is, "
        "but does not let go of the process held for the old one"
    )


def test_no_drop_site_was_left_behind_under_the_old_name():
    """The other three sites are inline, where `getsource` cannot reach them.

    `_on_send`'s backend change and the FreeBuff-model and Hermes-effort
    handlers each drop too, and a rename that missed one would leave a call to
    a method that no longer exists -- an AttributeError at exactly the moment a
    tab changes conversation. Reading the file catches all six at once.
    """
    source = Path(app.__file__).read_text(encoding="utf-8")
    assert "_drop_held_hermes" not in source, (
        "a call site still uses the old name; every drop site must go through _drop_held_backends"
    )


def test_cancel_worker_lets_go_too():
    source = inspect.getsource(app.SessionPanel.cancel_worker)
    assert "_drop_held_backends" in source


def test_dropping_stops_every_backend_this_panel_held():
    """A tab that switched backends mid-conversation may hold more than one."""

    class _Handle:
        def __init__(self) -> None:
            self.running = True
            self.stops = 0

        def stop(self) -> None:
            self.stops += 1
            self.running = False

    adapter = backend_pool.Adapter(
        start=lambda: _Handle(),
        alive=lambda h: h.running,
        interrupt=lambda _h, _t: True,
        stop=lambda h: h.stop(),
    )
    panel = type("_Panel", (), {})()
    pool = backend_pool.pool()
    claude, hermes = _Handle(), _Handle()
    try:
        pool.keep(
            backend_pool.pool_key(app.BACKEND_CLAUDE, panel),
            backend_pool.HeldProcess(claude, adapter),
        )
        pool.keep(
            backend_pool.pool_key(app.BACKEND_HERMES, panel),
            backend_pool.HeldProcess(hermes, adapter),
        )
        app.SessionPanel._drop_held_backends(panel)
        assert claude.stops == 1
        assert hermes.stops == 1
    finally:
        pool.drop_all()


def test_dropping_leaves_the_process_wide_backends_alone():
    """Codex's one process serves every tab, so one tab must not end it.

    This tab abandoning a conversation is not a reason to end four other tabs'
    work, and the shared key carries no panel to drop by.
    """

    class _Handle:
        def __init__(self) -> None:
            self.stops = 0

        def stop(self) -> None:
            self.stops += 1

    adapter = backend_pool.Adapter(
        start=lambda: _Handle(),
        alive=lambda _h: True,
        interrupt=lambda _h, _t: True,
        stop=lambda h: h.stop(),
    )
    panel = type("_Panel", (), {})()
    pool = backend_pool.pool()
    codex = _Handle()
    try:
        pool.keep(
            backend_pool.pool_key(app.BACKEND_CODEX), backend_pool.HeldProcess(codex, adapter)
        )
        app.SessionPanel._drop_held_backends(panel)
        assert codex.stops == 0
    finally:
        pool.drop_all()


def test_dropping_survives_a_panel_that_never_held_anything():
    """cancel_worker runs on half-built panels and on test stand-ins."""
    panel = type("_Panel", (), {})()
    app.SessionPanel._drop_held_backends(panel)  # must not raise


def test_quitting_sweeps_the_pool():
    source = inspect.getsource(app.MainFrame._on_close)
    assert "drop_all" in source or "stop_all_held_processes" in source


def test_the_window_starts_the_reaper_and_listens_for_it():
    source = inspect.getsource(app.MainFrame.__init__)
    assert "start_reaper" in source, "nothing ever reaps an idle backend"
    assert "on_reap" in source, "a reap would happen silently"


def test_the_reap_announcement_names_the_backend_that_restarts():
    """It goes through SessionPanel._say, which already decides that only the
    visible tab narrates -- so this adds no second narration path."""
    said: list[tuple[str, str]] = []

    class _Page:
        def _say(self, text: str, kind: str = "assistant") -> bool:
            said.append((text, kind))
            return True

    class _Notebook:
        def GetCurrentPage(self):
            return _Page()

    frame = type("_Frame", (), {"notebook": _Notebook()})()
    app.MainFrame._announce_reap(frame, "codex")
    assert said, "a reaped backend was not announced"
    assert "Codex" in said[0][0]


def test_the_reap_announcement_says_what_happens_next():
    """Naming the backend is not enough: the user has to be told that the
    delay on the next prompt is a restart, not a hang."""
    said: list[tuple[str, str]] = []

    class _Page:
        def _say(self, text: str, kind: str = "assistant") -> bool:
            said.append((text, kind))
            return True

    class _Notebook:
        def GetCurrentPage(self):
            return _Page()

    frame = type("_Frame", (), {"notebook": _Notebook()})()
    app.MainFrame._announce_reap(frame, app.BACKEND_CODEX)
    text, kind = said[0]
    assert "restart" in text.lower(), f"the next prompt's delay is unexplained: {text!r}"
    # Pinned against the tuple, not a literal: a kind outside it is dropped
    # unspoken in keep-up narration (blindpilot_app.py:6715), which is the one
    # mode where a silent cold start is most likely to be read as a hang. If
    # the tuple is ever narrowed, this must fail rather than quietly go mute.
    assert kind in app._ALWAYS_SPOKEN, (
        f"kind {kind!r} is not spoken in keep-up narration, so the cold start "
        f"it explains would still arrive in silence; _ALWAYS_SPOKEN is {app._ALWAYS_SPOKEN}"
    )
    # Said out loud, mid-work. Jargon from the implementation is not wording.
    lowered = text.lower()
    assert "pool" not in lowered and "held" not in lowered and "reap" not in lowered


def test_a_reap_with_no_visible_page_is_silent_rather_than_a_crash():
    """The window can be mid-teardown when the reaper fires."""

    class _Notebook:
        def GetCurrentPage(self):
            return None

    frame = type("_Frame", (), {"notebook": _Notebook()})()
    app.MainFrame._announce_reap(frame, "codex")  # must not raise


def test_the_reap_callback_is_marshalled_onto_the_gui_thread():
    """The reaper sweeps on a thread of its own, and wx narration must not be
    driven from there. Calling _announce_reap straight from the callback would
    speak from the wrong thread, which is a crash on Windows, not a warning."""
    source = inspect.getsource(app.MainFrame.__init__)
    on_reap = next(line for line in source.splitlines() if "on_reap" in line)
    assert "CallAfter" in on_reap, (
        "on_reap runs on the reaper's thread; it has to hand off to the window's"
    )
