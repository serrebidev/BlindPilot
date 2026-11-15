# Reference Parity Stage Six Implementation Plan

**Goal:** Per-event custom sounds, imported sound packs, and optional independent system speech with per-conversation overrides, without changing screen-reader navigation speech.

**Architecture:** New `soundpacks.py` (pack import/validation) and `system_speech.py` (SAPI voice wrapper) beside `earcons.py`. `Earcons` gains pack-folder resolution and per-cue override paths; one-shot playback already reuses platform players. Speech uses module-level `SAPI5`, never the screen-reader output.

**Spec:** `docs/superpowers/specs/2026-11-14-reference-app-parity-design.md`, staged release 6.

## Global Constraints

- GUI callbacks stay under the ADR 0001 50 ms budget; zip extraction and SAPI speech run on workers/threads.
- Sound-pack zips reject traversal, links and bombs; cues outside the known names are reported, never installed.
- Missing or unplayable custom sounds fall back to the built-in cue silently (current Earcons behavior).
- Independent speech only runs when enabled; NVDA/JAWS/VoiceOver narration is untouched. Speech is cancellable (a stop action interrupts it).
- Menu additions go to the Options > Sounds submenu (top-level menus are at the ten-item ceiling).
- No new dependencies.
- Before commit: Ruff check, Ruff format check, full `pytest -q -W error`, bare `mypy`, startup smokes.

## Task 1: Sound-pack import and selection

- [ ] `soundpacks.py`: `import_sound_pack(zip_path, packs_dir) -> Path`. Accepts files named after the four cues (`send`, `received`, `in_progress`, `error`, any supported audio extension), possibly inside one folder. Rejects traversal/links/oversize entries and reports unknown cue names in the result instead of installing them. Extracts to `packs_dir/<name>/` atomically.
- [ ] `list_sound_packs(packs_dir) -> list[str]` (builtin first).
- [ ] `Earcons.set_pack(pack_folder | None)`: re-resolve the four cues against pack folder then builtin folder.
- [ ] Options > Sounds: "Choose Sound Pack…" (builtin + installed packs), "Import Sound Pack…" (file picker), and "Custom Cue Sounds…" (per-cue file picker stored in config; Earcons override wins over the pack).
- [ ] Tests: fixture pack imports; unknown cue names reported; traversal rejected; Earcons resolves cues from the pack with builtin fallback.

## Task 2: System speech settings and playback

- [ ] `system_speech.py`: `SystemSpeech` over `SAPI5` — voice list, set voice/rate/volume, `speak(text, cancel)` on a worker thread. Module-level singleton; `enabled` flag persisted in config.
- [ ] Speech dialog: voice choice (list from SAPI), rate slider (-10..10), volume slider (0..100), "Speak complete responses" toggle. Test-voice button.
- [ ] Hook: at response completion, when enabled and the conversation's override allows it, speak the completed response text (worker thread, cancellable). On stop, interrupt speech.
- [ ] Per-conversation override: session preference inherit/on/off, stored in the preferences config keyed by session id; touched from the session status dialog.
- [ ] Tests: settings round-trip; override resolution (inherit→global, on, off); SAPI adapter guarded so headless CI never speaks (live SAPI calls only in the dialog's worker with the test doubled by a fake).
