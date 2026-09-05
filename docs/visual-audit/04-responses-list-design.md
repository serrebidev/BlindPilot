# 04 - Responses list design

Design for replacing the Responses `wx.ListBox` with a wrapping, owner-drawn
list that a screen reader reads exactly as it reads the native one today.
Approved 2026-09-05. Third of three stacked pull requests on top of the
September audit (safe wins, dark mode, this).

## Why

Every row of a conversation is one line cut off at the right edge of the
window (01-1, 02-1). A sighted person cannot read an answer. Rows of different
kinds, a heading, a tool result, an error, the model's own reasoning, are all
drawn as the same grey line (01-5, 01-14, 02-11). The native list cannot wrap
or draw by kind, and a custom list is invisible to a screen reader unless it
is given an accessible object. This design does both, and proves the second
half before and after.

What the spike proved on 2026-09-05 (wxPython 4.3.1, NVDA 2026.1.1, MSAA
through `wx.Accessible`, no UIA): NVDA speaks the row's name on every arrow,
Home and End; with the position in the accessible description it speaks
"Response 1, 2 of 5"; on Shift+Tab back into the control it speaks "Responses
list" and then the current row. The focused object reports role list item
with the selectable, focusable, selected and focused states. That is what the
native list gives today.

## What stays the same

- Every string a screen reader hears. Row labels are unchanged, and the
  position is spoken as "N of M" as it is now.
- Every key. Up, Down, Home, End, Ctrl+Up, Ctrl+Down, Enter for Read View,
  the Menu key and context gesture for the row menu, Tab and Shift+Tab out
  of the control, and the search filter.
- The row model. `markdown_rows.Row` with its `kind`, `label` and `payload`.
  The new list is handed rows, not strings, and reads `kind` to draw.
- The text-field mode ("Responses as a read-only text field"). It stays and
  gains wrapping.
- No colour is hard-coded anywhere. Everything comes from
  `wx.SystemSettings.GetColour`, so dark mode and high contrast keep working.

## Components

### `conversation_list.py` (new module)

`blindpilot_app.py` is over ten thousand lines; the list gets its own module
with no import of the window module. It imports `markdown_rows.Row`.

`class ConversationList(wx.VListBox)`

The surface the window uses, so the swap in `SessionPanel` is the constructor
and nothing else:

| Call | Meaning |
|---|---|
| `SetName(str)` | accessible name of the list ("Responses", "Conversations") |
| `GetCount() -> int` | rows shown |
| `GetSelection() -> int` | index or `wx.NOT_FOUND` |
| `SetSelection(int)` | move without stealing focus; scrolls the row into view |
| `Set(rows: Sequence[Row])` | replace every row; keeps the selection index when it still exists |
| `AppendItems(rows: Sequence[Row])` | add to the end without moving the selection or scrolling |
| `GetRows() -> list[Row]` | what is shown, for tests and the row menu |
| `HasFocus()`, `SetFocus()` | as `wx.Window` |
| events | `wx.EVT_LISTBOX` on selection change, `wx.EVT_LISTBOX_DCLICK` on double click, `wx.EVT_KEY_DOWN`, `wx.EVT_CONTEXT_MENU`, all as `wx.VListBox` already raises them |

`Set` and `AppendItems` accept plain strings too and wrap them as prose rows,
so the Recent Conversations, Hermes Conversations and Slash Command lists can
use the class with their existing string labels and gain wrapping.

Drawing:

- `OnMeasureItem(n)` returns the height of the label wrapped to the client
  width minus two paddings, plus two paddings. Wrapping uses
  `wx.lib.wordwrap.wordwrap` with the row's font. Results are cached per
  `(row index, client width)`; the cache is cleared on `EVT_SIZE` and on
  `Set`, and entries past the old count are dropped on `AppendItems`.
- `OnDrawItem(dc, rect, n)` draws the wrapped label inside the padded rect
  with `dc.DrawLabel`. Padding is `FromDIP(6)` vertical and `FromDIP(8)`
  horizontal, plus one indent step of `FromDIP(16)` for `tool` and `result`
  rows.
- `OnDrawBackground` is left to `wx.VListBox` for the selected row (system
  highlight) and overridden only for `error` rows, which get
  `wx.SYS_COLOUR_INFOBK` when not selected.
- Font and colour by kind, one system font throughout:

| kind | weight | colour | face |
|---|---|---|---|
| `you`, `header` | bold | window text | system |
| `heading` | bold | window text | system |
| `prose`, `list`, `quote` | regular | window text | system |
| `thinking` | regular | `wx.SYS_COLOUR_GRAYTEXT` | system |
| `tool`, `result` | regular | window text, indented | system |
| `code` | regular | window text | `wx.FONTFAMILY_TELETYPE`, same point size |
| `error` | regular | window text on `wx.SYS_COLOUR_INFOBK` | system |

  On the selected row the text colour is `wx.SYS_COLOUR_HIGHLIGHTTEXT`
  whatever the kind, so selection always reads.
- The focus rectangle is the one `wx.VListBox` draws; it is not suppressed.
- The vertical scrollbar appears only when rows overflow, which `wx.VListBox`
  does by itself. There is no horizontal scrollbar, because nothing needs one.

`class RowsAccessible(wx.Accessible)`

Attached with `SetAccessible` in the constructor. Child ids are 1-based row
indexes; 0 is the list itself.

| Method | list (id 0) | row (id n) |
|---|---|---|
| `GetRole` | `wx.ROLE_SYSTEM_LIST` | `wx.ROLE_SYSTEM_LISTITEM` |
| `GetName` | the control's name | `rows[n-1].label` |
| `GetDescription` | "" | `f"{n} of {count}"` |
| `GetState` | focusable, plus focused when the control has focus | selectable and focusable, plus selected and focused for the selected row, plus invisible when scrolled out |
| `GetLocation` | screen rect of the control | screen rect of the row from `GetItemRect` |
| `GetChildCount` | row count | not called |
| `GetFocus` | the selected row's id, or 0 | |
| `GetSelections` | the selected row's id, or none | |
| `GetDefaultAction` | "" | "Open" |
| `Navigate` | first and last child from the list; next, previous, up and down between rows | |
| `HitTest` | the row under the point from `VirtualHitTest`, or the list | |
| `GetValue`, `GetHelpText`, `GetKeyboardShortcut` | "" | "" |

Events: on every selection change and whenever the control gains focus, the
list calls `wx.Accessible.NotifyEvent` with `wx.ACC_EVENT_OBJECT_FOCUS` and
then `wx.ACC_EVENT_OBJECT_SELECTION` for `wx.OBJID_CLIENT` and the selected
row's id. When the control gains focus with no selection and at least one
row, it selects row 0 first, as the native list does.

### `SessionPanel` changes (`blindpilot_app.py`)

- `self.responses = ConversationList(self)` in place of the `wx.ListBox`.
- `_refresh_list` and `_append_rows` pass `Row` objects instead of labels.
  The append fast path stays: it compares labels as before.
- `_on_failed` already appends an `error` row (done in the safe-wins pull
  request); the list now draws it on the info background.
- `_one_line` stays for the text mode only.
- A `wx.ActivityIndicator` is added to the button row beside Stop, started
  where `earcons.start_progress()` is called and stopped where
  `stop_progress()` is called. It is not named for the screen reader and is
  never focusable; it exists so a sighted person can tell a working turn from
  a hung one.

### Text-field mode changes

- `wx.TE_DONTWRAP` is removed from `responses_text`, so lines wrap.
- Wrapping breaks the "one line is one row" mapping, because on Windows the
  control counts wrapped lines. The mapping moves to character offsets:
  `_refresh_list` and `_append_rows` record the start offset of each row as
  they write it (`self._row_starts: list[int]`), `_selected_row` finds the row
  by bisecting the insertion point against those offsets, and `_select_row`
  sets the insertion point to the row's start offset. Rows are still joined
  with a single newline, so the offsets are the cumulative label lengths plus
  one each.

### Other lists

`HistoryDialog`, the Hermes conversations dialog, and `SlashCommandDialog`
construct `ConversationList` instead of `wx.ListBox` and keep passing string
labels. Their selection handling is unchanged.

## Data flow

Rows arrive as they do today: `markdown_rows.parse_response` for a finished
answer, and the live kinds the panel adds during a turn. The list never
reads the panel's `_rows` directly; it is given `_displayed` after the search
filter is applied, so a search still hides rows and the position spoken is
the position within the filtered list, as now.

## Error handling

- A row whose label is empty is drawn as one blank line of the padded height
  so indexes stay aligned with `_displayed`.
- `wordwrap` on a width below one character's width returns the label
  unwrapped; the measure then reports one line, and the row clips at the
  edge instead of raising. This only happens while the window is being made
  very small.
- If `SetAccessible` is unavailable (wx built without accessibility, which
  is not the case for any supported build), the list still works; the
  screen reader then sees a generic client area. A warning is logged once at
  startup so the case is visible in the diagnostics log.

## Testing

Unit, on stubs, no display needed:

- `RowsAccessible` over a fake control: name, description "N of M", role,
  states for the selected and unselected row and for the list, `GetFocus`,
  `GetSelections`, `Navigate` at both ends, `HitTest` outside any row.
- The measure cache: same width hits the cache, a resize clears it, `Set`
  clears it, `AppendItems` keeps entries for the rows that stayed.
- Row kinds map to the font weight, colour and face in the table.
- Text mode: `_row_starts` after `Set` and after `AppendItems`, and
  `_selected_row` for a caret at the start, middle and end of a row and at
  the very end of the control.
- `SessionPanel` on the existing stubs: `_refresh_list` still takes the
  append path when only rows were added, the error row is present after
  `_on_failed`, the activity indicator is started and stopped with the
  earcons.

Live, through the NVDA bridge on an audit copy of the app with a real past
conversation loaded:

1. Before the change, record the spoken sequence for: Tab into Responses,
   Down five times, Home, End, Ctrl+Down, Enter (Read View opens), Escape,
   the Menu key (row menu opens), Escape, Shift+Tab out, and a search that
   hides rows.
2. After the change, record the same sequence and diff the two. Differences
   allowed: none in the row names or positions. The one expected difference
   is the list's own announcement, which must still be "Responses list".
3. The same for the Recent Conversations dialog.

Sighted side: screenshots of the main window with a populated transcript at
the default size, at 700x500, and in dark mode, viewed and described in the
applied report. Screenshots are not committed; the shots folder is ignored.

## Out of scope

- Any change to what is spoken, or to which keys do what.
- Redesign of the Options menu or the Chat panel.
- Persisting window size and position.
- Column or table layouts, icons in rows, animation.
