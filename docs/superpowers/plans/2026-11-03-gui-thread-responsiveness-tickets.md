# GUI-thread responsiveness ticket plan

Spec: [GitHub issue #6](https://github.com/thebetterfarhan/BlindPilot/issues/6). Local copy: [spec](../specs/2026-11-03-gui-thread-responsiveness.md).

This restores the decisions in grilling transcript `059b518a-286e-4c8a-80ff-d144bfc51230` and folds in useful delivery details from the earlier ten-item breakdown: warm results with explicit refresh, an invariant/ADR record, and a final all-handler sweep.

## 01. GUI-thread budget and blocking guard

**Status:** Complete in `a49dbf5` (glossary in `a5a2e49`).

## 02. Drop held conversation processes off the GUI thread

**Ticket:** [#7](https://github.com/thebetterfarhan/BlindPilot/issues/7)

**Status:** Implemented in `dd1e4cc`; focused check passed (`53 passed`). The
full repository gate and real screen-reader checks remain outstanding.

**Work:** Forget a dropped held process immediately, stop it on a worker, and ensure the next turn starts fresh.

## 03. Close promptly and safely clean up backend process trees

**Ticket:** [#8](https://github.com/thebetterfarhan/BlindPilot/issues/8)

**Blocked by:** #7 for coordination with pending asynchronous stops.

**Work:** Close promptly; clean up only BlindPilot-owned process trees off-thread. Resolve Windows Job Object ownership at process creation; define safe macOS/Linux behavior; never kill an unverifiable process by PID.

**Status:** Implemented in `facc89b`; focused integration suite passed (`270
passed, 1 skipped`), Ruff checks and `git diff --check` passed. macOS/Linux
process-tree cleanup and real screen-reader/device behavior remain unverified.

## 04. Move Ctrl+Shift+S conversation search off-thread

**Ticket:** [#9](https://github.com/thebetterfarhan/BlindPilot/issues/9)

**Work:** Scan up to 1,000 transcripts on a worker; retain cancellable progress UI and fill results when ready.

**Status:** Implemented in `151b03a`; focused suite passed (`97 passed`), Ruff
checks and `git diff --check` passed. Real screen-reader/device behavior remains
unverified.

## 05. Move Ctrl+Shift+D changed-file discovery off-thread

**Ticket:** [#10](https://github.com/thebetterfarhan/BlindPilot/issues/10)

**Work:** Run Git discovery on a worker, announce and fill results, warm per-folder results, and support explicit refresh.

**Status:** Implemented in `a15c072`; focused suite passed (`74 passed`), Ruff
checks and `git diff --check` passed. Discovery and selected-file reads run off
the GUI thread. Real screen-reader/device behavior remains unverified.

## 06. Move Ctrl+/ slash-command probes off-thread

**Ticket:** [#11](https://github.com/thebetterfarhan/BlindPilot/issues/11)

**Work:** Open picker promptly, probe on a worker, fill results, warm per-process results, and support explicit refresh.

**Status:** Implemented in `d7c6745`; focused suite passed (`126 passed`), Ruff
checks and `git diff --check` passed. Real screen-reader/device behavior remains
unverified.

## 07. Move Hermes session reload off-thread

**Ticket:** [#12](https://github.com/thebetterfarhan/BlindPilot/issues/12)

**Work:** Keep Hermes session reload from blocking its dialog; announce and fill results when ready.

**Status:** Implemented in `0d44645`; focused UI and callback-guard suite passed (49 passed), Ruff and `git diff --check` passed. Real screen-reader/device behavior remains unverified.

## 08. Move Muse.ai dialog actions off-thread

**Ticket:** [#13](https://github.com/thebetterfarhan/BlindPilot/issues/13)

**Work:** Keep Muse.ai remote and CLI actions from blocking their dialogs; announce and fill results when ready.

**Status:** Implemented in `9e6e368`; Muse.ai dialog tests passed (43 passed), Ruff and `git diff --check` passed. The combined GUI timing check had its known load-sensitive failure, which passed alone on rerun. Real screen-reader/device behavior remains unverified.

## 09. Move Muse.ai backend selection off the startup path

**Ticket:** [#14](https://github.com/thebetterfarhan/BlindPilot/issues/14)

**Work:** Avoid synchronous Muse.ai CLI work during window construction/backend selection and fill the result asynchronously.

**Status:** Implemented in `a3ca5db`; main-chat discovery and history loading now
run on a worker when Muse.ai is selected, with spoken loading/error states,
pending-send preservation, and stale-result guards. Deferred sends also check
panel lifetime, generation, and backend before running. Focused Muse.ai
main-chat tests passed (11 passed); Ruff, formatting, and `git diff --check`
passed. Independent review found no remaining issue. Real screen-reader/device
behavior remains unverified.

## 10. Move history Open, links, and `/resume` loading off-thread

**Ticket:** [#15](https://github.com/thebetterfarhan/BlindPilot/issues/15)

**Work:** Load turns on a worker and keep the agreed progress dialog while results arrive.

**Status:** Implemented in `c59676a`; focused history, Muse.ai, link, and
callback-lifetime suite passed (`28 passed`), Ruff, compileall, and
`git diff --check` passed. A modeless, keyboard-cancelable progress dialog
keeps history loading responsive. Real screen-reader/device behavior remains
unverified.

## 11. Warm FreeBuff discovery outside tab creation

**Ticket:** [#16](https://github.com/thebetterfarhan/BlindPilot/issues/16)

**Work:** Run login-shell executable discovery off-thread so creating or switching to a tab stays responsive.

**Status:** Implemented in `1f145de`; CLI discovery now runs in the prewarm
worker and stale results are discarded before launch. Focused prewarm tests,
Ruff, formatting, compileall, and `git diff --check` passed. The later full
gate passed after the baseline test-fixture repair in `8208075` (`2732 passed,
17 skipped`). Real screen-reader/device behavior remains unverified.

## 12. Warm Ctrl+Shift+I Claude-files discovery off-thread

**Ticket:** [#17](https://github.com/thebetterfarhan/BlindPilot/issues/17)

**Work:** Read globs and front matter once per folder on a worker, cache results, and support explicit refresh.

**Status:** Implemented in `01f72d2`; discovery runs on a worker, caches by
normalized folder, refreshes explicitly, reloads after edits, and discards
stale results. The full gate passed (`2732 passed, 17 skipped`); Ruff,
formatting, compileall, and `git diff --check` passed. Real screen-reader/device
behavior remains unverified.

## 13. Move repository list/clone off-thread

**Ticket:** [#18](https://github.com/thebetterfarhan/BlindPilot/issues/18)

**Work:** Keep Ctrl+T responsive while repository listing or cloning runs, with accessible progress and completion.

**Status:** Implemented on `agent/ticket-18`; repository listing and cloning
run on workers with cancellable progress, process-tree cleanup, stale-result
guards, and accessible status/error reporting. Full gate passed (`2739 passed,
17 skipped`); Ruff check, formatting, compileall, and `git diff --check` passed.
Real screen-reader/device behavior remains unverified.

## 14. Move desktop-shortcut PowerShell off-thread

**Ticket:** [#19](https://github.com/thebetterfarhan/BlindPilot/issues/19)

**Work:** Return from the shortcut handler promptly and report PowerShell result or error accessibly.

**Status:** Implemented on `agent/ticket-19`; shortcut creation runs on a worker,
reports progress and completion/errors through spoken status, prevents duplicate
requests, and invalidates callbacks on close. Full gate passed (`2743 passed,
17 skipped`); focused tests, Ruff, formatting, compileall, and `git diff --check`
passed. Real screen-reader/device behavior remains unverified.

## 15. Move setup wizard CLI checks off-thread

**Ticket:** [#20](https://github.com/thebetterfarhan/BlindPilot/issues/20)

**Work:** Keep setup responsive while backend CLI checks run and fill their outcomes asynchronously.

**Status:** Implemented on `agent/ticket-20`; CLI discovery, installer
availability, and persistent-PATH checks run in a worker. Spoken progress and
error/retry states are shown, and stale results are discarded after navigation
or close. Full gate passed (`2747 passed, 17 skipped`); focused tests, Ruff,
formatting, compileall, and `git diff --check` passed. Real screen-reader/device
behavior remains unverified.

## 16. Move cross-process `announce` work off-thread

**Ticket:** [#21](https://github.com/thebetterfarhan/BlindPilot/issues/21)

**Work:** Ensure synchronous cross-process status delivery cannot block a GUI callback while preserving status order and screen-reader feedback.

**Status:** Implemented on `agent/ticket-21`; Windows speaker initialization
and speech delivery run on a single FIFO worker, leaving GUI callers to enqueue
announcements. Focused speech tests passed (13 passed); full gate passed (`2750 passed, 17 skipped`),
Ruff, formatting, compileall, and `git diff --check` passed. Independent review
found no blocking issue. Real screen-reader/device behavior remains unverified.

## 17. Record the GUI-thread rule and quit decision

**Ticket:** [#22](https://github.com/thebetterfarhan/BlindPilot/issues/22)

**Blocked by:** #8.

**Work:** Add the no-blocking rule and 50 ms budget to project guidance and document the implemented exit strategy in an ADR.

**Status:** Contributor guidance now sets the 50 ms GUI-callback budget and
routes shutdown and process ownership to ADR 0001. The ADR records the existing
nonblocking close path, shared three-second cleanup deadline, Windows Job Object
ownership, and verified POSIX process-group behavior. Documentation checks
passed; platform process integration remains unverified.

## 18. Cover every handler and run the final gate

**Ticket:** [#23](https://github.com/thebetterfarhan/BlindPilot/issues/23)

**Blocked by:** #7–#22.

**Work:** Enumerate every menu, accelerator, timer, and `CallAfter` callback; exercise the blocking guard with no allowlist; run the full gate and record unavailable platform and assistive-technology checks.

**Status:** Callback measurement covers menu, accelerator, timer, `CallAfter`,
and `CallLater` handlers; the blocking guard is tested without handler
exceptions. Focused callback tests passed (20 passed). The full suite passed
(`2790 passed, 17 skipped`); Ruff check and formatting, mypy, compileall, and
`git diff --check` passed. Independent review found no blocking issue. Other
platform and real screen-reader/device behavior remain unverified.
