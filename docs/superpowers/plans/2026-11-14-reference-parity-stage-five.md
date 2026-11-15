# Reference Parity Stage Five Implementation Plan

**Goal:** Browse the connected tools and personal context a backend actually discovers, without scanning arbitrary folders or claiming enable/disable that no backend interface supports.

**Architecture:** New `connected_context.py` beside `remote_sessions.py`: pure path/config reading over locations the app already knows. Dialog in `connected_dialogs.py` following the other dialog patterns. Menu entry in Find and Browse next to Queued Messages.

**Spec:** `docs/superpowers/specs/2026-11-14-reference-app-parity-design.md`, staged release 5.

## Global Constraints

- No scanning arbitrary private folders; only backend locations already named by `agent_backends.py` settings files are read.
- Enable/disable and sign-in go through each backend's own interface; the browser never edits a config file. Where no interface exists, the dialog says so in words.
- GUI callbacks stay under the ADR 0001 50 ms budget; directory reads are small and bounded, on workers when listing many files.
- Menu additions go to the Find and Browse submenu (top-level menus are at the ten-item ceiling).
- No new dependencies.
- Before commit: Ruff check, Ruff format check, full `pytest -q -W error`, bare `mypy`, startup smokes.

## Task 1: Connected tools browser

- [ ] Parse the MCP configs BlindPilot already knows: project `.mcp.json`, `~/.copilot/mcp-config.json`, and Muse's settings (MCP servers + trust). Each entry shows server name, source file, transport/command, and availability (file present and JSON valid).
- [ ] Dialog lists them read-only with the source path; a note states that enabling, disabling and sign-in happen in the backend's own settings (e.g. Claude Code's `/mcp`).
- [ ] Tests: fixture configs parse to the right entries; malformed JSON is reported as unavailable, not fatal.

## Task 2: Memory, skills and instructions browser

- [ ] List instruction/memory/skill files at backend-known locations for the current session: Claude settings/skills dirs, `~/.agents/skills`, Hermes skills dir, Muse config. Each row shows backend, path, size, and the first non-empty line.
- [ ] Dialog is read-only; paths are shown so nothing is claimed to be "everything the model knows".
- [ ] Tests: only known locations are probed; missing files are skipped; first-line extraction is bounded.

## Task 3: Open sessions browser

- [ ] Dialog lists currently open sessions (backend, folder, session id) from the frame's session panels, excluding nothing but flagging running ones. Hiding is refused for running/open sessions; rename links to Recent Conversations where that lives.
- [ ] Tests: listing matches panels; running flag is correct.
