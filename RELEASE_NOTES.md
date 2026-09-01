# BlindPilot 0.9.1

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release closes a race that could corrupt a conversation and leave a running backend
unreachable, and moves the checks that would have caught it from release day to every
commit. It follows 0.9.0, which gave `/status` an answer on every backend and gave chat
mode the tools OpenRouter runs on its own servers.

Everything in 0.9.0 is in this release too; if you are coming from 0.8.1, read its notes
as well.

## Enter no longer starts a turn on top of the last one

Whether a run was in progress was decided by `worker.is_alive()`. A worker thread dies the
moment it has *queued* its last event, not when the window has acted on it — and the event
mailbox empties sixteen at a time and then hands the native queue a turn on purpose, so
that keystrokes and screen-reader events get one. A waiting Enter is therefore dispatched
inside that gap by design rather than by bad luck, and for the whole of it `is_alive()`
said False while the finished turn's own `complete` and `done` were still queued.

The disabled Send button was no guard: Enter in the prompt calls the send handler directly,
and Enter in the prompt is how this application is used.

The second turn then had the first turn's bookkeeping applied to it. The pending `complete`
wrote turn one's answer into turn two, so the saved transcript showed turn one unanswered
and turn two answered with text that was never a reply to it. The pending `failed` deleted
turn two outright, mid-run. The pending `done` cleared the worker reference while turn
two's backend was still working — and that reference is how Stop, Steer and the tab-close
cleanup reach a running backend, so the turn became unstoppable, its process was leaked on
exit, Send came back on, and the progress earcon stopped, which to a listener means
finished. The next Enter then started a third worker against the same session.

The last of those needed only `done` to be pending, which is true for at least one
event-loop iteration on every turn boundary, and the gap is not narrow: every event queued
behind a long turn's rows waits a whole drain cycle, and each cycle rebuilds the list.

A run is now in progress until the window has been told it ended — the worker reference is
cleared on the GUI thread when the queue reaches it, which is the only answer true at the
same moment as the rest of the state these handlers read. Starting a new conversation and
compacting ask the same question, and a worker whose thread fails to start clears the
reference by hand, so the one new risk, a Send refused for good, is closed as well.

## The tests run on every commit, not only on release day

The release workflow ran the tests and both static checks, but only on a `v*` tag. That
finds a broken commit with the release half built: artifacts uploaded for three platforms,
checksums written, and the job failing on something that had been true for days. A Tests
workflow now runs the same checks on every branch push and every pull request.

It also runs them on Linux for the first time. `linux_accessibility.py`, the pexpect
pseudo-terminal and the POSIX process-group handling all ship, and several tests skip
themselves on Windows and macOS, so they had never run anywhere at all. wxPython has no
Linux wheel on PyPI — only a source archive wanting the GTK development headers and the
better part of an hour — so the job installs Ubuntu's prebuilt package and runs under
xvfb.

And it starts the application rather than only importing it. The unit tests drive the
window's handlers on stub objects, and a stub has whatever the test hands it, so a menu
built before the notebook it describes cannot fail one. Both startup smoke checks the
release build already relied on now run on every commit.

## The linter can see a warning that had already got in

Ruff's rule selection had no `W`. An invalid escape sequence therefore reached the
repository — a Windows UNC path written without an `r` prefix, so `\s` and `\p` compiled to
a `SyntaxWarning` — and the tests stayed green. The release build runs `pytest -W error`,
so it would have failed a release days later with nothing before it saying a word.

`W` is selected now, together with `B`, `C4`, `A`, `RET` and `PIE`. `B012` alone earns its
place here, catching a `return` inside a `finally`, which is how much of the worker
teardown is written. What is deliberately left out was measured rather than assumed, and
the reasoning is recorded in `ruff.toml`. A test also compiles every module and fails on
any warning, so the guard does not depend on the linter's configuration at all.

Bringing those rules forward over four releases of newer code found two more things, both
fixed here: a generator passed to `dict()`, and a `lambda` that only returned an empty
dictionary.
