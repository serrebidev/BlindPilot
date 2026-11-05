from __future__ import annotations

from collections.abc import Callable

from dialog_tasks import TaskSlot


def _queued_slot() -> tuple[TaskSlot, list[Callable[[], None]], list[Callable[[], None]]]:
    workers: list[Callable[[], None]] = []
    deliveries: list[Callable[[], None]] = []

    def dispatch(callback: Callable[..., None], *args: object) -> None:
        deliveries.append(lambda: callback(*args))

    return TaskSlot(dispatch, workers.append), workers, deliveries


def test_task_delivers_result_through_injected_dispatcher() -> None:
    slot, workers, deliveries = _queued_slot()
    received: list[tuple[object, str]] = []
    slot.start(lambda: "ready", lambda result, error: received.append((result, error)))

    workers.pop()()
    assert received == []
    deliveries.pop()()

    assert received == [("ready", "")]


def test_task_converts_worker_exception_to_error_text() -> None:
    slot, workers, deliveries = _queued_slot()
    received: list[tuple[object, str]] = []

    def fail() -> object:
        raise OSError("gateway closed")

    slot.start(fail, lambda result, error: received.append((result, error)))
    workers.pop()()
    deliveries.pop()()

    assert received == [(None, "gateway closed")]


def test_new_task_invalidates_old_delivery_and_close_drops_current_delivery() -> None:
    slot, workers, deliveries = _queued_slot()
    received: list[tuple[object, str]] = []

    def done(result: object, error: str) -> None:
        received.append((result, error))

    slot.start(lambda: "old", done)
    workers.pop()()
    slot.start(lambda: "new", done)
    deliveries.pop()()
    workers.pop()()
    deliveries.pop()()
    assert received == [("new", "")]

    slot.start(lambda: "closing", done)
    workers.pop()()
    slot.close()
    deliveries.pop()()
    slot.start(lambda: "after close", done)

    assert received == [("new", "")]
    assert workers == []


def test_invalidate_drops_delivery_without_closing_the_slot() -> None:
    slot, workers, deliveries = _queued_slot()
    received: list[tuple[object, str]] = []

    def done(result: object, error: str) -> None:
        received.append((result, error))

    slot.start(lambda: "cancelled", done)
    workers.pop()()
    slot.invalidate()
    deliveries.pop()()
    slot.start(lambda: "next", done)
    workers.pop()()
    deliveries.pop()()

    assert received == [("next", "")]
