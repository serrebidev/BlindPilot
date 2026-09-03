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
