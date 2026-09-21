# BlindPilot 0.29.13

Three FreeBuff fixes from an end-to-end audit of the backend, and the audit
itself.

- A FreeBuff turn run on a model you did not pick now says so out loud.
  FreeBuff drops models between releases, and when the chosen one is gone from
  its picker there is no card to walk to, so after five seconds BlindPilot
  takes whatever is highlighted and runs the turn on that. The row announcing
  the swap was filed as agent activity, which Narration, Keep up drops along
  with every tool call - so the one mode where an answer unlike the chosen
  model's is least likely to be noticed was the mode that never heard why.
- A conversation you reopen keeps its own identity when its chat folder cannot
  be found: a chat deleted since the history list was drawn, or filed under a
  bucket this release no longer uses. The rule that learns a new conversation's
  id was still armed on that turn, and what it learns is whatever appeared under
  FreeBuff's projects since the turn began - which, with FreeBuff running in
  another tab, is the other tab's conversation. The id was overwritten and the
  next message resumed something you never opened. A turn that already knows its
  conversation keeps it now, in the watching loop and in the choice of what to
  start for the next message alike.
- Help, About BlindPilot names all seven backends again. It listed five, and had
  already been corrected by hand once; Muse Code and Command Code were both
  shipped without it, in a sentence read aloud. It is built from the backend
  table now, so the next backend cannot go missing from it.
- The audit is `docs/code-audit/freebuff.md`. It also records what was checked
  and deliberately left alone, and one open item that needs a captured frame
  from a real steered turn to settle.
