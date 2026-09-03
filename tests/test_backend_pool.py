"""The pool holds a backend's process across turns instead of per turn."""

from __future__ import annotations

import gc

import backend_pool


class _FakeHandle:
    """A stand-in for a Popen, counting what the pool does to it."""

    def __init__(self, alive: bool = True, confirms: bool = True) -> None:
        self.running = alive
        self.confirms = confirms
        self.stops = 0
        self.interrupts = 0

    def stop(self) -> None:
        self.stops += 1
        self.running = False


def _adapter() -> backend_pool.Adapter:
    def interrupt(handle: _FakeHandle, _timeout: float) -> bool:
        handle.interrupts += 1
        return handle.confirms

    return backend_pool.Adapter(
        start=lambda: _FakeHandle(),
        alive=lambda handle: handle.running,
        interrupt=interrupt,
        stop=lambda handle: handle.stop(),
    )


def test_a_held_process_reports_the_health_of_its_handle():
    handle = _FakeHandle()
    held = backend_pool.HeldProcess(handle, _adapter())
    assert held.alive() is True
    handle.running = False
    assert held.alive() is False


def test_stopping_twice_stops_the_handle_once():
    """Teardown paths run twice -- cancel_worker and then atexit. A second
    stop that reached the handle again would kill a process another key had
    already been given."""
    handle = _FakeHandle()
    held = backend_pool.HeldProcess(handle, _adapter())
    held.stop()
    held.stop()
    assert handle.stops == 1


def test_a_stopped_process_is_never_reported_alive():
    handle = _FakeHandle()
    held = backend_pool.HeldProcess(handle, _adapter())
    held.stop()
    handle.running = True  # the adapter would lie; the flag must win
    assert held.alive() is False


def test_an_interrupt_the_backend_confirms_is_reported_confirmed():
    handle = _FakeHandle(confirms=True)
    held = backend_pool.HeldProcess(handle, _adapter())
    assert held.interrupt(0.01) is True
    assert handle.interrupts == 1


def test_an_interrupt_the_backend_does_not_confirm_is_reported_unconfirmed():
    handle = _FakeHandle(confirms=False)
    held = backend_pool.HeldProcess(handle, _adapter())
    assert held.interrupt(0.01) is False


def test_an_interrupt_that_raises_counts_as_unconfirmed():
    """A backend whose pipe closed under the interrupt has not confirmed
    anything. Reporting True there would keep a dead process."""

    def boom(_handle: object, _timeout: float) -> bool:
        raise OSError("pipe closed")

    adapter = _adapter()._replace(interrupt=boom)
    held = backend_pool.HeldProcess(_FakeHandle(), adapter)
    assert held.interrupt(0.01) is False


def test_idle_time_is_measured_from_the_last_touch():
    held = backend_pool.HeldProcess(_FakeHandle(), _adapter(), now=lambda: 100.0)
    assert held.idle_seconds(100.0) == 0.0
    assert held.idle_seconds(160.0) == 60.0
    held.touch()
    assert held.idle_seconds(100.0) == 0.0


class _Panel:
    """Stands in for a SessionPanel: identity-hashed and weak-referenceable."""


def test_a_kept_process_is_given_back_to_the_same_key():
    pool = backend_pool.BackendPool()
    held = backend_pool.HeldProcess(_FakeHandle(), _adapter())
    key = backend_pool.pool_key("codex")
    pool.keep(key, held)
    assert pool.take(key) is held
    pool.drop_all()


def test_a_dead_process_is_discarded_rather_than_handed_on():
    pool = backend_pool.BackendPool()
    handle = _FakeHandle()
    held = backend_pool.HeldProcess(handle, _adapter())
    key = backend_pool.pool_key("codex")
    pool.keep(key, held)
    handle.running = False
    assert pool.take(key) is None
    assert pool.take(key) is None, "the dead one must not still be in the registry"
    pool.drop_all()


def test_taking_marks_the_process_used_so_the_reaper_leaves_it_alone():
    pool = backend_pool.BackendPool()
    held = backend_pool.HeldProcess(_FakeHandle(), _adapter(), now=lambda: 0.0)
    key = backend_pool.pool_key("codex")
    pool.keep(key, held)
    assert held.idle_seconds(500.0) == 500.0
    pool.take(key)
    assert held.idle_seconds(0.0) == 0.0


def test_two_panels_of_the_same_backend_hold_different_processes():
    """The per-conversation shape: one process per tab, not one per app."""
    pool = backend_pool.BackendPool()
    first, second = _Panel(), _Panel()
    one = backend_pool.HeldProcess(_FakeHandle(), _adapter())
    two = backend_pool.HeldProcess(_FakeHandle(), _adapter())
    pool.keep(backend_pool.pool_key("claude", first), one)
    pool.keep(backend_pool.pool_key("claude", second), two)
    assert pool.take(backend_pool.pool_key("claude", first)) is one
    assert pool.take(backend_pool.pool_key("claude", second)) is two
    pool.drop_all()


def test_every_panel_shares_one_process_wide_backend():
    """The process-wide shape: the panel is not part of the key at all."""
    pool = backend_pool.BackendPool()
    held = backend_pool.HeldProcess(_FakeHandle(), _adapter())
    pool.keep(backend_pool.pool_key("codex"), held)
    assert pool.take(backend_pool.pool_key("codex")) is held
    pool.drop_all()


def test_dropping_stops_the_process_and_forgets_it():
    pool = backend_pool.BackendPool()
    handle = _FakeHandle()
    key = backend_pool.pool_key("codex")
    pool.keep(key, backend_pool.HeldProcess(handle, _adapter()))
    pool.drop(key)
    assert handle.stops == 1
    assert pool.take(key) is None


def test_dropping_a_key_nobody_held_does_nothing():
    """Teardown runs on tabs that never started a turn."""
    pool = backend_pool.BackendPool()
    pool.drop(backend_pool.pool_key("codex"))
    pool.drop(backend_pool.pool_key("claude", _Panel()))


def test_dropping_one_panel_leaves_the_others_running():
    pool = backend_pool.BackendPool()
    first, second = _Panel(), _Panel()
    mine, yours = _FakeHandle(), _FakeHandle()
    pool.keep(backend_pool.pool_key("claude", first), backend_pool.HeldProcess(mine, _adapter()))
    pool.keep(backend_pool.pool_key("claude", second), backend_pool.HeldProcess(yours, _adapter()))
    pool.drop(backend_pool.pool_key("claude", first))
    assert mine.stops == 1
    assert yours.stops == 0
    pool.drop_all()


def test_drop_all_stops_everything_including_a_process_that_raises():
    """One backend that throws on stop must not strand the rest at quit."""
    pool = backend_pool.BackendPool()

    def explode(_handle: object) -> None:
        raise OSError("no")

    angry = _adapter()._replace(stop=explode)
    calm = _FakeHandle()
    pool.keep(backend_pool.pool_key("codex"), backend_pool.HeldProcess(_FakeHandle(), angry))
    panel = _Panel()
    pool.keep(backend_pool.pool_key("claude", panel), backend_pool.HeldProcess(calm, _adapter()))
    pool.drop_all()
    assert calm.stops == 1
    assert pool.held_count() == 0


def test_a_collected_panel_does_not_keep_its_process_registered():
    """A tab destroyed without cancel_worker running -- the case
    blindpilot_app.py:5872 already guards -- must not pin a process for the
    life of the application."""
    pool = backend_pool.BackendPool()
    panel = _Panel()
    pool.keep(
        backend_pool.pool_key("claude", panel), backend_pool.HeldProcess(_FakeHandle(), _adapter())
    )
    assert pool.held_count() == 1
    del panel
    gc.collect()
    assert pool.held_count() == 0
