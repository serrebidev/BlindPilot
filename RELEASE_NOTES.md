# BlindPilot 0.9.2

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release makes the three sound cues independently switchable. It follows 0.9.1, which
stopped a turn from starting on top of the last one's unfinished bookkeeping and moved the
checks that would have caught it onto every commit, and 0.9.0, which gave `/status` an
answer on every backend and gave chat mode the tools OpenRouter runs on its own servers.

If you are coming from 0.8.1, read the 0.9.0 and 0.9.1 notes as well — everything in them
is in this release.

## Each sound cue can be turned off on its own

**Play sound cues** turns all three off together. That is the right master switch and it
is unchanged. What one switch cannot express is that the three cues are not
interchangeable.

Sent and Answer received are one-shots: they confirm that something happened, and then
they are over. Working is a loop that runs for the whole turn, which makes it both the one
most likely to wear thin over a long fan-out and the only one that says a turn is still
alive without being asked. Wanting the loop gone is not the same wish as wanting silence —
but with a single switch, stopping it cost both confirmations too.

Options now carries a **Sounds** submenu underneath the master switch, with one check item
per cue. A cue sounds when the master switch is on and its own is, so the master still
wins, and the three are greyed out while it is off — three live switches beneath something
that mutes all three would be describing a choice that is not there.

Two behaviours are deliberately unchanged. The progress loop still stops the moment an
answer arrives, whatever any of these switches say, because it has to end when the turn
does or it outlives the turn with nothing left to stop it. And switching the working cue
off stops a loop that is already playing rather than waiting for the turn to end: somebody
reaching for that switch means now.

A configuration written by an earlier release has no per-cue setting, so it reads exactly
as it always did, with all three on. A setting this version does not recognise is dropped
rather than carried, so a configuration written by a later one cannot mute or break this
one.
