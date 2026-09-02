# BlindPilot 0.17.0

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release stops BlindPilot from alarming you about a process that was doing its job.
A user reported it after a turn that had worked perfectly:

> BlindPilot stopped Claude Code: it had not finished shutting down 30 seconds after it went quiet.

Nothing had gone wrong. That sentence was BlindPilot's own — and the bug was in the very
fix that introduced it.

## Stop waiting on a CLI whose turn is already over

A turn finishes and delivers its answer. BlindPilot closed the CLI's stdin, waited for
it to exit, gave up, and killed it. On Windows, `Popen.kill` is
`TerminateProcess(handle, 1)`, so it then read back the exit code it had just caused and
reported it. A correct answer, a thirty-second pause, and an alarming sentence.

### Why the wait did not work

The wait restarted its clock whenever the CLI wrote to stderr, reasoning that one still
writing is still working. That reasoning is sound for a CLI dying mid-turn — it is
writing the traceback there, and that traceback is the only thing BlindPilot can offer.
It is worthless at the end of a healthy turn, because a CLI shutting down cleanly has no
errors to write. The clock never restarted. What shipped was a flat thirty-second
timeout describing itself as patient.

### The change

The waiting was the mistake, not its duration. Once the answer is in, no exit code
changes what anybody hears — so a turn that answered neither waits for its process nor
kills it:

- `poll()`, which is free, sees whether it has already gone.
- A process still running is left to a daemon reaper thread that collects it, bounded at
  five minutes and silent, because the turn it belonged to ended correctly long ago.
- A bad exit code the CLI reached on its own is still reported; one BlindPilot caused no
  longer exists to report.

The thirty-second wait stays on the failure path, where its stderr signal is real, and
both docstrings now say which path is which.

### What this costs if left

Every turn slower than thirty seconds to shut down was killed partway through writing
the session file the next `--resume` reads, and partway through stopping its MCP servers
rather than cutting them off. A run that fanned out background agents has the most to put
away and is the most likely to be interrupted.

Four new tests drive a process that answers and then shuts down silently — which is what
the old fake could not express. Before, they would have failed: killed, ~30 seconds, and
the notice emitted. Now: instant, not killed, answer only.