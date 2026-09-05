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
        # How many turns are speaking through it, as a real backend counts.
        self.borrowers = 0

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
        busy=lambda handle: handle.borrowers > 0,
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


def test_a_process_idle_past_the_limit_is_reaped():
    pool = backend_pool.BackendPool()
    handle = _FakeHandle()
    held = backend_pool.HeldProcess(handle, _adapter(), now=lambda: 0.0)
    key = backend_pool.pool_key("codex")
    pool.keep(key, held)
    assert pool.reap(now=901.0, idle_limit=900.0) == [key]
    assert handle.stops == 1
    assert pool.take(key) is None


def test_a_process_used_recently_is_left_alone():
    pool = backend_pool.BackendPool()
    handle = _FakeHandle()
    pool.keep(
        backend_pool.pool_key("codex"),
        backend_pool.HeldProcess(handle, _adapter(), now=lambda: 0.0),
    )
    assert pool.reap(now=899.0, idle_limit=900.0) == []
    assert handle.stops == 0
    pool.drop_all()


def test_reaping_one_tab_leaves_a_recently_used_tab_running():
    """Recently used, which is not the same thing as running a turn: this is
    the idle clock on its own. A tab with a turn actually in flight is
    `test_a_process_serving_a_turn_is_never_reaped_however_long_the_turn_runs`."""
    pool = backend_pool.BackendPool()
    idle_panel, busy_panel = _Panel(), _Panel()
    idle_handle, busy_handle = _FakeHandle(), _FakeHandle()
    pool.keep(
        backend_pool.pool_key("claude", idle_panel),
        backend_pool.HeldProcess(idle_handle, _adapter(), now=lambda: 0.0),
    )
    pool.keep(
        backend_pool.pool_key("claude", busy_panel),
        backend_pool.HeldProcess(busy_handle, _adapter(), now=lambda: 900.0),
    )
    reaped = pool.reap(now=901.0, idle_limit=900.0)
    assert reaped == [backend_pool.pool_key("claude", idle_panel)]
    assert idle_handle.stops == 1
    assert busy_handle.stops == 0
    pool.drop_all()


def test_a_reap_is_announced_so_the_next_cold_start_is_never_a_surprise():
    """A user who cannot see a spinner infers a hang from silence. The reap is
    the reason the next prompt is slow, so it has to be sayable."""
    said: list[tuple[str, str]] = []
    pool = backend_pool.BackendPool()
    pool.on_reap = lambda backend, why: said.append((backend, why))
    pool.keep(
        backend_pool.pool_key("codex"),
        backend_pool.HeldProcess(_FakeHandle(), _adapter(), now=lambda: 0.0),
    )
    pool.reap(now=901.0, idle_limit=900.0)
    assert said == [("codex", backend_pool.REAP_IDLE)]


def test_the_idle_limit_is_fifteen_minutes():
    assert backend_pool._HELD_IDLE_SECONDS == 900.0


def test_the_pool_is_swept_at_exit(monkeypatch):
    """Quitting must not leave an app-server and its MCP children behind.

    Held Hermes connections have no atexit hook today; this is the first.
    atexit exposes no supported way to read what is registered, so this
    re-imports the module with a recording stand-in in place of `register`
    and asserts the sweep is what gets handed to it.
    """
    import atexit
    import importlib

    recorded: list = []
    monkeypatch.setattr(atexit, "register", lambda fn, *a, **k: recorded.append(fn) or fn)
    importlib.reload(backend_pool)
    try:
        assert backend_pool.stop_all_held_processes in recorded
    finally:
        # Reload again so the real registration is back in place and the
        # module globals other tests read are not the reloaded copies.
        importlib.reload(backend_pool)


def test_sweeping_at_exit_stops_every_held_process():
    pool = backend_pool.pool()
    handle = _FakeHandle()
    key = backend_pool.pool_key("codex")
    try:
        pool.keep(key, backend_pool.HeldProcess(handle, _adapter()))
        backend_pool.stop_all_held_processes()
        assert handle.stops == 1
        assert pool.held_count() == 0
    finally:
        pool.drop_all()


def test_the_reaper_runs_on_a_daemon_thread():
    """A non-daemon reaper would hold the interpreter open at quit."""
    thread = backend_pool.start_reaper(interval=0.01)
    try:
        assert thread.daemon is True
        assert thread.is_alive()
    finally:
        backend_pool.stop_reaper()
        thread.join(timeout=5)
        assert not thread.is_alive(), "the reaper did not stop when asked"


def test_the_shared_pool_is_the_same_object_every_time():
    assert backend_pool.pool() is backend_pool.pool()


def test_a_process_serving_a_turn_is_never_reaped_however_long_the_turn_runs():
    """The one that killed live turns: the idle clock ran from the START of a
    turn, so a turn still going a quarter of an hour later -- a long agentic
    run, or one waiting on an approval dialog while the user is away from the
    desk -- had its process stopped underneath it. For a shared app-server
    that ends every other tab's turn at the same time."""
    pool = backend_pool.BackendPool()
    handle = _FakeHandle()
    held = backend_pool.HeldProcess(handle, _adapter(), now=lambda: 0.0)
    key = backend_pool.pool_key("codex")
    pool.keep(key, held)
    handle.borrowers = 1  # a turn is speaking through it right now

    assert pool.reap(now=100_000.0, idle_limit=900.0) == [], "a live turn was reaped"
    assert handle.stops == 0
    assert pool.take(key) is held, "the process was taken from the turn using it"

    # And once the turn has let go, the same sweep does reap it -- so the
    # assertion above is about the borrow and not about the clock.
    handle.borrowers = 0
    assert pool.reap(now=100_000.0, idle_limit=900.0) == [key]
    assert handle.stops == 1


def test_a_backend_that_cannot_say_whether_it_is_busy_is_left_alone():
    """The answer that costs one process, rather than the one that ends turns
    in tabs that never asked for anything."""
    pool = backend_pool.BackendPool()

    def boom(_handle: object) -> bool:
        raise OSError("the pipe went")

    handle = _FakeHandle()
    pool.keep(
        backend_pool.pool_key("codex"),
        backend_pool.HeldProcess(handle, _adapter()._replace(busy=boom), now=lambda: 0.0),
    )
    assert pool.reap(now=901.0, idle_limit=900.0) == []
    assert handle.stops == 0
    pool.drop_all()


def test_a_backend_that_never_learned_the_question_is_reaped_as_before():
    """`busy` has a default, because four backends supply their own later."""
    pool = backend_pool.BackendPool()
    handle = _FakeHandle()
    plain = backend_pool.Adapter(
        start=lambda: _FakeHandle(),
        alive=lambda h: h.running,
        interrupt=lambda _h, _t: True,
        stop=lambda h: h.stop(),
    )
    key = backend_pool.pool_key("codex")
    pool.keep(key, backend_pool.HeldProcess(handle, plain, now=lambda: 0.0))
    assert pool.reap(now=901.0, idle_limit=900.0) == [key]
    assert handle.stops == 1


def test_the_idle_clock_starts_when_a_turn_ends_not_when_it_begins():
    """A fourteen-minute turn used to be reaped sixty seconds after it
    answered -- which is exactly when the follow-up prompt is being typed."""
    clock = [0.0]
    pool = backend_pool.BackendPool()
    handle = _FakeHandle()
    held = backend_pool.HeldProcess(handle, _adapter(), now=lambda: clock[0])
    key = backend_pool.pool_key("codex")
    pool.keep(key, held)  # the turn begins
    clock[0] = 840.0
    pool.keep(key, held)  # and fourteen minutes later it hands the process back

    assert pool.reap(now=901.0, idle_limit=900.0) == [], (
        "a process handed back a minute ago was reaped as idle for a quarter of an hour"
    )
    assert handle.stops == 0
    assert pool.reap(now=1741.0, idle_limit=900.0) == [key]


def test_a_process_used_while_the_sweep_was_asking_is_not_reaped():
    """Whether anyone is busy is asked with no lock held, so a turn can start
    between the two passes. Taking touches, and the second pass checks again."""
    clock = [0.0]
    pool = backend_pool.BackendPool()
    handle = _FakeHandle()
    key = backend_pool.pool_key("codex")
    held = backend_pool.HeldProcess(handle, _adapter(), now=lambda: clock[0])
    pool.keep(key, held)

    def busy(_handle: object) -> bool:
        clock[0] = 901.0  # a turn takes it while the sweep is asking
        pool.take(key)
        return False

    held._adapter = held._adapter._replace(busy=busy)
    assert pool.reap(now=901.0, idle_limit=900.0) == []
    assert handle.stops == 0
    pool.drop_all()


def test_a_backend_found_dead_is_announced_rather_than_restarted_in_silence():
    """The reap is spoken; a death found on take used to cost the next prompt
    four seconds of silence, on the path where nothing warned in advance."""
    said: list[tuple[str, str]] = []
    pool = backend_pool.BackendPool()
    pool.on_reap = lambda backend, why: said.append((backend, why))
    handle = _FakeHandle()
    key = backend_pool.pool_key("codex")
    pool.keep(key, backend_pool.HeldProcess(handle, _adapter()))

    handle.running = False  # killed while the laptop slept
    assert pool.take(key) is None
    assert said == [("codex", backend_pool.REAP_DIED)], (
        "a backend that had to be restarted said nothing about it"
    )


def test_a_key_nobody_held_says_nothing_when_it_is_taken():
    """The first turn of the session is not a restart, and must not sound like one."""
    said: list[tuple[str, str]] = []
    pool = backend_pool.BackendPool()
    pool.on_reap = lambda backend, why: said.append((backend, why))
    assert pool.take(backend_pool.pool_key("codex")) is None
    assert said == []


def test_narration_that_throws_does_not_fail_the_turn_that_found_it_dead():
    pool = backend_pool.BackendPool()

    def boom(_backend: str, _why: str) -> None:
        raise RuntimeError("no window")

    pool.on_reap = boom
    handle = _FakeHandle()
    key = backend_pool.pool_key("codex")
    pool.keep(key, backend_pool.HeldProcess(handle, _adapter()))
    handle.running = False
    assert pool.take(key) is None
