# Applied: safe wins in blindpilot_app.py and markdown_rows.py

Branch `visual/safe-wins`, on top of `734323c`. Files touched: `blindpilot_app.py`,
`markdown_rows.py`, `tests/test_menu_layout.py`, `tests/test_error_cue.py`,
`tests/test_live_rows.py`, `tests/test_preferences_dialog.py`, `tests/test_model_picker.py`,
new `tests/test_app_icon.py`, and the screenshots `shots/70` to `75`.
Findings are numbered as in `01-screenshot-audit.md` (01-N) and `02-code-layout-audit.md` (02-N).

## Done

1. Application identity (01-3, 02-3). `_app_icon_path()` resolves `packaging/BlindPilot.ico`
   under `_resource_dir()`, which is `sys._MEIPASS` when frozen and the source folder otherwise.
   `MainFrame.__init__` calls `SetIcons(wx.IconBundle(...))` when the file exists; the About box
   gets the 48 px icon from the same bundle. Tests: `test_app_icon.py`
   (`test_from_source_the_icon_is_next_to_the_application`,
   `test_frozen_the_icon_is_in_the_unpacked_bundle`). Note for the orchestrator: on Windows
   `wx.adv.AboutBox` shows the native message box only when the info is "simple"; an icon makes
   it use wx's generic About dialog instead (shot 73). It is a real dialog with the icon, bold
   name and version, the description and an OK button. If the message box was preferred for
   the screen reader, drop the two `SetIcon` lines and the rest stands.
2. DPI and sizes (02-4, 02-5, 02-6, 02-12, 02-14, 02-15, 02-17, 02-21). Module constants
   `PAD = 8` and `PAD_DIALOG = 12`, used everywhere as `window.FromDIP(PAD)`. Every literal
   `wx.Size`, `SetMinSize`, `Wrap(N)`, `FlexGridSizer` gap and sizer border in the file goes
   through `FromDIP`; the 4/6/8/10/12/14/16/24 borders are gone (labels and the controls under
   them now share one left edge in the main window, shot 70). `MainFrame` sizes itself with
   `SetSize(FromDIP(900, 760))` and `SetMinSize(FromDIP(640, 480))`. The five
   `StdDialogButtonSizer` rows that were `ALIGN_RIGHT` are `EXPAND | ALL, PAD_DIALOG`
   (Model, Question, New Session, Recent Conversations, Hermes Conversations). Recent
   Conversations and Hermes Conversations lost their `SetSize` after `SetSizerAndFit` and size
   themselves from a `FromDIP` min size on the list; the Backend and Show pickers are added with
   `EXPAND` so they fill their column. Backend Settings and ReadView also size from their list
   or text control and `Fit`; the setup wizard keeps a `SetSize(FromDIP(580, 400))` because its
   pages differ. Every dialog `__init__` that lacked it ends with `CentreOnParent()`. Text in the
   two resizable dialogs in this file (Backend Settings, the setup wizard) reflows on
   `EVT_SIZE` through a small `WrappedText` subclass that keeps the unwrapped label, because
   `Wrap` writes newlines into the label and a second wrap would keep the old breaks. In
   Preferences the nested sound-cue boxes get the same gap as their siblings (02-21), the
   "Working sound" radio labels are "Continuous", "Every N seconds (2-120)", "Off" (01-15), and
   the dead `size=` argument is gone (shot 71). Test:
   `test_preferences_dialog.py::test_the_working_sound_choices_do_not_repeat_the_group_name`.
   The existing dialog tests (`test_question_dialog`, `test_history_dialog_keys`,
   `test_hermes_sessions_ui`, `test_settings_dialog`, `test_new_session_dialog_*`,
   `test_remote_hermes_dialog`, `test_chat_mode`, `test_tab_strip_focus`) cover construction.
3. The empty band under the tab row (01-4, 02-7). The `wx.Notebook` stays, for the reasons in
   its comments. `MainFrame._fit_tab_strip()` runs once the first tab exists: it sends one size
   event, then reads the page's offset inside the control (the tab row) and what lies below the
   page (the frame), and sets the strip's min height to their sum, so the page area is zero
   pixels tall. Measured, not a literal. A page added later would sit at its default 20 x 20 in
   the control's top-left corner until the next size event, which with the strip cut to the tab
   row drew a white block over the first tab; `_sync_tab_switcher` now sends the control a size
   event after adding pages (shots 70 and 75).
4. Failed turn leaves a row (01-5, 02-10). `SessionPanel._on_failed` appends
   `Row(kind="error", label="Error: ...", payload=message, response_number=...)` (the streaming
   response's number, else the response count) and refreshes the list before announcing; the
   announcement is unchanged. "error" is listed among the row kinds in `markdown_rows.py`. The row
   is drawn like any other for now. Tests in `test_error_cue.py`:
   `test_a_failed_turn_leaves_one_error_row_in_the_list`,
   `test_the_error_row_belongs_to_the_response_that_was_streaming`,
   `test_the_error_row_changes_nothing_about_what_is_spoken`,
   `test_a_turn_the_user_stopped_leaves_no_error_row`; `test_live_rows.py::
   test_a_failed_turns_prompt_is_not_grouped_into_the_next_response` now expects the row.
5. Shortcuts shown one way (01-9). Next Session, Previous Session, Attach Files, Slash Command
   and Jump to Latest Response carry their chord in the label's tab column
   (`"Ne&xt Session\tCtrl+Tab"`, on macOS `Ctrl+Shift+]` and `[` which wxWidgets shows and binds
   as Command); the parenthesised text is gone. `_tab_chord_notes()` now returns the tab-column
   chords. Verified at runtime on Windows in the audit copy: Ctrl+/ opens the Slash Commands
   dialog from the prompt (shot 74); Ctrl+Tab switches tabs from the prompt box and from the
   responses control, Ctrl+Shift+Tab switches back (`.tmp_apply` captures, since deleted; shot 75
   shows the two-tab state). Tests: `test_menu_layout.py::
   test_a_shortcut_only_command_says_its_chord_in_the_accelerator_column[...]` and
   `test_next_and_previous_session_carry_their_chords_the_same_way`, which also check that
   wx registers an accelerator from each label (`GetAccel()` is not None, including for Tab).
6. Backticks in dialog text (01-11). `_plain()` strips backticks; `ModelDialog` uses it for the
   "reports the current model as" sentence and `_keep_choice` for the "(CLI default), currently
   ..." combo entry (shot 72). Test: `test_model_picker.py::
   test_the_keep_entry_drops_the_backticks_a_cli_puts_round_the_model_name`. Other backticks in
   the file are in text that is spoken or logged (the auth hints, installer log lines, the status
   bar's "Still using model `Opus 5`") and were left as they are, per the rule that nothing spoken
   changes.
7. Monospace for code (02-16). `_monospace_font(window)` builds
   `wx.Font(wx.FontInfo(pt).Family(wx.FONTFAMILY_TELETYPE))` at the window's own point size.
   `ReadView` takes `monospace=` and `_open_row` passes `row.kind == "code"`; the wizard's
   installer output box (`_cli_log`) uses it too. Nothing else changes font.
8. Tooltips (02-20). `RemoteHermesDialog` controls carry tooltips written from the dialog's own
   intro, hints and labels. No tooltips were added anywhere else.

## Skipped

- The Options menu restructure (01-13): changes what a screen reader user hears and presses.
- Any change to spoken text or keys beyond the label relabelling above. The status bar line
  "Still using model `Opus 5`" keeps its backticks because it is spoken.
- 02-13 (hand-rolled button rows in Backend Settings and Connect a Provider) was not in the
  list; those rows keep `ALIGN_RIGHT`, now with `FromDIP(PAD_DIALOG)`.
- The wizard heading keeps its `GetPointSize() + 2` bold, as instructed.

## Shortcuts: what moved out of the accelerator table and what did not

- Moved entirely: Ctrl+/ (Slash Command). The menu owns it; `_slash_active` does nothing in
  Chat mode either way, and the item is disabled there.
- Kept in the table as well as on the menu: Ctrl+Tab, Ctrl+Shift+Tab, Cmd+Shift+], Cmd+Shift+[,
  Ctrl+Shift+A and Ctrl+R. Not because wx fails to register them from the label (it does, on
  MSW: `GetAccel()` parses Tab, `/`, `]` and `[`, and the runtime check passed), but because
  their menu items are `_agent_item`s, greyed out in Chat mode, and a disabled item's accelerator
  never fires, while `_attach_active`, `_jump_to_latest_response` and `_cycle_tab` have always
  worked from those chords in Chat mode. Keeping the table entries keeps that identical. A key is
  translated once, by whichever table sees it first (the frame's, then the menu bar's on MSW), so
  nothing fires twice. The comment above `accel_entries` in `MainFrame.__init__` says the same.
- Ctrl+Tab in Agent mode is handled by `_on_agent_char_hook` before either table, as before.

## What the screenshots show

- `70-safe-wins-main.png`: BlindPilot's icon in the title bar; the tab row with no bordered band
  under it, only the tab baseline; "Backend:", "Responses:", "Prompt:" and the boxes under them
  on one left edge; the button row with the run, message and mode groups a dialog margin apart.
  Still wrong, out of scope here: the empty read-only Responses field shows both scrollbars
  (01-6, the `TE_DONTWRAP` text view), and the prompt shows a permanent vertical bar.
- `71-safe-wins-preferences.png`: the four sound-cue boxes have the same gap as the boxes above
  them, the "Working sound" choices are "Continuous", "Every N seconds (2-120)", "Off", one
  dialog margin everywhere, OK and Cancel bottom right. The dialog is narrower than before
  (504 px against 757) because it now fits its content.
- `72-safe-wins-model.png`: "reports the current model as: Opus 5." and "(CLI default),
  currently Opus 5" with no backticks; the button row spans the dialog.
- `73-safe-wins-about.png`: wx's generic About dialog with the BlindPilot icon, "BlindPilot
  0.21.4" in bold, the description and copyright, OK. See the note under item 1.
- `74-safe-wins-slash.png`: the Slash Commands dialog opened by Ctrl+/ after the move to the
  menu label. Unchanged in itself.
- `75-safe-wins-two-tabs.png`: two tabs with the second selected, both drawn whole, no page area.

## Commands and results

- `python -m pytest tests/test_menu_layout.py tests/test_error_cue.py tests/test_live_rows.py
  tests/test_preferences_dialog.py tests/test_model_picker.py tests/test_question_dialog.py
  tests/test_history_dialog_keys.py tests/test_hermes_sessions_ui.py tests/test_markdown_rows.py
  tests/test_narration_modes.py tests/test_tabs.py tests/test_tab_strip_focus.py
  tests/test_chat_mode.py tests/test_settings_dialog.py tests/test_new_session_dialog_remote.py
  tests/test_new_session_dialog_speech.py tests/test_remote_hermes_dialog.py
  tests/test_startup.py tests/test_closing_a_tab.py tests/test_hermes_sessions_dialog_keys.py
  tests/test_app_icon.py tests/test_settings_files.py tests/test_progress_cue.py
  tests/test_prompt_keys_and_hermes_menu.py -p no:randomly`: 313 passed.
- `python -m pytest tests/test_tabs.py tests/test_tab_strip_focus.py tests/test_chat_mode.py
  tests/test_closing_a_tab.py -p no:randomly` after the `_sync_tab_switcher` fix: 21 passed.
- `python -m ruff check` and `python -m ruff format` on the files above: clean.
- `python -m mypy`: Success, no issues found in 14 source files.
- `python blind_pilot.py --startup-gui-smoke`: exit 0.
- Audit copy launched with the README recipe (sandboxed APPDATA, retitled), driven with
  `tools/sendkeys.ps1` plus a variant that focuses a dialog by title (the stock script focuses
  the main window, which a modal dialog disables, so Escape sent that way never reached the
  Preferences dialog on the first attempt). Every dialog was closed and the process ended.

## Two things learned on the way

- `wx.MenuItem` wrappers outlive nothing: a test that keeps items from a menu built as a
  temporary and then calls methods on them segfaults the interpreter. The menu tests bind the
  menu to a local first.
- `wx.BookCtrlBase.CalcSizeFromPage` is not exposed in wxPython 4.3.1, which is why the strip
  height is measured from the page's position and size after one size event.


Orchestrator's note, 2026-09-05: the About box icon was removed before commit. With an icon set, wx.adv.AboutBox uses wx's generic dialog instead of the native message box, and the native box is what reads its whole text to a screen reader on opening. Shot 73 shows the generic dialog that is not shipped.
