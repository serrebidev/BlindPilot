"""The contract holds the real HeldProcess, and rejects a fake that lies."""

from __future__ import annotations

import pytest

import backend_pool
from pool_contract import ContractViolation, check_pool_contract


class _Handle:
    def __init__(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False


def _real() -> backend_pool.HeldProcess:
    handle = _Handle()
    return backend_pool.HeldProcess(
        handle,
        backend_pool.Adapter(
            alive=lambda h: h.running,
            interrupt=lambda _h, _t: True,
            stop=lambda h: h.stop(),
        ),
    )


def test_the_real_held_process_satisfies_the_contract():
    check_pool_contract(_real, "HeldProcess")


def test_a_process_that_never_admits_it_stopped_is_rejected():
    """The exact fake this harness exists to catch."""

    class _Liar(backend_pool.HeldProcess):
        def alive(self) -> bool:
            return True

    def build() -> backend_pool.HeldProcess:
        handle = _Handle()
        return _Liar(
            handle,
            backend_pool.Adapter(
                alive=lambda h: h.running,
                interrupt=lambda _h, _t: True,
                stop=lambda h: h.stop(),
            ),
        )

    with pytest.raises(ContractViolation, match="alive"):
        check_pool_contract(build, "_Liar")


def test_a_process_that_cannot_be_stopped_twice_is_rejected():
    class _Fragile(backend_pool.HeldProcess):
        def stop(self) -> None:
            if getattr(self, "_done", False):
                raise RuntimeError("already stopped")
            self._done = True

        def alive(self) -> bool:
            return not getattr(self, "_done", False)

        def interrupt(self, _timeout: float) -> bool:
            return False

    def build() -> backend_pool.HeldProcess:
        return _Fragile(_Handle(), _real()._adapter)

    with pytest.raises(ContractViolation, match="twice"):
        check_pool_contract(build, "_Fragile")


def test_a_process_that_keeps_confirming_interrupts_is_rejected():
    """Confirm an interrupt after being stopped keeps the process in the pool."""

    class _ConfirmsInterruptEvenAfterStop(backend_pool.HeldProcess):
        def interrupt(self, _timeout: float) -> bool:
            return True  # unconditionally confirms, even after stop

    def build() -> backend_pool.HeldProcess:
        handle = _Handle()
        return _ConfirmsInterruptEvenAfterStop(
            handle,
            backend_pool.Adapter(
                alive=lambda h: h.running,
                interrupt=lambda _h, _t: True,
                stop=lambda h: h.stop(),
            ),
        )

    with pytest.raises(ContractViolation, match="confirms an interrupt after being stopped"):
        check_pool_contract(build, "_ConfirmsInterruptEvenAfterStop")


def test_the_contract_rejects_a_pool_that_leaves_a_dead_process_registered(monkeypatch):
    """What clause 4 has that clause 2 does not: the dead entry is removed.

    A pool that returns None for a corpse and leaves it in the registry passes
    every other clause -- the process really is stopped, and really is not
    handed on -- while no replacement can ever be kept under that key. Nothing
    a stand-in can do produces this; it takes a pool that has lost the `del`,
    so that is what is put in front of the clause here. Before the count was
    asserted, this shape was invisible: the reviewer who deleted the removal
    saw all five contract tests pass.
    """

    class _KeepsTheCorpse(backend_pool.BackendPool):
        def take(self, key: tuple):
            held = self._shared.get(key[0])
            if held is not None and not held.alive():
                return None  # discarded from the caller's view, not the registry
            return backend_pool.BackendPool.take(self, key)

    monkeypatch.setattr(backend_pool, "BackendPool", _KeepsTheCorpse)
    with pytest.raises(ContractViolation, match="left in the registry"):
        check_pool_contract(_real, "HeldProcess")
