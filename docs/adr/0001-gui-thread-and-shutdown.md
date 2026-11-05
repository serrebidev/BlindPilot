# ADR 0001: GUI-thread budget and shutdown ownership

- **Status:** Accepted
- **Date:** 2026-11-04
- **Context:** Menu and shortcut work can stall painting, keyboard input, and
  screen-reader updates. Quitting must close the window promptly while still
  cleaning up backend processes BlindPilot owns.

## Decisions

GUI callbacks have a 50 ms budget. They do not wait or join, start subprocesses,
or perform network or other potentially slow work. Such work runs on a worker;
callbacks return promptly and deliver progress, results, and errors through the
GUI event path only while the owning UI is live. Small bounded in-memory and
filesystem operations remain allowed.

The main window closes without waiting for backend teardown. Its close handler
cancels each tab without waiting, then starts one shutdown worker. That worker
uses a single three-second deadline to join cancellation workers before asking
the backend pool to stop every held process. The pool also registers an
`atexit` sweep for shutdown paths that bypass the main window.

Process cleanup follows ownership established at launch. On Windows, each
backend root is created suspended, assigned to a private Job Object configured
to terminate descendants when its last handle closes, and resumed only after
assignment succeeds. Assignment failure kills the exact new root handle and
releases the Job Object.

On POSIX, backend roots start in a new session. Cleanup signals the process
group only while the root is live and verified as that group’s leader, then
stops and reaps the exact root. If the root has exited, BlindPilot does not use
its PID or a potentially reused group ID to find descendants. Failed ownership
checks therefore leave unverifiable descendants alone.

## Consequences

The window can disappear before backend cleanup finishes. The shutdown worker
and `atexit` sweep perform cleanup independently of the GUI event loop. If
process ownership cannot be verified, cleanup fails closed rather than risking
termination of an unrelated process. Platform process behavior still needs
integration verification on supported operating systems.
