# BlindPilot 0.21.2

BlindPilot is an accessible desktop front end for AI coding agents, built with
native wxPython controls so NVDA, JAWS, and VoiceOver read the application
itself. It is based on Claude Code Reader and remains available under the MIT
License, with credit to the original project throughout.

This release fixes a regression in 0.21.1: FreeBuff's own boot line ended a
turn that had not started, so the first message after launching the app died
one second in with "FreeBuff dropped its session…" when nothing was wrong.

## What was actually happening

The 0.21.1 drop detection failed a turn the instant "Freebuff session over;
holding queued messages until rejoin" appeared in the chat log. The logs of
both the working and the failing chats told the real story, and it is the
opposite reading:

- **"Session over" is the first line of every healthy boot.** FreeBuff 0.0.168
  opens each new chat with it, logs "Reconnection detected" within half a
  second, and delivers anything queued. A working chat at 01:05:39 shows the
  whole sequence, ending in "Start agent … glm-5-3-flash" — the message ran on
  GLM exactly as chosen. 0.21.1 killed such turns one second in and stopped
  listening.
- **The message is genuinely lost when it is typed too early.** A message
  given to FreeBuff's composer before the runtime behind it has reconnected is
  discarded silently: the chat log records no send at all. The composer being
  painted is not the same as the session being live.

## What changed

- **The message is held until the boot is live.** The composer appearing no
  longer triggers the send. The worker reads FreeBuff's chat log and types the
  message only once a "Reconnection detected" line has answered the boot's
  "session over". A one-time "FreeBuff is still starting; holding the message
  until it is ready" is said while it waits. A boot that never reconnects ends
  the hold after the startup-silence budget with the 0.21.1 remedy — the right
  words for a session that is genuinely gone.
- **A mid-turn drop gets thirty seconds of patience** instead of failing on
  sight: the session can rejoin on its own, so the drop is announced, watched,
  and only ends the turn if nothing answers it. The watch is cancelled the
  moment the reconnection lands.
- **Drop detection reads the log in order.** "Session over" only means a drop
  when no later "Reconnection detected" or "Start agent" line answers it — the
  last word the log has on the session is what counts.

## Verification

`tests/test_freebuff_session_drop.py` grew to fifteen tests: the hold
releasing on a late reconnection, a hold that never reconnects ending with the
remedy, a normal boot sending with no hold at all, the patience window on a
mid-turn drop, and every 0.21.1 reading kept. The full suite is green under
`-W error` — 1147 passed, 8 skipped — in both the fixed and the shuffled test
order; ruff check and ruff format are clean.
