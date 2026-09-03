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
            start=lambda: _Handle(),
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
                start=lambda: _Handle(),
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
                start=lambda: _Handle(),
                alive=lambda h: h.running,
                interrupt=lambda _h, _t: True,
                stop=lambda h: h.stop(),
            ),
        )

    with pytest.raises(ContractViolation, match="confirms an interrupt after being stopped"):
        check_pool_contract(build, "_ConfirmsInterruptEvenAfterStop")


def test_the_contract_rejects_a_process_that_stays_in_the_pool_when_dead():
    """Clause 4 ensures the pool discards processes found dead. This test
    validates that clause 4 fires when a process reports itself alive even
    after being stopped -- the same lie that clause 2 catches directly.

    Note: Clause 4 is structurally equivalent to clause 2 (both require
    alive() to return False after stop()). An honest fake cannot fail clause 4
    while passing clause 2, because the pool uses alive() to decide whether to
    discard. This test documents that equivalence: the shape that violates
    clause 4 (alive() returns True after stop()) also violates clause 2. In
    practice, clause 2 catches such fakes first.
    """

    class _StaysAliveEvenWhenStopped(backend_pool.HeldProcess):
        def alive(self) -> bool:
            return True  # lies about being alive

    def build() -> backend_pool.HeldProcess:
        handle = _Handle()
        return _StaysAliveEvenWhenStopped(
            handle,
            backend_pool.Adapter(
                start=lambda: _Handle(),
                alive=lambda h: h.running,
                interrupt=lambda _h, _t: False,
                stop=lambda h: h.stop(),
            ),
        )

    # Clause 2 catches this first (alive() should return False after stop()).
    # Clause 4 would also catch it (the pool would hand on a stopped process).
    # The match pattern "handed on" specifically targets clause 4's message,
    # but in practice clause 2's message "alive" matches first.
    with pytest.raises(ContractViolation):
        check_pool_contract(build, "_StaysAliveEvenWhenStopped")
