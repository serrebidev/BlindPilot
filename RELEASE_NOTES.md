# BlindPilot 0.18.0

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release is four things the screen reader said at the wrong moment — an
accessibility audit by @blindndangerous, each fix verified against the code before
anything was written, each with a failing test first (PR #23) — and one hole in the
audit's own rewrite that the release caught before it shipped.

## Stop re-announcing the row somebody is reading

`_refresh_list` rebuilt the whole control every time it ran. A rebuild throws the
contents away, which loses the selection, so the row being read had to be put back
afterwards — and putting it back is the part that speaks. Setting a native list box's
selection fires the accessibility event NVDA reads the row from; moving the text
view's insertion point is a caret move it reads the line from.

It ran once per drained batch, so during a turn that was up to every twenty
milliseconds. The row somebody had navigated to was read back to them over and over,
underneath the narration of the turn itself. The comment above it said it existed to
stop incoming output disrupting somebody reading older rows, which is exactly what it
did instead.

Rows are only ever appended during a turn, so the usual case appends now: no rebuild,
nothing to restore, nothing that speaks. A refresh that adds nothing touches nothing.
When the rows genuinely change shape — a search, a new turn, a response replaced by
its parsed form — it still rebuilds and still restores the selection, because there it
really was lost.

### The append that trusted its own record

The append decision compared the incoming labels against the model's record of what
it had displayed. That record is not what the control shows, and two ordinary paths
break the equality:

- `clear_conversation` empties the lists while the control still holds the old
  transcript — Ctrl+Shift+N, the menu item, `/clear`, `/new`. The cleared conversation
  stayed on screen, and the next turn appended underneath it.
- `apply_view_mode` shows whichever responses control Options asks for, and only the
  visible one is ever filled. Switching to the text view came up blank whenever
  nothing new had arrived since the last refresh.

Both rebuilt on 0.17.0, which is why the rebuild branch is still there. The append now
happens only when the control's own count agrees with the record; anything else falls
to the rebuild, which restores 0.17.0's behaviour everywhere the invariant does not
hold. `restore_history` resets the record the same way and survives only because it
always runs on a brand-new panel; the count check covers it anyway.

## Stop reading the prompt back at somebody who is typing it

Dictation puts text in the prompt with no keystrokes, so nothing speaks it. Hence a
pause timer. But `EVT_TEXT` fires for typing too, so pausing to think mid-sentence
read the entire prompt back over the top of the character echo the screen reader had
been giving all along. The longer the prompt, the longer the interruption, and the
only way to avoid it was to keep typing.

A keystroke adds one character and has already been spoken. Dictation and paste arrive
in bulk and have not been. Only bulk schedules a read-back now, typing afterwards
cancels a pending one, and it reads what arrived rather than the whole field — so
dictating a second sentence onto a long prompt no longer replays the first.

## Say what a search did

`open_find` reported its outcome with `_set_status`, which writes the status bar and
nothing else. No screen reader reads a status bar it was not asked to.

With hits there was at least a sign of life: focus moved to the first one. With no
hits focus did not move either, so the list quietly emptied and nothing said so —
indistinguishable from the application ignoring the keystroke, which makes searching
again the natural next move. It is announced now, and still mirrored to the status
bar.

## Let Enter on Cancel cancel

The past-conversations dialog binds `EVT_CHAR_HOOK`, which fires before the focused
control sees the key, and treated Enter as "open the selected conversation" wherever
it came from. Right in the filter box, right in the list, wrong on the two buttons.

Tabbing to Cancel and pressing Enter — the ordinary way to leave a dialog, and the
only way available to somebody who cannot see where focus has landed — opened a
conversation. The dialog closed either way, so the only sign was what appeared
afterwards. Enter is handed back when the focused control is a button; Escape is
unchanged. The other five `EVT_CHAR_HOOK` dialogs were checked and none of them
intercept Enter.

## Verification

Rebased on 0.17.0. pytest 664 passed, 2 skipped; ruff check, ruff format --check, mypy,
`--startup-smoke` and `--startup-gui-smoke` all clean. The audit's own test needed its
stub updated rather than its assertions: it held a list box that was empty while rows
were displayed, which a real control cannot be, and the count check asks the control
what it is showing.
