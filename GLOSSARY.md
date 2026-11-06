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
_Avoid_: Stop, kill, reap, cancel, terminate

## Accounts

**Account**:
A saved way to reach one provider: a name, a provider type, and its credential — an API key or a sign-in.
_Avoid_: Connection, credential, profile

**ChatGPT plan**:
The provider type whose usage is paid for by signing in with ChatGPT instead of by entering an API key. A **ChatGPT-plan account** is one **Account** carrying it.
_Avoid_: ChatGPT account, subscription account, OAuth account

**Registration**:
The client id ChatGPT issued for this installation, kept across sign-outs so the next sign-in reuses it instead of registering again.
_Avoid_: Client, app registration, connection

**Codex login**:
The Codex backend authenticating through Codex itself, instead of through a **ChatGPT-plan Account**.
_Avoid_: Codex account, Codex sign-in, Codex auth

## Conversations

**Session**:
A provider-side identity for one ongoing conversation, held by the backend that owns it — a Codex thread, a Hermes session, a Muse chat.
_Avoid_: Using "session" for a Held process or for a ChatGPT sign-in.
