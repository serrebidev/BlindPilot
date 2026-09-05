# Applied: agent_backends.py and backend_pool.py

Applied 2026-09-05 on branch audit/2026-09-fixes, from docs/code-audit/backends-and-pool.md and the maintainer's decisions. Nothing was committed. Line numbers below are in the working tree.

## Bugs fixed

Each with the test that expresses it. All tests were written first and seen to fail before the fix.

- B1 Windows child tree. `end_process_group` runs `%SystemRoot%\System32\taskkill.exe /T /F /PID <pid>` with CREATE_NO_WINDOW on Windows, then still calls `proc.kill()`; `OpencodeServer.stop` skips its terminate-and-wait courtesy on Windows and goes straight to the tree kill. Tests: `test_on_windows_a_child_is_stopped_with_its_whole_tree`, `test_on_windows_a_tree_kill_that_fails_still_kills_the_child`, `test_off_windows_no_tree_kill_is_attempted`, `test_opencode_is_stopped_with_its_tree_on_windows` (tests/test_backends.py).
- B2 FreeBuff prewarm TTL. `prewarm_freebuff` starts a daemon `threading.Timer(_FREEBUFF_PREWARM_TTL, expire)`; expiry re-takes the lock, checks the holding is still current, and closes the terminal, which ends the pump thread. Take and discard cancel the timer. Tests: `test_a_prewarmed_terminal_nobody_claims_is_closed_when_its_time_is_up`, `test_a_claimed_terminal_is_not_closed_when_its_time_would_have_been_up` (tests/test_freebuff_prewarm_race.py).
- B3 console-hiding thread leak. `_spawn_freebuff_pty` sets `stream_ended` and re-raises when `PtyProcess.spawn` raises. Test: `test_a_spawn_that_fails_does_not_leave_the_console_watcher_running` (tests/test_freebuff_windows_pty.py, Windows only).
- B4 FreeBuff spawned without `subprocess_env`. Spawn now passes `env=subprocess_env(args[0])`. Test: `test_the_terminal_is_started_with_the_environment_every_cli_gets` (tests/test_freebuff_windows_pty.py).
- B5 update does not stop the held Codex server. Nothing belongs in my files; the exact blindpilot_app.py change is below.
- B6 requests for an unbound thread dropped. `CodexServer._route` answers a request with no listener via `_declined_request`, outside the state lock. Tests: `test_a_request_for_a_conversation_nobody_is_reading_is_declined_not_dropped`, `test_a_question_for_a_conversation_nobody_is_reading_is_answered_with_nothing`, `test_a_notification_for_a_conversation_nobody_is_reading_is_still_dropped` (tests/test_codex_pool.py).
- B7 thread/resume error kills the shared server. A `thread/resume` error now sets `worker.lost_session = True`, fails the turn with the reason plus "The next message starts a new conversation.", and leaves the server in the pool. A `thread/start` error still discards the server. Test: `test_a_conversation_codex_cannot_resume_costs_the_tab_its_session_not_the_server` (tests/test_codex_pool.py). The window side (clearing the tab's session id) is in blindpilot_app.py; see below.
- B8 prewarm key on a fresh install. Key model is `model or _read_freebuff_choice() or FREEBUFF_PREFERRED_MODEL`. Test: `test_a_prewarm_before_any_model_was_chosen_fits_the_turn_that_follows` (tests/test_backends.py).
- B9 Windows pump drops the last output. The pump reads until `EOFError` instead of looping on `isalive()`. Test: `test_what_the_terminal_said_on_its_way_out_is_still_read` (tests/test_freebuff_windows_pty.py).
- B10 `.ps1` offered as a CLI. Dropped from both suffix tuples. Test: `test_a_powershell_script_is_not_offered_as_a_cli` (tests/test_backends.py).
- clientInfo.version. `_app_version()` reads `APP_VERSION` off `sys.modules["blindpilot_app"]` when it is loaded, "unknown" otherwise, so there is no import of the window module from here.

## Changes needed in blindpilot_app.py (not made; another agent owns the file)

B5, in `update_backend` (around line 1513, next to the opencode branch) and in `install_backend` (around line 1379):

```python
    if backend == BACKEND_CODEX:
        # The held app-server is the executable npm is about to replace, and
        # Windows will not overwrite one that is running.
        log("Stopping Codex's app-server so its executable can be replaced...")
        backend_pool.pool().drop(backend_pool.pool_key(BACKEND_CODEX))
```

Suggested test: keep a fake `HeldProcess` under `pool_key(BACKEND_CODEX)`, stub `_run_logged_process`, `_find_npm` and `_npm_update_argv`, run `update_backend("codex", log)`, assert `pool().take(key) is None`.

B7 window side, in `SessionPanel._on_failed` (line 6897), before `self._announce(...)`:

```python
        if getattr(self._worker, "lost_session", False):
            # Codex could not resume this conversation; the id names nothing.
            self._session_id = None
```

`_on_failed` runs from the event drain before `_on_worker_finished` clears `self._worker`, so the worker is still there to read. The worker already announces the reason through `on_failed`.

## Deletions

Lines are net, from `git diff --numstat` and the file lengths.

- `_offer` and its three `queue.Full` branches; every inbox is an unbounded `queue.Queue()`. 21 lines.
- `CodexWorker._request_id` and the fallback in `_next_id`. 5 lines.
- `CodexServer.detach` (test-only; the test uses `detach_listener`). 10 lines.
- `FreebuffWorker._clean_freebuff_screen`. 4 lines.
- `_SENTENCE_END_RE` alias and the `_complete_sentences` wrapper; `complete_sentences` is imported under the old name so tests still find it. 12 lines.
- `CodexWorker._decline_server_request` and the duplicated else in `_handle_server_request`, merged into module-level `_declined_request` and `_unhandled_request`, which `_route` now also uses for B6. 27 lines.
- Duplicated option parsing in `_codex_questions` and `_opencode_questions`, merged into `_question_options`. 8 lines.
- Duplicated FreeBuff credential reading in `backend_auth_ok` and `_freebuff_account_lines`, merged into `_freebuff_account` and `_freebuff_signed_in`. 9 lines.
- `_opencode_auth_ok` and the auth.json read in `_opencode_account_lines`, merged into `_opencode_providers`. 10 lines.
- Duplicated chat folder candidate lists, merged into `_freebuff_chat_folders`. 9 lines.
- `_freebuff_boot_ready` new-chat branch and `_freebuff_dropped_new_chat`, merged over `_freebuff_new_chat_log`. 6 lines.
- Duplicated last-line extraction in `_freebuff_launch_failure` and `_freebuff_startup_silence`, merged into `_last_visible_line`. 5 lines.
- Inline `CODEX_HOME` read in `codex_model_options`, now `_codex_home()`. 1 line.
- The first `find_backend_cli` call in `CodexWorker._do_run`; `_start_codex_server` already raises the install message. 4 lines.
- `Adapter.start` (nothing outside tests called it) and `HeldProcess.binding` (never set). 12 lines including their comments; the 7 test call sites updated.
- Comment prose: `CodexWorker.__init__` field comments (32 lines), `_is_this_turn` docstring (14), `_release` (6), `Adapter` docstring (8), `HeldProcess.interrupt` (2), the "Task 7" lock comment (rewritten, 1).
- docs/superpowers/plans (1959 lines) and docs/superpowers/specs (331 lines) removed with `git rm`; the spec's measurements and decisions are 23 lines in the backend_pool.py module docstring. The ruff.toml `extend-exclude` block and its comment: 9 lines. No .github/workflows file references the folder.

agent_backends.py: 6320 to 6305 lines with 149 lines of fixes added. backend_pool.py: 411 to 415 with the 23-line docstring added.

## Stale comments corrected

- `pool_key` docstring no longer claims opencode, Claude, Hermes and FreeBuff are on the pool.
- `drop_all` and `stop_all_held_processes` no longer say an update calls them; the update path drops one key.
- The idle limit is described as a constant in the module docstring; no docstring called it a setting.
- The sentence-regex comment about Hermes sharing is gone with the alias.
- `_FREEBUFF_TURN_SECONDS` and its note moved above the `_FREEBUFF_STARTUP_SILENCE_SECONDS` paragraph.
- `_codex_app_server_binary` docstring no longer promises "reliable ownership".
- `_FREEBUFF_PREWARM_TTL` comment now says a timer closes the terminal.
- `end_process_group` docstring explains the Windows tree kill.

## Skipped

- Cross-module duplicates (`no_window_kwargs` vs `blindpilot_app._no_window_kwargs` vs `hermes_backend.py`; the login-shell block vs `_login_shell_which`): the other copies are in files owned by other agents. `agent_backends.no_window_kwargs` and `find_backend_cli` are the ones to keep; the others could import them.
- A Job Object at spawn for B1: the maintainer chose taskkill.
- `Adapter.interrupt` kept, as decided.

## Commands run

- `python -m pytest tests/test_backends.py tests/test_backend_pool.py tests/test_codex_pool.py tests/test_pool_contract.py tests/test_held_process_drop_sites.py tests/test_freebuff_prewarm_race.py tests/test_freebuff_windows_pty.py tests/test_claude_stream_resilience.py tests/test_cli_install.py tests/test_exe_search_path.py tests/test_backend_login.py tests/test_screen_text_properties.py -q -p no:randomly`: 304 passed, 2 skipped (both POSIX-only or platform-gated).
- `python -m ruff check` on agent_backends.py, backend_pool.py, tests/pool_contract.py and the nine test files above: all checks passed.
- `python -m ruff format` on the same files: 2 reformatted (tests/test_freebuff_windows_pty.py and one wrapping change in a source file, with no change to any line count), 8 unchanged; a second `--check` afterwards reports all formatted.
- `python -m mypy`: no issues found in 14 source files.
