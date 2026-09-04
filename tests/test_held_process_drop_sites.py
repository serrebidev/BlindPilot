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
