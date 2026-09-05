# BlindPilot 0.21.5

A September audit of every backend, the window, the updater, and the docs, with the bugs it found fixed and a test written first for each.

## Backends

- Stopping Codex or opencode on Windows ends the whole process tree instead of one process. TerminateProcess left a dozen or more MCP children per app-server running with nobody to stop them; they are ended with taskkill /T by the full System32 path, built with Windows separators so the tree kill works wherever the code runs.
- A Codex conversation that cannot be resumed costs that tab its session, and the next message starts a new conversation, instead of taking the shared app-server down and breaking every other tab.
- Installing or updating Codex drops the held app-server first, because Windows refuses to overwrite a running exe.
- A prewarmed FreeBuff terminal nobody claims is closed when its TTL runs out instead of lingering forever.
- A resumed Claude CLI can emit a result for a leftover turn before ours arrives. The worker took that as the end of our turn, closed stdin with the prompt still queued, killed the CLI after thirty seconds, and reported the kill as the answer. It now reads on while turns are still queued, and never kills a turn that reached its result.

## The window

- A Hermes question with no preset options, which is how every sudo or secret request arrives, can be answered; the text box is offered from the start instead of a dialog nothing can satisfy.
- Enter on a conversation dialog's Cancel button opened the conversation it was meant to cancel.
- A Chat mode that cannot open falls back to Agent mode completely instead of leaving the window half switched, and agent-only commands are greyed out in Chat mode instead of acting on the hidden notebook.
- The model probe cache is keyed by backend and working folder and dropped when the CLI that answered changes, so a stale catalog cannot be served and the lookup never searches for the CLI on the GUI thread.

## Hermes and the updater

- The silent updater quotes the installer's /DIR= and /LOG= paths, so an account name with a space no longer breaks every silent update.
- Reply waits are timed by the clock rather than by counting empty reads. A held connection is dropped after an abnormal end instead of being handed on. Remote TLS verifies through the packaged trust store, a bracketed IPv6 host keeps its single port, and the update status file survives accented characters.

## The chat provider layer

- An answer cut off at the model's length limit says so, and an error OpenRouter reports inside a choice is raised instead of swallowed. HTML blocks and markdown tables are read as rows, and the last message is read without pulling every attachment blob with it.
- A damaged AccessibleAI database is skipped without leaving a half-written copy, and the tests no longer write into the real %APPDATA%.

## Docs

- The README was rewritten without its stale claims, the CHANGELOG was cut from about 19,000 words to about 4,400 keeping every version, the macOS icon's retina sizes were corrected, and the audit reports this release came from are kept in docs/code-audit/.

Verified with the regression suite, lint, formatting, and type checks, plus the startup and GUI smoke runs.
