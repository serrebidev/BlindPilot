# 01 - Screenshot audit (what a sighted user sees)

Evidence: `docs/visual-audit/shots/00` to `61`, captured from a sandboxed audit copy of BlindPilot
(window title "BlindPilot AUDIT COPY - ignore this window") on Windows 11, light theme, 1920x1080,
100% DPI, running from source under pythonw. Shot 00 is the user's live window for comparison.
Companion documents: `02-code-layout-audit.md` (findings from the code, numbered 02-N below) and
`03-reference-checklist.md`. This file does not repeat their content; it says what the pictures show.

Not captured, and why:
- Model > Connect a Provider: disabled in Agent mode with the Claude Code backend (shot 12) and the
  whole Model menu is disabled in Chat mode (shot 60). It is opencode-only; enabling it would mean
  switching the audit copy's backend, which was outside the safety rules.
- Manage Backends: the attempt opened the setup wizard instead (shot 46), which is what is recorded.
- The Responses control in its wx.ListBox form with content. The audit copy has "Responses as a
  read-only text field" turned on (shot 13), so shots 00, 30 and 31 show `responses_text`
  (`blindpilot_app.py:5211`, `TE_DONTWRAP | TE_RICH2`), not the ListBox at `:5201`.
- Chat mode with a real conversation (no accounts exist in the sandbox), the Account and Profile
  editor sub-dialogs, the History "Read-only text" variant, dark mode, high contrast, and 125%+ DPI.

## What a sighted user sees

BlindPilot looks like a plain Windows utility written with a classic toolkit, in the style of a
2005 to 2012 era dialog-based program: a grey window, a one-row menu bar (File, Conversation,
Model, Options, Chat, Help), a few text labels, one big white box, a smaller white box, and a row
of identical small buttons at the bottom. Every control is a stock Windows control in Segoe UI 9pt,
black on light grey or white. There is no colour anywhere except the blue focus outline and the
blue selection bar. Nothing is bold, nothing is larger, there are no icons, no separators, no
panels, no toolbar, no pictures. It reads as functional, unbranded and unfinished rather than ugly.

The three things a sighted person notices first, in order: (1) the title bar and taskbar show a
generic icon (title bar: the Windows default "blank window" glyph; taskbar: the Python file icon),
so the app has no identity at all; (2) under the single "BlindPilot" tab there is an empty bordered
strip about 14 px tall that looks like something failed to draw; (3) the big Responses box shows
both a vertical and a horizontal scrollbar even when it is empty, and once text is in it, long lines
run off the right edge and are cut mid-word (shot 00, the live window, shows lines that begin
mid-sentence because the box has been scrolled sideways).

The dialogs are more consistent than the main window. Preferences, New Session, Model, Slash
Commands and Recent Conversations are ordinary tidy Windows dialogs with OK/Cancel in the bottom
right, and a sighted user would accept them without comment. Two dialogs are visibly broken or
odd: the Software Update dialog draws a tiny text box on top of its own message and leaves 90% of
its area empty (shot 45), and the setup wizard is mostly empty space (shot 46). The Chat mode of the
main window is a different layout from Agent mode (two button rows instead of one, no header lines,
different left margins), so switching modes feels like switching to a different program.

Density is low and spacing is roomy but uneven: gaps between rows vary from 2 px to 24 px with no
system to it, labels sit 2 px to the right of the controls beneath them, and dialog content
hugs the top-left of oversized windows. Nothing overlaps at 700x500 (shot 03) and the maximised
window (shot 02) fills correctly, so the sizers work; what is missing is polish, hierarchy and
identity.

## Findings

Ranked by how much a sighted user would notice. "Planned" refers to the maintainer's agreed
follow-up: app icon on every window, DPI awareness for the frozen exe, FromDIP sizes, a visible
row when a turn fails, Windows dark mode, and an owner-drawn wrapping list with wx.Accessible
replacing the Responses wx.ListBox.

### 1. Transcript text is cut off at the right edge in every view that shows it
Shots: 00 (live window), 30, 31, 20.
Seen: in 30 and 31 every line of the conversation is a single unwrapped line; the user's prompt and
the "List: Security ..." row end at the box edge mid-word, with a thin horizontal scrollbar thumb
at the bottom left as the only hint that more exists. In 00 the box has been scrolled sideways, so
the visible lines start with fragments like "ll", ":" and ".Frame\|class" and there are blank
lines between them. The Recent Conversations list (20) has the same problem: rows end in
"- 10 minutes ag" and there is no horizontal scrollbar at all.
Why it matters: a sighted reader cannot read the answer. Sideways scrolling a chat transcript is
something no other chat program asks for.
Planned work: the wrapping owner-drawn list is the right fix for the ListBox. Two things it will
NOT cover as described: (a) the text-field mode (`responses_text`, `TE_DONTWRAP`), which is what
this user actually runs and what these shots show. Drop `wx.TE_DONTWRAP` there so it wraps, or
retire the mode once the new list is in. (b) the Recent Conversations and Slash Commands lists
(`wx.ListBox` at `blindpilot_app.py:4700` area and the slash dialog) should either use the same
wrapping list class or truncate with an ellipsis and a tooltip.

### 2. The Software Update dialog is visibly broken
Shots: 45, 45b (identical after settling).
Seen: a 640x470 dialog. In the top-left corner a small white box about 120x40 px containing the
raw text "# BlindPilot 0.21.4" sits on top of the status sentence, hiding its first words so it
reads "...available. You have 0.21.3. This copy runs from source, so it cannot replace itself."
A lone Close button follows and the remaining 80% of the dialog is empty grey.
Cause: `update_dialog.py` builds the sizer on an inner `panel` but `_set_state` calls
`self.Layout()` on the dialog, which never re-runs the panel's sizer. The notes box, hidden at
construction and shown later, is therefore placed at its default size and position (0,0). The
notes text is also raw markdown.
Why it matters: this is the dialog every user sees on the day an update ships; it looks like a
crash.
Fix (not in the planned work): call `panel.Layout()` (or `self.SetSizer(wx.BoxSizer())` containing
the panel with `wx.EXPAND`, so `self.Layout()` cascades) at the end of `_set_state`; strip or
render the markdown heading; `Fit()` the dialog to content when there are no notes.

### 3. No application identity anywhere
Shots: 06 and 57-icon-zoom-titlebar (title bar), 57-icon-zoom-taskbar (taskbar), 44 (About), 10
(Alt+Tab-style view of the window over other apps).
Seen: the title bar shows the Windows default application glyph (a small grey window with a
blue-green picture). The taskbar button shows the Python document icon. The About box shows the
system blue "i" circle. Every dialog inherits the same generic glyph.
Why it matters: it is the single strongest "unfinished" signal a sighted person gets, before they
read any text.
Planned work covers it. Concrete targets: `MainFrame.SetIcons(wx.IconBundle(...))` fixes the title
bar and dialogs; the taskbar icon when frozen comes from the exe resource (`BlindPilot.spec` icon,
already set) but when run from source needs an explicit `wx.IconBundle` with a 32 and 48 px size;
`wx.adv.AboutDialogInfo.SetIcon()` for the About box; `SetupIconFile` in the installer (02-19).

### 4. An empty bordered strip sits under the tab row
Shots: 01, 04, 61 (single tab), 30, 31 (two and three tabs).
Seen: below the "BlindPilot" tab there is a slightly lighter, bordered horizontal band roughly
14 px tall running the full width, then a gap, then "Backend: Claude Code". It is the page area of
a real `wx.Notebook` that has nothing in it.
Why it matters: it looks like a control that failed to render. It is the first oddity a sighted
user's eye lands on because it is directly under the menu.
Not covered by the planned work. Fix per 02-7: shrink the notebook's min height to the tab strip
only, or replace with a segmented row of `wx.ToggleButton`s carrying the same names.

### 5. Errors and progress look exactly like normal text
Shots: 30 (a failed turn), 01 versus 61 (status bar overwritten).
Seen: in 30 the only sign the turn failed is the plain line "Failed to authenticate: OAuth session
expired and could not be refreshed", set in the same grey-on-white as "Response 1" above it. There
is no red, no icon, no bold. While a run is working the only change is the Stop button becoming
enabled; nothing moves. In 01 the status bar says an update is available; in 61 the same bar just
says "Agent mode", so the notice is gone.
Why it matters: a sighted user scanning the box cannot tell success from failure or working from
hung.
Planned work: "a visible row when a turn fails" covers the existence of the row; the target should
be that the row also looks different, which needs the new owner-drawn list to draw by row kind
(error background from `wx.SystemSettings.GetColour(wx.SYS_COLOUR_INFOBK)` or a leading glyph,
bold headings, indented reasoning). Not covered: an activity indicator during a run (02-9) and a
persistent update notice (a `wx.InfoBar` under the menu bar instead of a status-bar line).

### 6. Scrollbars are shown on empty boxes, and differently in each mode
Shots: 01, 04, 61 (Responses: both bars, empty), 52 and 50 (Chat History: no bars; Message box:
vertical bar with arrows, empty), 30 and 31 (Responses: classic vertical bar plus a thin
modern-style horizontal thumb, so two scrollbar styles in one control).
Why it matters: permanent scrollbars on an empty box read as "old Win32". Two visual styles in one
control read as a bug.
Planned work: the owner-drawn list (`wx.VListBox`) only shows a bar when needed, which fixes the
Agent view. Not covered: the Prompt and Message text controls (`TE_MULTILINE | TE_RICH2`) show a
permanent vertical bar; add `wx.TE_NO_VSCROLL` or accept it, but make both modes match. The
text-field Responses mode needs `TE_DONTWRAP` removed (finding 1) which also removes its bar.

### 7. Chat mode is a different design from Agent mode
Shots: 50, 52 (Chat) against 01, 61 (Agent).
Seen in Chat mode: no "Backend:" and "Working directory:" lines; a Profile and Account picker
side by side, each half the width; a History box with a plain 1 px border and no scrollbar; a
Message box; a full-width Model box that is an editable combo (text cursor inside) unlike the
drop-down pickers above it; then two button rows, "Add files... / Remove selected / Clear all"
and "Send / Regenerate response / Stop generation / New conversation". Button widths vary from
about 72 px (Send) to 120 px (Regenerate response). The first row starts 2 px further left than
the second (border 8 vs 10 in `chat_panel.py:172` and `:186`). There is a blank 20 px band
between the Mode row and the Profile row. The status bar says "Ready" or "Chat mode".
Why it matters: switching the Mode combo looks like switching programs, and two button rows with
ragged widths is the most obvious layout tell in the app.
Not covered by the planned work. Fix: one button row (Send, Stop generation, New conversation on
the left; attachments as a small "Attach" button matching Agent mode); the same border constant
(02-6) for every row; keep the Backend/directory header or replace it with an equivalent
"Account: ... Model: ..." header line so the top of the window has the same shape in both modes.

### 8. Escape does not close three Chat dialogs
Shots: 55 (Accounts), 56 (Conversation Profiles), 59 (Diagnostics).
Seen: each has a single "Close" button in the bottom right. Sending Escape left the dialog open;
only the button or the title-bar X closes it. The `wx.ID_CLOSE` button is not an escape
target for `wx.Dialog`.
Why it matters: every Windows dialog closes on Escape; a user who presses it and sees nothing
happen assumes the app has hung.
Not covered. Fix: `self.SetEscapeId(wx.ID_CLOSE)` in `AccountsDialog`, `ProfilesDialog` and
`DiagnosticsDialog`, or give the button `wx.ID_CANCEL`.

### 9. Menu accelerators are shown two different ways
Shots: 10 (File), 11 (Conversation).
Seen: "New Session... Ctrl+T" has the shortcut right-aligned in the standard column, while
"Next Session (Ctrl+Tab)", "Attach Files... (Ctrl+Shift+A)", "Slash Command... (Ctrl+/)" and
"Jump to Latest Response (Ctrl+R)" carry it in parentheses inside the label. Both styles appear in
the same menu.
Why it matters: it looks hand-made. The comment at `blindpilot_app.py:9596` explains the reason
(those keys live in the frame accelerator table), but the user does not see the comment.
Not covered. Fix: remove those entries from the `wx.AcceleratorTable` and give the menu items a
`\t` accelerator instead; wx registers the accelerator from the menu label on MSW, including
Ctrl+Tab.

### 10. Dialogs open in the wrong place with the wrong amount of space
Shots: 55, 56, 59 (all opened at screen 109,121 while the main window was at 36,27, i.e. the
default cascade position, not centred on the parent), 46 (setup wizard: heading, two sentences,
one combo, then 60% empty), 20 (Recent Conversations: 40 px dead gap between "11 conversations"
and the buttons), 45 (see finding 2).
Why it matters: dialogs that appear off to one side of the window and are mostly empty feel
unrelated to the app that opened them.
Planned work: FromDIP sizes will change the numbers but not the shape. Add `CentreOnParent()`
(02-17) and `Fit()` after building, with a `SetMinSize` on the list controls instead of a fixed
dialog size (02-15).

### 11. Markdown punctuation leaks into labels
Shots: 41 (Model dialog: "reports the current model as: `Opus 5`" and the combo entry
"(CLI default) - currently `Opus 5`" with literal backticks), 45 ("# BlindPilot 0.21.4"), 20 and
43 (long dashes used as separators inside list rows, which is fine, but the backticks are not).
Why it matters: backticks in a dialog sentence look like a typo.
Not covered. Fix: strip backticks when building `StaticText` and `wx.Choice` labels; render the
release notes heading as plain text.

### 12. Left edges do not line up
Shots: 01 (labels "Backend:", "Responses:", "Prompt:" start 2 px right of the boxes below them),
52 (Chat attachment row 2 px left of the Send row), 20 (Backend and Show pickers are short and
left-packed while the Filter box below spans the full width, leaving a wide empty column).
Why it matters: small, but it is what makes a layout feel hand-placed rather than designed.
Planned work: FromDIP alone will not fix it; it needs the single border constant of 02-6 and
`wx.EXPAND` on the two pickers (02-21).

### 13. The Options menu is long and duplicates the Preferences dialog
Shots: 13 (15 items including four "Working sound:" radio lines and "Working sound interval..."),
40 (the same settings again as a dialog).
Why it matters: a 15-item menu of settings, with radio bullets and check marks mixed, is a 1990s
pattern; and the "Working sound:" prefix repeated on four rows makes them read as one long item.
Not covered. Fix: keep "Preferences..." and the two or three toggles people flip mid-run
(Speak activity aloud, Play sound cues), move the rest to a "Working sound" submenu or drop the
duplicates.

### 14. No text hierarchy
Shots: 01, 44, 46.
Seen: everything is 9 pt regular. "Backend: Claude Code" and "Working directory: ..." are the same
weight and size as the field labels, so they look like labels with no field. The only bold text in
the app is the wizard step heading (46), and the only large text is the system-drawn About title.
Why it matters: a sighted user has no anchor; the eye has nowhere to start.
Not covered, and should be done inside the owner-drawn list work: bold for headings and user turns,
a slightly muted colour (`wx.SYS_COLOUR_GRAYTEXT`) for reasoning rows, monospace for code (02-16).
Keep font sizes from `wx.SystemSettings.GetFont` so this survives DPI and dark mode.

### 15. Preferences dialog: nested sound-cue boxes are crammed and labels repeat
Shot: 40.
Seen: the four indented checkboxes under "Play sound cues" have no vertical gap while every other
checkbox has 8 px; inside the "Working sound" group the three radio buttons each repeat
"Working sound:" in their text.
Not covered. Fix per 02-21: `wx.TOP, 4` on the nested boxes; radio labels "Continuous",
"Every N seconds (2-120)", "Off".

### Positives to keep
- Native controls everywhere, one system font, no hard-coded colours: dark mode (planned) and
  high contrast will work without a redesign, and the focus rectangles are visible (20c, 46).
- Default buttons are marked (blue outline on Open in 20, Send in 01) and OK/Cancel sit bottom
  right in every standard dialog (40, 41, 42, 43).
- Preferences (40) with its `wx.StaticLine` sections and group boxes is the best-looking screen in
  the app and is the model the others should follow.
- The main window survives 700x500 (03) and full screen (02) without overlap.
- Menus are standard Windows menus with correct mnemonics, separators and check/radio marks.
- The status bar carries a helpful one-line description for every menu item (10 to 15).

## Per-screen notes

- `00-main-window-baseline.png` The user's live window (title "BlindPilot"). Responses in text-field
  mode scrolled sideways: visible lines begin mid-word, blank lines between; a prompt is typed;
  Send disabled. The one real-use shot; shows finding 1 at its worst.
- `01-audit-instance-startup.png` Audit copy at 886x753. Generic icon, menu bar, Mode combo,
  one tab, empty bordered band, three header/label lines, empty Responses with both scrollbars,
  empty Prompt with caret, button row, status bar with the update notice.
- `02-main-maximized.png` Full screen. Everything stretches correctly; the Prompt stays 3 lines
  tall and the buttons stay bottom-left. Placeholder text appears in the Prompt.
- `03-main-small-700x500.png` Responses shrink to 3 lines, Prompt keeps its height. No overlap.
- `04-main-restored-886x753.png` Same as 01 with the placeholder showing; the baseline for
  later shots.
- `05-menubar-zoom.png` Title and menu bar zoomed: mnemonics underlined, generic icon.
- `06-titlebar-icon-zoom.png` The icon at 8x: Windows default application glyph, not BlindPilot.
- `07-mode-combo-open.png` Mode drop-down with "Agent" and "Chat"; a two-item `wx.Choice`.
- `10-menu-file.png` File menu, 10 items. Mixed accelerator styles (finding 9). Window sits over a
  game screen, showing how flat and grey the app is against a modern background.
- `11-menu-conversation.png` Conversation menu, 7 items, same mixed accelerator styles.
- `12-menu-model.png` Model menu; "Connect a Provider..." disabled. Two submenus.
- `12b-menu-model-highlight.png` Same with "Backend Settings..." outlined; transcript text visible
  behind, cut at the right edge.
- `13-menu-options.png` Options menu, 15 items, check and radio marks mixed (finding 13).
- `14-menu-help.png` Help menu, 4 items. Fine.
- `15-menu-chat.png` Chat menu in Agent mode: everything greyed except "History view".
- `20-dlg-recent-conversations.png` 606x453 dialog. Two short pickers, full-width Filter, list of
  11 rows cut at the right edge, "11 conversations", dead gap, Open (default) and Cancel.
- `20b-recent-conv-current.png`, `20c-rc-after-tab.png` Same dialog; 20c shows the focus rectangle
  on the selected row after Tab. Selection bar is the standard dark blue.
- `30-responses-populated.png` Two tabs. Text-field Responses with a user line, "Response 1",
  and an unstyled failure line (finding 5). Thin horizontal scroll thumb bottom-left.
- `31-responses-rich.png` Three tabs; tab titles truncated with "..." which is correct. Six lines
  of transcript, all clipped at the right; "List:" prefix is the only structure.
- `40-dlg-preferences.png` 757x480. Group box for Narration, four checkboxes, nested sound cues
  (crammed), Working sound group, spinner, update checkbox, OK/Cancel. Best screen in the app.
- `41-dlg-model-effort.png` 305x180. One sentence with backticks, Model and Effort pickers,
  OK/Cancel. Tidy apart from the backticks.
- `42-dlg-new-session.png` 530x235. Folder box with Browse, Name box, hint line, OK/Cancel. Tidy.
- `43-dlg-slash-commands.png` 473x290. One sentence, list of nine commands each suffixed
  "[BlindPilot]", OK/Cancel. Tidy; the suffix is noise for a sighted reader.
- `44-dlg-about.png` Native About box: system "i" icon, "BlindPilot 0.21.3" large, five lines of
  text, URL as plain text, OK. Only the missing app icon is wrong.
- `45-dlg-update.png`, `45b-dlg-update-settled.png` Broken layout (finding 2).
- `46-manage-backends-attempt.png` Setup wizard "BlindPilot - Setup", "Step 1 of 5: Welcome"
  in bold, two sentences, a full-width Backend picker with focus rectangle, 60% empty, Cancel left
  and Back/Next right. Reasonable wizard shape, too much empty space, no icon.
- `50-chat-panel.png`, `52-agent-mode-before-chat.png` Chat mode (finding 7). 52 is the state the
  previous agent left the copy in; the Profile picker no longer has focus.
- `51-menu-chat-enabled.png`, `54-chat-menu-open-chat-mode.png` Chat menu in Chat mode: all five
  items enabled. 54 is a full-screen capture that also shows the Python icon on the taskbar.
- `55-chat-dlg-accounts.png` 636x473. "Accounts:" label, empty list, five buttons (Add, Edit,
  Delete, Test account, Refresh models) on one row, Close alone on a second row at the right.
  Two button rows where one belongs; Escape does not close it.
- `56-chat-dlg-conversation-profiles.png` 606x453. Same shape as 55 with four buttons. Same
  Escape problem.
- `57-icon-zoom-taskbar.png`, `57-icon-zoom-titlebar.png` 6x crops from 54: Python document icon
  on the taskbar, generic glyph in the title bar.
- `58-chat-menu-history-view-submenu.png` History view submenu: "List" (selected) and
  "Read-only text". Status bar shows the item's help text.
- `59-chat-dlg-diagnostics.png` 746x553. One line with the log path, an empty read-only text box
  with a caret, Refresh and Close bottom right. Log text would be proportional, not monospace.
- `60-model-menu-chat-mode.png` Model menu in Chat mode: every item greyed out, including the
  submenus. A sighted user gets no hint why.
- `61-agent-mode-final-state.png` Audit copy returned to Agent mode, no dialogs, status "Agent
  mode". Identical to 01 except the status text.
