# Applied: safe wins for the dialogs and packaging

Branch `visual/safe-wins`, on top of `734323c`. Files touched: `update_dialog.py`,
`accessible_ai/ui/accounts.py`, `accessible_ai/ui/chat_panel.py`,
`accessible_ai/ui/diagnostics.py`, `accessible_ai/ui/profiles.py`, `BlindPilot.spec`,
`installer/BlindPilot.iss`, `packaging/BlindPilot.manifest` (new), `tests/test_update_dialog.py`,
`tests/test_chat_dialogs.py` (new), `tests/test_packaging.py` (new). Nothing spoken or
keyboard-driven changed, except that Escape now closes three dialogs.

## Done

### 1. Software Update dialog (01-2, 01-11, 02-4, 02-5)
- The dialog now has its own sizer holding the panel with `wx.EXPAND`, and `_set_state` calls
  `_fit_to_state`, which lays out the panel every time. With notes it restores the full
  `FromDIP(640, 470)` if a fitted state left the dialog smaller; without notes it `Fit()`s down to
  the status line, gauge and buttons, above a `FromDIP(480, 160)` minimum.
- Release notes go through `plain_notes()`, which strips `#` heading marks from each line.
- `640x470`, `Wrap(590)`, and the 8 and 12 borders all go through `FromDIP` via `PAD`,
  `PAD_DIALOG`, `FULL_SIZE`, `MIN_SIZE`, `STATUS_WRAP`. `CentreOnParent()` at the end of `__init__`.
- Tests (`tests/test_update_dialog.py`):
  `test_showing_release_notes_lays_out_the_panel_that_holds_them` (spies on `panel.Layout`
  and checks the notes box sits below the status line at more than half the panel width),
  `test_a_state_without_notes_fits_the_dialog_and_notes_bring_the_size_back`,
  `test_a_markdown_heading_in_the_notes_is_shown_without_its_hashes`.
- Live proof: `docs/visual-audit/shots/71-update-dialog-fixed.png` against `45-dlg-update.png`.
  Status sentence, "What is new:", the notes box filling the dialog with the heading shown as
  plain text, Open release page and Close at the bottom right.

### 2. Escape closes the Chat dialogs (01-8)
- `self.SetEscapeId(wx.ID_CLOSE)` in `AccountsDialog`, `ProfilesDialog`, `DiagnosticsDialog`.
  Escape presses the existing Close button, so the same handler runs as for a click; while an
  account test is running the button is disabled and Escape does nothing, as before.
- Test: `tests/test_chat_dialogs.py::test_escape_presses_close_in_each_chat_dialog`.

### 3. DPI and sizes in accessible_ai/ui (02-4, 02-5, 02-6, 02-13, 02-16, 02-17)
- `PAD = 8` and `PAD_DIALOG = 12` at the top of each module; every sizer border, FlexGridSizer
  gap, size and wrap width goes through `FromDIP`. Sizes are named module constants
  (`DIALOG_SIZE`, `EDITOR_SIZE`, `LIST_DIALOG_SIZE`, `JSON_FIELD_HEIGHT`, `NOTE_WRAP`,
  `COMPAT_NOTE_WRAP`, `SERVER_TOOLS_HEIGHT`, `MESSAGE_INPUT_HEIGHT`, `ATTACHMENT_LIST_HEIGHT`,
  `EDIT_DIALOG_SIZE`, `EDIT_DIALOG_MIN_SIZE`).
- Dialog sizes are set with `self.SetSize(self.FromDIP(...))` after `super().__init__`, since
  there is no window to ask before it.
- `chat_panel.py`: the 6, 8 and 10 borders are all `PAD`, so the pickers, the boxes, the
  attachment row and the Send row share one left edge. The two button rows are unchanged
  otherwise (01-7's redesign is out of scope).
- `ProfilesDialog` and `AccountsDialog`: the action buttons sit on the left of one row with Close
  in a `wx.StdDialogButtonSizer` on the right. Every button and label is kept; creation order,
  and so tab order, is unchanged. `DiagnosticsDialog` gets the same shape (Refresh left, Close
  right), which is the hand-rolled row 02-13 names.
- `DiagnosticsDialog.text` uses
  `wx.Font(wx.FontInfo(point_size).Family(wx.FONTFAMILY_TELETYPE))`, the only non-system font.
- `CentreOnParent()` at the end of `AccountEditorDialog`, `AccountsDialog`,
  `ProfileEditorDialog`, `ProfilesDialog` and `DiagnosticsDialog`.
- Tests (`tests/test_chat_dialogs.py`): `test_the_diagnostics_log_keeps_its_columns`,
  `test_every_size_border_and_wrap_goes_through_from_dip[...]` (one per module; scans the source
  for a bare `wx.Size`, `size=(w, h)`, `Wrap(n)`, `vgap=n` or a sizer border literal).
- Live proof: `docs/visual-audit/shots/72-diagnostics-dialog-fixed.png` against
  `59-chat-dlg-diagnostics.png`.

### 4. Packaging (02-3, 02-18, 02-19, checklist section 3)
- `BlindPilot.spec`: `("packaging/BlindPilot.ico", "packaging")` added to `datas`, so the frozen
  app finds the icon at `sys._MEIPASS/packaging/BlindPilot.ico`.
- On Windows a `VSVersionInfo` is built from `APP_VERSION` (numbers padded to four) with
  ProductName, FileDescription, InternalName and OriginalFilename `BlindPilot`, CompanyName
  `serrebidev`, and passed as `version=` to `EXE`. `None` elsewhere.
- `packaging/BlindPilot.manifest` (new) declares `dpiAwareness` `PerMonitorV2, PerMonitor` with
  `dpiAware` `true/pm` as the fallback, plus the execution level, supported OS list, long path
  awareness and the common controls dependency PyInstaller would add anyway. Passed as
  `manifest=` to `EXE` on Windows. PyInstaller 6.22 reads a path for this argument and keeps
  the DPI elements when it merges its own requirements.
- `installer/BlindPilot.iss`: `SetupIconFile=..\packaging\BlindPilot.ico` under `[Setup]`,
  relative to the script like `LicenseFile`.
- Tests (`tests/test_packaging.py`): `test_the_window_icon_ships_beside_the_sounds`,
  `test_the_windows_exe_carries_a_version_resource_built_from_app_version`,
  `test_the_windows_exe_is_handed_the_dpi_manifest`,
  `test_the_manifest_declares_per_monitor_dpi_awareness_with_a_fallback` (parses the XML),
  `test_the_installer_shows_the_app_icon` (resolves the path and checks the file exists).
  PyInstaller was not run.

## Skipped
- Nothing from the list. Beyond it: `AccountsDialog` got the same one-row button layout as
  `ProfilesDialog` because the audit says the two dialogs share a shape (shot 55 against 56);
  `DiagnosticsDialog` likewise, per 02-13. `tests/test_ci_workflows.py` was left alone; the
  packaging checks live in their own file since they are not about workflows.
- Re-wrapping the status text on `EVT_SIZE` for the resizable dialogs (02-5's second half) was
  not done; the wrap width is DPI-correct but still fixed at construction.

## Changes needed in blindpilot_app.py
- Load the window icon from `packaging/BlindPilot.ico`, looking under `sys._MEIPASS` when
  frozen and under the source checkout otherwise, and apply it with
  `self.SetIcons(wx.IconBundle(path))` in `MainFrame.__init__`. The spec now ships the file at
  that relative path. Nothing else in this pass depends on `blindpilot_app.py`.

## Commands run
- `python -m pytest tests/test_update_dialog.py tests/test_packaging.py tests/test_ci_workflows.py -q -p no:randomly`
  first run: 1 failed (test assigned to a frozen dataclass; fixed with `dataclasses.replace`),
  then 23 passed.
- `python -m pytest tests/test_update_dialog.py tests/test_chat_dialogs.py tests/test_chat_mode.py -q -p no:randomly`
  15 passed, 1 skipped (the FromDIP scan skips `accessible_ai/ui/__init__.py`, which builds no windows).
- `python -m ruff check` on the eight Python files touched: All checks passed.
- `python -m ruff format` on the same files: 8 files left unchanged.
- `python -m mypy`: Success, no issues found in 14 source files.
- Throwaway `.tmp_apply/shoot_dialogs.py` showed `UpdateDialog` (fake 0.21.4 release with
  markdown notes) and `DiagnosticsDialog` (temporary log with four sample lines) for one second
  each and captured them with `docs/visual-audit/tools/capture.ps1 -ProcessId`. Deleted after.
  `.tmp_apply/` itself was left in place because the other agent's working files are in it.
- Line endings: files that were CRLF in the working tree are still CRLF; `profiles.py` and the
  new files are LF. `.gitattributes` normalises all of them to LF in the index.
