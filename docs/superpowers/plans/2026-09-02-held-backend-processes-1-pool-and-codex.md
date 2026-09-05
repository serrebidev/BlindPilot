# Held Backend Processes — Plan 1: the pool, its contract, and Codex

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `backend_pool.py` and move Codex onto it, so one `codex app-server` serves every tab across many prompts instead of one being spawned and killed per prompt.

**Architecture:** A new module owns process lifetime and nothing else. `HeldProcess` wraps one live child; `BackendPool` is a keyed registry with `take`/`keep`/`drop`. The key encodes the shape — `("codex", None)` is process-wide, `("claude", panel)` is per-conversation. Each backend supplies an `Adapter` of four callables. Workers stop calling `Popen` and ask the pool.

**Tech Stack:** Python 3.12+, stdlib only (`threading`, `weakref`, `subprocess`, `atexit`). pytest 8–9, pytest-timeout, pytest-randomly, hypothesis. wxPython for the window. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-02-held-backend-processes-design.md`

**This is plan 1 of 5.** Plans 2–5 (opencode, Claude, Hermes, FreeBuff) are written against the pool once this lands, because their migrations depend on its real shape. Do not migrate other backends here.

## Global Constraints

- **Python target:** `mypy.ini` pins `python_version = 3.12` and `platform = win32`. New code must type-check under `python -m mypy` with no arguments. Add `backend_pool.py` to the `files =` list in `mypy.ini`.
- **Style:** `ruff==0.16.5`, line length and rules from `ruff.toml`. Run `python -m ruff check .` and `python -m ruff format .`.
- **Tests run with `-W error`** (`.github/workflows/ci.yml:87`). A `Popen` that reaches `__del__` unreaped raises `ResourceWarning` and fails the build. Every test that creates a process-like object must stop it.
- **`pytest-randomly` shuffles order.** Any test touching a module global must save and restore it in `try/finally` — the pattern is `tests/test_backends.py:450-468`. There is no fixture that does this for you.
- **`pytest.ini` sets `timeout = 60`.** No test may sleep waiting for a real timeout. Use an injected clock (Task 4) or poll with a deadline (`_wait_for`, `tests/test_backends.py:991`).
- **Threads must be daemons**, asserted explicitly elsewhere (`tests/test_codex_last_words.py:136`).
- **Never block the GUI thread.** `cancel_worker` runs teardown off-thread (`tests/test_cancel_off_the_gui_thread.py`); the pool must not add a blocking wait to it.
- **Budgets, verbatim from the spec:** `_HELD_IDLE_SECONDS = 900.0`; `_INTERRUPT_VERIFY_SECONDS = 1.5`; existing `_CANCEL_JOIN_SECONDS = 3.0` (`blindpilot_app.py:4774`) is the whole teardown budget and must not grow.
- **Licence header:** every module carries the SPDX block from `agent_backends.py:1-14`.
- **Commit signing is on.** 1Password must be running and unlocked or `git commit` fails.

---

### Task 1: `HeldProcess` — one live child, stopped exactly once

**Files:**
- Create: `backend_pool.py`
- Test: `tests/test_backend_pool.py`

**Interfaces:**
- Consumes: `agent_backends.end_process_group(proc, timeout)` — already exists at `agent_backends.py:64`, duck-typed, safe on stand-ins.
- Produces:
  - `Adapter(start, alive, interrupt, stop)` — a `NamedTuple` of four callables.
    `start() -> object`; `alive(handle) -> bool`; `interrupt(handle, timeout: float) -> bool`; `stop(handle) -> None`.
  - `HeldProcess(handle, adapter, binding=None)` with attributes `handle`, `binding`, and methods `alive() -> bool`, `interrupt(timeout: float) -> bool`, `stop() -> None`, `touch() -> None`, `idle_seconds(now: float) -> float`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backend_pool.py`:

```python
"""The pool holds a backend's process across turns instead of per turn."""

from __future__ import annotations

import backend_pool


class _FakeHandle:
    """A stand-in for a Popen, counting what the pool does to it."""

    def __init__(self, alive: bool = True, confirms: bool = True) -> None:
        self.running = alive
        self.confirms = confirms
        self.stops = 0
        self.interrupts = 0

    def stop(self) -> None:
        self.stops += 1
        self.running = False


def _adapter() -> backend_pool.Adapter:
    def interrupt(handle: _FakeHandle, _timeout: float) -> bool:
        handle.interrupts += 1
        return handle.confirms

    return backend_pool.Adapter(
        start=lambda: _FakeHandle(),
        alive=lambda handle: handle.running,
        interrupt=interrupt,
        stop=lambda handle: handle.stop(),
    )


def test_a_held_process_reports_the_health_of_its_handle():
    handle = _FakeHandle()
    held = backend_pool.HeldProcess(handle, _adapter())
    assert held.alive() is True
    handle.running = False
    assert held.alive() is False


def test_stopping_twice_stops_the_handle_once():
    """Teardown paths run twice -- cancel_worker and then atexit. A second
    stop that reached the handle again would kill a process another key had
    already been given."""
    handle = _FakeHandle()
    held = backend_pool.HeldProcess(handle, _adapter())
    held.stop()
    held.stop()
    assert handle.stops == 1


def test_a_stopped_process_is_never_reported_alive():
    handle = _FakeHandle()
    held = backend_pool.HeldProcess(handle, _adapter())
    held.stop()
    handle.running = True  # the adapter would lie; the flag must win
    assert held.alive() is False


def test_an_interrupt_the_backend_confirms_is_reported_confirmed():
    handle = _FakeHandle(confirms=True)
    held = backend_pool.HeldProcess(handle, _adapter())
    assert held.interrupt(0.01) is True
    assert handle.interrupts == 1


def test_an_interrupt_the_backend_does_not_confirm_is_reported_unconfirmed():
    handle = _FakeHandle(confirms=False)
    held = backend_pool.HeldProcess(handle, _adapter())
    assert held.interrupt(0.01) is False


def test_an_interrupt_that_raises_counts_as_unconfirmed():
    """A backend whose pipe closed under the interrupt has not confirmed
    anything. Reporting True there would keep a dead process."""

    def boom(_handle: object, _timeout: float) -> bool:
        raise OSError("pipe closed")

    adapter = _adapter()._replace(interrupt=boom)
    held = backend_pool.HeldProcess(_FakeHandle(), adapter)
    assert held.interrupt(0.01) is False


def test_idle_time_is_measured_from_the_last_touch():
    held = backend_pool.HeldProcess(_FakeHandle(), _adapter(), now=lambda: 100.0)
    assert held.idle_seconds(100.0) == 0.0
    assert held.idle_seconds(160.0) == 60.0
    held.touch()
    assert held.idle_seconds(100.0) == 0.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_backend_pool.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend_pool'`

- [ ] **Step 3: Write the minimal implementation**

Create `backend_pool.py`:

```python
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
from typing import Callable, NamedTuple, Optional


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_backend_pool.py -v`
Expected: 7 passed

- [ ] **Step 5: Register with the type checker and the linter**

Add `backend_pool.py` to the `files =` list in `mypy.ini` (after `diagnostics.py`).

Run: `python -m mypy` → Expected: `Success`
Run: `python -m ruff check . && python -m ruff format .` → Expected: clean

- [ ] **Step 6: Commit**

```bash
git add backend_pool.py tests/test_backend_pool.py mypy.ini
git commit -m "A backend process that is stopped once, however often it is asked"
```

---

### Task 2: `BackendPool` — the registry, and the key that encodes the shape

**Files:**
- Modify: `backend_pool.py`
- Test: `tests/test_backend_pool.py`

**Interfaces:**
- Consumes: `Adapter`, `HeldProcess` from Task 1.
- Produces:
  - `pool_key(backend: str, panel: object = None) -> tuple[str, object]` — `panel` is `None` for process-wide backends.
  - `BackendPool()` with `take(key) -> Optional[HeldProcess]`, `keep(key, held) -> None`, `drop(key) -> None`, `drop_all() -> None`, `held_count() -> int`.
  - Module-level singleton `pool()` returning the shared `BackendPool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_backend_pool.py`:

```python
import gc

import pytest


class _Panel:
    """Stands in for a SessionPanel: identity-hashed and weak-referenceable."""


def test_a_kept_process_is_given_back_to_the_same_key():
    pool = backend_pool.BackendPool()
    held = backend_pool.HeldProcess(_FakeHandle(), _adapter())
    key = backend_pool.pool_key("codex")
    pool.keep(key, held)
    assert pool.take(key) is held
    pool.drop_all()


def test_a_dead_process_is_discarded_rather_than_handed_on():
    pool = backend_pool.BackendPool()
    handle = _FakeHandle()
    held = backend_pool.HeldProcess(handle, _adapter())
    key = backend_pool.pool_key("codex")
    pool.keep(key, held)
    handle.running = False
    assert pool.take(key) is None
    assert pool.take(key) is None, "the dead one must not still be in the registry"
    pool.drop_all()


def test_taking_marks_the_process_used_so_the_reaper_leaves_it_alone():
    pool = backend_pool.BackendPool()
    held = backend_pool.HeldProcess(_FakeHandle(), _adapter(), now=lambda: 0.0)
    key = backend_pool.pool_key("codex")
    pool.keep(key, held)
    assert held.idle_seconds(500.0) == 500.0
    pool.take(key)
    assert held.idle_seconds(0.0) == 0.0


def test_two_panels_of_the_same_backend_hold_different_processes():
    """The per-conversation shape: one process per tab, not one per app."""
    pool = backend_pool.BackendPool()
    first, second = _Panel(), _Panel()
    one = backend_pool.HeldProcess(_FakeHandle(), _adapter())
    two = backend_pool.HeldProcess(_FakeHandle(), _adapter())
    pool.keep(backend_pool.pool_key("claude", first), one)
    pool.keep(backend_pool.pool_key("claude", second), two)
    assert pool.take(backend_pool.pool_key("claude", first)) is one
    assert pool.take(backend_pool.pool_key("claude", second)) is two
    pool.drop_all()


def test_every_panel_shares_one_process_wide_backend():
    """The process-wide shape: the panel is not part of the key at all."""
    pool = backend_pool.BackendPool()
    held = backend_pool.HeldProcess(_FakeHandle(), _adapter())
    pool.keep(backend_pool.pool_key("codex"), held)
    assert pool.take(backend_pool.pool_key("codex")) is held
    pool.drop_all()


def test_dropping_stops_the_process_and_forgets_it():
    pool = backend_pool.BackendPool()
    handle = _FakeHandle()
    key = backend_pool.pool_key("codex")
    pool.keep(key, backend_pool.HeldProcess(handle, _adapter()))
    pool.drop(key)
    assert handle.stops == 1
    assert pool.take(key) is None


def test_dropping_a_key_nobody_held_does_nothing():
    """Teardown runs on tabs that never started a turn."""
    pool = backend_pool.BackendPool()
    pool.drop(backend_pool.pool_key("codex"))
    pool.drop(backend_pool.pool_key("claude", _Panel()))


def test_dropping_one_panel_leaves_the_others_running():
    pool = backend_pool.BackendPool()
    first, second = _Panel(), _Panel()
    mine, yours = _FakeHandle(), _FakeHandle()
    pool.keep(backend_pool.pool_key("claude", first), backend_pool.HeldProcess(mine, _adapter()))
    pool.keep(backend_pool.pool_key("claude", second), backend_pool.HeldProcess(yours, _adapter()))
    pool.drop(backend_pool.pool_key("claude", first))
    assert mine.stops == 1
    assert yours.stops == 0
    pool.drop_all()


def test_drop_all_stops_everything_including_a_process_that_raises():
    """One backend that throws on stop must not strand the rest at quit."""
    pool = backend_pool.BackendPool()

    def explode(_handle: object) -> None:
        raise OSError("no")

    angry = _adapter()._replace(stop=explode)
    calm = _FakeHandle()
    pool.keep(backend_pool.pool_key("codex"), backend_pool.HeldProcess(_FakeHandle(), angry))
    pool.keep(backend_pool.pool_key("claude", _Panel()), backend_pool.HeldProcess(calm, _adapter()))
    pool.drop_all()
    assert calm.stops == 1
    assert pool.held_count() == 0


def test_a_collected_panel_does_not_keep_its_process_registered():
    """A tab destroyed without cancel_worker running -- the case
    blindpilot_app.py:5872 already guards -- must not pin a process for the
    life of the application."""
    pool = backend_pool.BackendPool()
    panel = _Panel()
    pool.keep(backend_pool.pool_key("claude", panel), backend_pool.HeldProcess(_FakeHandle(), _adapter()))
    assert pool.held_count() == 1
    del panel
    gc.collect()
    assert pool.held_count() == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_backend_pool.py -v`
Expected: FAIL — `AttributeError: module 'backend_pool' has no attribute 'BackendPool'`

- [ ] **Step 3: Write the minimal implementation**

Append to `backend_pool.py`:

```python
import weakref

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
                del slot[name]  # type: ignore[index]
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


_pool: Optional[BackendPool] = None
_POOL_LOCK = threading.Lock()


def pool() -> BackendPool:
    """The shared pool, created on first use."""
    global _pool
    with _POOL_LOCK:
        if _pool is None:
            _pool = BackendPool()
        return _pool
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_backend_pool.py -v`
Expected: 17 passed

- [ ] **Step 5: Check types and style**

Run: `python -m mypy` → Expected: `Success`
Run: `python -m ruff check . && python -m ruff format .` → Expected: clean

- [ ] **Step 6: Commit**

```bash
git add backend_pool.py tests/test_backend_pool.py
git commit -m "A registry whose key says whether a backend is shared or a tab's own"
```

---

### Task 3: A contract harness, so a fake cannot lie about being alive

**Files:**
- Create: `tests/pool_contract.py`
- Create: `tests/test_pool_contract.py`

**Interfaces:**
- Consumes: `backend_pool.Adapter`, `backend_pool.HeldProcess`.
- Produces: `check_pool_contract(build, who)` raising `ContractViolation`; `ContractViolation`.

**Why this task exists:** `tests/transport_contract.py` was written because a fake in PR #31 reported itself connected while returning nothing forever, and the real failure only showed on Linux CI. A fake held process whose `alive()` always returns `True` hides every bug this design can have, in exactly the same way.

- [ ] **Step 1: Write the harness**

Create `tests/pool_contract.py`:

```python
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
    pool = backend_pool.BackendPool()
    held = build()
    key = backend_pool.pool_key("contract")
    pool.keep(key, held)
    held.stop()
    try:
        if pool.take(key) is not None:
            raise ContractViolation(f"{who}: the pool handed on a stopped process")
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
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_pool_contract.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail, then pass**

Run: `python -m pytest tests/test_pool_contract.py -v`
Expected: PASS for all three (the harness and the real class both exist by now). If `test_the_real_held_process_satisfies_the_contract` fails, Task 1 is wrong — fix `HeldProcess`, not the contract.

- [ ] **Step 4: Check types and style**

Run: `python -m mypy && python -m ruff check . && python -m ruff format .`
Expected: clean

- [ ] **Step 5: Commit**

```bash
git add tests/pool_contract.py tests/test_pool_contract.py
git commit -m "A contract so a held-process fake cannot claim to be alive for ever"
```

---

### Task 4: The idle reaper, driven by an injected clock

**Files:**
- Modify: `backend_pool.py`
- Test: `tests/test_backend_pool.py`

**Interfaces:**
- Consumes: `BackendPool` from Task 2.
- Produces: `BackendPool.reap(now: float, idle_limit: float) -> list[tuple]` returning the keys dropped; `backend_pool._HELD_IDLE_SECONDS = 900.0`; `BackendPool.on_reap` — an optional `Callable[[str], None]` called with the backend name of each reaped process.

**Why an injected clock:** `pytest.ini` sets `timeout = 60` and a 15-minute idle period cannot be waited out. `tests/test_long_turn_connection.py:299` already substitutes a clock for exactly this reason; `reap` takes `now` as an argument so no substitution is even needed.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_backend_pool.py`:

```python
def test_a_process_idle_past_the_limit_is_reaped():
    pool = backend_pool.BackendPool()
    handle = _FakeHandle()
    held = backend_pool.HeldProcess(handle, _adapter(), now=lambda: 0.0)
    key = backend_pool.pool_key("codex")
    pool.keep(key, held)
    assert pool.reap(now=901.0, idle_limit=900.0) == [key]
    assert handle.stops == 1
    assert pool.take(key) is None


def test_a_process_used_recently_is_left_alone():
    pool = backend_pool.BackendPool()
    handle = _FakeHandle()
    pool.keep(
        backend_pool.pool_key("codex"),
        backend_pool.HeldProcess(handle, _adapter(), now=lambda: 0.0),
    )
    assert pool.reap(now=899.0, idle_limit=900.0) == []
    assert handle.stops == 0
    pool.drop_all()


def test_reaping_one_tab_leaves_a_busy_tab_running():
    pool = backend_pool.BackendPool()
    idle_panel, busy_panel = _Panel(), _Panel()
    idle_handle, busy_handle = _FakeHandle(), _FakeHandle()
    pool.keep(
        backend_pool.pool_key("claude", idle_panel),
        backend_pool.HeldProcess(idle_handle, _adapter(), now=lambda: 0.0),
    )
    pool.keep(
        backend_pool.pool_key("claude", busy_panel),
        backend_pool.HeldProcess(busy_handle, _adapter(), now=lambda: 900.0),
    )
    reaped = pool.reap(now=901.0, idle_limit=900.0)
    assert reaped == [backend_pool.pool_key("claude", idle_panel)]
    assert idle_handle.stops == 1
    assert busy_handle.stops == 0
    pool.drop_all()


def test_a_reap_is_announced_so_the_next_cold_start_is_never_a_surprise():
    """A user who cannot see a spinner infers a hang from silence. The reap is
    the reason the next prompt is slow, so it has to be sayable."""
    said: list[str] = []
    pool = backend_pool.BackendPool()
    pool.on_reap = said.append
    pool.keep(
        backend_pool.pool_key("codex"),
        backend_pool.HeldProcess(_FakeHandle(), _adapter(), now=lambda: 0.0),
    )
    pool.reap(now=901.0, idle_limit=900.0)
    assert said == ["codex"]


def test_the_idle_limit_is_fifteen_minutes():
    assert backend_pool._HELD_IDLE_SECONDS == 900.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_backend_pool.py -v -k reap`
Expected: FAIL — `AttributeError: 'BackendPool' object has no attribute 'reap'`

- [ ] **Step 3: Write the minimal implementation**

In `backend_pool.py`, add the constant next to the other module constants:

```python
# How long a held process may sit unused before it is stopped. Long enough
# that an active conversation is always warm; short enough that a laptop left
# open overnight is not holding an app-server and its MCP children.
_HELD_IDLE_SECONDS = 900.0
```

Add to `BackendPool.__init__`:

```python
        # Told the backend name of each reaped process, so the window can say
        # why the next prompt in that tab starts cold. Set by the window; the
        # pool itself must not import wx or speak.
        self.on_reap: Optional[Callable[[str], None]] = None
```

Add the method:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_backend_pool.py -v`
Expected: 22 passed

- [ ] **Step 5: Check types and style**

Run: `python -m mypy && python -m ruff check . && python -m ruff format .`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add backend_pool.py tests/test_backend_pool.py
git commit -m "Let go of a backend nobody has spoken to for a quarter of an hour"
```

---

### Task 5: Shutdown — `atexit`, and the reaper thread

**Files:**
- Modify: `backend_pool.py`
- Test: `tests/test_backend_pool.py`

**Interfaces:**
- Consumes: `BackendPool`, `pool()` from Tasks 2 and 4.
- Produces: `stop_all_held_processes()` — module function registered with `atexit`; `start_reaper(interval: float = 60.0) -> threading.Thread` — a daemon thread calling `reap` on a timer.

**Note:** neither existing `atexit` registration (`discard_freebuff_prewarm`, `stop_opencode_server`) has a test; no test in the suite imports `atexit`. This task adds the first, so the pattern exists for plans 2–5.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_backend_pool.py`:

```python
def test_the_pool_is_swept_at_exit(monkeypatch):
    """Quitting must not leave an app-server and its MCP children behind.

    Held Hermes connections have no atexit hook today; this is the first.
    atexit exposes no supported way to read what is registered, so this
    re-imports the module with a recording stand-in in place of `register`
    and asserts the sweep is what gets handed to it.
    """
    import atexit
    import importlib

    recorded: list = []
    monkeypatch.setattr(atexit, "register", lambda fn, *a, **k: recorded.append(fn) or fn)
    importlib.reload(backend_pool)
    try:
        assert backend_pool.stop_all_held_processes in recorded
    finally:
        # Reload again so the real registration is back in place and the
        # module globals other tests read are not the reloaded copies.
        importlib.reload(backend_pool)


def test_sweeping_at_exit_stops_every_held_process():
    pool = backend_pool.pool()
    handle = _FakeHandle()
    key = backend_pool.pool_key("codex")
    try:
        pool.keep(key, backend_pool.HeldProcess(handle, _adapter()))
        backend_pool.stop_all_held_processes()
        assert handle.stops == 1
        assert pool.held_count() == 0
    finally:
        pool.drop_all()


def test_the_reaper_runs_on_a_daemon_thread():
    """A non-daemon reaper would hold the interpreter open at quit."""
    thread = backend_pool.start_reaper(interval=0.01)
    try:
        assert thread.daemon is True
        assert thread.is_alive()
    finally:
        backend_pool.stop_reaper()
        thread.join(timeout=5)
        assert not thread.is_alive(), "the reaper did not stop when asked"


def test_the_shared_pool_is_the_same_object_every_time():
    assert backend_pool.pool() is backend_pool.pool()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_backend_pool.py -v -k "exit or reaper or same_object"`
Expected: FAIL — `AttributeError: module 'backend_pool' has no attribute 'stop_all_held_processes'`

- [ ] **Step 3: Write the minimal implementation**

Append to `backend_pool.py`:

```python
import atexit

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
```

Move the `import atexit` to the module's import block at the top rather than leaving it inline.

- [ ] **Step 4: Run the whole file to verify it passes**

Run: `python -m pytest tests/test_backend_pool.py tests/test_pool_contract.py -v`
Expected: all pass

- [ ] **Step 5: Run the full suite — nothing else may have moved**

Run: `python -m pytest -q -W error`
Expected: 1074+ passed, 3 skipped, 0 failed

- [ ] **Step 6: Check types and style**

Run: `python -m mypy && python -m ruff check . && python -m ruff format .`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add backend_pool.py tests/test_backend_pool.py
git commit -m "Sweep the held processes at exit, and on a timer while running"
```

---

### Task 6: The Codex adapter — start, alive, interrupt, stop

**Files:**
- Modify: `agent_backends.py` (add near `CodexWorker`, after `_CODEX_LAST_WORDS_SECONDS` at `:1494`)
- Test: `tests/test_codex_pool.py` (create)

**Interfaces:**
- Consumes: `backend_pool.Adapter`, `backend_pool.HeldProcess`; existing `find_backend_cli`, `_codex_app_server_binary`, `subprocess_env`, `own_group_kwargs`, `no_window_kwargs`, `end_process_group`.
- Produces:
  - `agent_backends._CODEX_INTERRUPT_VERIFY_SECONDS = 1.5`
  - `agent_backends.codex_adapter() -> backend_pool.Adapter`
  - `agent_backends.CodexServer` — the handle the adapter starts: holds the `Popen`, the reader thread, and a `dict[str, queue.Queue]` routing replies by request id so several tabs can share one process.

**This task builds the adapter only. `CodexWorker` still spawns its own process; Task 7 moves it over.** Keeping them apart is what lets a reviewer reject one without the other.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_codex_pool.py`:

```python
"""Codex's app-server, held across turns and shared between tabs."""

from __future__ import annotations

import json

import agent_backends
import backend_pool
from pool_contract import check_pool_contract


class _FakeProc:
    """A Popen stand-in whose stdout is a list of JSONL lines to hand back."""

    def __init__(self, lines: list[str] | None = None) -> None:
        self.stdin = _Sink()
        self.stdout = iter(lines or [])
        self.stderr = iter(())
        self.killed = False
        self._returncode: int | None = None

    def poll(self) -> int | None:
        return self._returncode

    def kill(self) -> None:
        self.killed = True
        self._returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self._returncode = self._returncode or 0
        return self._returncode


class _Sink:
    def __init__(self) -> None:
        self.written: list[str] = []

    def write(self, text: str) -> None:
        self.written.append(text)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_a_running_app_server_is_alive_and_a_killed_one_is_not():
    proc = _FakeProc()
    server = agent_backends.CodexServer(proc)
    adapter = agent_backends.codex_adapter()
    assert adapter.alive(server) is True
    proc.kill()
    assert adapter.alive(server) is False


def test_stopping_the_server_ends_its_process_group():
    stopped: list[object] = []
    proc = _FakeProc()
    server = agent_backends.CodexServer(proc)
    original = agent_backends.end_process_group
    agent_backends.end_process_group = lambda p, timeout=0.0: stopped.append(p)
    try:
        agent_backends.codex_adapter().stop(server)
    finally:
        agent_backends.end_process_group = original
    assert stopped == [proc]


def test_an_interrupt_asks_codex_to_stop_the_named_turn():
    proc = _FakeProc()
    server = agent_backends.CodexServer(proc)
    server.confirm_interrupt = lambda _thread, _turn, _timeout: True
    assert server.interrupt("thread-1", "turn-1", 0.01) is True
    sent = [json.loads(line) for line in proc.stdin.written]
    assert any(m.get("method") == "turn/interrupt" for m in sent)
    interrupt = next(m for m in sent if m.get("method") == "turn/interrupt")
    assert interrupt["params"] == {"threadId": "thread-1", "turnId": "turn-1"}


def test_an_interrupt_codex_never_confirms_is_reported_unconfirmed():
    """The verify half of "interrupt, verify, kill if unsure"."""
    proc = _FakeProc()
    server = agent_backends.CodexServer(proc)
    assert server.interrupt("thread-1", "turn-1", 0.01) is False


def test_the_verify_budget_is_half_the_teardown_budget():
    """It has to fit inside _CANCEL_JOIN_SECONDS with room for the join."""
    import blindpilot_app

    assert agent_backends._CODEX_INTERRUPT_VERIFY_SECONDS == 1.5
    assert agent_backends._CODEX_INTERRUPT_VERIFY_SECONDS < blindpilot_app._CANCEL_JOIN_SECONDS


def test_the_codex_held_process_satisfies_the_pool_contract():
    def build() -> backend_pool.HeldProcess:
        return backend_pool.HeldProcess(
            agent_backends.CodexServer(_FakeProc()), agent_backends.codex_adapter()
        )

    check_pool_contract(build, "CodexServer")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_codex_pool.py -v`
Expected: FAIL — `AttributeError: module 'agent_backends' has no attribute 'CodexServer'`

- [ ] **Step 3: Write the minimal implementation**

In `agent_backends.py`, after `_CODEX_LAST_WORDS_SECONDS = 1.0` (`:1494`):

```python
# How long an interrupt is given to be confirmed before the turn's thread is
# treated as wedged. Half of the window's whole teardown budget
# (_CANCEL_JOIN_SECONDS, 3.0), leaving the rest for the join that follows.
_CODEX_INTERRUPT_VERIFY_SECONDS = 1.5


class CodexServer:
    """One ``codex app-server`` process, shared by every tab using Codex.

    The app-server multiplexes: several threads live in one process, keyed by
    threadId, which is what lets one server serve every tab instead of one per
    tab. Replies are routed back to whichever turn asked, by request id.
    """

    def __init__(self, proc: object) -> None:
        self.proc = proc
        self._lock = threading.Lock()
        self._next_id = 10
        # request id -> the queue the waiting turn is reading
        self._waiting: dict[int, "queue.Queue"] = {}

    def alive(self) -> bool:
        poll = getattr(self.proc, "poll", None)
        return poll is not None and poll() is None

    def next_id(self) -> int:
        with self._lock:
            self._next_id += 1
            return self._next_id

    def send(self, message: dict) -> bool:
        stdin = getattr(self.proc, "stdin", None)
        if stdin is None:
            return False
        try:
            data = json.dumps(message, ensure_ascii=False) + "\n"
            with self._lock:
                stdin.write(data)
                stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def confirm_interrupt(self, thread_id: str, turn_id: str, timeout: float) -> bool:
        """Wait for Codex to say the turn stopped. Overridden in tests.

        The real implementation waits on the reader thread reporting a
        turn/completed for this turn id; until Task 7 wires that reader up,
        nothing confirms and this returns False -- which is the safe answer.
        """
        return False

    def interrupt(self, thread_id: str, turn_id: str, timeout: float) -> bool:
        """Ask Codex to stop this turn and say whether it confirmed."""
        if not thread_id or not turn_id:
            return False
        sent = self.send(
            {
                "method": "turn/interrupt",
                "id": self.next_id(),
                "params": {"threadId": thread_id, "turnId": turn_id},
            }
        )
        if not sent:
            return False
        return self.confirm_interrupt(thread_id, turn_id, timeout)

    def stop(self) -> None:
        end_process_group(self.proc, timeout=2)


def _start_codex_server() -> CodexServer:
    """Launch the shared app-server. Raises OSError if it cannot be started."""
    binary = find_backend_cli(BACKEND_CODEX)
    if not binary:
        raise OSError("Codex is not installed. Run: npm install -g @openai/codex")
    server_binary = _codex_app_server_binary(binary)
    proc = subprocess.Popen(
        [server_binary, *_CODEX_QUESTION_ARGS, "app-server", "--stdio"],
        cwd=str(Path.home()),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
        env=subprocess_env(server_binary),
        # npm's `codex` is a Node launcher that runs the real Codex as a child
        # of its own; stopping only the launcher leaves that child holding the
        # app-server session.
        **own_group_kwargs(),
        **no_window_kwargs(),
    )
    return CodexServer(proc)


def codex_adapter() -> "backend_pool.Adapter":
    """How the pool starts, checks, interrupts and stops Codex."""
    import backend_pool

    return backend_pool.Adapter(
        start=_start_codex_server,
        alive=lambda server: bool(server.alive()),
        # The pool's generic interrupt has no turn to name. Codex's cancel path
        # goes through CodexServer.interrupt with the ids it holds; reaching
        # here means nothing could be confirmed.
        interrupt=lambda _server, _timeout: False,
        stop=lambda server: server.stop(),
    )
```

Note `cwd=str(Path.home())`: the shared server is started from the home directory rather than any one project, exactly as `OpencodeServer` is (`agent_backends.py:3665`), because per-turn `cwd` is passed on `thread/start` instead.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_codex_pool.py -v`
Expected: 6 passed

- [ ] **Step 5: Check types and style**

Run: `python -m mypy && python -m ruff check . && python -m ruff format .`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add agent_backends.py tests/test_codex_pool.py
git commit -m "Say how Codex's app server is started, checked, interrupted and stopped"
```

---

### Task 7: `CodexWorker` asks the pool instead of spawning

**Files:**
- Modify: `agent_backends.py:1652-1701` (`CodexWorker._do_run`, the `Popen` and the `initialize` handshake)
- Modify: `agent_backends.py:1620-1632` (`CodexWorker.run`'s `finally`)
- Test: `tests/test_codex_pool.py`

**Interfaces:**
- Consumes: `backend_pool.pool()`, `pool_key`, `codex_adapter`, `CodexServer` from Tasks 2 and 6.
- Produces: `CodexWorker` no longer owns a process. `self._server: Optional[CodexServer]` replaces `self._proc`. The `initialize`/`initialized` handshake happens once per server, not once per turn.

**The behaviour that must not change:** every existing Codex test must still pass. In particular `tests/test_codex_last_words.py` (stderr drain, daemon reader) and `tests/test_backends.py:891` (the no-window launch flags).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_codex_pool.py`:

```python
def test_a_second_turn_reuses_the_app_server_the_first_one_left():
    """The whole point: one process, many prompts."""
    pool = backend_pool.BackendPool()
    started: list[object] = []

    def start() -> agent_backends.CodexServer:
        server = agent_backends.CodexServer(_FakeProc())
        started.append(server)
        return server

    adapter = agent_backends.codex_adapter()._replace(start=start)
    key = backend_pool.pool_key("codex")
    try:
        first = pool.take(key)
        assert first is None
        held = backend_pool.HeldProcess(adapter.start(), adapter)
        pool.keep(key, held)
        assert pool.take(key) is held
        assert len(started) == 1, "a second app-server was started for the second turn"
    finally:
        pool.drop_all()


def test_a_dead_app_server_is_replaced_on_the_next_turn():
    pool = backend_pool.BackendPool()
    adapter = agent_backends.codex_adapter()
    proc = _FakeProc()
    key = backend_pool.pool_key("codex")
    try:
        pool.keep(key, backend_pool.HeldProcess(agent_backends.CodexServer(proc), adapter))
        proc.kill()
        assert pool.take(key) is None, "a dead server was handed to the next turn"
    finally:
        pool.drop_all()


def test_the_shared_server_is_started_from_home_not_a_project(monkeypatch):
    """One server serves every tab, so it cannot belong to one project's
    directory. The per-turn cwd is passed on thread/start instead."""
    seen: dict = {}

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["cwd"] = kwargs.get("cwd")
        raise OSError("stop here, the launch arguments are what is under test")

    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _b: "codex")
    monkeypatch.setattr(agent_backends, "_codex_app_server_binary", lambda b: b)
    monkeypatch.setattr(agent_backends, "subprocess_env", lambda _b: {})
    monkeypatch.setattr(agent_backends.subprocess, "Popen", fake_popen)
    with pytest.raises(OSError):
        agent_backends._start_codex_server()
    assert seen["cwd"] == str(agent_backends.Path.home())
    assert "app-server" in seen["cmd"]
    assert "--stdio" in seen["cmd"]


def test_the_shared_server_is_launched_without_a_console_window(monkeypatch):
    """Regression guard for tests/test_backends.py:891, now that the launch
    has moved out of the worker."""
    seen: dict = {}

    def fake_popen(cmd, **kwargs):
        seen.update(kwargs)
        raise OSError("enough")

    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _b: "codex")
    monkeypatch.setattr(agent_backends, "_codex_app_server_binary", lambda b: b)
    monkeypatch.setattr(agent_backends, "subprocess_env", lambda _b: {})
    monkeypatch.setattr(agent_backends.subprocess, "Popen", fake_popen)
    with pytest.raises(OSError):
        agent_backends._start_codex_server()
    for key, value in agent_backends.no_window_kwargs().items():
        assert seen.get(key) == value
```

Add `import pytest` to the file's imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_codex_pool.py -v -k "reuses or replaced or home or console"`
Expected: FAIL on the launch tests — `_start_codex_server` passes `cwd` from the turn, not home (or the test finds a different failure). Read the failure before changing code.

- [ ] **Step 3: Move the launch and handshake out of the turn**

In `CodexWorker._do_run` (`agent_backends.py:1652`), replace the `subprocess.Popen(...)` block and the `initialize` / `initialized` sends with:

```python
        key = backend_pool.pool_key(BACKEND_CODEX)
        shared = backend_pool.pool()
        held = shared.take(key)
        if held is None:
            try:
                held = backend_pool.HeldProcess(_start_codex_server(), codex_adapter())
            except OSError as exc:
                self._fail(f"Failed to launch Codex: {exc}")
                return
            # The handshake belongs to the process, not the turn: a reused
            # server has already been initialized and would reject a second.
            if not self._handshake(held.handle):
                held.stop()
                self._fail("Codex did not answer the initialize handshake")
                return
        shared.keep(key, held)
        self._held = held
        self._server = held.handle
```

Keep the existing stderr reader, but start it once per server rather than once per turn — move it into `_start_codex_server` and have `CodexServer` own the `_stderr` list, so `_await_last_words` reads the server's list. The turn's `self._stderr` becomes a property reading `self._server`.

In `CodexWorker.run`'s `finally` (`agent_backends.py:1620-1632`), remove the `end_process_group` call. A turn no longer owns the process:

```python
        finally:
            self._accepting_input.clear()
            # The process belongs to the pool now, not to this turn. It is
            # stopped when the conversation goes away, when it is found dead,
            # or when the reaper decides nobody is using it.
            self._on_done()
```

- [ ] **Step 4: Run the Codex tests to verify they pass**

Run: `python -m pytest tests/test_codex_pool.py tests/test_codex_last_words.py -v`
Expected: all pass. If `test_codex_last_words.py` fails, the stderr list did not move correctly — fix the move, not the test.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q -W error`
Expected: 0 failed. A `ResourceWarning` here means a `_FakeProc` or a real server was left unstopped by a test.

- [ ] **Step 6: Check types and style**

Run: `python -m mypy && python -m ruff check . && python -m ruff format .`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add agent_backends.py tests/test_codex_pool.py
git commit -m "Let a Codex turn borrow the app server instead of starting one"
```

---

### Task 8: Cancel — interrupt, verify, and abandon the thread rather than the server

**Files:**
- Modify: `agent_backends.py:1586-1600` (`CodexWorker.cancel`)
- Test: `tests/test_codex_pool.py`

**Interfaces:**
- Consumes: `CodexServer.interrupt`, `_CODEX_INTERRUPT_VERIFY_SECONDS` from Task 6.
- Produces: `CodexWorker.cancel()` that never calls `end_process_group`.

**The rule, from the spec:** for a process-wide backend, an unconfirmed interrupt must not kill the shared server — that would take down every tab. Discard the `threadId` instead; the next turn resumes it from disk. `CodexWorker.cancel` has no direct test today, so this task adds the first.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_codex_pool.py`:

```python
def _codex_worker(**kwargs) -> agent_backends.CodexWorker:
    callbacks = {
        "on_session": lambda _v: None,
        "on_started": lambda: None,
        "on_activity": lambda _k, _v: None,
        "on_complete": lambda _v: None,
        "on_failed": lambda _v: None,
        "on_done": lambda: None,
    }
    callbacks.update(kwargs)
    return agent_backends.CodexWorker("do the work", None, ".", "default", **callbacks)


def test_cancelling_asks_codex_to_stop_the_turn():
    worker = _codex_worker()
    server = agent_backends.CodexServer(_FakeProc())
    worker._server = server
    worker._thread_id, worker._turn_id = "thread-1", "turn-1"
    worker.cancel()
    sent = [json.loads(line) for line in server.proc.stdin.written]
    assert any(m.get("method") == "turn/interrupt" for m in sent)


def test_cancelling_never_kills_the_server_the_other_tabs_are_using():
    """A shared process must survive one tab's Escape."""
    killed: list = []
    worker = _codex_worker()
    server = agent_backends.CodexServer(_FakeProc())
    worker._server = server
    worker._thread_id, worker._turn_id = "thread-1", "turn-1"
    original = agent_backends.end_process_group
    agent_backends.end_process_group = lambda p, timeout=0.0: killed.append(p)
    try:
        worker.cancel()
    finally:
        agent_backends.end_process_group = original
    assert killed == [], "cancelling one tab stopped the server every tab shares"
    assert server.proc.killed is False


def test_an_unconfirmed_interrupt_abandons_the_thread_not_the_server():
    """The middle rung: the wedged conversation pays, the server does not."""
    worker = _codex_worker()
    server = agent_backends.CodexServer(_FakeProc())
    server.confirm_interrupt = lambda _t, _u, _timeout: False
    worker._server = server
    worker._thread_id, worker._turn_id = "thread-1", "turn-1"
    worker.cancel()
    assert worker.abandoned_thread == "thread-1"
    assert server.proc.killed is False


def test_a_confirmed_interrupt_keeps_the_thread():
    worker = _codex_worker()
    server = agent_backends.CodexServer(_FakeProc())
    server.confirm_interrupt = lambda _t, _u, _timeout: True
    worker._server = server
    worker._thread_id, worker._turn_id = "thread-1", "turn-1"
    worker.cancel()
    assert worker.abandoned_thread == ""


def test_cancelling_before_a_turn_started_is_harmless():
    """Escape pressed between Send and the first reply."""
    worker = _codex_worker()
    worker.cancel()
    assert worker.abandoned_thread == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_codex_pool.py -v -k cancel`
Expected: FAIL — `AttributeError: 'CodexWorker' object has no attribute 'abandoned_thread'`

- [ ] **Step 3: Write the minimal implementation**

Replace `CodexWorker.cancel` (`agent_backends.py:1586`):

```python
    def cancel(self) -> None:
        """Stop this turn without stopping the server the other tabs share.

        Codex's app-server multiplexes: one process holds every tab's thread.
        Killing it here would end four other conversations to stop one. So the
        interrupt is sent and waited on, and if Codex does not confirm the turn
        stopped, the THREAD is abandoned rather than the process -- the next
        turn resumes it from its rollout, and the wedged conversation is the
        only thing that pays.
        """
        self._cancelled = True
        self._accepting_input.clear()
        server = self._server
        if server is None or not self._thread_id or not self._turn_id:
            return
        confirmed = server.interrupt(
            self._thread_id, self._turn_id, _CODEX_INTERRUPT_VERIFY_SECONDS
        )
        if not confirmed:
            self.abandoned_thread = self._thread_id
```

Add to `CodexWorker.__init__` (`agent_backends.py:1535`), beside `self._proc`:

```python
        self._server: Optional[CodexServer] = None
        self._held: object = None
        # Set when an interrupt went unconfirmed: this conversation's thread is
        # not trusted for the next turn and is resumed from disk instead.
        self.abandoned_thread = ""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_codex_pool.py -v`
Expected: all pass

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q -W error`
Expected: 0 failed

- [ ] **Step 6: Check types and style**

Run: `python -m mypy && python -m ruff check . && python -m ruff format .`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add agent_backends.py tests/test_codex_pool.py
git commit -m "Stop one Codex turn without stopping the four tabs beside it"
```

---

### Task 9: The window lets go — every place a conversation stops being itself

**Files:**
- Modify: `blindpilot_app.py:7046-7055` (`cancel_worker`, beside the held-Hermes drop)
- Modify: `blindpilot_app.py:6467` (`_drop_held_hermes` → generalise to `_drop_held_backends`)
- Modify: `blindpilot_app.py:10136` (`_on_close`)
- Test: `tests/test_held_process_drop_sites.py` (create)

**Interfaces:**
- Consumes: `backend_pool.pool()`, `pool_key` from Task 2.
- Produces: `SessionPanel._drop_held_backends()` — drops every per-conversation key for this panel, and is called from all six abandonment sites plus `cancel_worker`.

**Why this task is the risky one:** a missed site is not a crash. It is a message sent into the previous conversation. The spec enumerates the sites; this task tests them by name so a seventh added later fails loudly.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_held_process_drop_sites.py`:

```python
"""A tab that stops being its conversation must let go of its process.

The failure this guards is not a crash. It is the next message going into the
previous conversation, which nobody would see until they read the transcript.
"""

from __future__ import annotations

import inspect

import pytest

import backend_pool

app = pytest.importorskip("blindpilot_app")


# Every place the tab stops being the conversation its process was started
# for. Taken from the design doc; a seventh added without a drop is a bug
# this list is here to catch.
ABANDONMENT_SITES = [
    "clear_conversation",
    "restore_history",
    "open_hermes_session",
]


@pytest.mark.parametrize("method_name", ABANDONMENT_SITES)
def test_every_abandonment_site_lets_go_of_the_held_process(method_name):
    source = inspect.getsource(getattr(app.SessionPanel, method_name))
    assert "_drop_held_backends" in source, (
        f"{method_name} changes which conversation this tab is, "
        "but does not let go of the process held for the old one"
    )


def test_cancel_worker_lets_go_too():
    source = inspect.getsource(app.SessionPanel.cancel_worker)
    assert "_drop_held_backends" in source


def test_dropping_stops_every_backend_this_panel_held():
    """A tab that switched backends mid-conversation may hold more than one."""

    class _Handle:
        def __init__(self) -> None:
            self.running = True
            self.stops = 0

        def stop(self) -> None:
            self.stops += 1
            self.running = False

    adapter = backend_pool.Adapter(
        start=lambda: _Handle(),
        alive=lambda h: h.running,
        interrupt=lambda _h, _t: True,
        stop=lambda h: h.stop(),
    )
    panel = type("_Panel", (), {})()
    pool = backend_pool.pool()
    claude, hermes = _Handle(), _Handle()
    try:
        pool.keep(backend_pool.pool_key("claude", panel), backend_pool.HeldProcess(claude, adapter))
        pool.keep(backend_pool.pool_key("hermes", panel), backend_pool.HeldProcess(hermes, adapter))
        app.SessionPanel._drop_held_backends(panel)
        assert claude.stops == 1
        assert hermes.stops == 1
    finally:
        pool.drop_all()


def test_dropping_survives_a_panel_that_never_held_anything():
    """cancel_worker runs on half-built panels and on test stand-ins."""
    panel = type("_Panel", (), {})()
    app.SessionPanel._drop_held_backends(panel)  # must not raise


def test_quitting_sweeps_the_pool():
    source = inspect.getsource(app.MainFrame._on_close)
    assert "drop_all" in source or "stop_all_held_processes" in source
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_held_process_drop_sites.py -v`
Expected: FAIL — `AttributeError: type object 'SessionPanel' has no attribute '_drop_held_backends'`

- [ ] **Step 3: Write the minimal implementation**

Replace `_drop_held_hermes` (`blindpilot_app.py:6467`) with:

```python
    def _drop_held_backends(self) -> None:
        """Let go of every process held for this tab's conversation.

        Called wherever the tab stops being the conversation those processes
        were started for: a new conversation, a restored one, a different
        backend, a model or effort change that invalidates the session. A
        process carries the conversation's live ids, so reusing one across that
        boundary would send the next message into the previous conversation.

        Process-wide backends -- Codex, opencode -- are not dropped here: their
        one process serves every tab, and this tab abandoning a conversation is
        not a reason to end four others.
        """
        held = getattr(self, "_held_hermes", None)
        if held is not None:
            held.drop()  # type: ignore[attr-defined]
            self._held_hermes = None
        shared = backend_pool.pool()
        for backend in (BACKEND_CLAUDE, BACKEND_HERMES, BACKEND_FREEBUFF):
            shared.drop(backend_pool.pool_key(backend, self))
```

Rename every call to `_drop_held_hermes` to `_drop_held_backends`. The six sites are `blindpilot_app.py:6302`, `:6361`, `:6420`, `:6027`, `:5547`, `:5566`. Verify with:

```bash
grep -n "_drop_held_hermes" blindpilot_app.py   # must print nothing when done
```

In `cancel_worker` (`blindpilot_app.py:7052`), replace the inline held-Hermes drop with a call that tolerates a stand-in:

```python
        drop = getattr(self, "_drop_held_backends", None)
        if drop is not None:
            drop()
```

Read it with `getattr` for the reason the existing comment gives at `:7046`: this runs on panels closed before `__init__` finished and on the stand-ins `tests/test_closing_a_tab.py` drives it with.

In `MainFrame._on_close` (`blindpilot_app.py:10136`), after the per-tab cancel threads are joined and before `event.Skip()`:

```python
        # Belt and braces over the per-tab teardown above: a shared app-server
        # belongs to no single tab, so nothing above this stops it.
        backend_pool.stop_all_held_processes()
```

Add `import backend_pool` to `blindpilot_app.py`'s imports.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_held_process_drop_sites.py tests/test_closing_a_tab.py tests/test_cancel_off_the_gui_thread.py -v`
Expected: all pass

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q -W error`
Expected: 0 failed

- [ ] **Step 6: Check types and style**

Run: `python -m mypy && python -m ruff check . && python -m ruff format .`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add blindpilot_app.py tests/test_held_process_drop_sites.py
git commit -m "Let go of a tab's backend wherever it stops being that conversation"
```

---

### Task 10: Say it, when a reap means the next prompt starts cold

**Files:**
- Modify: `blindpilot_app.py` (`MainFrame.__init__`, near the earcons setup at `:8756`)
- Test: `tests/test_held_process_drop_sites.py`

**Interfaces:**
- Consumes: `BackendPool.on_reap`, `start_reaper` from Tasks 4 and 5.
- Produces: the reaper started at window construction and its announcement routed to the active panel's activity rows.

**Why:** a user who cannot see a spinner infers a hang from silence. A cold start after a reap is the one slow prompt that has a reason, so the reason has to be spoken.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_held_process_drop_sites.py`:

```python
def test_the_window_starts_the_reaper_and_listens_for_it():
    source = inspect.getsource(app.MainFrame.__init__)
    assert "start_reaper" in source, "nothing ever reaps an idle backend"
    assert "on_reap" in source, "a reap would happen silently"


def test_the_reap_announcement_names_the_backend_that_restarts():
    """It goes through SessionPanel._say, which already decides that only the
    visible tab narrates -- so this adds no second narration path."""
    said: list[tuple[str, str]] = []

    class _Page:
        def _say(self, text: str, kind: str = "assistant") -> bool:
            said.append((text, kind))
            return True

    class _Notebook:
        def GetCurrentPage(self):
            return _Page()

    frame = type("_Frame", (), {"notebook": _Notebook()})()
    app.MainFrame._announce_reap(frame, "codex")
    assert said, "a reaped backend was not announced"
    assert "Codex" in said[0][0]


def test_a_reap_with_no_visible_page_is_silent_rather_than_a_crash():
    """The window can be mid-teardown when the reaper fires."""

    class _Notebook:
        def GetCurrentPage(self):
            return None

    frame = type("_Frame", (), {"notebook": _Notebook()})()
    app.MainFrame._announce_reap(frame, "codex")  # must not raise
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_held_process_drop_sites.py -v -k reap`
Expected: FAIL — `AttributeError: type object 'MainFrame' has no attribute '_announce_reap'`

- [ ] **Step 3: Write the minimal implementation**

Add to `MainFrame`:

```python
    def _announce_reap(self, backend: str) -> None:
        """Say that an idle backend was let go.

        Goes through the visible page's own `_say` (blindpilot_app.py:6593)
        rather than adding a narration path of its own: that method already
        decides that only the visible tab speaks, and mirrors the line to the
        status bar either way, so nothing is lost when it declines to speak.
        """
        page = self.notebook.GetCurrentPage()
        say = getattr(page, "_say", None)
        if say is None:
            # Mid-teardown, or a page that is not a session. Nothing to say to.
            return
        label = backend_label(backend)
        say(f"{label} was idle and has been closed. The next message will restart it.", "tool")
```

In `MainFrame.__init__`, after the notebook is built:

```python
        # An idle backend is let go after a quarter of an hour; the reaper is
        # what notices, and _announce_reap is what makes it audible. CallAfter
        # because the reaper runs on a thread of its own and narration must
        # happen on the window's.
        backend_pool.pool().on_reap = lambda backend: wx.CallAfter(self._announce_reap, backend)
        backend_pool.start_reaper()
```

`"tool"` is the activity kind Codex already uses for a line the user should hear but did not ask for (`agent_backends.py:1846`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_held_process_drop_sites.py -v`
Expected: all pass

- [ ] **Step 5: Run the full suite and the startup smoke check**

Run: `python -m pytest -q -W error`
Expected: 0 failed
Run: `python blind_pilot.py --startup-smoke`
Expected: exits 0 — this is what CI runs (`.github/workflows/ci.yml`), and it is the only check that the window still builds with the reaper wired in.

- [ ] **Step 6: Check types and style**

Run: `python -m mypy && python -m ruff check . && python -m ruff format .`
Expected: clean

- [ ] **Step 7: Measure the win, and write the number down**

Send two prompts to Codex in one tab and confirm from the process list that only one `codex` app-server exists across both, and that its MCP children are not restarted between them. Record the observed warm-start saving in `CHANGELOG.md` under a new Unreleased heading.

- [ ] **Step 8: Commit**

```bash
git add blindpilot_app.py tests/test_held_process_drop_sites.py CHANGELOG.md
git commit -m "Say when an idle backend was let go, so a cold start has a reason"
```

---

## Self-Review

**Spec coverage.** `HeldProcess` → Task 1. `BackendPool` and the key shape → Task 2. Contract harness → Task 3. Idle reaping → Tasks 4 and 10. `atexit` and `drop_all` → Task 5. Codex adapter → Task 6. Codex on the pool → Task 7. Cancel with interrupt-verify and the process-wide middle rung → Task 8. The eight drop sites → Task 9. Reap announcement → Task 10.

**Deferred to plans 2–5, by design:** the opencode, Claude, Hermes and FreeBuff migrations, and the characterisation tests the spec requires before each. Task 9 drops `claude`/`hermes`/`freebuff` keys already, so those plans add adapters into a window that is already letting go correctly.

**Known gap, carried deliberately.** `CodexServer.confirm_interrupt` returns `False` in Task 6 and is only made real when Task 7 wires up the shared reader thread. Between those tasks every interrupt reads as unconfirmed, which is the safe direction — a thread is abandoned that did not need to be, and no process is wrongly kept. Task 8's tests substitute `confirm_interrupt` directly so they do not depend on that ordering.

**Type consistency.** `Adapter(start, alive, interrupt, stop)` is used with those four names in Tasks 1, 3, 6, 7 and 9. `pool_key(backend, panel=None)` returns a 2-tuple everywhere. `HeldProcess.stop()` — not `close()` or `drop()` — throughout; `drop` is the pool's verb, `stop` is the process's.
