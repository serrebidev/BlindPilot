# Held backend processes

*Design, 2026-09-02. Branch `feat/held-backend-processes`.*

## The report

Jage (@Jage@mas.to), on Mastodon:

> I notice for ChatGPT it spins up a new instance each time I send a prompt.
> It seems like it may be possible to not do this, though this would take some
> scoping.

It is true, it is true of four of the five backends, and it costs more than the
startup time it looks like it costs.

## What is actually happening

`CodexWorker` is a `threading.Thread` built per turn. `_do_run` launches
`codex app-server --stdio` (`agent_backends.py:1656`), sends `initialize`, then
`thread/start` or `thread/resume`, runs exactly one `turn/start`, and `run`'s
`finally` calls `end_process_group`. `ClaudeWorker` has the same shape with
`--resume` (`blindpilot_app.py:3409`).

Measured on a Windows machine with the npm Codex install:

| | |
|---|---|
| cold spawn to `thread/start` acknowledged | ~4.4s |
| warm spawn to `thread/start` acknowledged | ~0.55s |
| `thread/resume`, rollout under 0.5 MB | ~0.37s |
| `thread/resume`, 13 MB rollout | 0.62s |
| `thread/resume`, 113 MB rollout | 4.41s |

Resume cost scales with the conversation, so the tax grows the longer somebody
talks — the opposite of what a user expects.

The larger cost is invisible. With nine MCP servers configured in
`~/.codex/config.toml`, one app-server had **17 descendant processes half a
second after `thread/start`, and 29 by twenty seconds**. All are killed at turn
end and started again on the next prompt. `thread/start` is acknowledged at
~0.5s while those servers are still coming up, so a turn that reaches for a tool
early waits on a cold-started MCP server every single time.

A separate probe confirmed one app-server holds several threads at once (three
concurrent `thread/start` calls all succeeded), so a shared server matches
BlindPilot's multi-tab model rather than fighting it.

## What the repo already believes

Nobody decided on the current situation; it accumulated. Five backends, three
different answers, none written down as a policy:

| Backend | Today | Where |
|---|---|---|
| opencode | Persistent, process-wide | `OpencodeServer`, `agent_backends.py:3641`; singleton `:3752`; `atexit` `:3778` |
| Hermes | Persistent, per-conversation | `HeldConnection`, `hermes_worker.py:348`; reuse `:617`; hand-back `:564` |
| FreeBuff | Speculative prewarm, keyed and TTL'd | `prewarm_freebuff`, `agent_backends.py:2647`; claim `:2706`; `atexit` `:2729` |
| Codex | Spawn per turn | `agent_backends.py:1656` |
| Claude | Spawn per turn | `blindpilot_app.py:3409` |

`HeldConnection`'s own docstring already argues the case:

> So the connection outlives the worker and belongs to the conversation. It is
> handed to each turn in turn, and only dropped when the conversation is closed,
> when the user cancels, or when it is found dead.

The work is not inventing that idea. It is finishing it, and giving the other
four backends the same vocabulary.

## Decisions taken

1. **One lifecycle, per-backend shapes.** A single named concept with one set of
   rules, implemented in whichever shape each protocol wants. Forcing five
   backends into one identical shape would mean either discarding opencode's
   working singleton or running one Codex app-server — and its 29 children — per
   open tab.
2. **Cancel means interrupt, verify, and kill if unsure.** Send the protocol
   interrupt, wait a bounded time for confirmation the turn stopped, keep the
   process if confirmed, discard it if not. Escape keeps meaning what a user who
   cannot see a spinner thinks it means.
3. **Idle processes are reaped, and the reap is spoken.** A held process is
   dropped after **15 minutes** with no turn (`_HELD_IDLE_SECONDS = 900.0`,
   settable). The next prompt in that tab pays a cold start, and BlindPilot
   announces the restart through the same activity path a turn already uses —
   `on_activity("tool", ...)`, as Codex reports its warnings
   (`agent_backends.py:1846`) — so silence is never unexplained.

## Architecture

A new module, `backend_pool.py`, owns process lifetime and nothing else.
Protocol handling stays in each worker.

### `HeldProcess`

Wraps one live child and the identity it is bound to.

- `alive() -> bool` — cheap health check, via the adapter.
- `interrupt(timeout) -> bool` — the adapter's interrupt, then wait up to
  `timeout` for confirmation. The return value is the "verify" in decision 2.
- `stop()` — terminate, then `end_process_group`.
- Carries an opaque `binding` for ids the protocol needs beyond the process.
  Hermes needs this today: it holds a stored `session_id` *and* a separate
  `_live_session` the gateway actually answers to (`hermes_worker.py:533-544`),
  and steering by the wrong one fails with "session not found".

This is where the `own_group_kwargs` / `end_process_group` pairing lives once,
instead of being re-derived in four workers.

### `BackendPool`

A keyed registry. One lock, one reaper thread, one `atexit` hook.

- `take(key) -> Optional[HeldProcess]` — the held process for this key if still
  alive, else `None`, discarding the dead one. Mirrors `HeldConnection.take`.
- `keep(key, held)` — hand a process back at end of turn.
- `drop(key)` — stop and forget. Idempotent; safe on a key never held.
- `drop_all()` — shutdown and pre-update sweeps.

**The key encodes the shape.** That is the whole trick:

| Key | Backends | Effect |
|---|---|---|
| `("codex",)`, `("opencode",)` | Codex, opencode | One process serves every tab |
| `("claude", panel)`, `("hermes", panel)`, `("freebuff", panel)` | Claude, Hermes, FreeBuff | One process per conversation |

`panel` is the `SessionPanel` object itself — the only stable per-conversation
identity. `cwd` is not unique (`/btw` opens a second tab in the same directory,
`blindpilot_app.py:9198`) and is empty for remote Hermes; `session_id` starts as
`None` and is assigned write-once when the backend reports it through
`on_session` (`blindpilot_app.py:6480`). The frame already routes by panel
identity for exactly this reason (`_panel_title_changed`,
`blindpilot_app.py:10043`), and `_held_hermes` is already keyed this way
(`blindpilot_app.py:5077`).

A tuple key would keep the panel alive, so the registry is not a flat dict of
tuples: it is a `WeakKeyDictionary` from panel to `{backend: HeldProcess}` for
the per-conversation shapes, plus a small strong dict for the process-wide ones.
A panel that is destroyed without `cancel_worker` running — the case
`blindpilot_app.py:5872-5876` already guards, where a closed tab's event queue is
discarded — therefore drops its entry on collection. That is a backstop, not the
mechanism: collection is not prompt and a process must not wait on it, so the
six enumerated drop sites remain the contract. `WeakKeyDictionary` also requires
`SessionPanel` to stay hashable by identity, which it is (`wx.Panel` does not
define `__eq__`).

### Adapters

Each backend contributes four callables — start, alive, interrupt, stop — and
nothing else. Adapters are imported lazily, so a machine without a backend pays
nothing for it, matching how `hermes_worker` is imported today
(`agent_backends.py:4893`).

## How the three existing mechanisms fold in

- **opencode** becomes a process-wide adapter. `opencode_server()` keeps its
  public name and signature; its body becomes `pool.take` / `pool.keep`.
- **Hermes** becomes a per-conversation adapter. `HeldConnection`'s
  take/keep/drop *is* the pool's interface, so this is mostly a move.
- **FreeBuff prewarm** becomes `keep()` on a key nobody has taken yet. That is
  what prewarming always was. Its key tuple already includes everything that
  invalidates the process — `(cwd, session_id, model)`, `agent_backends.py:2655`
  — which is the worked example every other adapter's key must follow.

## Cancellation, precisely

`cancel_worker` (`blindpilot_app.py:7007`) is the single teardown entry point and
already drops the held Hermes connection at `:7052`. The pool hooks in there.
Two rules, because the shapes genuinely differ:

**Per-conversation backends.** Interrupt; if unconfirmed within the budget,
`drop(key)`. The next turn starts cold. Hermes drops unconditionally on cancel
today (`hermes_worker.py:555`) because a cancelled turn leaves unread frames on
the wire; that stays, expressed as an adapter that always reports the interrupt
unconfirmed.

**Process-wide backends.** "Kill if unsure" would take down every tab, so they
get a middle rung: if the interrupt is not confirmed, **abandon the thread, not
the server.** For Codex that means discarding the `threadId` and letting the next
turn `thread/resume` it — the server and its MCP children survive, and only the
wedged conversation pays. The pool only drops a process-wide process when
`alive()` says it is already gone, or on an explicit user-driven restart.
opencode already works this way on cancel (`agent_backends.py:4272-4274` leaves
the shared server up), so this rule is the existing behaviour named.

The existing budget is `_CANCEL_JOIN_SECONDS = 3.0` (`blindpilot_app.py:4774`)
for the whole teardown, and cancel must never run on the GUI thread
(`tests/test_cancel_off_the_gui_thread.py`). The interrupt-verify wait lives
inside that budget, not beside it: `_INTERRUPT_VERIFY_SECONDS = 1.5`, half of
it, leaving the remainder for the `join` that follows. Quitting shares one
budget across all tabs (`tests/test_cancel_off_the_gui_thread.py:136` asserts
four tabs finish in under 1.2s, not 3s each), so the verify wait must be
per-pool-key and concurrent, never summed per tab.

## Where a held process must be dropped

`_drop_held_hermes` states the invariant — *"called wherever the tab stops being
the conversation the connection was opened for"* — and today that is six places.
All six become `pool.drop(key)`:

| Site | Why |
|---|---|
| `blindpilot_app.py:6289` `clear_conversation` | `/clear`, `/new` — session id cleared |
| `blindpilot_app.py:6349` `restore_history` | adopts a different session id |
| `blindpilot_app.py:6398` `open_hermes_session` | adopts a Hermes id |
| `blindpilot_app.py:6019` in `_on_send` | backend changed since last turn |
| `blindpilot_app.py:5546` | FreeBuff model change |
| `blindpilot_app.py:5565` | Hermes reasoning-effort change |

Plus tab close (`:9990`, via `cancel_worker(wait=False)` at `:10001`) and app
quit (`_on_close`, `:10136`).

A missed site is not a crash; it is a message sent into the previous
conversation. That is the failure this design most has to avoid, so the drop
points are enumerated here and tested by name.

## Error handling

- **Process found dead on `take`.** Discard, start a new one, say the backend
  restarted. Never silently.
- **Process dies mid-turn.** Unchanged: the worker already reports it through
  `_fail` / `on_failed` and `diagnostics.log_unfinished_turn`.
- **Start fails.** Reported through the existing `on_failed` path with the
  install guidance each backend already carries.
- **Reap.** Announced, per decision 3.
- **Shutdown.** `drop_all()` from `atexit` *and* from `_on_close`. Today there is
  no `atexit` for held Hermes connections; the pool gets the belt-and-braces that
  `discard_freebuff_prewarm` and `stop_opencode_server` already have.
- The window must never block on the pool. `cancel_worker`'s `wait=False` path
  and its `getattr` defensiveness (`:7046-7055`) exist because teardown runs on
  half-built panels and on test stand-ins; the pool is read the same way.

## Testing

The suite is 1074 tests and its conventions are strict. New work follows them
rather than inventing a parallel style.

**Follow the house pattern.** `tests/conftest.py` is 60 lines and holds no
process fakes. The dominant idiom is a hand-written duck-typed stand-in
implementing only the methods under test, injected by **swapping the
accessor/factory** (`monkeypatch.setattr(agent_backends, "find_backend_cli", ...)`,
25 uses; `opencode_server`, 11 uses) in preference to patching `Popen`. Turns are
driven by calling `worker._do_run()` synchronously, or `worker.run()` on a daemon
thread with a bounded `join`. `_Recorder` in
`tests/test_worker_failure_reporting.py:26`, parametrised across worker classes,
is the model to copy.

**A pool contract harness.** `tests/transport_contract.py` exists because a fake
in PR #31 could lie about being connected. The same hazard applies here — a fake
held process that always reports `alive()` would hide every bug this design can
have. So `backend_pool` gets a contract in the same shape, checking at minimum:
`drop` is idempotent (teardown paths run twice), a dropped process reports
`alive()` False, `take` after `drop` returns `None`, and a process that fails to
confirm an interrupt is not kept. Note that `test_transport_contract.py:200`
sweeps every test file for unregistered transport-shaped fakes, so a new fake may
be caught by it automatically; register rather than reshape.

**Test the reaper with a fake clock, not a sleep.**
`tests/test_long_turn_connection.py:299` monkeypatches `hw._now` to advance by
the read timeout on every read, turning a two-minute wait into milliseconds. The
idle reaper is tested that way. Polling with a deadline (`_wait_for`,
`tests/test_backends.py:991`) is the house alternative; `sleep`-then-assert is
not used anywhere and must not start here.

**Write the characterisation tests before migrating.** This is the finding that
changes the plan. The two mechanisms being migrated as "refactors with no
behaviour change" have **no coverage of the behaviour being preserved**:

- `opencode_server()` — the locked get-or-create and the restart-on-dead-`alive()`
  path — is never called by any test. Neither is `stop_opencode_server()`. The
  module global `agent_backends._opencode_server` is never set or reset by any
  fixture.
- `prewarm_freebuff` is not tested at all: not the model write, the `before`
  chat snapshot, the dedupe-on-identical-key branch, the background thread, nor
  the stale-holder swap. Only `_take_freebuff_prewarm`'s key-mismatch and TTL
  branches are covered (`tests/test_backends.py:433`, `:472`).
- Neither `atexit` registration is exercised; no test imports `atexit`.
- `CodexWorker.cancel` has no direct test.

"No behaviour change" is an unverifiable claim against untested code. Each
migration step therefore begins by writing tests against the *current*
implementation, and those tests must pass unchanged after the move. That is the
step's definition of done.

**Hazards the suite will enforce.** CI runs `pytest -q -W error`
(`.github/workflows/ci.yml:87`), so a leaked `Popen` reaching `__del__` fails the
build — a held process that outlives its test is a red build, not a slow one.
`pytest-randomly` shuffles order, so any test touching a module global must save
and restore it in `try/finally` (`tests/test_backends.py:450-468` is the
pattern); the pool's registry is exactly such a global. `pytest.ini` sets
`timeout = 60`. Threads must be daemons, asserted explicitly at
`tests/test_codex_last_words.py:136`.

## Scope

**In:** `backend_pool.py`; Codex and Claude moved onto it; opencode and Hermes
migrated behind characterisation tests; FreeBuff prewarm expressed as a pool key;
idle reaping with announcement; the six drop sites; a pool contract harness.

**Out:** any change to protocol handling, prompt building, narration, or the row
model. No new user-facing settings beyond the idle timeout. Not a refactor of
`agent_backends.py` or `blindpilot_app.py` beyond moving process lifetime out of
them.

## Sequencing

1. **Codex** — the reported bug, the biggest measured win, and it exercises both
   the process-wide key and the interrupt-verify path.
2. **opencode** — characterisation tests first, then migrate. Proves the
   abstraction fits code that already works.
3. **Claude** — gated on the spike below.
4. **Hermes** — mostly a move of `HeldConnection`.
5. **FreeBuff** — last; the PTY prewarm is the most delicate and the least
   covered.

## Open question, gated

Claude Code must serve two turns on one process for step 3 to be possible.

A throwaway probe confirmed the structure: with `--input-format stream-json` and
stdin left open, the process stayed alive after the first `result`, accepted a
second user message, returned a second `result`, and reported the same
`session_id` for both. The CLI's `--replay-user-messages` flag ("re-emit user
messages from stdin back on stdout for acknowledgment") only makes sense for a
long-lived bidirectional stream, which agrees.

**This is not yet conclusive.** The probe ran against an expired OAuth session,
so both turns returned an authentication failure rather than model output. The
transport handled two turns; an authenticated turn has not been observed doing
so. Step 3 begins by re-running that probe against a working login. If an
authenticated Claude closes its stream after `result`, Claude keeps its per-turn
spawn and nothing else in this design changes.
