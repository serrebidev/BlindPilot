# BlindPilot 0.26.0

Muse Code — Meta's terminal coding agent — is now a backend BlindPilot drives, alongside Claude Code, Codex, FreeBuff, opencode and Hermes. This page says what that is, what it does, and what was verified before it shipped.

## What Muse Code is

Muse Code is Meta's agent for writing and changing code from a terminal, installed with the one line its own documentation gives:

```
curl -fsSL https://dev.meta.ai/install.sh | bash
```

Its CLI ships for macOS and Linux only. On a Windows machine there is nothing to install natively — so BlindPilot reaches it inside WSL, through the same bridge that carries Hermes: the desktop's stdin and stdout are connected straight through to the child, and only the working directory needs translating on the way over. If WSL is not on the machine, the wizard says so rather than pretending the backend is there.

## What it does in BlindPilot

Everything the other backends do, done the way this protocol actually works:

- **Turns stream a fragment at a time** and are spoken only when a sentence is complete, so the screen reader never reads torn words. When Muse hands over the authoritative whole answer at the end of a message, it replaces whatever fragments were already spoken rather than reading the answer twice.
- **Steering and cancelling** work mid-turn. Guidance joins the turn that is already running; a cancel is answered by the agent itself rather than killing the process, so the transcript says how the turn ended.
- **Tool approvals are put in front of you.** When a tool wants permission, BlindPilot asks — allow once, allow for this conversation, or deny. Nobody watching to answer means the request is denied rather than leaving a run wedged mid-air. A decision carries the protocol's own multi-stage guard object through verbatim, so an answer aimed at one stage can never satisfy another.
- **The agent's own questions reach you.** MSP lets the model ask a clarifying question with choices, including multi-select; BlindPilot shows it in the same question dialog the other backends use.
- **Compaction** works from the same `/compact` command as everywhere else: the conversation is summarised in place to free up context, and the row says what happened.
- **Past conversations reopen** by session id. If the stored one no longer exists on the host, BlindPilot says so instead of silently starting a different conversation in its place.
- **The model picker reads Muse's live catalog** from a real Muse host, and offers the reasoning-effort levels the CLI documents, filtered to the vocabulary the protocol itself accepts — a tier the wire would refuse is never offered to anyone.
- **`/status`** names the launcher and its version, whether a Meta account is signed in, and which providers Muse holds credentials for.

The setup wizard installs Muse through its official script — inside WSL on Windows, where that script cannot run natively — and signs you in through its device flow: the sign-in page opens in your browser, and the wizard reports whether the CLI came back signed in.

## One account-side note

Signing in works, and the catalog answers — but Meta's provider returns HTTP 402 for model calls on this account: authenticated, with no Muse model access attached to it yet. That is the account's side of the wall, not the app's, and it is the same shape as the "credits required" grind documented for Hermes. When Meta switches the account's access on, the backend needs nothing further.

## What was verified

The integration was measured against Muse 1.0.3's own wire schema (`muse schema`), not inferred: every request and notification the worker sends — initialize, session/start, session/resume, turn/start, turn/steer, turn/cancel, model/list, approval/decide, userInput/answer, session/compact — was checked against the published schema, and the handshake, a full turn, the model catalog, and the device-flow sign-in were verified live against a real `muse serve`. Two protocol rules were learned the hard way and are pinned by tests: the client must introduce itself with a name matching `^[a-z0-9_]+$` (a mixed-case display name is refused before any conversation starts), and WSL's `--cd` rejects a relative directory outright, printing its error on the very stream the protocol reads — so the workspace is always handed over absolute.

The full test suite runs with the new backend in the registry: the worker driven frame by frame against a scripted MSP host (streaming, steering, approvals, questions, cancel, compaction, replay), the adapter's WSL bridge and credential reading, the worker contract every backend answers to, and the transport-contract harness that holds every fake to the semantics a real pipe has. 1610 tests pass; ruff, the formatter and mypy are clean.
