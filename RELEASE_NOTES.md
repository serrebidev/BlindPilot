# BlindPilot 0.29.16

FreeBuff was run for real against the release installed here, 0.0.180, and it
found one more thing to fix.

- FreeBuff draws each message in the transcript under a divider carrying the time
  it was sent, so a message is two lines rather than one. The prompt's divider
  sits above the reading's boundary and went unread by accident; a steer's sits
  below it, so taking the steer's own line out left the divider behind, and the
  time was read out on its own and joined onto the front of the next row.
- The divider above a message is now part of that message, recognised by shape,
  so a release that stops drawing dividers takes nothing with it.
- The steered turn is also what showed the previous release's fix working. Before
  it, the reading put the person's own instruction in front of them as the
  model's words, copy marker and all, and repeated the same thinking paragraph
  four times as the text it compares against shifted underneath it. After it, the
  turn read `pong` and `kumquat`, one row each, with no echo of the message that
  asked for them.
- The same run confirmed what had only ever been assumed. The model catalog is
  read out of 0.0.180's own 126 MB executable and all five models come back; the
  boot hold, the reasoning split, learning the conversation's id and noticing the
  turn had finished all work on a release the test suite has never seen.

The measurements are in `docs/code-audit/freebuff.md`.
