# Handover — 2026-10-14

## Full-repo audit fixes — 2026-10-14 (night)

- The user approved all 20 ranked findings from a four-way subagent audit (backends, workers, router/app, storage/updater). Finding 11 (hash-only update verification) was deliberately a `shortcut:` comment at `app_updater.py:300`, not a signing system. Held back as "nice to have" (not fixed, listed): Google stderr reader, exit-0 unnamed rows, `gemini_auth_ok` timeout ignored, `_free_port` TOCTOU, concurrent acp spawns, `thinking_end` dead entry, row-menu binding leak, attachment blobs twice per send, crash log rotation, opencode per-untitled query, JSONL heads re-read, three test-only gaps, `_fired` leak.
- Five parallel subagents with disjoint file ownership, then lead integration. Same-bug-class sweep during integration: transport-contract registration + dead-connection behaviour for the two new `test_hermes_worker.py` fakes (`send` False after `close`, `connected` False after `close`).
- Commits on `fix/commandcode-cooldown-lock`, each reviewed staged: `16a85d8` backends (Muse log shell-argv, Hermes `http://` cleartext refusal with env escape hatch, Code exit-3 wins over stale auth.json, FreeBuff cache temp+replace, WSL probe caches set only on success, probe stderr joined, `end_process_group` bounded wait + pipe close on server stop); `c0cbb41` workers (completion-race guards in all three, subagent rows counted, Muse reasoning buffered to one row at item/completed); `0803803` router (capacity cooldown casefold); `ddab41b` app (`SETTINGS.save()` returns bool + spoken failure at the four named sites, `_call_after_alive` post-death guard, incremental `_live_answer` parse with newline double-count fix, `_search_key` cache); `c8c6380` storage/updater (status file moved from shared temp to `app_data_dir()`, profiles duplicate deep-copies OpenRouter features, `_json_column` tolerance, pending-event cap 4000/2000).
- Gates after integration: full suite **2,379 passed, 17 skipped** in 194.66s (was 2,342 at `e76060c`); `ruff check` clean; `ruff format --check` 225 files; `mypy --no-incremental` no issues in 25 source files. Tree clean except this untracked file.
- Deliberately not fixed (each needs its own inversion test per the new AGENTS rule): four sibling `_on_complete` sites of the same completion-race class in workers; sixteen unguarded `SETTINGS.save()` claim sites beyond the four named ones.
- Deliberately deferred classes, now done in `b77af17` and `6f9c42f`: 13 double-complete guards across the three workers (each invert-proven by a test that fails when the guard is removed — second result line, cancel-after-answer, compaction/replay paths, hermes turn/compaction/replay/session re-entry) and every bare `_save_config`/`SETTINGS.save` call site routed through `_save_or_announce` (blindpilot_app.py:3565) or a claim-or-error branch — 12 new tests in `test_config_write_failures.py`, 3 invert-proof runs. Full suite after both: **2,405 passed, 17 skipped** in 238.41s; ruff/format/mypy clean.
- Still deliberately out of scope: `_write_key` credential-file failure remains log-only; void-await holes in commandcode_worker (`_advertise_server_requests` discards `_await_response`, `_apply_live_selection`) — guards stop the damage, the holes remain; real-device speech ordering unverified.
- Held "nice to have" audit findings, done in `c5a4ed3`, `ae75072`, `86ecb4f`: hermes void-await holes wired (`_advertise_server_requests`/`_apply_live_selection` now return bool and callers stop on a dead turn; the "discarded await" claim was half-wrong — the awaits were discarded in hermes only, commandcode had none) plus a same-class cancel guard on the slash-check path; `_write_key` failure spoken once through `save()`; row-menu bindings moved onto the menu (leak per open); exit-0 silent turns no longer spend a response number on an empty row; concurrent ACP spawn deduped via in-flight Event in `commandcode_backend.py`; opencode port race retried 3× (with `shortcut:` comment); crash log trimmed before faulthandler opens it; google stderr reader joined on every exit path; muse `_fired` capped at 64; JSONL heads cached with mtime invalidation; dead `thinking_end` entry removed; keyring coverage added (5 tests). Finding 8 of the held list ("attachment blobs twice per send") not located — every uploader encodes once; near-miss: chat `protocols.py` encodes text attachments then discards. Not invented.
- Gates after this stretch: full suite **2,425 passed, 17 skipped** in 217.52s; ruff clean; 226 files formatted; mypy clean (agent's in-flight dedupe had 2 union-attr errors, restructured to narrow the Event, logic-equivalent, backend tests green).
- Fresh package after `86ecb4f`: `dist\BlindPilot\BlindPilot.exe`, 7,267,537 bytes, SHA-256 `1DADCEDB61E29CC011E6EC8FE0810C680358801CC24C16A1776C3693F2A56CCB`; both startup smokes exited 0. Installed app still the older build (`0FC473E7…`).
- `e4eb6b2` (test-only): the two stub-wx test files (`test_remote_hermes_settings.py`, `test_progress_cue.py`) shared a copy-pasted stub whose `__getattr__` fabricated `__path__` as a class, so each file died alone at collection (`TypeError: 'type' object is not iterable`) and only ran when a real-wx file loaded first. Extracted `tests/stub_wx.py`: dunder guard, int constants for `ID_*`/all-caps names, `wx.lib.wordwrap` registered. Both files now pass alone (20 passed, 1 skipped) and together. Full suite 2,426 passed, 17 skipped; ruff/format/mypy clean. The `test_commandcode_hidden_spawns` flake seen once under a randomized agent run did not reproduce in 9 runs (standalone ×9 and randomized with neighbours ×2) — treated as a load/timing flake, no fix invented. Package not rebuilt (test-only diff; `86ecb4f` package still current).
- Pushed and PR'd with explicit authorization: branch rebased onto origin/main `37b2930` (v0.46.0) — one CHANGELOG conflict resolved (upstream v0.44-v0.46 sections kept, Unreleased block above them); post-rebase gates 2,470 passed, 17 skipped, ruff/format/mypy clean; pushed to `fork`; PR #81 `Harden backends, workers, settings persistence, and failure races` against serrebidev/BlindPilot main. Post-rebase package not rebuilt (pre-rebase candidate `1DADCEDB…` no longer matches source — rebuild before any install).
- Still unverified: real live 429 capacity failure timing, install over the running app (rebuild first). Pre-existing (untouched): `tests/test_remote_hermes_settings.py` dies alone on stub-wx `wx.lib.wordwrap` import; one order/timing flake seen once in `test_commandcode_hidden_spawns` under a randomized run (passes standalone and under `-p no:randomly`).

## Autorouter improvements — 2026-10-14 (evening)

- Continued the plan the user approved in a Command Code session ("yes all of them"): eight autorouter improvements, implemented by subagent, gated locally, committed in three pieces on `fix/commandcode-cooldown-lock`.
- Context: a prior Codex turn died mid-edit and left `commandcode_router.py` truncated to 95 lines (660 lost). Restored from `bcd67bc` before implementing; baseline focused tests were green (127 passed) after the restore.
- `3733de9` — router policy and state: capacity cooldown per model (`record_capacity_hit`, `capacity_cooling_down`, never shortens a longer one), `exclude_models` in selection, `tier_effort` (QUICK prefers low/medium, STANDARD high, DEEP max, falls back to the model's strongest), expanded `classify()` risk terms with the "add a note" false positive trimmed, `context_tokens` pricing parameter (default keeps the old fixed profile), catalog/state cache TTL+mtime invalidation, defensive float/plan parsing, POSIX flock now bounded at 5s, best-effort `.lock` cleanup, `COMMANDCODE_CREDIT_COOLDOWN_MINUTES` env override for the code-10 window. Policy stays above a marked I/O section in the same module (single-file, no API split).
- `7ee0a4c` — worker: a non-zero exit whose stderr matches the server's capacity words (`429`/`at capacity`/`limited providers`/`providers considered`) records the model at capacity and retries once on the next paid model in the same tier; free fallback only when no paid candidate remains; no retry after answer/tool activity; announcement either way. (Installed CLI fact from a research subagent: this 429 reaches the app as exit 1 with raw server text; exit 5 only fires for standard rate-limit metadata.)
- `e27fc7f` — app: `_set_auto_route` replaces the 3x announcement duplication; `_turn_model` passes the conversation's last known context size into `route()`; fallback announcement renamed to "Switched to {model} after a Command Code stop."
- Verification after the three commits: focused router/worker/backend/slash-command/model-picker suites 192 passed (31 new, XML-confirmed); full suite 2,337 passed, 17 skipped in 222.38s; `ruff check` clean; `ruff format --check` 223 files formatted; `mypy --no-incremental` no issues in 25 source files (two pre-existing unchecked-body notes); `git diff --check` clean; all six touched files are LF-only. PyInstaller build succeeded; `dist\BlindPilot\BlindPilot.exe` version 0.43.12, 7,257,588 bytes, SHA-256 `81A8527E0A3DE112FCF63C68B5E17ADCA4247568DF13CA761836BB53EBE8F885`; both `--startup-smoke` and `--startup-gui-smoke` exited 0.
- Still unverified: real live provider-capacity failure timing, the POSIX flock branch on a Unix host, and the lock-unlink race window. Installed app not replaced (running process left untouched). No push made.
- Two-axis code review (Matt Pocock `code-review`, Standards + Spec sub-agents) of `bcd67bc...HEAD` then applied in `e76060c`. Spec axis caught four real defects: classify regression ("remove the password string" classified quick because the small-edit shortcut had jumped ahead of the risk scan — restored risk-first with a narrow additive-documentation escape), capacity-withheld free models still selectable (`select()`/`best_free_model` now check for any model), QUICK capacity retries going free while paid candidates remained (`_paid_capacity_retry` now routes with `include_free=False`), and inert `context_tokens` (the worker now exposes it; the app's existing `getattr` reads it; the worker's own retry `route()` uses it). Standards axis findings folded in: shared retry guard (`_safe_to_replay`), one `_rerun_on_model` for both retry kinds, one `LIMIT_LABELS` map in the router, merged cooldown/capacity long-stop helper, frozen `_FileCache` dataclass, dead `resolve_effort` and `_read_catalog_text` cut (tests updated). The "Switched to … after a Command Code stop" rename was kept deliberately — capacity stops fall back too, so the old "after a Command Code limit" wording had become wrong.
- After `e76060c`: focused router/worker/model-picker suites 170 passed; the wider focused set 197 passed; full suite **2,342 passed, 17 skipped** in 202.05s; Ruff clean; 223 files formatted; mypy clean. The package built before the review fixes is now stale.
- `verify-and-stop` negative controls: disabling the classify risk-first check failed 5 classify tests; removing `best_free_model`'s capacity check failed `test_capacity_stop_withholds_a_free_model_too` (line 606). Both restored; router suite 53/53 green; tree byte-identical to `e76060c`.
- POSIX flock smoke (Ubuntu WSL, Python 3.14, stdlib only): a holder child held the sidecar lock 8s; the parent's `record_limit_hit(code=5)` returned `False` at 5.0s (the `_LOCK_TIMEOUT_SECONDS` bound), exit 0. This closes the "POSIX flock branch untested" gap on a real Unix host. Temporary smoke script removed after the run.
- Fresh package after `e76060c`: `dist\BlindPilot\BlindPilot.exe` version 0.43.12, 7,259,545 bytes, SHA-256 `1CFEDA9B8EFDDD98F0A10E34E77E041CCD7DD1E66A59CE005D543736BA3AC529`; both startup smokes exited 0. Installed app still the older build.

## Cooldown race follow-up — 2026-10-14

- Follow-up `bcd67bc` makes an unexpected Command Code Auto router failure audible once per unchanged status: "Auto routing unavailable; using the Command Code default." The turn still starts with the CLI default. Regression failed first with no announcement; afterwards model-picker and Command Code input tests passed (65), Ruff, format check, mypy, and `git diff --check` passed. The last full suite (2,305 passed, 17 skipped) was at `05dbfa4`, before this four-line UI change. PyInstaller and both package startup smokes passed for `bcd67bc`; current `dist\BlindPilot\BlindPilot.exe` is 7,250,097 bytes, SHA-256 `577EC9F9B68FF1A841D82F374808FD29943F3074DA42BA42793F21381DE2F414`. The installed app was not replaced.
- Command Code Auto follow-up on user-reported "paid model tried, then no retry": the exact prior failure code and activity stream were unavailable. Official Command Code headless docs and the installed CLI v1.82.1 identify exit 5 as rate limit and 10 as insufficient credits. Current installed catalog parsed 89 models and confirmed `inclusionai/ling-3.1-flash:free` as free. A recorded cooldown previously left standard/deep turns on the CLI default, which could repeat a paid limit; the user chose confirmed free routing for these tasks, accepting possible quality loss. Local commits `26aafbc` and `05dbfa4` now route any tier to a catalog-confirmed, plan-eligible free model during cooldown and announce the choice. If no free route is confirmed or an immediate retry is unsafe after answer/tool activity, Auto announces why. No generic error-text retry was added because the CLI documents distinct limit exit codes.
- Verification for `05dbfa4`: focused router/worker/input/model-picker/backend/slash-command tests 191 passed; full suite 2,305 passed, 17 skipped in 216.35 seconds; Ruff check and format check passed on changed Python files; mypy found no issues in 25 source files (two existing unchecked-function notes); `git diff --check` passed. PyInstaller 6.22.3 build passed, and both package startup smokes exited 0. Candidate `dist\BlindPilot\BlindPilot.exe`: version 0.43.12, 7,250,051 bytes, SHA-256 `3103812620150FD6F429D19C635106FE81E96C26AAA23E975F2990A1F354534D`. Installed executable still hashes `0FC473E7A06780F0737618CBA4555BF88F561F5588A6D5AAAC6CE6B021817506`; no install, live Command Code limit run, or screen-reader pass was performed on this candidate.
- Follow-up commit `6768319` handles a malformed numeric `cooldownUntil` as absent state, so a new limit can still be recorded. Regression failed first with `TypeError: fromisoformat: argument must be str`; after the fix, all 31 router tests, Ruff check and format check, and `git diff --check` passed. Its prior v0.43.12 package was 7,249,544 bytes with SHA-256 `E975902083BE2B2EB706B613DF4EA7FBDED401477A6C428138EFA9C27BC40955`; both startup smokes exited 0. The newer `05dbfa4` package is recorded above.
- Live keyboard check on the running installed v0.43.12 app: UI Automation showed Prompt focused before and after F6 and Shift+F6 during an active Codex turn. Source `_cycle_pane` deliberately keeps focus on Prompt for the status-bar stop and calls `announce`; later focus movement and actual NVDA speech were not confirmed in this attempt. Ctrl+L restored Prompt focus, confirmed by UI Automation. This is not a pass for the new candidate or the complete F6 cycle.
- Deferred manual install was rejected in review and never started. Its rollback missed a failed runtime move, the staged runtime was not fully protected during the wait, and a relaunch could mix runtimes. Do not run `C:\Users\farhan\AppData\Local\Temp\BlindPilot-install-dd65851-after-exit.ps1`. The helper and `C:\Users\farhan\AppData\Local\Temp\BlindPilot-candidate-dd65851` remain inert because the attempted cleanup command was rejected before execution with `blocked by policy`. The installed app remains unchanged. Use a reviewed installer path after this active session can close safely.
- Unix runtime smoke: Ubuntu 26.04 under WSL imported the committed router, ran concurrent rate- and credit-limit writers against a temporary Linux state file, and confirmed the longer cooldown remained active at +29 minutes (exit 0). WSL Python has no pytest (`No module named pytest`), so this was a standard-library smoke, not the Unix test suite.
- Install preparation only: the installed folder includes the running executable, Inno Setup uninstaller metadata, and user/project files such as `AGENTS.md` and `.commandcode`. The checked-in installer would `CloseApplications=force`; no Inno Setup compiler was found on PATH or in the standard Program Files locations. The installed executable hash remains `0FC473E7A06780F0737618CBA4555BF88F561F5588A6D5AAAC6CE6B021817506`; the local candidate hash is recorded below. No process was stopped and no installed file was replaced.
- Local gate for commit `dd65851`: `rtk proxy python -m pytest -q -p no:randomly` passed with 2,302 passed and 17 skipped in 223.05 seconds. `rtk proxy python -m PyInstaller --noconfirm --clean BlindPilot.spec` succeeded. The new `dist\BlindPilot\BlindPilot.exe` reports version 0.43.12, size 7,249,531 bytes, SHA-256 `374313E71EE73A1A4101CDC723DA3768D0778B0D2EDF6AA8655A49800DE7497F`; both `--startup-smoke` and `--startup-gui-smoke` exited 0. The running installed app was not replaced. This proves packaged startup, not live rate-limit handling or screen-reader behavior in the new package.
- Physical retry on the running installed v0.43.12 app: F1 opened an accessible `Keyboard Shortcuts` dialog containing the key list; Escape closed it and UI Automation reported Prompt focused again. F6, Shift+F6, and Ctrl+Up did not produce an observed focus change in the active conversation window. The user reported NVDA did announce status or pane names during F6; the exact sequence and focus path remain unverified because the current turn was producing responses. Ctrl+L was sent to restore Prompt focus, which UI Automation confirmed.
- Earlier physical check: CUA exposed the running BlindPilot window and a focused, named Prompt document while NVDA was running. An F1 input attempt was rejected with `user input was detected in this window; call get_window_state before continuing`; a fresh state still showed Prompt focused. No keyboard action was verified in that attempt.
- A deterministic spawned-process test reproduced a cross-process race: a short rate-limit write could replace a longer credit cooldown. The router now re-reads state while holding a per-user sidecar file lock, then atomically replaces the JSON. Windows uses `msvcrt` and Unix uses `fcntl`; a contended Windows lock waits up to five seconds before the existing best-effort failure path.
- A second spawned-process test on Windows confirmed that a competing writer waits for the sidecar lock. The installed BlindPilot process was left untouched; this source change is not in the installed v0.43.12 build.
- Verification: focused router, worker, backend, and slash-command tests 125 passed; router suite 30 passed; Ruff check and format check passed; mypy passed for 25 source files with two pre-existing annotation notes; `git diff --check` passed. Full suite and packaged startup passed as recorded above. A Unix cross-process smoke passed; the Unix test suite, live limit timing, and physical screen-reader behavior of the new package remain unverified.

## Status update — 2026-10-14

- Upstream PR #80, `Harden Command Code routing and backend failure handling`, merged at 2026-10-14 15:48:59 UTC. Its merge commit is `9b9fa1f`; `git diff --quiet 7a2e6b5 9b9fa1f` exited 0, confirming the submitted branch and merge commit have identical trees.
- Upstream published `v0.43.12` at 2026-10-14 15:55:32 UTC. `origin/main` currently points to `012052f`, a later forced-kill test fix.
- Cleanup completed: the local `submit/commandcode-auto` branch and `.worktrees\\blindpilot-upstream-verify` were removed after PR #80 and release v0.43.12 had already landed. The checkout is now detached at `origin/main` `012052f`; remotes and `HANDOVER.md` remain.
- Current detached-source package check: rebuilding from `origin/main` `012052f` produced `dist\\BlindPilot\\BlindPilot.exe` version `0.43.12`, size 7,248,423, SHA-256 `9D3D70B764AA6124BEA196550291ADC8935FAA8BA3B2FA39E66F32A8A4A60AF3`. Both package smoke modes exited 0. This local build is source-equivalent in version, but its hash differs from the published ZIP because build inputs are not byte-for-byte reproducible.
- Current upstream focused regression check: `python -m pytest -q -p no:randomly tests/test_commandcode_router.py tests/test_commandcode_backend.py tests/test_commandcode_worker.py tests/test_slash_commands.py` — 123 passed in 21.30 seconds.
- Current upstream full-suite check: `python -m pytest -q -p no:randomly` — 2,300 passed, 17 skipped in 196.07 seconds.
- Current upstream quality gates: Ruff passed; format check reports 223 files already formatted; mypy passed with no issues in 25 source files (two existing unchecked-function annotation notes).
- Final automated evidence: current detached `origin/main` `012052f` full suite passed `2,300 passed, 17 skipped` in 196.07 seconds. The Windows accessibility bridge exposed no native apps or windows during two inventory checks, so physical screen-reader, focus traversal, spoken-label, and error-recovery behavior remain manual checks.
- Temporary artifact cleanup completed: verified `C:\Users\farhan\AppData\Local\Temp\\blindpilot-release-check-20261014` contained only the downloaded release ZIP and extraction, then removed it successfully.
- UI bridge configuration fix: backed up and changed the active unified-computer-use MCP config so `CUA_REPL_ENABLED_SURFACES` is `browser,computer` and trusted services include Sky. The already-running CUA server retains its old browser-only environment; restart the Codex/Desktop session before repeating physical validation.
- Physical pass checklist from `README.md`: with Narrator/NVDA active, verify F1 opens Keyboard Shortcuts; Ctrl+L reaches the prompt; F6/Shift+F6 traverse tabs, answers, and prompt; Enter reads an answer row; Shift+Enter opens the answer page and Escape returns; approval dialogs expose Allow, session Allow, Deny, and Escape denies; Command Code Auto announces route/status and one retry after a confirmed limit. Record spoken output and focus restoration from the exact released v0.43.12 build.
- Source PR and release are verified through GitHub metadata. Local installation, physical screen-reader behavior, live Command Code limit timing, and cross-process cooldown-file races remain unverified.
- Installed bundle check: `C:\Users\farhan\AppData\Local\Programs\BlindPilot\BlindPilot.exe` reports file/product version `0.43.12`, SHA-256 `0FC473E7A06780F0737618CBA4555BF88F561F5588A6D5AAAC6CE6B021817506`, and both `--startup-smoke` and `--startup-gui-smoke` exited 0. A normal BlindPilot process was already running; it was left untouched. This does not verify the installer or physical screen-reader behavior.
- Fresh focused regression check: `python -m pytest -q -p no:randomly tests/test_commandcode_router.py tests/test_commandcode_backend.py tests/test_commandcode_worker.py tests/test_slash_commands.py` — 122 passed in 75.93 seconds.
- Fresh full-suite check: `python -m pytest -q -p no:randomly` — 2,299 passed, 17 skipped in 218.70 seconds.
- Fresh quality gates: `python -m ruff check --no-cache .` passed; `python -m ruff format --check .` reported 223 files already formatted; `python -m mypy --no-incremental` passed with no issues in 25 source files (two existing annotation notes in unchecked router functions).
- Fresh package gate: `python -m PyInstaller --noconfirm --clean BlindPilot.spec` completed successfully. The resulting `dist\\BlindPilot\\BlindPilot.exe` passed both `--startup-smoke` and `--startup-gui-smoke` with exit 0.
- Release artifact comparison: published `v0.43.12` Windows ZIP contains version `0.43.12`, size 7,441,241, SHA-256 `0FC473E7A06780F0737618CBA4555BF88F561F5588A6D5AAAC6CE6B021817506`, matching the installed executable. The fresh local `dist\\BlindPilot\\BlindPilot.exe` is version `0.43.9`, size 7,247,581, SHA-256 `4E5BAE38BF59506E753A842D4F7C581A5AF93812BB49710FF74B7526ECE45398`; this checkout's source version is older than the released artifact and should not be installed as v0.43.12. Temporary comparison files remain under `C:\Users\farhan\AppData\Local\Temp\\blindpilot-release-check-20261014` because cleanup was blocked by command policy.
- Version diagnosis: the removed local submission branch was based on `APP_VERSION = "0.43.9"`; current `origin/main` `012052f` reports `APP_VERSION = "0.43.12"`.

Original working tree: `submit/commandcode-auto` tracking `fork/submit/commandcode-auto`.
Submission candidate: `.worktrees/blindpilot-audit-fixes` on `submit/blindpilot-audit-fixes`, based on current `origin/main` (`b48e35d`).
No push, PR, install, or deployment was made in this continuation.

## Local branches

- `fefdce9` — `fix: harden Command Code routing lifecycle`
- `d1b70aa` — `fix: preserve agent files on failed save`
- `ff18cac` — `fix: align wxPython UI types with runtime`
- `595baf6` — `fix: harden backend failure handling`
- `5ed10cd` — `docs: record audit fixes`
- The original-branch commits remain preserved; upstream has since merged Command Code Auto as PR #79.
- The clean submission branch replays the same slices as `5e2f43a`, `ea33690`, `f05fde6`, `896269d`, and `7a2e6b5`. Its tracked working tree and index are clean and it is five commits ahead of `origin/main`.
- `HANDOVER.md` remains untracked in the original checkout as the session handover.

## Current work

- Windows chat credentials again request local-machine persistence (`2`), with a focused API-call test.
- Agent memory, taste, and Hermes edits write a temporary file before replacing the original. A failed replacement leaves the original and removes the temporary file.
- Command Code auto routing honors a recorded cooldown when its price catalog is unavailable or static routing was requested.
- Command Code records every rate or credit stop, even when a free replay is disabled or unsafe. Cooldown writes are atomic, synchronized within the process, and cannot replace a longer credit cooldown with a shorter rate cooldown.
- ACP slash-command discovery reaps a process after forced termination and safely releases a stubborn stdout reader. Static slash-command tests no longer start the installed CLI. A Muse transcript test now stubs an unrelated provider diagnostic instead of reading a real session path.
- wxPython 4.3.2 type errors are resolved in the conversation list and UI callbacks. The conversation list keeps a copy of the supplied drawing rectangle, and the Windows web view passes a string backend name.

## Candidate verification

- Fresh upstream baseline: `python -m pytest -q` passed with 2,281 passed and 17 skipped before replaying the audit commits.
- `python -m pytest -q -p no:randomly`: 2,300 passed, 17 skipped. Focused Command Code tests: 123 passed.
- `python -m ruff check --no-cache .`: passed.
- `python -m ruff format --check .`: 222 files formatted.
- `python blind_pilot.py --startup-smoke` and `--startup-gui-smoke`: both exit 0.
- `python -m mypy --no-incremental`: passed for all 25 configured source files; two annotation notes remain in unchecked `commandcode_router.py` functions.
- One randomized full-suite run reported the physical console negative control as hidden instead of visible. The exact test then passed alone, and all three console-spawn tests passed together; the audit diff does not touch that behavior. No test was weakened or retried inside the suite.

## Prepared PR

Title: `Harden Command Code routing and backend failure handling`

### Summary

```text
Command Code limits
  record cooldown atomically
  withhold paid routes during cooldown
  retry eligible empty turns on a confirmed free model

Agent and backend failures
  preserve agent files through atomic replacement
  reap ACP and muse.ai processes
  decline Hermes Don't ask prompts
  surface worker crashes

wxPython 4.3.2
  align callbacks and drawing types with runtime
```

### Evidence

- **Before:** audit reproduction covered truncated agent files, cooldown bypass, leaked ACP readers, unsafe Hermes approval behavior, and wxPython type errors.
  **After:** `python -m pytest -q -p no:randomly` — 2,300 passed, 17 skipped.
- `python -m pytest -q tests/test_commandcode_router.py tests/test_commandcode_backend.py tests/test_commandcode_worker.py tests/test_slash_commands.py` — 123 passed.
- Ruff, format check, mypy, startup smoke, and GUI startup smoke pass.

### Merge Danger

**Door:** Two-way

Each slice can be reverted without a data migration or dependency rollback.

**Blast Radius:** Medium

Touches Command Code routing and process cleanup, agent-file persistence, backend failure handling, and wxPython event bindings. Physical screen-reader behavior and live Command Code limit timing remain manual checks.

## Next step

Physical screen-reader behavior and live Command Code limit timing remain open validation items. Cross-process cooldown-file races, malformed cooldown recovery, and Auto fallback improvements are on local branch `fix/commandcode-cooldown-lock` at `bcd67bc`; the installed build does not contain those fixes. The current package candidate passed both startup smokes. Do not run the rejected temporary helper; use a reviewed installer route when the active BlindPilot session can close safely. Do not push without explicit authorization.
