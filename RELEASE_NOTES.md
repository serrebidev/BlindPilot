# BlindPilot 0.8.1

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release fixes five failures that happened in silence. It follows 0.8.0, which added
Chat mode, replaced the session combo box with a real tab strip, and rebuilt the updater
as a single accessible dialog.

## A crashed turn says what happened

The Codex, FreeBuff, and opencode run loops caught nothing. Their cleanup ran either way,
turning Send back on and stopping the progress earcon, so a turn that crashed looked
exactly like a turn that finished — apart from the answer never arriving. The explanation
went to a standard error the packaged windowed build does not have.

All three now report the failure in a row you can read and hear. Codex and FreeBuff also
gained the guard opencode already had, so a turn that already said why it stopped and then
failed while cleaning up does not speak a second error over the top of the first.

## FreeBuff gives its terminal back

Teardown was written as a fallback chain: terminate, and close only if terminate raised.
Terminate works, so the close never ran, and the handle FreeBuff was reached through — a
ConPTY on Windows, pexpect's master file descriptor elsewhere — was left open. This runs
at the end of every single turn, so the handles built up for as long as the session
lasted. Both calls are now made, because both are wanted.

## Errors are spoken, not just displayed

Ten error messages went to the status bar and stopped there, and neither NVDA nor JAWS
reads a status-bar change on its own. A copy that succeeded announced itself; a copy that
failed said nothing at all, leaving silence and a clipboard still holding whatever was in
it before.

Steering with nothing running, stopping with nothing running, steering a run that just
finished, every clipboard failure, a code save that could not write, and copying an empty
conversation are all spoken now. They still appear in the status bar, so nothing is lost
from the display.

## Enter sends the answer in the question dialog

The "type your own answer" box uses `TE_PROCESS_ENTER`, which takes Enter away from the
dialog's default button and hands it to the box — where nothing was listening, so the key
did nothing. This is the dialog that opens unannounced in the middle of a run and holds
the turn until it is answered, and the box is exactly where focus lands after choosing
"Other". Enter now sends, applying the same validation the button does.

## Sign-in addresses are checked before they are opened

opencode hands back an address and expects the client to open it. That address comes from
the provider catalogue behind opencode, describing close to two hundred providers, not
from opencode itself. On Windows the platform opener is the default protocol handler, so
`file:` would open whatever sits at that path, including one on a network share, and
`search-ms:` or `ms-msdt:` would be handed to a program of its own.

Both sign-in paths now go through one opener that accepts only `http` and `https`. An
address that is refused is still spoken and shown, so a machine with no default browser
is not left with nothing to go on.
