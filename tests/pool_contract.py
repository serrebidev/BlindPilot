"""What every stand-in for a held backend process must actually do.

A fake that reports itself alive for ever makes the pool look correct while
hiding the one behaviour that matters: that a process found dead is replaced
rather than handed to the next turn. This is the transport contract's argument
(tests/transport_contract.py), applied to process lifetime.
"""

from __future__ import annotations

from typing import Callable

import backend_pool


class ContractViolation(AssertionError):
    """A stand-in that would let a real bug through."""


def check_stop_is_idempotent(build: Callable[[], backend_pool.HeldProcess], who: str) -> None:
    """Teardown paths run twice: cancel_worker, then atexit."""
    held = build()
    held.stop()
    try:
        held.stop()
    except Exception as exc:
        raise ContractViolation(f"{who}: stopping twice raised {exc!r}") from exc


def check_a_stopped_process_is_not_alive(
    build: Callable[[], backend_pool.HeldProcess], who: str
) -> None:
    held = build()
    held.stop()
    if held.alive():
        raise ContractViolation(f"{who}: reports alive after stop, so a dead process is reused")


def check_a_stopped_process_confirms_no_interrupt(
    build: Callable[[], backend_pool.HeldProcess], who: str
) -> None:
    """An interrupt cannot be confirmed by something already stopped. Saying
    True keeps the process, and the pool would hand on a corpse."""
    held = build()
    held.stop()
    if held.interrupt(0.01):
        raise ContractViolation(f"{who}: confirms an interrupt after being stopped")


def check_the_pool_replaces_it_once_dead(
    build: Callable[[], backend_pool.HeldProcess], who: str
) -> None:
    """Not handed on, AND not left in the registry.

    Refusing to hand a corpse to the next turn is clause 2's ground, reached
    through the pool. What is only this clause's is the removal: an entry that
    stays behind is one a replacement cannot be kept under, so every prompt
    after it would start a process the pool then forgets.
    """
    pool = backend_pool.BackendPool()
    held = build()
    key = backend_pool.pool_key("contract")
    pool.keep(key, held)
    held.stop()
    try:
        if pool.take(key) is not None:
            raise ContractViolation(f"{who}: the pool handed on a stopped process")
        if pool.held_count() != 0:
            raise ContractViolation(
                f"{who}: a dead process was left in the registry, so nothing can replace it"
            )
    finally:
        pool.drop_all()


CLAUSES = (
    check_stop_is_idempotent,
    check_a_stopped_process_is_not_alive,
    check_a_stopped_process_confirms_no_interrupt,
    check_the_pool_replaces_it_once_dead,
)


def check_pool_contract(build: Callable[[], backend_pool.HeldProcess], who: str) -> None:
    """Every clause, each against a freshly built object.

    Rebuilt per clause so no clause inherits the state another left behind --
    the same reason transport_contract.py rebuilds.
    """
    for clause in CLAUSES:
        clause(build, who)
