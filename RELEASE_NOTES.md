# BlindPilot 0.21.4

One Codex serves every tab between messages, and FreeBuff turns no longer die mid-answer with "string index out of range".

- Codex keeps its app server running between messages and shares it across tabs, so the second message and the second tab start at once instead of waiting for a cold start.
- Stop interrupts the current Codex turn instead of killing the process every tab is using. A turn that cannot be interrupted is dropped alone and resumed from Codex's own record on the next message.
- A Codex left idle for fifteen minutes is closed, and one that stopped on its own is restarted. Both are announced instead of showing up as a silent wait.
- FreeBuff's terminal screen no longer crashes the reader. A redraw over an emoji or CJK character left the terminal emulator holding an empty cell, and reading it raised IndexError. Those cells are repaired before the screen is read.

Verified with the regression suite, lint, formatting, and type checks, including a test built from the exact terminal sequence that crashed FreeBuff's reader.
