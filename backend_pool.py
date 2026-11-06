"""Backend processes held across turns rather than started for each one.

Four of the five backends used to start a fresh CLI for every prompt and kill
it at turn end. On Codex that meant paying the app-server's start-up on every
message and, where MCP servers are configured, discarding and restarting all
of their child processes with it.

This module owns process lifetime and nothing else. Each backend says how to
check, interrupt and stop its own process; the pool decides when.

What was measured before this existed, on Windows with the npm Codex install:
a cold spawn to an acknowledged `thread/start` took about 4.4 seconds and a
warm one about 0.55; `thread/resume` took 0.37 seconds on a rollout under
half a megabyte, 0.62 at 13 MB and 4.41 at 113 MB, so the per-turn tax grew
with the conversation. With nine MCP servers configured one app-server had 17
descendant processes half a second after `thread/start` and 29 twenty seconds
later, all killed at turn end and started again on the next prompt. One
app-server was confirmed to serve several threads at once.

The key encodes the shape. `("codex", None)` is one process for every tab,
because the app-server multiplexes conversations and one per tab would mean
one MCP tree per tab. `(backend, panel)` is one process per conversation, for
the backends whose protocol wants that. The panel object is the key because
`cwd` is not unique (`/btw` opens a second tab in the same folder) and the
session id does not exist until the first turn has started.

A process-wide backend may also run on one **Account** rather than on the
login it holds itself, which adds that Account's identity to the key: Codex on
a ChatGPT-plan Account is `(backend, None, account)`, so two Accounts mean two
processes and the tabs on one Account share one. Codex on its own login takes
no third part at all and keeps the key it has always had.

Idle processes are reaped after `_HELD_IDLE_SECONDS` with no turn, and the
reap is announced so a slow next prompt is never unexplained. The limit is a
constant, not a preference. A process-wide process is dropped when it is found
dead, on an explicit restart, before an update replaces its executable, or when
the Account it runs on is signed out or switched away from -- and that last one
waits for a turn already speaking through it (see `drop_when_idle`). A cancel
that goes unconfirmed gives up the conversation, never the server other tabs
are still using.

Today Codex is the only backend on the pool. opencode keeps its own
singleton, Hermes its `HeldConnection`, and FreeBuff its prewarm.

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


class Adapter(NamedTuple):
    """What a backend must say about its process, and nothing more.

    ``interrupt`` returns whether the backend CONFIRMED the turn stopped
    within the timeout. An unconfirmed interrupt is not a failure to report to
    the user; it is the signal that this process cannot be trusted for the
    next turn. Codex's cancel path does not go through it, so nothing in
    production calls it yet. Whoever does must not write
    `if not held.interrupt(t): pool.drop(key)` for a shared process: an
    unconfirmed interrupt says nothing about the other tabs using it.

    ``busy`` says whether a turn is speaking through the process right now.
    Only the backend knows, and without it the reaper measures only how long
    ago a turn STARTED, which is not what "idle" means.
    """

    alive: Callable[[object], bool]
    interrupt: Callable[[object, float], bool]
    stop: Callable[[object], None]
    busy: Callable[[object], bool]


class HeldProcess:
    """One live backend process and how long it has sat unused."""

    def __init__(
        self,
        handle: object,
        adapter: Adapter,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.handle = handle
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
        """Whether the backend confirmed the turn stopped. See `Adapter`."""
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


def pool_key(backend: str, panel: object = None, account: object = None) -> tuple:
    """The registry key for this backend, in the shape its protocol wants.

    No panel means one process for every tab, which is how Codex is held. A
    panel, the SessionPanel object itself, means one process per conversation,
    the shape a backend like Claude or FreeBuff would take if it moved onto
    the pool. The panel is the only stable per-conversation identity: `cwd` is
    not unique, because `/btw` opens a second tab in the same directory, and
    `session_id` does not exist yet when the first turn starts.

    ``account`` is the identity of the Account a process-wide backend runs on,
    and joins the key only when one is chosen. Two Accounts therefore never
    share a process, while a backend on its own login keeps exactly the two-part
    key it has today -- no third part to tell apart, because there is nothing
    to tell apart.
    """
    if account is None:
        return (backend, panel)
    return (backend, panel, account)


def _filed_as(key: tuple) -> tuple:
    """The name a pool key is filed under inside its slot, without its panel.

    The panel is left out because the per-conversation registry is a weak table
    keyed by the panel itself: a name carrying the panel would be a strong
    reference from that table's value back to its own key, and the tab could
    then never be collected -- which is the one thing the weak table is there to
    allow. Everything else in the key is kept, so a backend holding one process
    per Account is filed once per Account rather than once per backend.
    """
    return (key[0], *key[2:])


def _key_of(name: tuple, panel: object) -> tuple:
    """The pool key a filed name stands for, in the slot that panel selects."""
    return (name[0], panel, *name[1:])


class BackendPool:
    """Which backend process belongs to which conversation, and for how long."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Process-wide backends: one entry per filed name, for the whole app.
        # A filed name carries everything in the key except the panel (see
        # `_filed_as`), so a backend holding one process per Account is kept
        # once per Account rather than once per backend.
        self._shared: dict[tuple, HeldProcess] = {}
        # Per-conversation backends. Weak on the panel so a tab destroyed
        # without a teardown cannot pin its process for the life of the app.
        # This is a backstop, not the mechanism: collection is not prompt, so
        # the enumerated drop sites remain the contract.
        self._per_panel: "weakref.WeakKeyDictionary[object, dict[tuple, HeldProcess]]" = (
            weakref.WeakKeyDictionary()
        )
        self._stopping: set[threading.Thread] = set()
        # Processes taken out of the registry whose stopping worker has not
        # been and gone yet. `drop_all` and the reaper stop anything left here
        # from their own thread, so a drop that could not get a worker is still
        # stopped -- just never on the caller's thread. See `drop_async`.
        self._unstopped: list[HeldProcess] = []
        # Keys whose process was let go of while a turn was still speaking
        # through it, and whose drop is owed to the turn that finishes last.
        # See `drop_when_idle`.
        self._waited_for: set[tuple] = set()
        self._closing = False
        # Told the backend name of each process let go, and why, so the
        # window can say what the next prompt in that tab is waiting for. Set
        # by the window; the pool itself must not import wx or speak.
        self.on_reap: Optional[Callable[[str, str], None]] = None

    def _slot(self, key: tuple) -> Optional[dict]:
        """The dict this key lives in, or None if nothing is held for it."""
        panel = key[1]
        if panel is _SHARED:
            return self._shared
        return self._per_panel.get(panel)

    def take(self, key: tuple) -> Optional[HeldProcess]:
        """The process for this key if it can still serve a turn, else None.

        A process found dead is discarded here rather than handed on, so the
        caller's only job is to start a new one when this returns None. That
        is said out loud: a backend that was killed while the laptop slept
        costs the next prompt a cold start, and silence is how a hang sounds.
        """
        with self._lock:
            slot = self._slot(key)
            if slot is None:
                return None
            name = _filed_as(key)
            held = slot.get(name)
            if held is None:
                return None
            if not held.alive():
                del slot[name]
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
        panel = key[1]
        # Idle is time with no turn, so the clock starts when the turn ENDS.
        # Measured from the start instead, a fourteen-minute turn was reaped
        # sixty seconds after it finished -- while the follow-up prompt was
        # being typed.
        held.touch()
        name = _filed_as(key)
        stale: Optional[HeldProcess] = None
        with self._lock:
            if panel is _SHARED:
                slot = self._shared
            else:
                slot = self._per_panel.setdefault(panel, {})
            stale = slot.get(name)
            slot[name] = held
            if stale is held:
                stale = None
        if stale is not None:
            stale.stop()

    def drop(self, key: tuple) -> None:
        """Stop this key's process and forget it. Safe on a key never held."""
        with self._lock:
            slot = self._slot(key)
            held = slot.pop(_filed_as(key), None) if slot is not None else None
        if held is not None:
            held.stop()

    def _stop_on_a_worker(self, held: HeldProcess) -> None:
        """Stop one process on a thread of its own, and say it is out of hand."""
        try:
            held.stop()
        finally:
            with self._lock:
                self._stopping.discard(threading.current_thread())
                if held in self._unstopped:
                    self._unstopped.remove(held)

    def drop_async(self, key: tuple) -> None:
        """Forget this key now and stop it on a worker. Never on the caller.

        A drop is reachable from a window callback -- signing out of an Account
        is one, and the close handler's teardown reaches the rest -- and ADR
        0001 does not allow stopping a process there, shutting down or not. So
        the stop is always handed to a worker thread, and `drop_all` is what
        waits for them.

        The process is remembered until that worker has stopped it, so a sweep
        -- the shutdown one, or the reaper past it -- can stop anything the
        workers never got to. The process is still stopped; it is just never
        stopped on the thread that asked.
        """
        with self._lock:
            slot = self._slot(key)
            held = slot.pop(_filed_as(key), None) if slot is not None else None
            if held is None:
                return
            self._unstopped.append(held)
            worker = threading.Thread(
                target=self._stop_on_a_worker,
                args=(held,),
                name="backend-pool-stop",
                daemon=True,
            )
            self._stopping.add(worker)
            try:
                worker.start()
            except Exception:
                # No worker could be started. It stays on `_unstopped`, which
                # the shutdown sweep and the reaper both stop from their own
                # thread, so the process is still stopped by somebody.
                self._stopping.discard(worker)

    def busy(self, key: tuple) -> bool:
        """Whether a turn is speaking through this key's process right now.

        Asked with no lock held, the way `reap` and `drop_pending` ask: the
        answer comes from the backend's own state, and holding the registry
        lock across a call into a backend is how a lock order gets inverted.
        """
        with self._lock:
            slot = self._slot(key)
            held = slot.get(_filed_as(key)) if slot is not None else None
        return held is not None and held.busy()

    def drop_when_idle(self, key: tuple) -> bool:
        """Drop this key's process now, or once the turn running on it ends.

        Signing out of an Account, deleting it, or choosing another one takes
        that Account's process away. A turn already speaking through it is never
        interrupted -- it finishes on the credential it started with, which is
        what switching promises -- so the drop is left to `drop_pending`, and
        the caller is told it is waiting so it can say so.

        Returns whether the drop had to wait. False for a key nobody held:
        there is then nothing to wait for and nothing to report.
        """
        with self._lock:
            slot = self._slot(key)
            held = slot.get(_filed_as(key)) if slot is not None else None
        if held is None:
            return False
        if held.busy():
            with self._lock:
                self._waited_for.add(key)
            return True
        self.drop_async(key)
        return False

    def wait_to_drop(self, key: tuple) -> None:
        """Owe this key a drop, for a caller that could not ask in time.

        For the one thing `drop_when_idle` cannot do: answer without waiting.
        A caller on the window's own thread must not queue behind a turn that is
        launching a process, so it owes the drop instead of asking. Nothing
        held when it comes due is discarded there, so owing one costs nothing.
        """
        with self._lock:
            self._waited_for.add(key)

    def drop_pending(self) -> None:
        """Carry out the drops that were waiting on a turn, for those now idle.

        Called when a turn hands its process back. A process another tab is
        still speaking through is left where it is: the wait is for all of the
        turns holding it, not for whichever of them finishes first.

        Asked outside the lock, as `reap` does, because the answer comes from
        the backend's own state and holding the registry lock across a call
        into a backend is how a lock order gets inverted.
        """
        with self._lock:
            waiting = list(self._waited_for)
        for key in waiting:
            with self._lock:
                slot = self._slot(key)
                held = slot.get(_filed_as(key)) if slot is not None else None
            if held is not None and held.busy():
                continue
            with self._lock:
                self._waited_for.discard(key)
            # Nothing held any more means the drop has happened already;
            # `drop_async` does nothing for a key nobody holds.
            self.drop_async(key)

    def drop_backend(self, backend: str) -> None:
        """Drop every process this backend holds, whichever Account holds it.

        For replacing the executable: Windows will not overwrite a file a
        process is running from, and which Account started that process does
        not change it. This is the one drop that reaches across Accounts, and
        it is for a reason outside every conversation -- a tab letting go of
        its own conversation must never use it.
        """
        with self._lock:
            gone = [self._shared.pop(name) for name in [n for n in self._shared if n[0] == backend]]
            for slot in list(self._per_panel.values()):
                gone.extend(slot.pop(name) for name in [n for n in slot if n[0] == backend])
        for held in gone:
            held.stop()

    def drop_all(self) -> None:
        """Stop everything, at quit. An update drops only the key it replaces.

        This runs on the shutdown worker or at exit, never on a window
        callback, so it is the one place that may stop a process itself rather
        than hand it to a worker of its own.
        """
        with self._lock:
            self._closing = True
            self._waited_for.clear()
            everything = list(self._shared.values())
            self._shared.clear()
            for slot in list(self._per_panel.values()):
                everything.extend(slot.values())
                slot.clear()
        for held in everything:
            # One backend that throws on the way down must not strand the
            # rest; HeldProcess.stop already swallows, this is belt and braces.
            held.stop()
        while True:
            with self._lock:
                stopping = tuple(self._stopping)
            if not stopping:
                break
            for worker in stopping:
                worker.join()
        self._stop_strays()

    def _stop_strays(self) -> None:
        """Stop anything a drop handed on that no worker has stopped yet.

        For a stop that could not get a thread of its own, and for one a worker
        was still starting when the sweep looked for work. Only ever called from
        this pool's own threads -- the shutdown worker, the reaper, or exit --
        because it stops the process where it stands.
        """
        with self._lock:
            left = list(self._unstopped)
            self._unstopped.clear()
        for held in left:
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
        # A drop whose stopping worker could not be started is still a process
        # to stop. This sweep runs on a thread of its own, so it may.
        self._stop_strays()
        return [key for key, _held in expired]

    def _idle(self, now: float, idle_limit: float) -> list[tuple[tuple, HeldProcess]]:
        """Everything whose idle clock has run out, without judging it yet.

        Each entry is reported under the key it was filed by, rebuilt with the
        panel that names its slot, so an Account in the key comes back whole
        and the caller drops exactly what it was given.
        """
        found: list[tuple[tuple, HeldProcess]] = []
        with self._lock:
            for name, held in list(self._shared.items()):
                if held.idle_seconds(now) > idle_limit:
                    found.append((_key_of(name, _SHARED), held))
            for panel, slot in list(self._per_panel.items()):
                for name, held in list(slot.items()):
                    if held.idle_seconds(now) > idle_limit:
                        found.append((_key_of(name, panel), held))
        return found

    def _forget(
        self, candidates: list[tuple[tuple, HeldProcess]], now: float, idle_limit: float
    ) -> list[tuple[tuple, HeldProcess]]:
        """Take these out of the registry, unless they were used meanwhile."""
        gone: list[tuple[tuple, HeldProcess]] = []
        with self._lock:
            for key, held in candidates:
                slot = self._slot(key)
                name = _filed_as(key)
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
    """Stop every held process, at quit.

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


atexit.register(stop_all_held_processes)
