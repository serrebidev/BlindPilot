# BlindPilot 0.19.2

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This is a point release about one thing: Shift+Tab into the session tab strip was a
place you could get into and not usefully get out of.

## Arrowing the strip threw you out of it

The strip is a native tab control, so the arrow keys are how you walk it, and the
screen reader announces the conversation and "tab 2 of 4" itself. Changing the tab
changes the visible conversation — and showing that conversation focused it. wxSimplebook
does that from C++ on every selection change, unconditionally, so neither the page nor
the book can decline it, and the first arrow press dropped the user into the prompt of
the tab they had just arrowed onto. The second arrow press never reached the strip at
all, because focus was no longer there.

This was fixed once, in v0.5.0, back when the strip and the pages were the same
control: a wxNotebook does not take focus off its own tab area. Separating the strip
from the pages in v0.8.0 — so that entering a conversation no longer made Windows
announce "tab control" — brought the behaviour back through the new page container.

The session change now runs with the incoming page disabled, which is the only way to
refuse the focus the book insists on giving it, and focus is asked back onto the strip
afterwards in case a platform refuses differently. A page's own controls keep whatever
enabled state they had. The strip keeps focus through arrow keys, Ctrl+Tab pressed
while inside it, and Cmd+1..9; from anywhere else, switching sessions still announces
the session and lands in its prompt, as before.

## A routed Tab that moved nothing was still eaten

Every boundary in Agent mode — Mode picker to tab strip, tab strip into the
conversation, Permission mode back out to Mode — is crossed by hand, from a
frame-level character hook that has to see Tab before wxWidgets performs its own
navigation. Claiming the key and then failing to move focus is a keyboard trap: the
keypress is consumed, native traversal never runs, and the control you are on is the
control you stay on however many times you press Tab. A hidden or disabled control
accepts a focus request by doing nothing at all, so this was reachable rather than
theoretical.

Focus is now compared after each boundary move, and a move that did not happen hands
the key back to wxWidgets, which will find somewhere ordinary to go. In the one case
that could produce it — Tab out of the strip into a responses list that is not the
visible view — the conversation's Prompt is used instead.

## Verification

pytest 772 passed, 2 skipped, including four new tests covering the strip keeping
focus through a session change, a session change from elsewhere still landing in the
conversation, a boundary move that moved nothing reporting that it did not, and the
fall-through to the Prompt. ruff check, ruff format --check, mypy, `--startup-smoke`
and `--startup-gui-smoke` all clean.

Keyboard behaviour was checked with real keystrokes driven into the running window —
Tab and Shift+Tab all the way round the cycle in both directions, with one tab and
with several, with an empty and a populated responses list, and the arrow keys along
the strip.
