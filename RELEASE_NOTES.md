# BlindPilot 0.29.15

The last item from the FreeBuff audit: your own steered message is no longer
read back to you as though the model had said it.

- Steering types a second message into the same composer the prompt went
  through, and FreeBuff writes it into the transcript exactly as it writes the
  reply - plain text, with nothing to say whose words they are. That is why the
  reading cuts the transcript at the echo of what was typed, and only the prompt
  was ever looked for: a steer's echo landed inside the section that gets
  spoken, and the person heard their own instruction read out as the answer.
- Moving the boundary to the newer echo would have cost the answer above it, and
  where no saved chat can be found that is the first half of the turn's work.
  The echoes are taken out instead: every message typed this turn is remembered,
  and the lines each one covers are skipped when the reading is built. The text
  either side of a steer is kept, and a steer that asks for work still reaches
  FreeBuff unchanged.
- By span rather than by the single matched line, because an echo the terminal
  had to wrap is taller than one line. That fixes the older half of the same
  bug as well: a long prompt's wrapped tail was being read as the answer, with
  or without a steer.
