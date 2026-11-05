"""Run dialog work without blocking wx and discard stale completions."""

from __future__ import annotations

import threading
from typing import Callable


def _start_daemon_worker(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True).start()


class TaskSlot:
    """One replaceable stream of work owned by a dialog."""

    def __init__(
        self,
        dispatch: Callable[..., None],
        start_worker: Callable[[Callable[[], None]], None] = _start_daemon_worker,
    ) -> None:
        self._dispatch = dispatch
        self._start_worker = start_worker
        self._generation = 0
        self._closed = False

    def start(
        self,
        work: Callable[[], object],
        done: Callable[[object, str], None],
    ) -> None:
        if self._closed:
            return
        self._generation += 1
        generation = self._generation

        def run() -> None:
            try:
                result, error = work(), ""
            except Exception as exc:
                result, error = None, str(exc)
            self._dispatch(self._deliver, generation, done, result, error)

        self._start_worker(run)

    def invalidate(self) -> None:
        self._generation += 1

    def close(self) -> None:
        self._closed = True
        self.invalidate()

    def _deliver(
        self,
        generation: int,
        done: Callable[[object, str], None],
        result: object,
        error: str,
    ) -> None:
        if self._closed or generation != self._generation:
            return
        done(result, error)
