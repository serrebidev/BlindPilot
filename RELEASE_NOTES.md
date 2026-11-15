# BlindPilot 0.57.12

Computer Use now lives in Conversation, Find and Browse as an agent item, beside Queued Messages and the other session tools. In Chat mode it opens the same opt-in dialog; in Agent mode it explains computer use is Chat-only. It is no longer in the Chat menu, and the history list's context menu still carries it.

For the features in it, see the 0.57.8 and 0.57.10 notes: opt-in automatic computer use via accessibility trees on Windows and macOS, Ctrl+Shift+F12 stop on Windows, ask-before-acting option.
Windows ships an installer and portable archive; macOS ships an Apple Silicon archive. Linux remains source-only.

Computer Use moved to the Chat menu. It used to live only on the conversation history list's context menu (Shift+F10), which is hard to find with a screen reader. The same Computer Use… entry now sits in the Chat menu, reachable with the keyboard like every other Chat command. Both entries open the same opt-in dialog.

For the features in it, see the 0.57.8 and 0.57.10 notes: opt-in automatic computer use via accessibility trees on Windows and macOS, Ctrl+Shift+F12 stop on Windows, ask-before-acting option.
Windows ships an installer and portable archive; macOS ships an Apple Silicon archive. Linux remains source-only.

macOS computer use: the same opt-in per-conversation control offered on Windows in 0.57.8, now driving other apps through their macOS accessibility trees (numbered controls, press and set-value actions, peer validation, no coordinates, BlindPilot itself cannot be operated).

Automatic actions need Accessibility permission. Starting computer use on a Mac requests it once through the system prompt; without it, the app says exactly where to enable it (System Settings, Privacy and Security, Accessibility) instead of failing silently. The stop key is Windows-only in this release, so on macOS the Ask-before-each-action mode is the recommended setting; in-window Stop always works.

For the features in it, see the 0.57.8 notes: automatic and ask-approved actions, Ctrl+Shift+F12 on Windows, 25-action turn bound.
Windows ships an installer and portable archive; macOS ships an Apple Silicon archive. Linux remains source-only.
