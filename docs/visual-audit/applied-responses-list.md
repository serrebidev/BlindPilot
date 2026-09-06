# Applied: the wrapping Responses list

What the third visual pull request changed, how it was tested, and what NVDA
said before and after. Spec: `04-responses-list-design.md`. Plan:
`05-responses-list-plan.md`. Branch `visual/responses-list` on top of
v0.21.6.

## What changed, by file

`conversation_list.py` (new)

- `RowStyle`, `style_for(kind)` and `as_rows(items)`. Every row kind maps to
  bold, muted, mono, indented or error. Bare strings become prose rows so the
  other pickers can keep passing labels.
- `ConversationList(wx.VListBox)`. Rows wrap to the client width with
  `wx.lib.wordwrap`, heights are cached per (row, width) and the cache is
  cleared before the row count changes and on resize. Padding, indent and the
  minimum text width all go through `FromDIP`. Colours come from
  `wx.SystemSettings` only: highlight text on the selected row, grey text for
  reasoning, the info background behind an unselected error row. The code font
  is the system font's size in the teletype family.
- `RowsAccessible(wx.Accessible)`. The control is a list, each row a list
  item with a 1-based child id. Name is the row label, description is empty,
  states carry selectable, focusable, selected, focused and invisible.
  Selection changes only raise `ACC_EVENT_OBJECT_FOCUS` then
  `ACC_EVENT_OBJECT_SELECTION` for the selected row. Focus with nothing
  selected selects row 0 first, as the native list did.

`blindpilot_app.py`

- `SessionPanel.responses` is a `ConversationList`. `_refresh_list` hands it
  `_displayed` rows, not labels; the append fast path still compares labels
  and calls `_append_rows(rows)`.
- The read-only text view lost `wx.TE_DONTWRAP`. Rows are found by character
  offset (`_row_starts`, `_row_at`, `_starts_of`) instead of line number,
  because a wrapped control counts wrapped lines.
- A `wx.ActivityIndicator` sits beside Stop, shown at every
  `start_progress()` and hidden at every `stop_progress()` in `SessionPanel`
  (nine sites). It has no name and never takes focus.
- `HistoryDialog` and `HermesSessionsDialog` build a `ConversationList`
  named "Conversations". The slash picker is a small `SlashCommandDialog` on
  the same control in place of the stock `wx.SingleChoiceDialog`, with the
  same title, message, Enter, Escape and double click.

Tests: `tests/test_conversation_list.py` (styles, rows, measure cache, fonts,
accessible object, focus events, the pickers),
`tests/test_responses_text_mode.py` (offsets through a rebuild and an append,
folded labels, caret placement), `tests/test_slash_command_dialog_keys.py`
(the picker's keys), plus one test each in `tests/test_live_rows.py` (rows,
not labels, reach the list) and `tests/test_error_cue.py` (the indicator
starts and stops once). Stubs in six other test files gained the two
indicator methods or read `GetRows()`.

## What NVDA said, before and after

Recorded on an audit copy with the first past conversation loaded, the same
nineteen keys in the same order, `nvda-list-before.json` against
`nvda-list-after.json`. The after file was read through `get_speech`, which
returns text after NVDA's symbol processing, so the diff compares the two
with punctuation, spaces and case removed. Sixteen keys are identical,
including the focused row's role and states after the find. Three differ:

```
{ENTER}
  before: []
  after:  ["I'll report back as results come in.  dialog",
           "edit  read only  multi line  I'll report back as results come in."]
{ESC}
  before: []
  after:  ['Blind Pilot AUDIT COPY - ignore this window', 'Responses  list',
           "I'll report back as results come in."]
{ENTER}-find
  before: ['BlindPilot AUDIT COPY - ignore this window', 'Responses:  list',
           'List: Security ... and an actual test run  not selected',
           'You: Launch 5 general-purpose subagents ...']
  after:  ['Blind Pilot AUDIT COPY - ignore this window', 'Responses  list',
           'You: Launch 5 general-purpose subagents ...']
```

- Enter and the Escape after it. On the native list, Enter did nothing and
  Escape had nothing to close. `_on_list_key` has always opened Read View on
  Enter, and the design lists "Enter for Read View" among the keys that stay,
  so the native `wx.ListBox` was swallowing the key before the handler saw
  it. The new list delivers it. This is the one behavioural change, and it
  restores what the code intended.
- After the find. The native list spoke one unselected row ("List:
  Security ... not selected") between the list name and the selected row.
  The new list speaks the list name and the selected row only.
- The list name. The native control took its MSAA name from the "Responses:"
  label; the new one uses the control's name, "Responses". NVDA does not
  speak the colon at its default symbol level, so the two sound the same.

`GetFocus` had the wrong arity during this recording (`GetFocus(self, childId)`
instead of the zero-argument override wx actually calls), so every real call
to it raised `TypeError`; it was fixed afterwards, without a re-recording.
Re-checked on the fixed build: arrows, Home and Tab into the list speak the right rows once each, the focused row reports list item, selectable, focusable, focused, selected, and the diagnostics log shows no tracebacks where the earlier recording's log showed eighteen. Tab into the list had spoken the row twice once GetFocus worked, because the focus-time announcement repeated what the reader already found; that announcement was removed.

Recent Conversations, checked once: Tab into the list speaks "Conversations
list" then the row label, and the focused row reports list item, selectable,
focusable, focused, selected.

## What a sighted person sees

Three screenshots, viewed at audit time, not committed.

- `80-list-default.png`, the window at its default size in list mode. Every
  row wraps inside the list; the "You:" row is three bold lines, "Response 1"
  is a bold heading, the long "List:" row wraps to four lines, and nothing is
  cut at the right edge. There is no horizontal scrollbar.
- `81-list-700x500.png`, the same window at 700 by 500 with the find filter
  "the" applied. The "You:" row wraps to five lines, a vertical scrollbar
  appears because the three matching rows overflow, the selected row shows
  the system highlight, and the status line reads "Showing 3 of 6 rows for
  'the'".
- `82-list-dark.png`, the default size with the appearance preference set to
  Dark. The window, list, prompt and buttons are dark with light text; the
  bold rows and the wrapped layout are the same as in the light shot.

## Skipped

- The spec's one-time warning when `SetAccessible` is unavailable. Every
  supported wxPython build has accessibility, so it was dropped from scope.
- A position in the accessible description. The baseline showed the native
  list speaks none even with position reporting on, so adding one would have
  added speech.
- Type-ahead. Typing a letter to jump to a row existed in `wx.ListBox`;
  `wx.VListBox` has none, and this pass did not add it back.
- The text view was not re-recorded with NVDA. Down there now moves by
  visual line because rows wrap, and the last-row guard consumes Down only
  on the last visual line, not on every visual line of the last row.
- `GetDefaultAction` says "Open" but there is no `DoDefaultAction`. Left for
  a follow-up.
