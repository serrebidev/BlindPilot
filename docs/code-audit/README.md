# Code audit records

These files are **dated snapshots of audits**, not a description of the current
code. Each was written against the tree at the time it names; the code has moved
since.

## How to read them

- `applied-*.md` records what was actually changed in response to an audit,
  including what was skipped and why.
- The report files are the original findings. A finding is **not** open just
  because it appears in one.
- Before acting on a finding, re-check it against the current tree. If it still
  applies, it belongs in the tracker, not here.

## Where each report went

| Report | Applied record |
|---|---|
| `app-part1.md` | `applied-blindpilot_app.md` |
| `app-part2.md` | `applied-blindpilot_app.md` |
| `app-part3-and-small-modules.md` | `applied-blindpilot_app.md`, `applied-accessible-ai-small-modules.md` |
| `accessible-ai-tests-ci.md` | `applied-accessible-ai-small-modules.md` |
| `backends-and-pool.md` | `applied-backends.md` |
| `hermes-and-updater.md` | `applied-hermes-updater.md` |
| `freebuff.md` | `applied-backends.md`, partly |
| `docs-unslop.md` | `applied-docs.md` |

## Open work

Nothing from these reports is open as of the structure pass. Everything the
reports named was either applied, recorded in an `applied-*.md` file, or closed
as dead code. The work that finished it is merged on the fork
(`thebetterfarhan/BlindPilot`):

- spec #32 — backend and app structure, closed
- #34 — installation, portable Node and PATH extraction, closed
- #39 — type checking widened to every production module, closed
- #40 — platform and release decision, closed
- #41 — this record, closed

New findings belong in the tracker, not here. This file is a dated snapshot:
read the date on a report before acting on it.
