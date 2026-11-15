# Reference Parity Stage Seven Implementation Plan

**Goal:** Opt-in automatic computer use in direct Chat conversations on Windows, with a global stop key, target validation, and ask-before-acting as an option.

**Architecture:** New `computer_use.py` (UI Automation actions via pywinauto, Windows only) and `computer_tools.py` (model-facing tool definitions + bounded executor). Kind/provider layer gains an optional tool executor that runs client-side calls and resumes the turn. Everything vision/coordinate-based is out; screenshots are an explicit per-conversation opt-in.

**Spec:** `docs/superpowers/specs/2026-11-14-reference-app-parity-design.md`, staged release 7.

## Global Constraints

- GUI callbacks stay under the ADR 0001 50 ms budget; UIA work runs on worker threads.
- Actions are accessibility-driven only; screenshots only when the conversation explicitly enables them and the model accepts images.
- Automatic actions are the default after opting in (user decision); ask-before-acting is an optional per-conversation mode.
- The stop key is registered before automatic actions start; its exact key is announced. Stop invalidates pending actions and blocks new ones until the user explicitly resumes. The stop path does not depend on model/network/pipeline state and has an in-window Stop twin.
- Never operate BlindPilot itself; validate target/foreground before each action; never fall back to unchecked coordinates.
- Bounded loop: at most 25 tool actions per turn, and a per-conversation total.
- No dependency beyond pywinauto (Windows-only, declared with platform guard and graceful absence).
- macOS follows once Windows ships; the dialog reports that honestly.
- Before commit: Ruff check, Ruff format check, full `pytest -q -W error`, bare `mypy`, startup smokes.

## Task 1: Automation layer

- [ ] `computer_use.py`: `list_controls(window_title) -> list[ControlRef]` (numbered, named like FastChat's tree), `press(control)`, `set_value(control, text)`, `type_into(control, text)`, `launch(app_path)`. Every action re-resolves the foreground window and refuses ones whose exe/title is BlindPilot. All calls raise `ComputerUseError` with a speakable reason when the target is gone (never coordinates).
- [ ] Tests: fixture desktop double for list/press/set_value; target-gone errors; BlindPilot-window refusal.

## Task 2: Tool loop in the provider layer

- [ ] `computer_tools.py`: tool definitions (list controls, press, set text, type, launch) and an executor honoring Ask mode (per-action approval callback) and the max-actions bound. Execution errors return tool results, never silent failures.
- [ ] `GenerationService.generate` gains an optional `tool_executor`; `ProtocolMixin` runs client-side calls through it and resumes the turn (append tool results, new request, same cancel). The empty-response "does not run" error remains for non-computer-use calls.
- [ ] Tests: executor round-trip with a fake provider stream (tool call then final text); bound stops the loop; cancelled turns do not execute.

## Task 3: Opt-in conversation UI and stop key

- [ ] Chat menu: "Computer Use…": choose target app (or launch), ask-before-acting checkbox, screenshots checkbox (default off), and the announced stop key. Registers `Ctrl+Shift+F12` (ctypes user32) before starting; requires registration success before any automatic action.
- [ ] In-window Stop during computer-use turns; stop triggers the same cancellation path as the hotkey. Resume is an explicit button, not automatic.
- [ ] Tests: dialog construction, key-registration failure blocks automatic mode with a spoken reason, stop invalidates pending actions.

## Task 4: Live verification

- [ ] Real Notepad run: open, type via UIA, verify text landed; stop during a queued action sequence halts before the next action.
- [ ] Real NVDA check of the opt-in dialog before release.
