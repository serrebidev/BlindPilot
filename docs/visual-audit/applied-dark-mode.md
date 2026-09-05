# Applied: dark mode preference (finding 02-8)

Branch `visual/dark-mode`, on top of `cf2aa3c` (`visual/safe-wins`). Files touched:
`blindpilot_app.py`, `tests/test_preferences_dialog.py`, new screenshots `shots/76` to `79`,
and this file. `CHANGELOG.md` is untouched.

## What changed

1. Config key `appearance` with the values `system`, `light`, `dark`. Constants
   `APPEARANCE_SYSTEM`, `APPEARANCE_LIGHT`, `APPEARANCE_DARK`, the `(key, label)` table
   `APPEARANCES`, and `APPEARANCE_RESTART_NOTE` sit next to the working-cue constants.
   `_valid_appearance(value)` maps a missing, unknown, or oddly cased value to `system`, the
   same shape as `_valid_progress_cue`. `_appearance_for(value)` returns
   `wx.App.Appearance.System`, `.Light`, or `.Dark` (checked on this build: those are the enum
   names) and hands anything unknown to `System`.
2. `_Settings` gains `appearance`, read in `__init__` and written in `save()` beside
   `text_view`.
3. `PreferencesDialog` gains a `wx.RadioBox` labelled "Appearance" with the choices
   "Follow system", "Light", "Dark", in its own `wx.StaticLine` section between "Working sound"
   and "Check for updates at startup". The sentence "Appearance will change the next time
   BlindPilot starts." is a `wx.StaticText` under the box and the box's tooltip. Sizes go
   through `FromDIP(PAD_DIALOG)` and `FromDIP(PAD)` like the rest of the dialog. The dialog
   exposes it as the `appearance` property.
4. `MainFrame._apply_preferences` stores the choice, saves it with the others, and when it
   differs from the stored value calls `announce(APPEARANCE_RESTART_NOTE)` once, after
   "Preferences applied" (`announce` queues, so both are heard).
5. `main()`, right after `app.SetAppName` / `SetAppDisplayName` and the existing
   `cfg = _load_config()`, calls `app.SetAppearance(_appearance_for(cfg.get("appearance")))`.
   Any result other than `wx.App.AppearanceResult.Ok` is logged once at INFO through
   `logging.getLogger("blindpilot")` and startup continues.

## Tests (tests/test_preferences_dialog.py)

- `test_the_appearance_setting_round_trips_through_settings` (default `system`, unknown value
  reads as `system`, saved value reads back)
- `test_appearance_for_maps_the_config_value_to_the_wx_enum`
- `test_the_dialog_offers_the_three_appearances` (label "Appearance", the exact three choices,
  property follows the selection)
- `test_changing_the_appearance_says_it_takes_effect_next_launch` (sentence spoken exactly
  once, not again when the value is unchanged)
- `test_the_dialog_reads_the_live_settings` also asserts `dialog.appearance`, and the dialog
  stub in `test_applying_the_dialog_updates_the_settings_and_the_menus` carries
  `appearance`.

The tests failed first (six failures, `APPEARANCE_SYSTEM` missing), then all nine passed.

## Screenshots

The audit copy ran with the recipe in `README.md` and the sandbox config's `appearance`
rewritten before each launch. The machine's Windows apps theme is light
(`AppsUseLightTheme = 1`), so "Follow system" means light here.

- `76-appearance-system.png`: main window, `system`. Light, identical to 77.
- `77-appearance-light.png`: main window, `light`. Light, as before this change.
- `78-appearance-dark.png`: main window, `dark`. Title bar, menu bar, panel, static text,
  notebook tab, Responses list, prompt box, combo boxes, buttons and status bar are all drawn
  dark with light text.
- `79-appearance-dark-preferences.png`: Preferences over the dark run, captured by title,
  closed with Escape. Radio boxes, checkboxes, spin control, static lines, the new Appearance
  section with "Dark" selected, the note under it, and OK/Cancel all draw dark.

The sandbox log (`sandbox/Local/BlindPilot/Logs/blindpilot.log`, level INFO) recorded the
three launches and no appearance line, so `SetAppearance` returned `Ok` for all three values.

## Drawn wrong in dark mode (not fixed, no colours added)

Seen in shot 78, all on wxWidgets' own known-rough list for wxMSW dark mode:

- Disabled buttons: "Steer" and "Stop" look exactly like the enabled "Send" and "Attach". In
  light mode their labels are greyed. Nothing tells a sighted user they are off.
- The menu bar draws a lighter grey box behind "File" although no menu is open.
- The tab strip (the empty `wx.Notebook` from finding 02-7) draws a lighter outlined band to
  the right of the "BlindPilot" tab across the full width; in light mode it is a faint line.
- The square where the Responses list's two scrollbars meet stays white.

Shot 79 shows no disabled control (sound cues were on and the periodic cue was selected), so
the disabled-checkbox and disabled-spin cases were not observed. While Escape closed the
dialog, wx printed `msw\window.cpp(539): 'SetFocus' failed with error 0x00000057` once;
this was not checked against a light run and is not something this change touches.

## Commands and results

- `python -m pytest tests/test_preferences_dialog.py -q -p no:randomly`: 6 failed, 3 passed
  before the implementation; 9 passed after.
- `python -m ruff format blindpilot_app.py tests/test_preferences_dialog.py`: 2 files left
  unchanged.
- `python -m ruff check blindpilot_app.py tests/test_preferences_dialog.py`: All checks passed.
- `python -m mypy`: Success, no issues found in 14 source files.
- `python blind_pilot.py --startup-gui-smoke`: exit 0.
- Three audit launches through a throwaway `run_shot.ps1` (sandboxed `APPDATA` and
  `LOCALAPPDATA`, window retitled, `capture.ps1 -NoFocus`, then `Stop-Process`); the dark run
  also `sendkeys.ps1 -Keys "^,"`, `capture.ps1 -Title "BlindPilot Preferences"`, and
  `sendkeys.ps1 -Keys "{ESC}"`. Every audit process ended after its capture.
- Line endings preserved: `blindpilot_app.py` LF, the test file and this document CRLF.
