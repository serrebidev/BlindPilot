# BlindPilot 0.20.6

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release is what the last one's standard was for. The New Session dialog was held
to the same captured-log bar as the install wizard — no sentence may name something it
is not about, context a dialog is built around cannot be silent, and nothing may be
spoken twice — and three findings came out of it, each the same class as one the wizard
had already given us.

## The name-field help stopped claiming Hermes names every session

"Leave it empty to let Hermes name it after your first message" was true for a Hermes
session and false for a local Claude, Codex, FreeBuff or opencode one, where the first
message names the tab and Hermes is not involved in naming anything at all — the same
class of false statement as the wizard's "npm could not be installed" on a machine that
has npm. The help now reads "Leave it empty to let the first message name it", which is
true in every mode for every backend.

## The remote-mode context is spoken

The dialog's only explanation of its remote shape — the session will run on another
machine, so folders on this computer do not apply — was a StaticText, and no screen
reader announces a StaticText when a dialog opens. The person landed on the name field
having heard none of the context that makes the dialog's shape make sense. The one
sentence that explains it is now spoken the moment the dialog opens, before the field
takes the focus. A local dialog says nothing extra, which is its own guard.

## A refusal is said once

A folder the dialog could not use was announced explicitly and then shown in a modal
dialog, which announces the same sentence again — the duplicate-speech class the
backend-selection announcement was removed for. The refusal is now said by the dialog
that asks for a correction, and nothing more.

## The suite is hermetic again, and fully green

The official Hermes installer sets `HERMES_HOME` persistently, and two test fixtures
that point every history store at a throwaway home never cleared it — so on a machine
where the installer has run, fifteen history tests and one settings-file test answered
with the machine's own conversations instead of the ones the tests had built. The
fixtures now delete `HERMES_HOME` the way they already delete `CODEX_HOME` and the
opencode variables, and the suite is green with `-W error` for the first time since
Hermes was installed here.

One real leak surfaced by the same standard: the Hermes login path caught an
`HTTPError` without closing the response body it carries, which warns "Implicitly
cleaning up" when it is collected — a failure under `-W error`, and an open response
stream in real use until then. It is closed now, deterministically: the test that
exposed it passes every run instead of one in six.

## Verification

Five new tests, all failing-first against the three dialog findings. Full suite:
**1084 passed, 3 skipped, zero failures** with `-W error` — the first fully green run
on this machine since the Hermes install. ruff, format and mypy clean; both startup
smokes clean.