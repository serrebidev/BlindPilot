# Reference parity, stage one: verification

Date: 2026-11-14. Release target: 0.57.0.

## Scope

Conversation-only branching/retry, tools-disabled Ask Aside, bulk textual
exports, and pre-output transient Chat retries. This is stage one of the
approved parity design, not completion of all seven stages. Computer use
remains a later stage, with automatic actions and a global stop as approved.

## Behavioral checks

New tests were first run against missing behavior, then run again after the
implementation. They cover atomic Chat prefix copies, original-message and
attachment preservation, foreign boundary rejection and transaction rollback;
fork boundary preparation and failed-response numbering; restoring attachment
references; source identity changes; stale GUI completions; separate Aside
settings and cancellation; account-configured and ChatGPT built-in tool
suppression; export filename collisions, traversal-like titles, escaping,
cancellation and per-item failures; and bounded/cancellable transient retries.

A fresh reviewer identified six issues: accepted batches could replay, Clear
retained an abandoned copy, agent Retry dropped attachments, response-number
gaps selected the wrong turn, copied modes did not update the visible picker,
and stale completions could activate a tab. Fixes and regressions cover these,
including context-copy attachment paths and empty successful responses.
Ambiguous user rows sharing a response number direct the user to select the
completed answer instead; failed/empty agent turns are not recovery boundaries.

## Protocol and live checks

The installed Codex app-server schema declares `thread/fork` with required
`threadId` and optional `lastTurnId`, an inclusive completed-turn boundary.
Claude fork flags were checked against installed help and the official
[Python SDK transport](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/_internal/transport/subprocess_cli.py).
Codex rollout `turn_context.payload.turn_id` was also verified in the disposable
session created by this check and retained by the history reader.

Disposable real Claude and Codex sessions were forked through the normal
BlindPilot workers. Both returned distinct destination IDs; source message
lists and source transcript SHA-256 digests were unchanged. The first Codex
seed attempt exceeded the check's 45-second limit and was cancelled; a repeat
using the installed `gpt-6-luna` model with low effort passed.

## Real screen-reader check

An isolated source-build window, “BlindPilot Parity Test,” used fabricated
conversation text and a sample attachment. It used a separate profile and did
not send an API message. NVDA was running, but the automation tool could not
reliably activate its commands. The user therefore checked the actual screen
reader and reported **“NVDA checks passed.”**

The requested check covered History selection; Shift+F10 Branch/Retry commands;
Retry restoring the question and sample.png; Conversation, Find and Browse,
Conversation Copies, Ask Aside; File, Recent Conversations, Export Conversations;
labels, keyboard navigation, and focus. This is a real-reader result, separate
from the automated UI and storage checks. No VoiceOver check was performed.

## Baseline blockers fixed

The initial full suite had two pre-existing failures. The installed Command
Code catalog no longer contained Beam; the router and fixtures now use the
current Inkling 2 Small free entry. The Windows updater inherited PowerShell 7's
archive module through a Python process, then ran it under Windows PowerShell
5.1. A diagnostic reproduction identified that exact module path in the
exception stack. Importing the current host's bundled archive module fixes the
existing end-to-end updater test. This follows Microsoft's documented
[explicit module import](https://learn.microsoft.com/powershell/module/microsoft.powershell.core/import-module?view=powershell-7.6).

## Release checks

On Windows, the final source suite passed: **3,096 passed, 15 skipped** with
`python -m pytest -q -W error`. Ruff check and format check passed. Configured
mypy passed for all 44 listed production modules. Source and locally built
Windows executable each passed `--startup-smoke`, `--startup-gui-smoke`, and
`--startup-chat-gui-smoke`.

Ruff, configured mypy, full warnings-as-errors pytest, source startup smokes,
packaged startup smokes, and artifact checksums must pass before publication.
Published tag and asset verification is recorded in the execution ledger when
the release is complete. Windows is built locally; macOS is built on the user's
Apple Silicon Mac. No Linux binary is introduced by this stage.
