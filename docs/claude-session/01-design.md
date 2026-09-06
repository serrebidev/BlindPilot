# 01 - A held Claude Code process

Design for keeping one Claude Code process alive per tab between turns, so
agents it started in the background, or resumed with SendMessage, survive the
end of the turn that started them. Approved 2026-09-05.

## Why

BlindPilot starts a fresh `claude -p --input-format stream-json` process for
every turn, passes `--resume <session id>` so the conversation continues, and
closes the process's stdin when the turn's `result` event arrives. The CLI
then shuts down and exits with code 1 if anything was still running inside
it. Whatever that was is lost.

The worker already reads `subagent_stats` from the result and keeps the
process up while `started_in_background` exceeds the completed, failed and
killed counts. That protects agents spawned in the background. It cannot
protect a resumed agent: in the CLI, `started_in_background` is incremented
only in `recordSpawn`, which only the Agent tool's spawn path calls. The
SendMessage resume path ("Resuming agent ...") records nothing. After a
foreground agent completes and is later resumed, the stats read spawned 1,
completed 1, background 0, and nothing in the result event says an agent is
running. Read from the CLI binary, version 2.1.258, on 2026-09-05.

Seen five times on 2026-09-05 in `blindpilot.log` as `turn ended early:
exit_code=1 completed=True`, each followed by the app being restarted.

Holding the process also removes the per-turn cost the user hears today: MCP
servers reconnecting at the start of every turn.

## What stays the same

- Everything the panel drives a turn through: the `AgentWorker` protocol,
  `ClaudeWorker`'s constructor and callbacks, `steer`, `cancel`, the earcons,
  the question dialog, the live rows.
- Every spoken string and every key.
- The command line, apart from when it is run: once per process instead of
  once per turn.
- The pool. `backend_pool.HeldProcess`, `Adapter`, the per-panel key, the
  fifteen-minute idle reaper with its announcement, `_drop_held_backends`
  and `stop_all_held_processes` are used as they are.

## The other backends

The user's rule is that a fix applies to every backend unless it cannot.
Checked 2026-09-05:

- Codex holds one shared app-server process between turns
  (`backend_pool`, `CodexWorker._borrow_server`). Background work survives.
- opencode talks to one `opencode serve` process started on first use and
  reused (`opencode_server()`). Same.
- Hermes talks to a gateway that outlives turns (`HeldConnection`). Same.
- FreeBuff is driven through a pseudo-terminal, has no headless interface and
  no agents. Nothing to hold.

Claude Code is the one backend that tears its process down per turn.

## Components

### `claude_session.py` (new)

`class ClaudeSession`. One live Claude Code process and the threads that
belong to it, not to a turn.

| Member | Meaning |
|---|---|
| `start(binary, cwd, permission_mode, model, effort, session_id) -> ClaudeSession` | runs the same command line `ClaudeWorker._do_run` builds today, starts the stdout reader and the stderr drainer |
| `settings` | the `cwd`, `permission_mode`, `model`, `effort` it was started with, updated by `set_model` and `set_permission_mode` |
| `session_id` | from the first `system/init` event; `None` until then |
| `send_user(text) -> bool` | writes one user message; the same JSON `_write_message` writes today |
| `send_control(subtype, **fields) -> Optional[dict]` | writes a `control_request` with a fresh `request_id`, waits up to a timeout for the matching `control_response`, returns its `response` or `None` |
| `set_model(model) -> bool`, `set_permission_mode(mode) -> bool` | `send_control` with those subtypes, `True` when the CLI answered `success` |
| `interrupt(timeout) -> bool` | `send_control("interrupt")`; `True` only when the CLI answered `success` in time |
| `attach(sink)` / `detach()` | one turn at a time reads events; `attach` raises if another is attached |
| `set_idle_sink(callback)` | where events go when no turn is attached; called with the first such event |
| `alive()`, `stop()` | process alive; end the process group, idempotent |
| `stderr_mark()`, `stderr_since(mark)` | what stderr said since a turn began, so a turn explains itself with its own lines |

The reader thread parses each stdout line as JSON. A `control_response` is
matched to its waiting `send_control` by `request_id`. Every other event is
given to the attached sink's queue if a turn is attached, otherwise to the
idle sink. Events that arrive while no sink of either kind exists (the first
milliseconds of a process) are buffered and delivered to whoever attaches
first. Every event calls the held process's `touch()`, so the pool's idle
clock measures silence from the CLI, not time since the last prompt.

`claude_adapter()` returns a `backend_pool.Adapter`: `alive` is
`ClaudeSession.alive`, `stop` is `ClaudeSession.stop`, `busy` is "a turn is
attached", `interrupt` is `ClaudeSession.interrupt`.

`take_or_start(panel, wants) -> ClaudeSession` is the one way a turn gets a
session. Under one lock per panel it takes the held process from the pool
for `pool_key(BACKEND_CLAUDE, panel)`. A held session is reused when its
`cwd`, `effort` and `session_id` match what the turn wants; `model` and
`permission_mode` differences are sent down the stream first, and a session
that refuses either is stopped and replaced. A session whose `cwd`, `effort`
or `session_id` differ is stopped and a new one started, because the CLI has
no stream request for those. A new session is registered with `pool.keep`.

### `ClaudeWorker` (blindpilot_app.py)

Stays the per-turn thread with the same constructor. `_do_run` changes shape:

1. `session = take_or_start(panel, wants)`; a launch failure fails the turn
   with the same message as today.
2. `session.attach(self)`; `mark = session.stderr_mark()`.
3. If the worker has a prompt, `session.send_user(prompt)`. A worker built
   for a late turn has `prompt=None` and skips this.
4. The event loop is the one that exists today, reading from the worker's
   queue instead of `proc.stdout`, with three changes. The `result` that ends
   the turn no longer closes stdin; it detaches and returns. The
   `subagent_stats` wait is removed: the process survives without it, and
   what the agents find arrives as late turns. A `result` with
   `queued_turn_count` still reads on, as today.
5. `steer` writes through `session.send_user`, as it does now through the
   pipe.
6. `cancel` calls `session.interrupt(_INTERRUPT_SECONDS)`, five seconds, the
   same budget Codex gives its interrupt. Confirmed: the
   turn ends with the result the CLI sends, the process stays. Unconfirmed:
   the held process is dropped from the pool, which stops it, and the turn
   reports as a cancelled turn does today.
7. If the process dies mid-turn (the reader sees EOF while a sink is
   attached), the worker reports it as it does today: stderr since `mark`,
   `_log_unfinished_turn`, and the answer kept if there was one.

`_wait_for_shutdown`, `_reap_in_background`, `_close_stdin` and the exit
code path after a completed turn go, because nothing shuts the process down
at the end of a turn any more.

### `SessionPanel` (blindpilot_app.py)

- When it takes a session it registers its idle sink once. The idle sink,
  called on the reader thread with the first unsolicited event, queues a
  panel event that starts a late turn on the GUI thread: the panel builds a
  `ClaudeWorker` with `prompt=None` through the same code path as a sent
  turn, so Send is disabled, Stop is enabled, the earcon and the working
  indicator run, rows appear, the question dialog opens if asked, and the
  follow-up answer is added as a new response. There is no "You:" row,
  because nobody typed anything; the response number still advances. The
  first event is handed to that worker's queue so nothing is lost.
  `ClaudeWorker`'s `prompt` parameter becomes `Optional[str]` for this.
- A message sent while a late turn is running goes through `steer`, as a
  message sent during any running turn does.
- `_drop_held_backends` includes `BACKEND_CLAUDE` in the per-panel backends
  it drops, so closing the tab, switching backend and quitting stop the
  process through the paths that already exist and are tested.
- The reaper's announcement names Claude Code when it stops an idle process
  or finds one dead, through the existing `_announce_reap`.

## Data flow

Sent turn: panel builds worker, worker takes the session, writes the user
message, reads its events to the result, detaches. Process stays.

Late turn: an agent finishes, the CLI runs a follow-up turn, its first event
reaches the idle sink, the panel builds a prompt-less worker, the worker
attaches and reads to the result, detaches.

Next sent turn: same process, same session id, no `--resume`, no MCP
reconnect.

## Error handling

- Process dead when taken: the pool's `take` already refuses a dead process;
  a new one starts with `--resume`.
- Process dies between turns: the reaper finds it and announces a restart
  with the existing `REAP_DIED` wording; the next turn starts a new process.
- Interrupt not confirmed within the timeout: process stopped and dropped;
  the next turn starts fresh. Never "kill if unsure" on a confirmed
  interrupt.
- `set_model` or `set_permission_mode` refused: process stopped and
  replaced with the new settings on the command line.
- A `control_request` between turns: reaches the late turn's worker, which
  answers it exactly as a sent turn's worker would.
- Idle fifteen minutes with no turn attached and no CLI output: reaped and
  announced, as Codex is today. An agent silent that long is reaped with it;
  the announcement says the backend restarts on the next message.

## Testing

Unit, with a fake process (pipes and a script that speaks stream-json), no
display:

- A result does not close stdin and the process is alive afterwards.
- A second turn reuses the process and writes a second user message; no
  second `Popen`.
- An event arriving with no turn attached reaches the idle sink once, and a
  worker attached afterwards receives it first.
- `interrupt` returns True on a `success` response and False on a timeout,
  and the unconfirmed case drops the held process.
- `set_model` and `set_permission_mode` write the right control requests;
  a changed `effort` or `cwd` starts a new process.
- A process that dies mid-turn reports stderr since the turn's mark.
- `_drop_held_backends` drops the Claude key, via the existing drop-site
  test's backend list.
- The pool contract (`tests/pool_contract.py`) run against `claude_adapter`.
- The existing resilience tests keep their intent and lose the stdin-closing
  assertions.

Live, on an audit copy signed in to Claude Code: send a turn that spawns a
foreground agent and ends; send a turn that resumes it with SendMessage and
ends; confirm the follow-up arrives as a late turn with rows and an answer,
and that the process id did not change across the three turns.

## Out of scope

- Holding FreeBuff, which has no headless mode.
- A stream request for effort or working directory; both restart the
  process.
- Any change to what is spoken or to which keys do what.
