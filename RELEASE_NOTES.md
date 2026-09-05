# BlindPilot 0.21.3

Fixes FreeBuff requests getting stuck when sent during or after background
startup.

- macOS and Linux now read FreeBuff's terminal output from launch, so an
  unread output buffer cannot stall background startup.
- Sending a message adopts the terminal being started. It also cancels stale
  delayed starts, preventing competing sessions from confusing the readiness
  and completion checks.
- Completion and disconnect checks now read actual log events. Words inside
  your prompt or the answer cannot falsely end a turn or release a held message.

Verified with the full regression suite, lint, formatting, type checks, and
packaged macOS startup checks. Real FreeBuff tests covered immediate sending
and sending after background startup; both returned their answers and finished.

BlindPilot remains available under the MIT License and is based on Claude Code
Reader, with credit to the original project throughout.
