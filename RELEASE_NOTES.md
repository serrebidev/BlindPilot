# BlindPilot 0.11.1

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

One bug, reported over and over since 0.10.0: **Claude Code exited with code 1**. This
release is three separate causes of it. The first is not Claude Code failing at all — it
is BlindPilot, reporting its own kill as the CLI's error.

Nothing here removes a button or changes a chord.

## BlindPilot produced the exit code it then complained about

At the end of a turn, BlindPilot closed the CLI's input and waited five seconds flat
before killing it. On Windows `Popen.kill()` is `TerminateProcess(handle, 1)`, so a killed
process reports exit code *exactly* 1 — and the next line said "Claude Code exited with
code 1".

Both the number and the ending were BlindPilot's own, attributed to the CLI. Nothing in
the message told that apart from a genuine failure, which is why this has been so hard to
place from a bug report.

Five seconds was the wrong measure too. Shutting down is not instant — a session is
written to disk and MCP servers are torn down — and a run that has just kept a fan-out of
agents alive is the one with the most to put away. The 0.8.0 fix that let those agents
finish therefore made this fire *more* often, not less.

Waiting now watches for silence instead of counting seconds. A CLI that is still writing
is still working, so the clock restarts whenever it says anything, and only a process that
has gone quiet for thirty seconds is stopped. It is still bounded, so a genuinely stuck
CLI cannot hang the turn. And when BlindPilot does stop it, it says so plainly rather than
inventing an exit code for it.

## An event that said nothing about the agents ended the run

The number of background agents still going was read from each result event on its own,
with nothing remembered between them. An event carrying no `subagent_stats` returned zero,
closed stdin, and killed every agent — the original 0.8.0 bug, brought back by a field
simply being absent.

That was not a hypothetical. Plain `{"type": "result"}` is the ordinary shape of a turn.
What was last known now stands until an event actually says otherwise.

Two smaller faults in the same counting are fixed with it. Both hang a turn forever rather
than ending it early:

- `True` is an `int` in Python, so `started_in_background: true` counted as one agent that
  could never settle. Counts now refuse bools.
- A `killed` field arriving as anything other than a dictionary was ignored entirely,
  leaving those agents counted as running for good.

## A late error threw away an answer you had already been given

Waiting for background agents made a late `is_error` result reachable for the first time,
and that path failed the turn outright — which drops it from the transcript.

The exit-code path directly below it is careful to keep an answer that arrived before the
process ended badly. This one now does the same: how the run ended is worth saying, but
not instead of the work it already produced.

## Known, and not fixed here

`FreebuffWorker` has the same premature-kill shape. It records a running status for its
agents and then ends the turn when the main prompt completes, without ever consulting
that, after which the terminal is killed.

It is left alone on purpose. Verifying a process-teardown change there needs a real
FreeBuff terminal to run against, and fixing that path blind is how one bug becomes two.
