# Applied: a held Claude Code process

What the held-Claude-session branch changed, file by file, the tests that
cover it, what the other backends already did, and what could not be checked
live on this machine. Spec: `01-design.md`. Branch
`feat/held-claude-session`.

## What changed, by file

`claude_session.py` (new)

- `Wants`, a frozen dataclass of what a turn needs the process started with:
  `cwd`, `permission_mode`, `model`, `effort`, `session_id`.
- `build_command(binary, wants, prompt_tool)`, the same command line
  `ClaudeWorker._do_run` used to build, now built once per process instead of
  once per turn.
- `ClaudeSession`, one live process and the threads that belong to it, not to
  a turn. `attach`/`detach` let one turn at a time read its events; a
  `set_idle_sink` callback takes events that arrive with nothing attached.
  Control requests go through `send_control`, with `interrupt`,
  `set_model` and `set_permission_mode` built on it. `can_serve` reports
  whether a held session matches what a turn wants; `adopt` sends a model or
  permission-mode change down the stream to bring a mismatched session into
  line, stopping and reporting a refusal instead of guessing. `wait` and
  stderr marks (`stderr_mark`, `stderr_since`) let a turn read only the
  stderr lines that arrived during it.
- `claude_adapter()`, the `backend_pool.Adapter` Claude registers with, so
  the pool's idle reaper and drop sites treat it like Codex and Hermes.
- `take_or_start(panel, wants)`, the one way a turn gets a session: one lock
  per tab, so one tab's slow model change never blocks another tab's turn.
  Reuses a held process when it can serve what is wanted, adopts a model or
  mode change onto it, and stops and replaces it when the directory,
  reasoning effort or conversation differ, because the CLI has no stream
  request for those.

`blindpilot_app.py`

- `ClaudeWorker` no longer owns a process; it borrows one. `_do_run` calls
  `take_or_start`, attaches, sends the prompt if it has one, and detaches
  when the result arrives instead of closing stdin. `_take` and `_read_turn`
  carry that shape. `cancel` sends an interrupt through the held session and
  only drops the process from the pool, stopping it, when the CLI does not
  confirm in time; a confirmed interrupt ends the turn with the result the
  CLI sends and leaves the process alive. A wake marker, `_CANCEL_WAKE`, is
  queued so a stopped turn's reader re-evaluates its
  `_CANCEL_DRAIN_SECONDS` deadline instead of blocking on a queue nothing
  more will arrive on. `_deny` answers a control request with a refusal
  while the turn is stopping, so a question asked mid-cancel gets an answer
  rather than a hang. `ClaudeWorker.stop_seconds`, at 12, is what the panel's
  Stop joins the worker thread with, longer than `_CANCEL_JOIN_SECONDS`
  (unchanged, at 3, and still what Codex derives its verify budget from), so
  Stop is not reported as failed while the CLI confirms the interrupt and
  the worker thread answers it.
- Deleted: `_close_stdin`, `_wait_for_shutdown`, `_reap_in_background`,
  `_reap`, `_drain_stderr`, `_stderr_text`, `_background_agents_running`,
  `_stopped_by_us`, `_SHUTDOWN_QUIET_SECONDS`, `_REAP_SECONDS`. Nothing shuts
  the process down at the end of a turn any more, so the code that used to
  wait for that shutdown, drain its stderr on the way out, and decide
  whether the exit looked like our own kill, has nothing left to do.
- `SessionPanel` gained `_claude_worker_extra` (what a Claude turn needs
  beyond the message: whose process it borrows, and a callback that wakes
  the tab when the CLI speaks with no turn attached) and `_launch_turn`,
  extracted from `_on_send` so a late turn can start a worker through the
  same path a sent turn does. `_start_late_turn` builds a worker with no
  prompt when the idle sink's callback reaches the GUI thread through the
  `late_turn` mailbox event: it disables Send, enables Stop, plays the
  earcon, shows the working indicator, appends `Turn(prompt="")`, and adds
  no "You:" row, because nobody typed anything.
  `_late_turn_waiting` defers the late turn if one is already running,
  and the turn's `done` handler starts the deferred one.

## Tests, by file

- `tests/test_claude_session.py` (new, 32 tests). The command line, event
  ordering, the idle sink taking events with nothing attached and a turn
  reading them once attached afterwards, only one attach at a time, EOF
  reaching the attached turn and not the idle sink, malformed lines skipped,
  stderr marks, every event touching the pool's idle clock, `stop` ending
  the process group once, interrupt confirmed/timed out/refused,
  model and permission-mode changes going down the stream and being
  remembered, `can_serve` and `adopt`, the adapter's alive/busy/stop, and
  `take_or_start` reusing a process, starting a new one on a changed
  directory or effort or conversation, sending a model change to a held
  process, and one tab's slow change not blocking another's.
- `tests/test_late_turns.py` (new, 4 tests). The worker told which tab holds
  it and how to wake it, a late turn starting a prompt-less turn with the
  earcon running, a late turn waiting while a turn is still finishing and
  starting after it, and the mailbox routing the wake-up to the late turn.
- `tests/test_claude_stream_resilience.py` (rewritten to the new contract).
  A turn ending at its result with the process staying alive for the
  agents, an answer surviving a nonzero exit, a queued-turn result not
  ending the run, a completed turn with no answer saying so without being
  killed, an error result after an answer keeping the answer, two Stop
  tests (`test_stop_interrupts_the_turn_and_keeps_the_process`,
  `test_the_panel_join_budget_outlasts_an_interrupt_and_its_drain`), and a
  stopped turn ending at the drain when no result follows, guarded by a
  "BlindPilot stopped" check so a turn we stopped is never reported as
  Claude Code failing.
- `tests/test_live_rows.py`: eight Claude worker tests moved to patch
  `claude_session._popen` instead of a process the worker started itself.
- `tests/test_pool_contract.py`: the existing pool contract run against
  `claude_adapter`, alongside Codex's and Hermes's.
- `tests/test_shutdown_patience.py` deleted. It tested only the shutdown
  code this branch removes.

## The announcements this branch changes

"A background agent has reported. Receiving response", spoken when the idle
sink wakes a tab and `_start_late_turn` opens the turn. New wording.

Two sentences the pool has always had are now reachable for Claude, because
Claude now has a process for the pool to hold. Both come from the reaper's
existing announcement, `MainFrame._announce_reap`, unchanged, and both are
new for Claude only in that a Claude tab can now be the one to hear them:

- "Claude Code had stopped running. Restarting it, which takes a moment."
  Spoken on the next message after the held process died between turns.
- "Claude Code was idle and has been closed. The next message will restart
  it." Spoken by the fifteen-minute idle reaper.

Two things that used to be spoken are gone because nothing left in the code
can say them: "Waiting for N background agents to finish", because the process
no longer waits for them before ending the turn, and "No response received",
now unreachable. A process that dies without a return code says "Claude Code
exited without a code" (`_do_run`'s existing wording, unchanged). A late turn
woken for a process that has already gone says "Claude Code finished the turn
without saying anything", the sentence a turn that reached its result in
silence already said.

## What the other backends do

Checked 2026-09-05, copied from `01-design.md`:

- Codex holds one shared app-server process between turns
  (`backend_pool`, `CodexWorker._borrow_server`). Background work survives.
- opencode talks to one `opencode serve` process started on first use and
  reused (`opencode_server()`). Same.
- Hermes talks to a gateway that outlives turns (`HeldConnection`). Same.
- FreeBuff is driven through a pseudo-terminal, has no headless interface and
  no agents. Nothing to hold.

Claude Code was the one backend that tore its process down per turn; this
branch brings it in line with the other three.

## What was not checked live

The design's Testing section lists a live check: on an audit copy signed in
to Claude Code, send a turn that spawns a foreground agent and ends, send a
turn that resumes it with SendMessage and ends, confirm the follow-up
arrives as a late turn with rows and an answer, and that the process id does
not change across the three turns. That check did not run. The audit copy's
Claude Code could not sign in on this machine, its on-disk credentials are
stale, so no `claude -p` process on it gets past authentication. The person
who wrote this signs in with an API key and cannot run `claude /login`, so
those live steps are still to run, unchanged from the design, by someone with
a Claude account who can sign the CLI in on an audit copy. Until then the
branch is verified by its tests and the fake process only.

## The one CLI fact this rests on

In Claude Code 2.1.258, `started_in_background` in `subagent_stats` is
incremented only by the Agent tool's spawn path (`recordSpawn`). A
SendMessage resume records nothing there, so the wait the worker used to do
on that count could never see a resumed agent finish; the process had
already been torn down by the time it reported back. Holding the process
removes the need to see it in `subagent_stats` at all: whatever the CLI says
after the turn ends now reaches a live process instead of a dead one.
