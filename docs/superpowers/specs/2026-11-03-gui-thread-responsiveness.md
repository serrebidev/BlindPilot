# BlindPilot GUI-thread responsiveness

## Problem Statement

BlindPilot users can encounter stalls when menu or shortcut handlers perform
process, network, history, or filesystem work on the GUI thread. During a stall,
the window cannot repaint or respond to input, and screen-reader speech can stop
until the event loop resumes. A user may also lose input if repeated key presses
arrive while the handler is blocked.

## Solution

Keep GUI callbacks within the 50 ms GUI-thread budget. Move work that can exceed
the budget to background workers, return promptly, and deliver accessible
progress, results, errors, and cancellation through the existing UI event path.
Measure named overruns and make blocking work fail deterministically in the
handler guard. Cover the complete identified offender set on Windows, macOS,
and Linux.

## User Stories

1. As a BlindPilot user, I want shortcut and menu actions to return promptly,
   so that focus, speech, repainting, and queued input remain responsive.
2. As a user searching all conversations, I want transcript scanning to run in
   the background with progress and cancellation, so that a large history does
   not stall the window.
3. As a user opening changed files, I want Git discovery to run in the
   background and reuse warm results, so that repeated opens are immediate.
4. As a user opening slash commands, I want backend probes to run in the
   background and reuse warm results, so that the picker opens promptly.
5. As a Claude user opening the files picker, I want file discovery and
   front-matter reads to be warmed in the background, so that repeated opens
   stay responsive.
6. As a user opening a past conversation, I want transcript loading to happen
   off the GUI thread while progress remains available, so that large or remote
   histories do not stall the app.
7. As a user closing or resetting a conversation, I want its held process
   forgotten immediately and stopped asynchronously, so that the next turn
   cannot reuse a process being torn down and the UI remains responsive.
8. As a user quitting BlindPilot, I want the window to close promptly while
   BlindPilot safely cleans up only its own backend process trees, so that no
   unrelated process is terminated and provider processes do not outlive the
   application.
9. As a user invoking repository, PowerShell, setup, Hermes, or Muse.ai
   actions, I want blocking process and network work outside GUI callbacks, so
   that the full handler surface remains responsive from launch onward.
10. As a maintainer, I want every menu and accelerator handler covered by a
    structural guard and timing instrumentation, so that future offenders fail
    the regression gate instead of silently reintroducing stalls.
11. As a screen-reader user, I want progress, completion, failure, and focus
    changes to remain accessible while work runs asynchronously, so that the
    responsive UI remains understandable and keyboard-operable.

## Acceptance Criteria

- [ ] Every identified offender returns control to the event loop promptly by
  moving potentially slow work to a worker or warm cache.
- [ ] A structural guard covers every GUI callback on Windows, macOS, and Linux;
  process starts, thread joins, event waits, or network access from a callback
  fail the guard with no allowlist.
- [ ] Timing instrumentation covers GUI callbacks, timers, and `CallAfter`;
  strict 50 ms checks are opt-in, with a generous CI bound.
- [ ] Async results, errors, cancellation, progress, and focus updates reach
  only the owning live UI and remain keyboard/screen-reader accessible.
- [ ] Quit closes the window promptly and safely cleans up only BlindPilot-owned
  backend process trees.

## Implementation Decisions

- The GUI-thread budget is 50 ms per callback. Instrumentation records named
  overruns in a bounded buffer and DEBUG logs; strict timing assertions are
  enabled behind `BLINDPILOT_STRICT_TIMING`.
- The test guard invokes handlers at their highest practical seam and fails on
  process starts, thread joins, event waits, and network entry points. Small
  bounded in-memory and filesystem operations remain allowed.
- The initial seams are menu and accelerator dispatch. The final coverage sweep
  enumerates every built handler, with no allowlist.
- Asynchronous work reports back through the existing GUI event/callback
  pattern. Results must be discarded when their dialog, selection, conversation,
  or request is no longer current.
- Long history operations retain their progress UI, cancellation, keyboard
  navigation, spoken status, and focus restoration.
- Changed-file, slash-command, and Claude-files discovery use warm per-context
  results with explicit refresh where already agreed in the handoff.
- A per-conversation process drop removes the held process from the pool
  synchronously, then stops it off-thread. Shutdown coordinates pending stops
  after the window closes.
- Quit cleanup must never select a process by PID alone. On Windows, investigate
  assigning BlindPilot-owned backend roots to Job Objects at creation so owned
  descendants remain covered when parents exit. Preserve a safe platform
  strategy for macOS/Linux and fail closed when ownership cannot be verified.
  Do not use a broad `taskkill /T` against a reused PID.
- Scope includes the full offender inventory recovered from the grilled
  session: per-conversation process drops; quit's join and cleanup path;
  all-conversation search; changed-file Git calls; slash-command probes;
  Hermes-session reload; Muse.ai dialog actions and backend selection during
  startup; history loading from Open, links, and `/resume`; FreeBuff prewarm on
  tab creation; Claude-files discovery; repository list/clone; desktop-shortcut
  PowerShell; setup CLI checks; and synchronous cross-process `announce` calls.
  Any additional offender found by the all-handler guard is also in scope.
- Speech changes are limited to measuring existing behavior; do not add new
  narration solely for this work.

## Testing Decisions

- Test observable behavior: callbacks return before controlled slow work ends;
  results update the right live UI; stale results do not overwrite new state;
  progress, cancellation, focus, and errors remain keyboard- and screen-reader
  accessible.
- Exercise menu and accelerator callbacks through their binding seams. Use
  controlled worker events to prove the GUI callback returns before work
  completes. Exercise process ownership with injected primitives and
  platform-gated integration checks where available.
- Reuse the GUI budget and blocking-contract tests, existing history and picker
  tests, asynchronous cancel tests, and held-process pool tests.
- Run focused checks after each slice and the repository gate after the final
  coverage sweep. A real screen-reader/device check is required for changed
  focus, progress, or spoken status behavior when the environment is available.

## Verification

- `python -m ruff check .`
- `python -m ruff format --check .`
- `python -m pytest -q -W error`
- Run focused tests per offender. Run strict 50 ms timing only when
  `BLINDPILOT_STRICT_TIMING` is enabled on a suitable test machine; CI timing
  uses a generous bound to avoid load-dependent flakes.
- Exercise Windows process ownership and assistive-technology behavior on
  available Windows/NVDA hardware; unit tests on other platforms do not verify
  those boundaries.

## Out of Scope

- Fixing backend protocol reliability unrelated to GUI-thread stalls.
- Changing the 50 ms budget or adding a user-facing performance dashboard.
- Killing arbitrary descendant processes that BlindPilot did not create or own.
- Rewriting unrelated backend launch or dialog architecture.

## Further Notes

- The GUI budget and timing harness landed in commit `a49dbf5` on
  `fix/gui-thread-stalls`.
- The async per-panel drop change was committed as `dd1e4cc` on
  `fix/gui-thread-stalls`; focused tests passed (53 passed). The parser changes
  in `agent_backends.py` and `tests/test_trailing_question.py` are separate and
  must be preserved.
- The prior full-suite run timed out in an updater subprocess test after an
  early failure; record that failure before changing its code and rerun the
  relevant gate after the final edit.
- The original grilling session selected a detached helper and marker sweep. A
  later continuation rejected PID-only identity cleanup and selected Windows
  Job Objects as the safer quit-cleanup direction. Resolve Job Object ownership
  across backend spawn paths before implementing quit cleanup.
