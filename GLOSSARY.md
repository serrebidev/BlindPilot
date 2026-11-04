# BlindPilot

A screen-reader-first desktop app that drives AI coding CLIs in ordinary windows. Its language keeps apart what a person *hears and feels* from what the process is doing, because "it feels stuck" has several different causes and they are fixed differently.

## Language

**Stall**:
The event loop has stopped processing events: the window stops repainting, speech goes silent, and keystrokes queue until it returns.
_Avoid_: Freeze, hang, lock-up, "stuck".

**Delay**:
The event loop is healthy, but the effect of a keystroke lands later than the keystroke did.
_Avoid_: Lag, slowness, latency.

**Dropped input**:
A keystroke that is lost rather than queued, so the press is never answered at all.
_Avoid_: Missed key, ignored key, swallowed key.

**GUI-thread budget**:
The longest a keyboard-shortcut handler may take on the GUI thread: 50 ms. Work that can exceed it belongs on a worker.
_Avoid_: Timeout, threshold, limit.

**Offender**:
A shortcut handler that exceeds the GUI-thread budget.
_Avoid_: Slow path, hotspot, bottleneck.

**Announce**:
BlindPilot speaking for itself through the screen reader, with the same words mirrored to the status bar.
_Avoid_: Narrate, say, speak, read out.

**Warm**:
Reading something before it is asked for, so that asking arrives with no wait.
_Avoid_: Prefetch, preload, prime.

**Held process**:
A provider process kept alive between turns instead of started per message, so a conversation does not pay its start-up every time.
_Avoid_: daemon, pool entry, session process, background process.

**Drop**:
Releasing a held process: ending it and its children, and forgetting it.
_Avoid_: Stop, kill, reap, cancel, terminate.
