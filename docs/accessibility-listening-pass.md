# Accessibility listening pass

The GUI-thread responsiveness work (issues #6–#23) closed with one caveat
repeated on every checkpoint: *real screen-reader/device behavior remains
unverified*. Automated tests cannot hear the app. This is the manual pass that
closes it, and it cannot be delegated to an agent.

Run it with a screen reader on, using the keyboard only. Record the date, the
reader and its version, and the build under test. Accept a row only when the
app says something true and says it once.

## What to press

| Flow | Steps | Expected to hear | Known risk |
|---|---|---|---|
| New session with a worktree | New Session, tick worktree, type a branch, OK | Nothing during the checkout, then the session opens | Silence and a frozen window if the checkout is on the GUI thread |
| Setup wizard, sign-in step | Next until step 3 | "Checking whether … is signed in…" | A stall before that line if CLI discovery runs on the GUI thread |
| Chat: send during a live turn | Start a reply, then Send again | "A response is already being generated." | Silence: the status bar changes but nothing is spoken |
| Chat mode, first open | Switch to Chat | "…were imported." when a legacy database exists | A freeze before the tab appears, with no announcement |
| Accounts: delete | Select an account, Delete, Yes | A spoken confirmation | Silence, and focus on a row that is now a different account |
| Conversations: delete | Delete a conversation | A spoken confirmation | Silence, and the list reshuffles under focus |
| Any long action | e.g. refresh models, install a CLI | Progress, then completion | Anything that only changes the status bar |

## While it runs

`over_budget_work()` (`blindpilot_app.py:452`) names any handler that exceeds
the 50 ms GUI budget, so a freeze has a name rather than a shrug. Note the
handler it prints alongside the moment it happened.

## Recording the result

Add a dated section to `docs/code-audit/` with, for each row: pass, fail, or not
run; the reader; and one sentence of what was actually heard. A failure is
written as the sentence the app should have said but did not — that is the
specification for the fix.
