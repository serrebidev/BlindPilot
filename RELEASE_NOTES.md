# BlindPilot 0.12.0

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release is about how much a run says out loud. Until now it said everything: every
tool call, every result, every subagent's running commentary, in order. On a short turn
that is right. On a fan-out it is minutes of backlog you cannot skip.

Nothing here removes a button or changes a chord, and the default is unchanged.

## Follow a run step by step, or just keep up with it

The backlog is not BlindPilot's to manage. It sits in the screen reader's own queue,
which cannot be measured, shortened, or popped from — only purged entirely, which would
silence other applications too. So this is a choice offered rather than a cleverness
applied.

**Options > Narration** now has two modes:

- **Follow everything** — the default, and what BlindPilot has always done. Nobody's
  narration goes quiet because of an upgrade they did not ask for.
- **Keep up** — speaks what the turn is saying: your message, the answer, notices and
  errors. The step-by-step stays in the list.

Nothing is lost in Keep up, only unspoken. Every tool call and result is still a row,
still under the review cursor, still in the status bar.

Two new kinds of activity make that possible. **Notices** are BlindPilot speaking for
itself — waiting on background agents, how a run ended — and are never muted, because
muting "Waiting for 3 background agents" would lose the one line that explains a long
silence. **Subagent** lines are somebody else's commentary rather than this turn's reply;
five agents' monologues merged into one voice is what makes a fan-out unfollowable.

## Failure has a sound

There was no error cue at all — sent, working, received, and nothing for failure — so the
only signal a turn had died was a sentence that might be a long time coming.

It ships no audio file. `EarCons/` holds three, and authoring a fourth is not something to
fake, so this uses the platform's own error sound: `MessageBeep` on Windows, Basso on
macOS, and nothing on Linux, where a wrong guess is worse than none. That is also the
sound you already associate with something having gone wrong on your machine.

It sits with the others as a fourth switch under **Options > Sounds**.

Interrupting the speech was the other option, and was rejected on purpose: it purges the
reader's whole queue, including speech belonging to other applications. A sound costs
nobody else anything.

## macOS was doing the opposite of Windows

Every announcement posted at `NSAccessibilityPriorityHigh` — the tier VoiceOver treats as
speak-now — so the same code queued politely on Windows and chopped off the previous line
on macOS. The same complaint from the two platforms would have needed two different fixes.
High is now what an error gets rather than what everything gets.

Stated plainly: **this half is unverified.** It is a one-line change in the direction the
Windows path already documents as its intent, but nobody with VoiceOver has listened to it
yet. If you use VoiceOver, this is the change worth telling us about.

## Also

Narration stops when Stop is pressed. Queued lines kept arriving and kept talking
afterwards, which sounds exactly like a Stop that did not work.

FreeBuff turns out not to have the premature-kill bug 0.11.1 suspected it of, and the
obvious fix would have introduced a worse one. That was checked against a real FreeBuff
rather than reasoned about — two live turns and the saved chats on disk. The run-complete
marker is written after every agent finishes, so ending the turn on it cuts nothing short;
meanwhile an agent can be recorded as "running" permanently, so waiting for them all to
finish would hang the turn for an hour. The finding is now a test, so a later attempt at
the obvious fix fails there first.
