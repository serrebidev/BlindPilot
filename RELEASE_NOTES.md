# BlindPilot 0.57.10

macOS computer use: the same opt-in per-conversation control offered on Windows in 0.57.8, now driving other apps through their macOS accessibility trees (numbered controls, press and set-value actions, peer validation, no coordinates, BlindPilot itself cannot be operated).

Automatic actions need Accessibility permission. Starting computer use on a Mac requests it once through the system prompt; without it, the app says exactly where to enable it (System Settings, Privacy and Security, Accessibility) instead of failing silently. The stop key is Windows-only in this release, so on macOS the Ask-before-each-action mode is the recommended setting; in-window Stop always works.

For the features in it, see the 0.57.8 notes: automatic and ask-approved actions, Ctrl+Shift+F12 on Windows, 25-action turn bound.
Windows ships an installer and portable archive; macOS ships an Apple Silicon archive. Linux remains source-only.
