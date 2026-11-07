# Scan remediation: eight defects from the 2026-11-07 code scan

A code scan of 97k lines across three areas produced ten findings. Three were
fixed immediately in `3989e63` (installer hang, silent refused Send, sign-in
discovery on the GUI thread). This spec covers the remaining eight, which are
filed as GitHub issues #51–#58.

## Scope

| Issue | Defect | Files |
|---|---|---|
| #51 | A cancelled sign-in can orphan the CLI process | `agent_backends.py` |
| #52 | CLI discovery can pick an executable from the current directory | `agent_backends.py`, `installers.py` |
| #53 | Remote Control records accumulate in the settings file | `blindpilot_app.py` |
| #54 | A damaged settings file is silently replaced by defaults | `accessible_ai/storage/preferences.py`, `blindpilot_app.py` |
| #55 | Deleting an account is announced only in the log | `accessible_ai/ui/accounts.py` |
| #56 | First switch to Chat copies the legacy database on the GUI thread | `chat_integration.py`, `blindpilot_app.py` |
| #57 | Creating a worktree Stalls the New Session dialog | `blindpilot_app.py` |
| #58 | Deleting a conversation is silent and moves focus | `accessible_ai/ui/conversations.py` |

## Constraints

- **The 50 ms GUI budget holds.** Any new slow path runs on a worker and speaks
  before it starts. `over_budget_work()` names a handler that exceeds it.
- **Accessibility is not optional.** A new user-visible outcome is spoken once,
  and focus lands somewhere announced.
- **No behaviour change beyond the ticket.** Where a ticket needs a design
  decision (#56, #57), the decision is recorded in the ticket before the code.
- **Every ticket closes with the full suite green** (`python -m pytest -q -W
  error`), `ruff check` and `ruff format` clean, and mypy passing through the
  repo's own pre-commit.

## Task graph

Tickets are independent in intent but collide on files, so the graph is shaped by
write surface, not by feature:

```
wave 1 (disjoint):   #54 preferences.py      #55 accounts.py      #58 conversations.py
wave 2 (disjoint):   #53 blindpilot_app.py   #52 agent_backends, installers
wave 3 (serial):     #51 agent_backends.py   after #52 (same file)
wave 4 (serial):     #57 blindpilot_app.py   after #53 (same file, needs a design decision)
                     #56 chat_integration.py after #57 (needs the same dialog/panel seam)
```

Wave 1 touches three files nobody else touches. Wave 2 is two disjoint files.
Waves 3 and 4 are serialized because they share a file with the wave before.

## Verification

- `python -m pytest -q -W error` — the whole suite, which ran 2900 passed /
  17 skipped before this work.
- `python -m ruff check .`, `python -m ruff format --check .`, and the
  pre-commit hooks (ruff, format, mypy, diff checks).
- The spoken half of #55 and #58 cannot be proved by tests: `docs/accessibility-listening-pass.md`
  gains a row for each and stays open until someone runs it with a reader.

## Out of scope

- The scanner's remaining hypotheses (muse.ai poll chain outliving its tab,
  macOS PID kill, FreeBuff token URL, PYTHONPATH shadowing). Reported, not filed.
- Any change to the upstream fork relationship.
