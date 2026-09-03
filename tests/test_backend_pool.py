"""The pool holds a backend's process across turns instead of per turn."""

from __future__ import annotations

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
