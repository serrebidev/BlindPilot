# Reference Parity Stage One Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for native execution, or superpowers:subagent-driven-development if the user chooses that method. Steps use checkbox syntax for tracking.

**Goal:** Ship conversation branching/retry, isolated Ask Aside, bulk export, and bounded API retries without changing original history.

**Architecture:** Extend existing Chat storage and backend worker options rather than adding a second conversation engine. Keep bulk history work and backend forks off the GUI thread. Use existing row renderers, native dialogs and callback delivery.

**Tech Stack:** Python 3.12, wxPython, SQLite, httpx, existing agent CLI protocols, pytest, Ruff and mypy.

**Spec:** `docs/superpowers/specs/2026-11-14-reference-app-parity-design.md`.

## Global Constraints

- Full useful parity remains the objective; this plan implements stage one only.
- GUI callbacks have a 50 ms budget; network, subprocess and bulk history work runs on workers.
- Preserve original transcripts, conversation settings and attachment bytes.
- Ask Aside cannot execute tools, modify files or add a turn to the source session.
- Preserve shortcuts and menu limits; use native controls and announce results without moving focus unexpectedly.
- Do not edit `BlindPilot.spec`, `.release-setup/`, `artifacts/` or visual-audit evidence paths without explicit authorization.
- No new dependencies unless installed packages, standard library and native APIs cannot meet a requirement.
- Computer use is stage seven. The approved default is automatic actions after starting a computer-use session, with a functioning global stop key and in-window Stop. Ordinary Chat remains unchanged.
- Before every commit: Ruff check, Ruff format check, full pytest with `-W error`, and configured bare mypy must pass.
- Real screen-reader verification is required before claiming accessibility changes complete.

## Review Focus

- A stale selected message belongs to another conversation: reject without copying the wrong history.
- A backend fails or closes during a fork: preserve the source and do not silently resume it for the new prompt.
- An API stream fails after reasoning or a tool event: never replay the request and possibly duplicate effects.
- Export titles collide, contain reserved Windows names, or an existing file appears during export: never overwrite another file.
- A dialog closes or the active conversation changes during worker completion: discard stale UI updates and preserve the user's draft.

## File Responsibilities

- `accessible_ai/storage/database.py`: atomic direct-Chat prefix copy including attachments and profile snapshots.
- `accessible_ai/ui/chat_panel.py`: selected-message Branch/Retry actions and draft/attachment restoration.
- `accessible_ai/providers/base.py`: structured HTTP error status needed to distinguish retryable failures.
- `accessible_ai/services/generation_service.py`: finite retry policy using existing `StreamEvent(kind="status")`.
- `session_history.py`: preserve native message/turn identifiers alongside display text, and load source prefixes.
- `backend_workers/contract.py`, `backend_workers/factory.py`, `claude_worker.py`, `claude_session.py`, `backend_workers/codex_worker.py`: verified native fork parameters and lifecycle.
- `conversation_recovery.py` (new): conversation prefix/retry preparation, independent of wx.
- `conversation_export.py` (new): bulk rendering and atomic, collision-safe output, using existing renderers.
- `ask_aside.py` (new): tools-disabled provider request preparation and cancellable independent request lifecycle.
- `blindpilot_app.py`: menu routing, dialogs and worker-result application only.
- `tests/test_chat_recovery.py`, `tests/test_generation_retry.py`, `tests/test_conversation_recovery.py`, `tests/test_bulk_export.py`, `tests/test_ask_aside.py` (new): behavior/preservation tests.
- Existing worker, menu, export, history and Chat tests: regression coverage and stand-in updates as needed.

## Task 1: Cancellable transient API retries

**Interfaces:** Keep `GenerationService.generate(account, settings, cancel) -> Iterator[StreamEvent]`. Extend `ProviderError` with keyword-only `status_code: int | None = None` and `retry_after: float | None = None`; preserve string messages and existing constructor callers.

- [ ] Write tests in `tests/test_generation_retry.py`: 429 then success attempts twice; 503 exhaustion attempts three times; 400/401/403 never retry; cancellation during delay makes no further request; text, reasoning, tool or sources followed by failure never replay. Status/header-only events may precede a retry.
- [ ] Test network connect failures separately from ambiguous request/read failures. Retry connect failures before any response; do not retry read/write timeouts with uncertain server execution. Provider construction/credential failures never retry.
- [ ] Run `python -m pytest tests/test_generation_retry.py -q -W error`; confirm failures are missing behavior.
- [ ] Populate structured error metadata in `BaseProvider.raise_for_status`. Retry only HTTP 408/429/500/502/503/504 or connection establishment failures, before any nonempty content or tool event. At most two retries, delays 1 and 2 seconds, respecting a valid Retry-After up to 30 seconds. Use `cancel.wait(delay)`, not an uncancellable sleep. Announce retry status through existing stream status events. Treat malformed Retry-After as absent.
- [ ] Run new tests plus provider regression tests selected with `rg` from `tests/`; run all commit gates before committing `feat(chat): retry transient API failures before output`.

## Task 2: Direct Chat branch and retry

**Interfaces:** Add `Database.branch_conversation(conversation_id: int, through_message_id: int | None, title: str) -> int`. A `None` boundary copies zero messages. A non-None boundary must belong to the source conversation and is inclusive. The method returns the new conversation ID. It copies all conversation settings/profile snapshots and attachment data in one transaction.

- [ ] Write tests in `tests/test_chat_recovery.py`: prefix inclusion and order; source rows and attachment bytes unchanged; new IDs; preserved account/model/system/profile snapshots; foreign/missing boundary rejected; rollback leaves no partial destination.
- [ ] Run `python -m pytest tests/test_chat_recovery.py -q -W error`; confirm the absent method fails.
- [ ] Implement atomic copy using the database's connection/transaction conventions. Inspect all current conversation snapshot fields, including migrated profile settings, rather than copying only fields visible in the `Conversation` dataclass.
- [ ] Add native context-menu actions Branch from Here and Retry from Here. Branch includes the selected completed message. Retry is offered on saved user messages and copies only preceding messages, then restores that user's text and attachments into the new draft. Neither action sends automatically or changes the source. Disable both during generation and for transient status rows.
- [ ] Perform database copying on a worker using captured source/boundary values. Apply only to the requesting live panel; do not replace a newly typed draft when completion is stale. Announce the destination and focus its prompt on success.
- [ ] Test stale selection, nonempty draft preservation, worker failure, closing panel, and attachment restoration with existing wx test patterns. Run Chat/history/menu regression tests and commit gates before committing `feat(chat): branch and retry saved conversation messages`.

## Task 3: Agent conversation fork and retry

**Interfaces:** Add immutable `RecoveryRequest` in `conversation_recovery.py` with source `HistoryEntry`, selected boundary identifier, operation (`branch` or `retry`), and source modification identity. Produce `RecoveryResult` with new session identity or copied-context payload, restored prompt/attachment references, and an explicit native/context-copy label. New worker options carry fork intent separately from ordinary resume; a failed fork cannot fall through to source resume.

- [ ] Inspect installed Claude help and Codex app-server JSON schemas plus official protocol documentation. Record verified message-boundary/fork fields and tools-disable capabilities in the implementation notes. Current Claude help confirms `--fork-session`, `--session-id` and `--tools`; do not infer an earlier-turn boundary flag from that alone.
- [ ] Write tests in `tests/test_conversation_recovery.py`: selected turn maps to a stable backend identifier; native source bytes unchanged; fork result binds only the new session; failed/unsupported fork cannot send into the source; stale source identity is rejected. Test an unrelated tool/control record between user turns.
- [ ] Run these tests to confirm missing behavior. Extend `HistoryTurn` with optional native identifiers and attachment references without breaking existing constructors/readers. Preserve the existing display-text cleanup.
- [ ] Implement native Claude/Codex forks only using verified supported protocol fields, with owned-process cleanup and cancellation. Use worker options/factory rather than backend-specific process launch in GUI handlers. Use an explicitly labeled copied-context new conversation when native support cannot safely identify the boundary. Never edit provider transcript files to simulate a fork.
- [ ] Route Branch/Retry from selected response/message actions and a suitable Conversation submenu. Retry restores the original user prompt without sending. Document that conversation branching leaves workspace files unchanged. Preserve the selected session's folder/model/effort/permission settings.
- [ ] Exercise fake-server failure, cancellation, close-during-fork and source-preservation tests. Run history/worker/pool/menu regression tests. Perform a small real Claude and Codex fork using a disposable folder/session when credentials permit; verify original transcript hashes and new IDs. Run commit gates before committing `feat(agents): branch conversations without changing source sessions`.

## Task 4: Isolated Ask Aside

**Interfaces:** `ask_aside.py` prepares `GenerationSettings` from a captured completed history prefix plus question, with no tools/plugins and no write to the source database/session. Use `GenerationService` with a separate cancellation event for the direct-Chat fallback. Native Claude/Codex adapters are offered only when their tools-disabled boundary can be enforced and verified.

- [ ] Write `tests/test_ask_aside.py`: source IDs/history/draft unchanged; running/incomplete response excluded; tools/plugins empty; external MCP and skills cannot reintroduce tools; cancellation and errors affect only Aside; no account/model silently selected for a fallback send.
- [ ] Run the tests to show missing behavior. Build the minimal wx dialog: read-only selected context summary, question edit, account/model choice when needed, Ask, Stop, read-only answer, Close. Labels precede controls; Escape closes/cancels only Aside.
- [ ] Use verified native tools-disable flags plus isolated session lifecycle when possible. Otherwise state that a selected direct Chat account will receive the captured text and require the user's Ask action. Never fall back to a normal tool-enabled agent.
- [ ] Deliver streaming/status events only while the dialog/request is current. Keep generated Aside output out of source history and ordinary send queues. Test a late callback after close.
- [ ] Verify real NVDA navigation and answer/status announcements, record results, run commit gates and commit `feat: ask about a conversation without changing its history`.

## Task 5: Bulk export

**Interfaces:** `conversation_export.export_many(entries, destination: Path, format: str, cancel: Event, on_progress) -> ExportResult`, where entries are captured agent-history or Chat-history references; format is `md`, `html` or `txt`. `ExportResult` reports completed paths, skipped/failed items and cancellation. No attachments are silently represented as exported binary files; textual exports state their filenames only.

- [ ] Write tests in `tests/test_bulk_export.py`: two equal titles produce distinct files; reserved/path-like/empty names stay under destination; pre-existing files remain unchanged; HTML escapes executable markup using existing rendering; cancellation removes temporary files and preserves already completed exports; a read error reports one failure without hiding successful files.
- [ ] Run tests to confirm missing behavior. Reuse current row assembly/HTML rendering for agent history, and role-preserving Chat message rendering. If extraction of the existing export renderer is necessary, preserve its public behavior and current `tests/test_export_conversation.py` expectations.
- [ ] Publish each file atomically without replacing existing files. Enforce source identity for history changed while export runs, and report a changed/unavailable item rather than exporting the wrong conversation. Keep source stores read-only.
- [ ] Add Export Conversations under File in an appropriate submenu if the top-level limit requires it. Native multi-selection list with filter, format choice, destination picker, Export, Cancel, progress text and final summary. Capture a selection snapshot; no network or bulk I/O in callbacks.
- [ ] Test closing dialog, source disappearance, Unicode filenames, and source conversation unchanged. Verify real NVDA multi-selection/filter/progress/cancel flows. Run export/menu/history tests, commit gates, and commit `feat: export selected conversations together`.

## Task 6: Integrate, verify and release stage one

- [ ] Review the entire diff against the spec's stage-one outcomes, including source preservation, tools-disabled Aside, attachment handling, cancellation, and truthful native/context-copy labels. Obtain a fresh whole-change review using the selected execution workflow; fix actionable findings.
- [ ] Run `python -m ruff check .`, `python -m ruff format --check .`, `python -m pytest -q -W error`, and `python -m mypy`. All must exit zero. Run `python blind_pilot.py --startup-smoke`, `--startup-gui-smoke`, and `--startup-chat-gui-smoke`. Record actual results.
- [ ] Complete real NVDA checks for every new action/dialog, including errors and cancellation, using the existing host's screen-reader test approach. Do not substitute UI trees for this gate. Record unavailable live-provider verification separately from passed checks.
- [ ] Update README/help, version, CHANGELOG and RELEASE_NOTES for the actual delivered behavior. Keep automatic computer use listed as remaining work, not a released stage-one feature. Repeat checks required by changed files and all commit gates, then commit the release.
- [ ] Inspect current Forgejo release workflow and local release tooling. Build Windows portable/installer using existing packaging, run packaged startup smokes, write checksums, push commit and a new immutable tag, publish artifacts, dispatch existing arm64 macOS attachment workflow, and verify published asset names/checksums/tag commit. Do not change protected packaging/evidence paths incidentally.
- [ ] Update the stage ledger below with actual commit/tag, verification and any unresolved limitation. Continue with a separate plan for the next subsystem; stage-one release does not complete the original request.

## Remaining Stage Ledger

- [ ] Stage 1: recovery/export/API retries — this plan; not implemented yet.
- [ ] Stage 2: history protection, consistent backup/restore and import — separate storage plan after stage-one interfaces settle.
- [ ] Stage 3: usage/cost reports, native Ollama and Chat compaction — separate provider/storage plan.
- [ ] Stage 4: queues, individual task control, protocol announcements and file checkpoints — separate backend-control plan.
- [ ] Stage 5: MCP controls, personal-context browser and session tools — separate integration plan.
- [ ] Stage 6: sound packs and speech/per-conversation options — separate preferences/audio plan.
- [ ] Stage 7: automatic computer use on Windows, then macOS — separate native-platform plan. Mandatory tests cover global stop while another app has focus, network-request stall, pending-action invalidation, lost/changed target, failed hotkey registration, and explicit resume. Per-action approval is optional and off by default.

These later plans refine implementation details within the approved design; they
must not drop outcomes or silently change the automatic-action default.

## Plan Self-Review

Stage-one outcomes each map to a task. The five review-focus failures each have
an owning test step. Backend protocol fields remain an explicit evidence task,
not guessed wire names. Later subsystem implementation details deliberately
belong to separate plans, avoiding an unverified all-at-once rewrite.
