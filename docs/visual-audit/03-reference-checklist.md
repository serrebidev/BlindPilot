# Visual Audit Reference Checklist

Sighted-user visual expectations for a native desktop app, mapped to concrete wxPython/wxWidgets 3.3 APIs. One line per item; verify against source before relying on memory.

## 1. Windows 11 Fluent conventions

- [ ] Default UI font is Segoe UI Variable (Win32 apps typically render via "Segoe UI" 9pt at 96 DPI). https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/typography
- [ ] Segoe UI Variable optical-size axis scales glyph shape 8pt-36pt; classic GDI apps get static "Segoe UI" fallback, not the variable font. https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/typography
- [ ] Fluent 2 spacing scale is 4px-based: XXS 2, XS 4, S 8, M 12, L 16, XL 20, XXL 24, XXXL 32 — audit control/group gaps against these steps. https://fluent2.microsoft.design/layout
- [ ] Classic Win32 dialog margins: 7 DLU between related controls / 11 DLU dialog edge margin, min 3 DLU (~5px) gap between any non-touching controls. https://learn.microsoft.com/en-us/previous-versions/windows/desktop/bb226818(v=vs.85)
- [ ] Dependent/child controls indent 12 DLU (~18px) from parent control's left edge. https://learn.microsoft.com/en-us/previous-versions/windows/desktop/bb226818(v=vs.85)
- [ ] Standard command button size in Win32 layout spec: 50x14 DLU; Fluent min touch target for buttons is 40px (changed from 44px in 2018 guidance). https://learn.microsoft.com/en-us/previous-versions/windows/desktop/bb226818(v=vs.85) ; https://fluent2.microsoft.design/layout
- [ ] Fluent corner radius: 4px for standard buttons/rectangles, 2px for small (<32px) shapes — check for square-cornered wx buttons/panels that look pre-Win11. https://fluent2.microsoft.design/layout
- [ ] Windows 11 rounds top-level window corners at the DWM/compositor level for most apps automatically; verify BlindPilot's frame isn't opting out or rendering square via custom borders. https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/ui/apply-rounded-corners
- [ ] Dark mode must follow the OS setting, not be absent — use `wx.SystemSettings.GetAppearance().IsDark()` to branch custom colours. https://docs.wxpython.org/wx.SystemAppearance.html
- [ ] Mica/acrylic backdrops are a WinUI3/XAML feature, not applicable to classic wx/Win32 HWND apps — do not attempt; focus dark-mode effort on system colour APIs instead. (No wx equivalent exists.)

## 2. macOS HIG differences

- [ ] Button order: affirmative/default action is rightmost, Cancel sits immediately to its left (opposite of Windows OK-then-Cancel). https://www.nngroup.com/articles/ok-cancel-or-cancel-ok/
- [ ] `wx.StdDialogButtonSizer.Realize()` auto-applies the correct per-platform order/spacing (Windows/GTK/macOS) — never hardcode button order manually. https://docs.wxpython.org/wx.StdDialogButtonSizer.html
- [ ] Preferences/Settings window has no OK/Cancel; changes apply immediately, Help button (if any) sits lower-right. https://leopard-adc.pepas.com/documentation/UserExperience/Conceptual/AppleHIGuidelines/XHIGControls/XHIGControls.html
- [ ] Use a sheet (attached, slide-down modal) for document/window-scoped tasks; use a separate window/alert for app-wide or complex tasks. https://developers.apple.com/design/human-interface-guidelines/components/presentation/sheets/
- [ ] Minimum window size guidance: roughly 480-600pt wide x 320-400pt tall as a starting point; always set explicit min/max size when resizable. https://zenn.dev/usagimaru/articles/b2a328775124ef?locale=en

## 3. wxWidgets 3.3 specifics

- [ ] `wx.App.SetAppearance(wx.App.Appearance.System | .Light | .Dark)` — new in wxWidgets 3.3.0/wxPython 4.3, sets app-wide appearance before windows are created. https://wxwidgets.org/blog/2024/10/hello-darkness/
- [ ] `wxSystemAppearance::IsDark()` now reflects the *app's* dark-mode state on MSW; use `AreAppsDark()`/`IsSystemDark()` for the system-wide setting. https://github.com/wxwidgets/wxwidgets/blob/master/docs/changes.txt
- [ ] `wx.SystemSettings.GetAppearance()` returns a `wx.SystemAppearance` with `IsDark()`, `IsUsingDarkBackground()`, `GetName()` for querying current theme. https://docs.wxpython.org/wx.SystemAppearance.html
- [ ] **`MSWEnableDarkMode()`/dark mode on wxMSW is explicitly experimental and disabled by default** — Microsoft provides no official Win32 dark-mode API, so wxWidgets relies on undocumented calls; known issues remain open in 3.3.1 (disabled buttons/static text, notebook backgrounds in high-contrast, toolbar selection). Opt-in via `SetAppearance(Appearance::System)` in code or `wx_msw_dark_mode` env var (1=system, 2=force dark). https://wxwidgets.org/blog/2024/10/hello-darkness/ ; https://github.com/wxwidgets/wxwidgets/blob/master/docs/changes.txt
- [ ] `wx.Window.FromDIP(size_or_point)` converts DIPs to logical/physical pixels — use for every hardcoded pixel size instead of literal ints. https://docs.wxwidgets.org/3.3/ (high_dpi overview, doxygen/overviews/high_dpi.md)
- [ ] `wx.SizerFlags.GetDefaultBorder()` (and `GetDefaultBorderFractional()`) returns the DPI-scaled standard border; prefer `.Border()/.DoubleBorder()/.TripleBorder()` over hardcoded pixel margins in sizers. https://docs.wxpython.org/wx.SizerFlags.html
- [ ] `wx.StdDialogButtonSizer` (add via `AddButton`/`SetAffirmativeButton`/`SetCancelButton`, then `Realize()`) guarantees correct button order/spacing per OS — audit any dialog using a raw `wx.BoxSizer` for its button row instead. https://docs.wxpython.org/wx.StdDialogButtonSizer.html
- [ ] `wx.IconBundle` + `wx.TopLevelWindow.SetIcons()` supply multiple icon resolutions (16/32/48/256px) so taskbar, Alt-Tab, and title bar each get a crisp size — a single `SetIcon()` call or the default wx icon is a "dated/unbranded" tell. https://docs.wxpython.org/wx.IconBundle.html
- [ ] PyInstaller-built exe needs an embedded manifest with `dpiAwareness=PerMonitorV2` (and `dpiAware=true` for pre-1703 fallback) or the app renders blurry/scaled on high-DPI monitors; wx's own `wx/msw/wx.rc` uses `wxUSE_DPI_AWARE_MANIFEST` (1=system DPI aware, 2=per-monitor) when compiled, but a PyInstaller-frozen script does not inherit that automatically — must supply/patch the manifest manually. https://docs.wxpython.org/high_dpi_overview.html
- [ ] `wx.VListBox` supports variable-height, owner-drawn rows (e.g., wrapped multi-line text) via `OnDrawItem`/`OnMeasureItem`; plain `wx.ListBox` only supports fixed-height single-line native rows — if BlindPilot wraps text in a ListBox it will look truncated/clipped. https://docs.wxwidgets.org/3.3/classwx_v_list_box.html (see also wxWidgets changes_32.txt for related listbox enhancements)
- [ ] `wx.RichTextCtrl` supports styled runs, images, and proper wrapping for read-only display text; a `wx.TextCtrl` with `wx.TE_RICH2` only gives RTF-like styling with a plainer look and weaker wrapping/layout control — prefer RichTextCtrl for any "nicely formatted read-only" panel. https://github.com/wxwidgets/wxwidgets/blob/master/samples/richtext/readme.txt
- [ ] `wx.ActivityIndicator` (`Start()`/`Stop()`) is the correct control for "app is busy, no known duration" (spinning indicator); `wx.Gauge` is for measurable/determinate progress — using a Gauge in indeterminate pulse mode where an ActivityIndicator belongs is a common dated-UI mismatch. https://docs.wxpython.org/wx.ActivityIndicator.html

## 4. Contrast and focus visibility

- [ ] WCAG 1.4.11 Non-text Contrast (AA, WCAG 2.1+): UI component states (borders, focus rings, checkbox/toggle state) need >=3:1 contrast against adjacent colors. https://w3c.github.io/wcag21/understanding/non-text-contrast.html
- [ ] Focus indicators fall under both 1.4.11 (contrast of the indicator itself) and 2.4.7 Focus Visible (a focus indicator must exist at all) — verify wx's default dotted-rectangle/native focus rect is not swallowed by custom-drawn or owner-drawn controls (VListBox, custom buttons). https://w3c.github.io/wcag21/understanding/non-text-contrast.html
- [ ] Because BlindPilot relies on system colours (`wx.SystemSettings.GetColour`), contrast is only as good as the active Windows/macOS theme+accent combo — audit under both light and dark system themes and under a non-default accent colour, not just defaults. https://docs.wxpython.org/wx.SystemAppearance.html

## 5. Common "dated app" tells to check for

- [ ] Cramped/inconsistent borders — sizer items not using `GetDefaultBorder()`/DIP-scaled spacing, mixed hardcoded pixel margins. https://docs.wxpython.org/wx.SizerFlags.html
- [ ] Mismatched button widths/heights in the same row instead of equal-sized `wx.StdDialogButtonSizer` buttons. https://docs.wxpython.org/wx.StdDialogButtonSizer.html
- [ ] Unaligned labels/fields — not using `wx.FlexGridSizer`/`wx.GridBagSizer` column alignment, static text baselines not matching adjacent control baselines.
- [ ] Hardcoded fonts/point sizes (e.g., literal `wx.Font(8, ...)`) instead of `wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)` or DIP-scaled sizes — breaks Fluent/Segoe UI Variable and user font-size prefs. https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/typography
- [ ] Default/generic wx application icon (or none) instead of a branded `wx.IconBundle` — the single most obvious "unfinished app" signal in taskbar/Alt-Tab. https://docs.wxpython.org/wx.IconBundle.html
- [ ] No dark mode / stark white windows on a dark-mode OS while every native app around it is dark — check via `wx.SystemSettings.GetAppearance().IsDark()`. https://wxwidgets.org/blog/2024/10/hello-darkness/
- [ ] Horizontal scrollbars appearing on list/tree controls because columns aren't sized to content — indicates missing `SetColumnWidth`/autosize logic on `wx.ListCtrl`/`wx.TreeCtrl`, a classic "not touched since XP" tell.
- [ ] Non-DPI-aware rendering (tiny controls or blurry text on a 4K/150% display) from a missing PyInstaller manifest — see PyInstaller DPI item above. https://docs.wxpython.org/high_dpi_overview.html
