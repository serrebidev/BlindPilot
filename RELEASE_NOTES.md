# BlindPilot 0.21.1

BlindPilot is an accessible desktop front end for AI coding agents, built with
native wxPython controls so NVDA, JAWS, and VoiceOver read the application
itself. It is based on Claude Code Reader and remains available under the MIT
License, with credit to the original project throughout.

This release fixes the FreeBuff backend losing a message to a session nobody
can see — the "I send a message and it just sits there" report, found,
reproduced and fixed on a real install of FreeBuff 0.0.168.

## What was actually happening

BlindPilot drives the `freebuff` CLI through a hidden pseudo-terminal and reads
the answer off its screen. Three defects stacked up, two of them introduced by
FreeBuff 0.0.168 and one of them a FreeBuff bug BlindPilot can only report:

1. **The message went to the wrong model.** 0.0.168's welcome screen stopped
   drawing the `›` focus marker its predecessors drew. The adapter, which only
   trusted a marked row, could not see where the highlight was and pressed
   Enter on the recommended card — so a message meant for GLM 5.3 Flash ran on
   GPT-5.6 Luna.
2. **The picker changed shape.** The welcome screen now opens with the full
   model list already expanded ("↑ Show fewer"). The old code pressed Down to
   reach a "See all models" entry that no longer exists, which would have
   walked off the first card and sent the message to the second one.
3. **FreeBuff can drop the session and say nothing.** 0.0.168 logs "Freebuff
   session over; holding queued messages until rejoin" into a brand-new chat,
   keeps its terminal and composer running, accepts the message, and never
   answers it. Nothing on screen distinguishes this from working, so a turn
   waited out its whole hour in silence.

## What changed

- The model picker is read **positionally**: with no marker on screen, the
  content row of the first painted card is where the highlight starts, on both
  the welcome screen and the expanded list. The rule is held back whenever the
  catalog cannot name that first model, so a degraded catalog reports no focus
  rather than a wrong one.
- The expanded list is recognised from "Show fewer" / "See all N models", and
  navigation counts real steps from the focused card.
- The session drop is detected from the log FreeBuff itself writes, wherever
  the turn discovers it — the terminal closing early, the two-minute startup
  silence, or a live turn going quiet. Each ends at once with the remedy
  spoken: quit and reopen FreeBuff, then send the message again.
- Readiness is recognised from the "Describe your task" placeholder 0.0.168
  paints before its caption scrolls in, so a message is no longer held through
  a two-minute silence that looked exactly like a hang.

## Verification

Ten new tests in `tests/test_freebuff_session_drop.py` are built from the real
captured welcome screen of 0.0.168, fed through the same terminal emulator the
worker reads, painting frames the way the TUI does. They cover the marker-less
focus reading, the expanded-screen navigation (accept the recommended card;
reach GLM two rows down with one Down and one Enter), the degraded catalogs at
both ends, the drop detected at each of the three discovery points, the
mid-turn drop on a resumed chat, and the run-status reader keeping its older
complete/cancelled readings. The full suite is green under `-W error` — 1144
passed, 8 skipped — with and without the random seed shuffle; ruff check and
ruff format are clean.
