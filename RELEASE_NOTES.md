# BlindPilot 0.21.0

BlindPilot is an accessible desktop front end for AI coding agents, built with
native wxPython controls so NVDA, JAWS, and VoiceOver read the application
itself. It is based on Claude Code Reader and remains available under the MIT
License, with credit to the original project throughout.

This release makes the app feel like a Mac app instead of one that merely runs
on a Mac.

## The shortcuts that were wrong

The chords BlindPilot owns that wxWidgets already mapped to Command (Cmd+T,
Cmd+W, Cmd+F, Cmd+Q, Cmd+Shift+A, Cmd+/, Cmd+R, Cmd+Shift+]/[) were fine. Three
were not, all because menu accelerators are written as Ctrl and turned into
Command at runtime:

- **Ctrl+H** — Recent Conversations — became the system's **Hide BlindPilot**,
  which lives in the application menu and wins. A VoiceOver user pressing the
  documented chord had the whole app vanish. It is now **Ctrl+Shift+H**.
- **Ctrl+M** — Model and Effort — became the system's **Minimize**. It is now
  **Ctrl+Shift+E**.
- **Ctrl+Tab** to switch sessions became Cmd+Tab, which belongs to the system
  application switcher and never reached the app at all. The menu now names the
  chords that actually work: **Cmd+Shift+]** and **Cmd+Shift+[** on macOS.

The menu labels that are read aloud as literal text — Attach Files, Slash
Command, Jump to Latest Response — now say **Cmd** on macOS instead of telling
you a Control chord you do not have.

## Settings live where a Mac user looks

Settings used to sit in the Linux-style `~/.config/blindpilot` and managed
CLIs in `~/.local/share/blindpilot`, while the chat database lived in the
native `~/Library/Application Support/BlindPilot`. Everything now lives in one
place: `~/Library/Application Support/BlindPilot`. On the first launch of this
version, an older install's files are moved there once — entry by entry, never
overwriting anything already present, and never failing to start if a move
does not go through.

## Preferences… at Cmd+,

The application menu now has a **Preferences…** item (Cmd+,), the way every
Mac app does. It opens one dialog holding every Options-menu setting — live
activity, speaking, narration mode, reasoning, the four sound cues and the
working-sound interval, the read-only text view, and update checks — applied
through the same switches the menu items use, so the menu and the dialog can
never disagree.

## A finished product in every sense

- **About** uses the native macOS About panel with the app's name and icon.
- The application menu reads "BlindPilot" even when run from source.
- **Create Desktop Shortcut** now works on macOS, instead of reporting that
  shortcuts are a Windows thing.
- The app has an icon for the first time — a gradient tile with a white
  prompt chevron, rendered by `tools/make_icon.py` (pure standard library,
  no image dependency) into `.icns` and `.ico`.
- `BlindPilot.spec` now builds every platform and carries the bundle
  identifier, the version, `LSMinimumSystemVersion` 10.15, the icon, and all
  the per-platform hidden imports, so Get Info describes a finished product
  and the release workflow no longer names PyInstaller flags by hand.

## Verification

The full suite is green under `-W error` — 1131 passed, 8 skipped — including
new tests for the macOS directory migration (fake platform, runs everywhere),
the Preferences dialog read/apply/enable behaviour, and the platform-aware
menu labels. ruff check and ruff format are clean, and the packaged startup
smoke test passes. Two tests were made hermetic along the way: the startup
tests' fake application object gained the app-name methods, and a
Windows-searching environment variable that was sitting in a developer's real
environment no longer leaks into the POSIX test.