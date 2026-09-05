# BlindPilot 0.21.4

One Codex serves every tab between messages, and FreeBuff turns no longer die
mid-answer with "string index out of range".

- Codex keeps its app server between messages instead of starting a new one
  for every prompt, shared across tabs, so the second message and the second
  tab pay nothing to start. Stop now interrupts the turn rather than ending
  the process every other tab is using; a conversation that could not be
  interrupted is given up alone and resumed from Codex's own record on the
  next message. A backend left idle for fifteen minutes is closed, and one
  that stopped on its own is restarted, both announced out loud rather than
  left to look like a hang.
- The terminal screen FreeBuff is read off can no longer crash the reading
  loop. A redraw that repaints the cell holding an emoji or CJK character
  without its blank filler left the terminal emulator holding an empty cell,
  and the next read of the screen raised IndexError, ending the turn with
  nothing to show for it. Those cells are repaired before the screen is read,
  so a turn that is still running now keeps running.

Verified with the full regression suite, lint, formatting, and type checks,
including a regression test built from the exact terminal sequence that
crashed FreeBuff's reader.

BlindPilot remains available under the MIT License and is based on Claude Code
Reader, with credit to the original project throughout.
