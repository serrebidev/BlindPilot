# BlindPilot 0.28.0

Command Code is now a backend BlindPilot can drive, alongside Claude Code, Codex, FreeBuff, opencode, and Hermes.

- Install it from Model, Manage Backends, or by hand with `npm install -g command-code` and `command-code login`. The wizard installs it for your user, verifies that it starts, adds it to PATH, and signs you in.
- Answers stream a sentence at a time, with tool calls and their results shown as they happen. The model picker reads Command Code's own catalog and opens on the model and reasoning effort its config records.
- Permission modes map onto Command Code's: Default, Accept edits, Plan, Auto, Don't ask, and Bypass permissions (its launch-only `--yolo`).
- Past conversations are listed under Recent Conversations and reopen by their session id.
- Codex sign-in is fixed: BlindPilot now opens the sign-in page itself, because Codex's own browser launch does not reliably arrive when `codex login` runs hidden. Codex's `http://localhost:1455` callback address is no longer read out as the page to visit.

Command Code runs one process per message, so a running turn cannot be steered the way the other backends allow. Its built-in slash commands are interactive-only — a slash string sent headlessly is treated as text — so the picker lists them as a discovery aid, and compaction is not offered.

Verified against Command Code 1.53.1 on Windows: a turn streams text and thinking, runs a tool and reports its result, names the session it created, and resumes that conversation on the next message.
