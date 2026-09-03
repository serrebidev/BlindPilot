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

import atexit
import threading
import time
import weakref
from typing import Callable, NamedTuple, Optional


# How long a held process may sit unused before it is stopped. Long enough
# that an active conversation is always warm; short enough that a laptop left
# open overnight is not holding an app-server and its MCP children.
_HELD_IDLE_SECONDS = 900.0


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


# The panel half of a key for a backend whose one process serves every tab.
_SHARED = None


def pool_key(backend: str, panel: object = None) -> tuple:
    """The registry key for this backend, in the shape its protocol wants.

    Codex and opencode multiplex several conversations through one process, so
    the panel is left out and every tab shares. Claude, Hermes and FreeBuff run
    one conversation per process, so the panel is what separates them.

    The panel -- the SessionPanel object itself -- is the only stable
    per-conversation identity. `cwd` is not unique, because `/btw` opens a
    second tab in the same directory; and `session_id` does not exist yet when
    the first turn of a conversation starts.
    """
    return (backend, panel)


class BackendPool:
    """Which backend process belongs to which conversation, and for how long."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Process-wide backends: one entry per backend, for the whole app.
        self._shared: dict[str, HeldProcess] = {}
        # Per-conversation backends. Weak on the panel so a tab destroyed
        # without a teardown cannot pin its process for the life of the app.
        # This is a backstop, not the mechanism: collection is not prompt, so
        # the enumerated drop sites remain the contract.
        self._per_panel: "weakref.WeakKeyDictionary[object, dict[str, HeldProcess]]" = (
            weakref.WeakKeyDictionary()
        )
        # Told the backend name of each reaped process, so the window can say
        # why the next prompt in that tab starts cold. Set by the window; the
        # pool itself must not import wx or speak.
        self.on_reap: Optional[Callable[[str], None]] = None

    def _slot(self, key: tuple) -> tuple[Optional[dict], str]:
        """The dict a key lives in and the name within it, or (None, name)."""
        backend, panel = key
        if panel is _SHARED:
            return self._shared, backend
        return self._per_panel.get(panel), backend

    def take(self, key: tuple) -> Optional[HeldProcess]:
        """The process for this key if it can still serve a turn, else None.

        A process found dead is discarded here rather than handed on, so the
        caller's only job is to start a new one when this returns None.
        """
        with self._lock:
            slot, name = self._slot(key)
            held = slot.get(name) if slot is not None else None
            if held is None:
                return None
            if not held.alive():
                del slot[name]  # type: ignore[index, union-attr]
                dead = held
            else:
                held.touch()
                return held
        dead.stop()
        return None

    def keep(self, key: tuple, held: HeldProcess) -> None:
        """Hand a process back at the end of a turn."""
        backend, panel = key
        stale: Optional[HeldProcess] = None
        with self._lock:
            if panel is _SHARED:
                stale = self._shared.get(backend)
                self._shared[backend] = held
            else:
                slot = self._per_panel.get(panel)
                if slot is None:
                    slot = {}
                    self._per_panel[panel] = slot
                stale = slot.get(backend)
                slot[backend] = held
            if stale is held:
                stale = None
        if stale is not None:
            stale.stop()

    def drop(self, key: tuple) -> None:
        """Stop this key's process and forget it. Safe on a key never held."""
        with self._lock:
            slot, name = self._slot(key)
            held = slot.pop(name, None) if slot is not None else None
        if held is not None:
            held.stop()

    def drop_all(self) -> None:
        """Stop everything -- at quit, and before an update replaces a CLI."""
        with self._lock:
            everything = list(self._shared.values())
            self._shared.clear()
            for slot in list(self._per_panel.values()):
                everything.extend(slot.values())
                slot.clear()
        for held in everything:
            # One backend that throws on the way down must not strand the
            # rest; HeldProcess.stop already swallows, this is belt and braces.
            held.stop()

    def held_count(self) -> int:
        with self._lock:
            return len(self._shared) + sum(len(slot) for slot in self._per_panel.values())

    def reap(self, now: float, idle_limit: float = _HELD_IDLE_SECONDS) -> list:
        """Stop every process idle longer than the limit. Returns their keys.

        Takes ``now`` rather than reading the clock so a fifteen-minute rule
        can be tested in a suite whose per-test timeout is sixty seconds.
        """
        expired: list[tuple[tuple, HeldProcess]] = []
        with self._lock:
            for backend, held in list(self._shared.items()):
                if held.idle_seconds(now) > idle_limit:
                    del self._shared[backend]
                    expired.append((pool_key(backend), held))
            for panel, slot in list(self._per_panel.items()):
                for backend, held in list(slot.items()):
                    if held.idle_seconds(now) > idle_limit:
                        del slot[backend]
                        expired.append((pool_key(backend, panel), held))
        announce = self.on_reap
        for key, held in expired:
            held.stop()
            if announce is not None:
                try:
                    announce(key[0])
                except Exception:
                    # Narration failing must not strand the sweep.
                    pass
        return [key for key, _held in expired]


_pool: Optional[BackendPool] = None
_POOL_LOCK = threading.Lock()


def pool() -> BackendPool:
    """The shared pool, created on first use."""
    global _pool
    with _POOL_LOCK:
        if _pool is None:
            _pool = BackendPool()
        return _pool


_reaper: Optional[threading.Thread] = None
_reaper_stop = threading.Event()


def stop_all_held_processes() -> None:
    """Stop every held process -- at quit, and before an update replaces a CLI.

    Belt and braces alongside the window's own teardown: `_on_close` cancels
    each tab, but a crash on the way out, or a path that never reaches the
    window at all, would otherwise leave an app-server and its MCP children
    running with nobody to stop them.
    """
    running = _pool
    if running is not None:
        running.drop_all()


def start_reaper(interval: float = 60.0) -> threading.Thread:
    """Sweep idle processes on a timer, on a thread of its own."""
    global _reaper
    _reaper_stop.clear()

    def sweep() -> None:
        while not _reaper_stop.wait(interval):
            try:
                pool().reap(now=time.monotonic())
            except Exception:
                # A sweep that throws must not end the sweeping.
                pass

    _reaper = threading.Thread(target=sweep, name="backend-pool-reaper", daemon=True)
    _reaper.start()
    return _reaper


def stop_reaper() -> None:
    _reaper_stop.set()


atexit.register(stop_all_held_processes)
