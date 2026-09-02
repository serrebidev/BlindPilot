# BlindPilot 0.19.1

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This is a point release carrying one fix, contributed from outside: the setup wizard
said the backend you had just chosen twice, and now says it once.

## The backend you chose, said twice

Choosing a backend in the first-run wizard drives everything that follows it — the
sign-in instructions, the install step, the finish page are all rewritten for the
backend the picker now holds. The picker is a native choice control, so the change of
value is announced by the control itself: the screen reader reads the newly focused
entry as part of its own behaviour, the same way it reads any combo box on the machine.

The wizard then said it again. `_on_backend_choice` ended with the application's own
announcement, "Backend selected: …", sent through the speech channel `announce()` uses
— on Windows accessible_output2, a stream separate from the control's native
announcement, so the two do not merge into one utterance. A screen-reader user heard
the same backend twice on every change of the picker: once from Windows, once from
BlindPilot, with nothing in between worth the interruption.

The line is gone. It was the only announcement in the application that narrated a
native control's own selection — the others all report events the machine has no other
way to convey: a backend asking a question, a status check starting, an error. And
there was no programmatic selection relying on it: `_on_backend_choice` has exactly one
caller, the choice event itself, which fires only when the user changes the picker.

## Verification

pytest 767 passed, 2 skipped; ruff check, ruff format --check, mypy, `--startup-smoke`
and `--startup-gui-smoke` all clean at the release commit.
