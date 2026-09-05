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


# Why a process was let go, for the sentence the window says about it. The
# two are not the same event to somebody listening: one was announced by a
# rule they can predict, the other happened to them.
REAP_IDLE = "idle"
REAP_DIED = "died"


def _never_busy(_handle: object) -> bool:
    """The default for a backend that does not yet count who is using it.

    False, not True: it is what every backend did before there was anything to
    ask, so an adapter that has not been taught the question keeps its present
    behaviour rather than quietly becoming un-reapable for ever.
    """
    return False


class Adapter(NamedTuple):
    """What a backend must say about its process, and nothing more.

    ``interrupt`` returns whether the backend CONFIRMED the turn stopped
    within the timeout. An unconfirmed interrupt is not a failure to report to
    the user; it is the signal that this process cannot be trusted for the
    next turn.

    Nothing in production calls this yet -- Codex is the only backend on the
    pool so far, its own cancel path never goes through the pool, and its
    adapter's `interrupt` is a hardcoded `lambda _server, _timeout: False`.
    It is here for opencode, Claude, Hermes, and FreeBuff to use as they
    migrate. The trap for whoever wires one up: generic code shaped like
    `if not held.interrupt(t): pool.drop(key)` would drop a server other
    tabs are still sharing, since an unconfirmed interrupt says nothing about
    whether anyone else is using the process -- only that this one turn could
    not be confirmed stopped.

    ``busy`` says whether a turn is speaking through the process at this
    moment. Only the backend knows -- Codex counts borrowers on the app-server
    itself -- and without it the reaper measures nothing but how long ago a
    turn STARTED, which is not what "idle" means.
    """

    start: Callable[[], object]
    alive: Callable[[object], bool]
    interrupt: Callable[[object, float], bool]
    stop: Callable[[object], None]
    busy: Callable[[object], bool] = _never_busy


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
        # Not set by anything yet -- Codex is the only backend on the pool so
        # far. It is here for opencode, Claude, Hermes, and FreeBuff to use as
        # they migrate.
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
        """No production caller yet -- see `Adapter.interrupt` for the trap
        waiting in whatever calls this once a backend other than Codex is on
        the pool."""
        if self._stopped:
            return False
        try:
            return bool(self._adapter.interrupt(self.handle, timeout))
        except Exception:
            # The interrupt did not land, so nothing was confirmed. Saying
            # True here would hand the next turn a process still working on
            # the last one.
            return False

    def busy(self) -> bool:
        """Whether a turn is running through this process right now.

        The idle clock alone cannot answer this. It is set when a turn takes
        the process and again when the turn hands it back, so a turn that has
        been running for longer than the idle limit -- a long agentic run, or
        one waiting on an approval dialog nobody is at the desk to answer --
        looks exactly like a process nobody wants. Stopping a shared
        app-server there does not end one turn; it ends every tab's.
        """
        if self._stopped:
            return False
        try:
            return bool(self._adapter.busy(self.handle))
        except Exception:
            # Nothing is known about who holds it. The answer that leaves a
            # process running costs one process; the other one ends live
            # turns in tabs that never asked for anything.
            return True

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
        # Told the backend name of each process let go, and why, so the
        # window can say what the next prompt in that tab is waiting for. Set
        # by the window; the pool itself must not import wx or speak.
        self.on_reap: Optional[Callable[[str, str], None]] = None

    def _slot(self, key: tuple) -> tuple[Optional[dict], str]:
        """The dict a key lives in and the name within it, or (None, name)."""
        backend, panel = key
        if panel is _SHARED:
            return self._shared, backend
        return self._per_panel.get(panel), backend

    def take(self, key: tuple) -> Optional[HeldProcess]:
        """The process for this key if it can still serve a turn, else None.

        A process found dead is discarded here rather than handed on, so the
        caller's only job is to start a new one when this returns None. That
        is said out loud: a backend that was killed while the laptop slept
        costs the next prompt a cold start, and silence is how a hang sounds.
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
        self._announce(key[0], REAP_DIED)
        return None

    def _announce(self, backend: str, reason: str) -> None:
        """Say a process was let go. Never a reason for the caller to fail."""
        announce = self.on_reap
        if announce is None:
            return
        try:
            announce(backend, reason)
        except Exception:
            # Narration failing must not strand a sweep or fail a turn that
            # is about to start a replacement perfectly well.
            pass

    def keep(self, key: tuple, held: HeldProcess) -> None:
        """Hand a process back at the end of a turn."""
        backend, panel = key
        # Idle is time with no turn, so the clock starts when the turn ENDS.
        # Measured from the start instead, a fourteen-minute turn was reaped
        # sixty seconds after it finished -- while the follow-up prompt was
        # being typed.
        held.touch()
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

        A process serving a turn is never reaped, however long that turn has
        been running. The idle clock cannot tell a long agentic run, or a turn
        waiting on a question nobody is at the desk to answer, from a process
        nobody wants -- and for a shared app-server the difference is every
        other tab's turn ending too.

        In three passes rather than one, so the backend is asked whether it is
        busy with no lock held: the answer comes from the backend's own state,
        and the pool holding its registry lock across a call into a backend is
        how a lock order gets inverted. Anything used between the passes is
        left alone, because taking a process touches it and the second pass
        checks the clock again against the same entry.
        """
        candidates = self._idle(now, idle_limit)
        unused = [(key, held) for key, held in candidates if not held.busy()]
        expired = self._forget(unused, now, idle_limit)
        for key, held in expired:
            held.stop()
            self._announce(key[0], REAP_IDLE)
        return [key for key, _held in expired]

    def _idle(self, now: float, idle_limit: float) -> list[tuple[tuple, HeldProcess]]:
        """Everything whose idle clock has run out, without judging it yet."""
        found: list[tuple[tuple, HeldProcess]] = []
        with self._lock:
            for backend, held in list(self._shared.items()):
                if held.idle_seconds(now) > idle_limit:
                    found.append((pool_key(backend), held))
            for panel, slot in list(self._per_panel.items()):
                for backend, held in list(slot.items()):
                    if held.idle_seconds(now) > idle_limit:
                        found.append((pool_key(backend, panel), held))
        return found

    def _forget(
        self, candidates: list[tuple[tuple, HeldProcess]], now: float, idle_limit: float
    ) -> list[tuple[tuple, HeldProcess]]:
        """Take these out of the registry, unless they were used meanwhile."""
        gone: list[tuple[tuple, HeldProcess]] = []
        with self._lock:
            for key, held in candidates:
                slot, name = self._slot(key)
                if slot is None or slot.get(name) is not held:
                    # Dropped, or replaced by a newer process, since the sweep
                    # looked. Either way this key is not this object's any more.
                    continue
                if held.idle_seconds(now) <= idle_limit:
                    # A turn took it while the sweep was asking, and taking
                    # touches it. It is in use, not idle.
                    continue
                del slot[name]
                gone.append((key, held))
        return gone


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
    """No production caller -- `start_reaper` runs once for the app's whole
    life, from `MainFrame.__init__`. This exists so tests can tear the
    sweeping thread down between runs instead of leaking one per test."""
    _reaper_stop.set()


atexit.register(stop_all_held_processes)
