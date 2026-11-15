# Reference Parity Stage Four Implementation Plan

**Goal:** Review/manage queued agent messages, announce backend status changes, and verify what the installed backends support for task-level control and file checkpoints.

**Architecture:** New `queue_dialogs.py` beside `history_dialogs.py`, using the same TaskSlot dialog pattern. Worker protocol stays untouched: per-task stop and file-checkpoint restore are only offered when an installed backend's protocol proves them.

**Spec:** `docs/superpowers/specs/2026-11-14-reference-app-parity-design.md`, staged release 4.

## Global Constraints

- GUI callbacks stay under the ADR 0001 50 ms budget; queue state reads are in memory and dialog-local.
- Menu additions go to the Find and Browse submenu (top-level menus are at the ten-item ceiling).
- Unsupported backend capabilities are reported accurately, never claimed or faked.
- No new dependencies.
- Before commit: Ruff check, Ruff format check, full `pytest -q -W error`, bare `mypy`, startup smokes.

## Findings against installed backends

- Queue management exists as slash commands (`/queue list|clear|resume`); the gap is a GUI dialog.
- Task steps are model-tracked (`TaskCreate`/`TodoWrite`), not OS processes; the worker protocol (`start/is_alive/join/cancel/steer`) has no per-task stop. Stop-one-task is unsupported and is not claimed.
- Installed `claude --help` shows `--resume`/`--fork-session` but no file-checkpoint/restore flag. File-checkpoint restore is not offered.
- Fallback-model changes update `_auto_route` but are never announced (permission-mode changes and tool steps already are).

## Task 1: Queued Messages dialog

- [ ] `queue_dialogs.py`: dialog listing the active session's `_pending_messages` (text or attached-files placeholder), with Remove selected, Clear all, and Pause/Resume controls. State changes call the panel's existing queue mutators and are announced. Works on the GUI thread (in-memory list only).
- [ ] Conversation → Find and Browse → "Queued Messages…" entry opening it for the current session.
- [ ] Tests: dialog lists standby messages; remove/clear mutate the panel list; menu wiring test mirrors the history-dialogs pattern.

## Task 2: Fallback-model announcement

- [ ] Route the worker-finished fallback-model note through `_set_auto_route` (which announces), replacing the direct `_auto_route` assignment.
- [ ] Tests: simulate a finished turn with a fallback model and assert the announcement path runs.
