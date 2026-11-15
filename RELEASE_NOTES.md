# BlindPilot 0.57.8

This is the final release in the feature comparison with The Chat Place and FastChat: automatic computer use on Windows.

Computer Use is a new entry in the Chat history context menu. It is opt-in per conversation: choose the app (or just use the window in front), and the model can then act on it through its accessibility tree, as a numbered list of controls with press and set-text actions. There are no coordinates in this design; if the target window disappears, the action fails instead.

Actions run automatically by default. An "Ask before each action" box offers approval per action for those who want it. The stop key is Ctrl+Shift+F12; it is registered before automatic actions start, works with another app in front, and cancels pending work. In-window Stop does the same thing. BlindPilot itself cannot be operated (protection by process id), each action re-validates its target window first, and turns are bounded at 25 actions.

macOS computer use is not in this release and follows in a later one. Windows ships an installer and portable archive; macOS ships an Apple Silicon archive. Linux remains source-only.
