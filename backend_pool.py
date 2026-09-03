"""Backend processes held across turns rather than started for each one.

Four of the five backends used to start a fresh CLI for every prompt and kill
it at turn end. On Codex that meant paying the app-server's start-up on every
message and, where MCP servers are configured, discarding and restarting all
of their child processes with it.

This module owns process lifetime and nothing else. Each backend says how to
start, check, interrupt and stop its own process; the pool decides when.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import threading
import time
from typing import Callable, NamedTuple


class Adapter(NamedTuple):
    """What a backend must say about its process, and nothing more.

    ``interrupt`` returns whether the backend CONFIRMED the turn stopped
    within the timeout. An unconfirmed interrupt is not a failure to report to
    the user; it is the signal that this process cannot be trusted for the
    next turn.
    """

    start: Callable[[], object]
    alive: Callable[[object], bool]
    interrupt: Callable[[object, float], bool]
    stop: Callable[[object], None]


class HeldProcess:
    """One live backend process and the identity it is bound to."""

    def __init__(
        self,
        handle: object,
        adapter: Adapter,
        binding: object = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.handle = handle
        # Ids the protocol needs beyond the process itself. Hermes holds a
        # stored session id and a separate live one the gateway answers to,
        # and steering by the wrong one fails; the pool carries it without
        # knowing what it is.
        self.binding = binding
        self._adapter = adapter
        self._now = now
        self._stopped = False
        self._touched = now()
        self._lock = threading.Lock()

    def alive(self) -> bool:
        if self._stopped:
            return False
        try:
            return bool(self._adapter.alive(self.handle))
        except Exception:
            return False

    def interrupt(self, timeout: float) -> bool:
        if self._stopped:
            return False
        try:
            return bool(self._adapter.interrupt(self.handle, timeout))
        except Exception:
            # The interrupt did not land, so nothing was confirmed. Saying
            # True here would hand the next turn a process still working on
            # the last one.
            return False

    def stop(self) -> None:
        """Stop the process. Safe to call again; only the first call acts."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        try:
            self._adapter.stop(self.handle)
        except Exception:
            # Nothing above this can act on a failure to stop, and raising
            # here would abandon the rest of a teardown sweep.
            pass

    def touch(self) -> None:
        self._touched = self._now()

    def idle_seconds(self, now: float) -> float:
        return max(0.0, now - self._touched)
