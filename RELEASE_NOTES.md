# BlindPilot 0.47.2

This release fixes keyboard navigation in Agent mode's Recent Conversations.

- Tab and Shift+Tab now reach the conversation list in Recent Conversations (Ctrl+Shift+H). Before, the arrow keys worked once you were in the list, but tabbing away and back skipped it in both directions, so there was no way to find it again without closing the dialog. Windows only stops Tab on windows marked as tab stops, and the list, which BlindPilot draws itself so long titles wrap, never had that mark. It does now, and the Hermes conversations and slash command pickers, which use the same list, are fixed too. Chat mode's History list and its Recent Conversations already used a standard list and were not affected. Thanks to Kaveinthran for the report.
- Running BlindPilot from source on Python 3.13 or later no longer breaks the Gemini and Antigravity backends. Python's own thread class started using the name their message handler had, which hid it. The released builds use Python 3.12 and were not affected.
