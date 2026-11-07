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
| New session with a worktree | New Session, tick worktree, type a branch, OK | "Creating a worktree for fix." as the dialog closes, then "New session: <name>" when the tab opens | The tab opening long after the sentence, so the two sound unrelated; a failed checkout must speak git's own reason |
| Setup wizard, sign-in step | Next until step 3 | "Checking whether … is signed in…" | A stall before that line if CLI discovery runs on the GUI thread |
| Chat: send during a live turn | Start a reply, then Send again | "A response is already being generated." | Silence: the status bar changes but nothing is spoken |
| Chat mode, first open | Switch to Chat | "…were imported." in the status bar when a legacy database was copied | The copy runs at startup now; opening Chat before it finishes waits with nothing said |
| Accounts: delete | Select an account, Delete, Yes | "Deleted Beta. Alpha is selected.", or "Deleted Beta." when it was the last | The wrong name if the list re-sorts under focus |
| Conversations: delete | Delete a conversation | "Deleted <title>. <next title> is selected." | A long title running into the next sentence |
| Damaged settings file | Corrupt `config.json`, then start the app | "Your settings file could not be read. It was kept as config.json.broken-<stamp>, and BlindPilot has started from the defaults." | Said during startup, before the window takes focus, so it may go unheard |
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
