# 02 — Code layout audit (what a sighted user sees)

Read-only audit of `blindpilot_app.py`, `markdown_rows.py`, `update_dialog.py`,
`accessible_ai/ui/*.py`, `BlindPilot.spec`, `installer/BlindPilot.iss`.
Findings ranked by visible impact. No files were modified.

Environment assumed: Windows 11, wxPython 4.3.1 / wxWidgets 3.3.3, Python 3.13.

---

## Findings

### 1. The Responses list clips every long row — no wrap, no horizontal scrollbar
`blindpilot_app.py:5228` `self.responses = wx.ListBox(self, style=wx.LB_SINGLE | wx.LB_NEEDED_SB)`.
Row labels are deliberately flattened to one line (`markdown_rows.py:219`, `blindpilot_app.py:2370 _one_line`).
`SetHorizontalExtent()` is never called and `wx.LB_HSCROLL` is not set, so MSW gives no horizontal
scrollbar. A paragraph-length assistant reply is drawn as one row cut off dead at the right edge —
mid-word, no ellipsis, no way to see the rest.
**Fix:** replace `wx.ListBox` with `wx.html.HtmlListBox` (or `wx.VListBox` with `OnMeasureItem`)
so rows wrap to the control width; as a stopgap call `self.responses.SetHorizontalExtent(...)` on
each refresh so at least a scrollbar appears.

### 2. Code blocks are never shown, only counted
`markdown_rows.py:250-255` `_code_row` sets `label = f"Code, {lang}, {n} lines"`; the actual code
goes into `payload`, reachable only through the modal `ReadView` (`blindpilot_app.py:7014`).
An answer that is 80% code renders on screen as a single grey line reading "Code, Python, 34 lines".
**Fix:** in text view (`blindpilot_app.py:6862`) emit the code payload indented under its label
instead of the label alone; in list view keep the label but render the code in the row below.

### 3. No application icon on any window
No `SetIcon`, `SetIcons` or `wx.IconBundle` call exists in any Python file. `packaging/BlindPilot.ico`
is used only by the PyInstaller EXE (`BlindPilot.spec:57`) and macOS bundle (`BlindPilot.spec:106`).
`MainFrame` (`blindpilot_app.py:8738`) and all ~14 `wx.Dialog` subclasses get the wxWidgets default —
title bar, Alt+Tab and every dialog show a generic icon; running from source shows the Python icon.
`wx.adv.AboutDialogInfo` (`blindpilot_app.py:9445-9450`) also omits `SetIcon()`.
**Fix:** in `MainFrame.__init__`, `self.SetIcons(wx.IconBundle(os.path.join(_resource_dir(), "BlindPilot.ico")))`
and add the `.ico` to `datas` in `BlindPilot.spec:33`. Dialogs inherit the parent frame's icon on MSW.

### 4. All sizes are raw pixels — the app shrinks at 125/150/200% DPI
Exactly one `FromDIP` call exists in the codebase (`blindpilot_app.py:9021`). Everything else is
device pixels: `blindpilot_app.py:8738` frame `wx.Size(900, 760)`, `:3801` (780×480), `:3875` (700×500),
`:4233` (420×260), `:4605`/`:4623` (420 wide), `:4809` (620×460), `:5020` (640×480), `:7441` (580×400),
`:8578` (620), `update_dialog.py:39` (640×470), `accessible_ai/ui/diagnostics.py:14` (760×560),
`accessible_ai/ui/profiles.py:346` (620×460), `accessible_ai/ui/chat_panel.py:407` (500×300),
`:151` (height 90), `:167` (height 62), `accessible_ai/ui/accounts.py:207`/`:216` (height 70).
At 200% the main window opens at 450×380 logical — roughly a quarter of the intended area.
**Fix:** wrap every literal in `FromDIP(...)`. For dialog constructors that have no window yet,
set the size after construction: `self.SetSize(self.FromDIP(wx.Size(780, 480)))`.

### 5. `StaticText.Wrap()` at hard-coded pixel widths
`blindpilot_app.py:3970` `Wrap(520)`, `:4062` `Wrap(560)`, `:4227` `Wrap(520)`, `:7506`/`:7526`/`:7528`
`Wrap(520)`, `:8393` `Wrap(520)`; `update_dialog.py:104` `Wrap(590)`; `accounts.py:158` `Wrap(640)`,
`:193` `Wrap(620)`, `:231` `Wrap(640)`. Two consequences: at 200% DPI the paragraph occupies half the
dialog width with a large empty gutter on the right, and the wrap is baked in at construction so
resizing a `wx.RESIZE_BORDER` dialog never reflows the text.
**Fix:** `Wrap(self.FromDIP(520))`, and re-wrap on `wx.EVT_SIZE` for the resizable dialogs
(`SettingsFilesDialog`, `SetupWizard`, `UpdateDialog`, `AccountDialog`).

### 6. Labels and their controls use different borders — ragged left edge
`blindpilot_app.py:5313-5321` (SessionPanel): labels added with border `8`, the controls under them
with `6`. Every label in the main window sits 2 px right of the control it names.
`blindpilot_app.py:9046-9049` (MainFrame): mode picker row `8`, tab strip `4`, notebook `4`.
`accessible_ai/ui/chat_panel.py:190` (10) vs `:193` (6) vs `:196` (8) vs `:172`/`:176` (10).
Across the app borders are 4, 6, 8, 10, 12, 14 and 24 with no rule.
**Fix:** one module constant, e.g. `PAD = 8` / `PAD_DIALOG = 12`, used for every `Add(...)` border.

### 7. Empty `wx.Notebook` used purely as a tab strip
`blindpilot_app.py:9019-9021` creates a real `wx.Notebook` with `SetMinSize(wx.Size(-1, FromDIP(38)))`
and `:9679` fills it with empty `wx.Panel`s; the real content lives in a separate `wx.Simplebook`
(`:9042`). A native notebook always draws its page frame, so a sighted user sees the tab row followed
by an empty, bordered ~14 px band, then a second unrelated content area — it reads as a rendering bug.
The rationale (`:9016-9018`, `:9038-9041`) is screen-reader behaviour, so the control must stay.
**Fix:** shrink the min height to the strip only (`tab_switcher.GetBestSize().y` after one page is
added) so no page area is visible, or replace with a `wx.ToggleButton` segmented row that carries the
same tab semantics via `SetName`.

### 8. No dark-mode support
`main()` at `blindpilot_app.py:10723` calls `wx.App(False)` and never calls
`wx.App.SetAppearance()` / `MSWEnableDarkMode()` / checks `wx.SystemSettings.GetAppearance()`.
On Windows 11 with dark mode enabled the whole app stays light while the title bar is dark —
the most obviously dated thing about it.
**Fix:** immediately after `app = wx.App(False)`, `app.SetAppearance(wx.App.Appearance.System)`.
This is low-risk here (see "already good" — there are no hardcoded colours to fight it).

### 9. Nothing visual indicates a run is in progress
The only run-state signals are audio earcons, screen-reader speech, one line of status-bar text
(`blindpilot_app.py:10507-10508`) and button enable/disable (`:6209`, `:6246-6250`, `:6305-6306`,
`:6829-6833`). There is no gauge, no `wx.ActivityIndicator`, no busy cursor in `SessionPanel` —
a sighted user cannot tell a 90-second run from a hung one. The codebase already has a `wx.Gauge`
(`update_dialog.py:73`), just not here.
**Fix:** add an indeterminate `wx.Gauge` (or `wx.ActivityIndicator`) to the bottom row at
`blindpilot_app.py:5303-5310`; `Pulse()`/`Start()` alongside `earcons.start_progress()`, hide on
`_on_worker_finished` (`:6822`).

### 10. Turn failures leave no trace in the transcript
`blindpilot_app.py:6810-6820` `_on_failed` plays an error earcon and calls `_announce`, which writes
one line to the status bar (`:5810-5813`). No row is added to the responses list and the status bar
is overwritten by the next event. Visually a failed turn is indistinguishable from a completed one:
Send re-enables and the list is unchanged.
**Fix:** append a `Row(kind="error", label=f"Error: {message}", ...)` before announcing, and give
error rows a distinguishing prefix (see 11).

### 11. Content types are distinguished by text prefix only, and inconsistently
`markdown_rows.py:219-227` `_prose_label`: lists get `"List: "`, quotes `"Quote: "`, headings and
prose get **nothing**. `blindpilot_app.py:6690` deliberately drops the "Thinking:" prefix from
reasoning rows. So headings, body prose and the model's private reasoning render as three identical
grey lines. User messages (`:6663`), results (`:2378`) and assistant text (`:6708-6716`) do carry
prefixes. No colour, no icon, no indentation, no font weight anywhere.
**Fix:** keep the prefixes for speech but add a purely visual layer once the list is a
`VListBox`/`HtmlListBox` (finding 1): bold headings, indent thinking, monospace code, distinct
background for errors — all sourced from `wx.SystemSettings.GetColour()` so contrast themes work.

### 12. Window size and position are never saved; no minimum size
`MainFrame.__init__` (`blindpilot_app.py:8738`) hard-codes 900×760 and binds no `EVT_SIZE`/`EVT_MOVE`;
`_save_config` / `_load_config` are used for everything else. There is no `SetMinSize`, so the frame
can be dragged down to a sliver with the tab strip, prompt and button row overlapping.
**Fix:** `self.SetMinSize(self.FromDIP(wx.Size(640, 480)))` and persist `GetRect()`/`IsMaximized()`
into the existing config on `EVT_CLOSE`.

### 13. Hand-rolled right-aligned button rows in five places
`blindpilot_app.py:3825-3830` (SettingsFilesDialog), `:4241-4245` (ConnectDialog),
`update_dialog.py:76-83`, `accessible_ai/ui/diagnostics.py:24-28`,
`accessible_ai/ui/profiles.py:363-373` (ProfilesDialog, which stacks Add/Edit/Duplicate/Delete on one
row and Close on a second row below — two button rows where one belongs). These get Windows spacing
by accident and the wrong button order on macOS, where Cancel must sit left of OK.
**Fix:** `CreateSeparatedButtonSizer(...)` / `wx.StdDialogButtonSizer` + `Realize()` for the
OK/Cancel pair, leaving action buttons (Open, Connect, Refresh) in their own left-aligned row.

### 14. `StdDialogButtonSizer` added with `wx.ALIGN_RIGHT` instead of `wx.EXPAND`
`blindpilot_app.py:3976`, `:4107`, `:4652`, `:4807`, `:5017`. `Realize()` has already applied the
platform's button order and margins; re-aligning the sizer right discards the leading spacer and,
on macOS, the mandated right-edge inset. The same file gets it right at `:8455` and `:8682`, and
`accounts.py:240` / `profiles.py:355` also use `EXPAND` — so the app is internally inconsistent.
**Fix:** change those five to `wx.EXPAND | wx.ALL, 12`.

### 15. `SetSizerAndFit()` followed by `SetSize()`
`blindpilot_app.py:4808-4809` and `:5019-5020`. `Fit` establishes the sizer minimum; the following
`SetSize(620, 460)` / `(640, 480)` is silently clamped up whenever the fitted content is larger —
which it will be at 150% DPI or with a long localised label. The dialog is then a different shape
per machine for no visible reason.
**Fix:** drop the `SetSize` and instead give the list control a `FromDIP` min size, or call
`SetSize` before `SetSizerAndFit` and use `SetSizer` + `Layout`.

### 16. No monospace font anywhere
No `wx.FONTFAMILY_TELETYPE` or `wx.Font` construction in the codebase. `ReadView`
(`blindpilot_app.py:3879-3884`) is the only way to see code and shows it with `wx.TE_DONTWRAP` in the
proportional UI font, so indentation and column alignment collapse. Same for the installer output
box (`:7546`) and the diagnostics log (`accessible_ai/ui/diagnostics.py:19`).
**Fix:** `ctrl.SetFont(wx.Font(wx.FontInfo().Family(wx.FONTFAMILY_TELETYPE)))` in `ReadView` when the
row kind is `code`, in `_cli_log`, and in `DiagnosticsDialog.text`.

### 17. Dialog centring is inconsistent
Only `PreferencesDialog` (`blindpilot_app.py:8684`) and the chat_panel edit dialog
(`accessible_ai/ui/chat_panel.py:408`) call `CentreOnParent()`. The other twelve dialogs rely on the
default placement, which is not the same across MSW/GTK/Cocoa and is not the same for resizable vs
fixed dialogs.
**Fix:** call `self.CentreOnParent()` as the last line of each dialog's `__init__`.

### 18. The PyInstaller EXE carries no version resource
`BlindPilot.spec:76-88` — `EXE(...)` has no `version=` argument, so Explorer's Properties → Details
tab is blank and the SmartScreen / UAC prompt shows no product name or publisher. `APP_VERSION` is
already parsed at `BlindPilot.spec:24-31` and only used for the macOS plist.
**Fix:** build a `VSVersionInfo` from `APP_VERSION` and pass `version=` on Windows.

### 19. The Inno Setup installer has no icon
`installer/BlindPilot.iss` sets `UninstallDisplayIcon` (`:51`) but no `SetupIconFile=`, so the
downloaded `setup.exe` shows Inno Setup's generic icon before the user ever sees the app.
**Fix:** `SetupIconFile=..\packaging\BlindPilot.ico` in `[Setup]`.

### 20. Sparse tooltips
Six `SetToolTip` calls in `blindpilot_app.py` (`:5279`, `:5287`, `:5507`, `:5511`, `:9009`) and six in
`accessible_ai/ui/profiles.py`. Nothing in the settings, history, connect, remote-Hermes or accounts
dialogs — a sighted user hovering anything in `RemoteHermesDialog` (`:8399-8428`) or
`AccountDialog` (`accounts.py:132-220`) gets nothing.
**Fix:** attach the same help text already written for the screen reader as `SetToolTip`.

### 21. Minor spacing/dead-code items
- `blindpilot_app.py:8578` `size=wx.Size(620, -1)` is dead: `root.Fit(self)` at `:8683` overwrites it.
- `blindpilot_app.py:8618` `cues_box.Add(check, 0, wx.LEFT, 24)` gives the nested sound-cue checkboxes
  zero vertical gap while every sibling checkbox uses `wx.TOP, 8` — they look crammed together.
- `blindpilot_app.py:7454-7456` the wizard's step heading is `GetPointSize() + 2` bold. On Windows
  that is 9→11 pt, barely a heading; on macOS 13→15 pt. Use a proportional bump or
  `wx.FONTWEIGHT_BOLD` alone plus more vertical space.
- `blindpilot_app.py:4795-4798` `pickers.AddGrowableCol(1, 1)` but the `wx.Choice` controls are added
  with proportion 0 and no `wx.EXPAND`, so the column stretches and the controls do not — the
  Backend/Show pickers sit left in a wide empty column. `RemoteHermesDialog:8443` does this correctly.

---

## What is already good

- **Zero hardcoded colours.** No `SetForegroundColour` / `SetBackgroundColour` / `wx.Colour` anywhere
  in the UI code. Everything inherits system colours, so high-contrast themes work today and dark
  mode will work as soon as finding 8 is applied.
- **Only one `SetFont` call** (`blindpilot_app.py:7456`) — no font-size drift between dialogs.
- **`CreateStdDialogButtonSizer` is used in seven dialogs** (`:3969`, `:4098`, `:4648`, `:4787`,
  `:5005`, `:8435`, `chat_panel.py:403`), so OK/Cancel order is platform-correct there, and button
  relabelling (`:4100-4106`, `:4790-4792`) keeps the standard IDs.
- **`wx.FlexGridSizer` with `AddGrowableCol(1, 1)` for label/control forms** (`:3963`, `:8437`,
  `accounts.py:129`, `profiles.py:62`, `chat_panel.py:118`) — correct label-to-control alignment.
- **`wx.EXPAND` and proportion 1 are applied consistently to the growable control** in every dialog
  (list boxes, text areas), so resizing behaves.
- **`wx.StaticLine` separators** group the Preferences sections (`:8610`, `:8623`, `:8663`).
- **Status bar exists and is routed per-tab** (`:9052`, `:10507-10513`) — right idea, just underused.
- **`wx.RESIZE_BORDER` on the dialogs that need it** (`:3802`, `:3877`, `:4029`, `:4749`, `:4974`,
  `:7442`, `update_dialog.py:41`, `diagnostics.py:15`, `profiles.py:347`).
- **`FromDIP` is used correctly in the one place it appears** (`:9021`) — the pattern is understood,
  just not applied.
- **macOS is handled properly where it matters**: `SetAppName`/`SetAppDisplayName` (`:10725-10726`),
  `wx.ID_PREFERENCES` auto-relocation (`:8566-8571`), Cmd-key label rewriting (`:8549`), and
  `wx.adv.AboutBox` for the native About panel (`:9450`). No macOS-only geometry hacks leak into the
  Windows path.
