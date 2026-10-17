# Backend Update Service Implementation Plan

> Required subskill: `superpowers:executing-plans` (or `superpowers:subagent-driven-development` if the user chooses delegated execution)

**Goal:** Give the BlindPilot setup wizard one reliable, accessible check for installed agent CLI versions and available updates, while keeping updates manual and preserving each backend's existing installer.

**Architecture:** Add a small `backend_updates.py` module containing result types, version comparison, a short-lived cache, and adapters for npm-backed, official-installer, and installed-only backends. The wizard asks the service for a result after it finds the selected executable, renders one status/detail paragraph, and keeps the existing update worker as the only installer path. A generation token prevents a late worker result from changing a different backend or a closed wizard.

**Tech Stack:** Python 3 standard library (`dataclasses`, `enum`, `json`, `pathlib`, `subprocess`, `threading`, `time`, `typing`), existing wxPython wizard, existing `pytest`/`pytest-qt`-free test doubles, and the current backend metadata in `agent_backends.py`. No new dependency.

**Spec:** `docs/superpowers/specs/2026-10-17-backend-update-service-design.md`

**Global Constraints:** Preserve dirty work and current backend install commands. No unattended installs. Do not expose credentials, raw environment values, or installer output in cache. Preserve Command Code's `COMMANDCODE_SKIP_UPDATES` and hidden-process safeguards. Keep screen-reader status text concise and deterministic. Do not change the installed BlindPilot bundle.

**Review Focus:** npm package/executable identity mismatch; invalid, prerelease, or missing versions; cache failure/TTL/forced refresh and in-flight deduplication; stale wizard callbacks after backend change or close; installer exit code 0 with an unchanged executable; preservation of existing Command Code update guards.

---

## Task 1: Add update domain types and safe version comparison

**Files:**
- Create `backend_updates.py`.
- Create `tests/test_backend_updates.py`.

**Steps:**

1. Write failing tests for `BackendUpdateState`, `BackendUpdateResult`, and the version parser/comparator. Cover numeric stable versions, `v` prefixes, missing/invalid versions, prereleases, and never using lexicographic ordering.
2. Implement a frozen result dataclass with the spec fields: backend id, executable path, installed version, available version, state, source, timestamp, and concise failure reason. Keep state/source values constrained to the documented strings.
3. Implement one stdlib-only version parser and comparison helper. Stable versions compare numerically; prereleases are ignored unless an adapter explicitly opts into that channel; invalid input returns an error path rather than a guessed version.
4. Run `python -m pytest -q tests/test_backend_updates.py` and `python -m ruff check backend_updates.py tests/test_backend_updates.py`.

## Task 2: Implement adapters, verification, and bounded cache

**Files:**
- Modify `backend_updates.py`.
- Create or extend `tests/test_backend_updates.py`.

**Steps:**

1. Add an adapter protocol with explicit methods for executable resolution, installed-version probing, latest-version discovery, manual update command selection, and post-update verification. Keep the existing `install_backend`/`update_backend` executors outside the service.
2. Implement the npm adapter using the backend's declared package identity and a metadata query that does not install anything. Resolve and probe the same executable; report a package/executable mismatch instead of claiming an update.
3. Implement official-installer and CLI-only adapters. Official installers may report latest metadata only when the source documents it; CLI-only backends report installed state with unavailable latest data. Do not guess from installer command text.
4. Add a short configurable TTL cache for both successful and failed checks, a forced refresh path, and per-backend in-flight deduplication. Store only sanitized result data with atomic writes; never persist credentials, environment values, or command output.
5. Add tests for npm metadata, source selection, mismatch, installer exit/unchanged-version verification, cache hit/expiry/forced refresh/failure, and concurrent same-backend deduplication.
6. Run the focused pytest and Ruff commands from Task 1.

## Task 3: Integrate the service with the setup wizard

**Files:**
- Modify `blindpilot_app.py` around `SetupWizard._check_cli`, `_check_npm_backend_cli`, `_update_cli`, and `_on_update_done`.
- Extend `tests/test_backend_updates.py` or add `tests/test_wizard_backend_updates.py`.

**Steps:**

1. Write failing wizard tests for installed/current, update-available, unavailable-latest, and failed-check states. Assert exact accessible status/detail text and that the Update button is shown only when an update is available and manual update is supported.
2. Start the service check off the UI thread after the selected executable is found. Keep the existing install guidance and missing-prerequisite branches unchanged.
3. Add a wizard generation token keyed to the selected backend and wizard lifetime. Discard late results from a prior backend, a prior check, or a closed dialog before touching controls or announcing text.
4. Render one concise status paragraph and one detail paragraph. Announce only the selected backend's current result; a manual Check Again bypasses the success TTL but reuses an in-flight request.
5. Leave `_run_update` and `update_backend` as the manual update path. After an update, invalidate the selected cache entry, verify the same executable/version, invalidate model options only on verified success, and report unchanged/broken results clearly.
6. Run the focused wizard tests, `tests/test_commandcode_updates.py`, and the existing callback-lifetime test.

## Task 4: Preserve backend metadata and document the operational behavior

**Files:**
- Modify `agent_backends.py` only if an explicit package/source field is required to remove duplicated package-name parsing.
- Update `README.md` or the project handover only if the user-visible check behavior or troubleshooting steps need documentation.
- Add/extend focused tests for every backend in `BACKENDS`.

**Steps:**

1. Prefer existing `BackendInfo` fields and a small explicit mapping over parsing free-form install-command prose. If metadata must change, add one field with a default so existing constructors and tests remain compatible.
2. Add a parameterized test that every backend produces a deterministic installed-only or source-backed result and that no backend silently falls into npm because its command text happens to contain `npm`.
3. Confirm Command Code child-process environment tests still pass, including `COMMANDCODE_SKIP_UPDATES=1`, hidden probes, and no-window stdin.
4. Document the manual-only update policy, cache/Check Again behavior, and the unsupported-latest-source case if the final UI exposes it.

## Task 5: Gate, review, and user-authorized integration

**Steps:**

1. Run the inner loop: `python -m pytest -q tests/test_backend_updates.py tests/test_wizard_backend_updates.py tests/test_commandcode_updates.py tests/test_wizard_outlives_its_callbacks.py` (using the actual test file names after implementation), then `python -m ruff check .` and `python -m mypy .` if those checks are configured for this checkout.
2. Run the repository gate from `README.md`: `python -m pytest -q -W error` plus Ruff format/check and mypy as applicable. Record pre-existing failures separately from regressions.
3. Inspect the exact diff for secrets, environment files, logs, generated artifacts, and unrelated changes. Run `git diff --check`.
4. Perform one fresh-context read-only verification against the spec and focused tests. Do not install the app or perform physical screen-reader/device validation.
5. Stop for explicit user authorization before making a commit, push, or PR. If authorized, create one cohesive commit for the service and wizard integration, then report the exact commit and checks.

## Self-review

- **Coverage:** Every spec requirement maps to Tasks 1–4; review risks map to tests in Tasks 1–3.
- **Step scan:** Each step names a file, a concrete change, and a verification command; no step depends on an unstated API.
- **Type consistency:** Result fields and state/source values match the approved spec; adapter boundaries keep installer execution separate from discovery.
- **Review focus:** The five required failure classes are explicitly tested and reviewed.
- **Proportion:** The plan adds one service module and a narrow wizard integration, reusing current install/update code and adding no dependency or unattended behavior.
