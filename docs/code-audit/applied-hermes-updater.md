# Applied: hermes_worker.py, hermes_backend.py, app_updater.py

What was done with `hermes-and-updater.md` on branch `audit/2026-09-fixes`
(base 730d183, v0.21.4). Nothing was committed. Tests were written first;
each line names the test that pins it. The work ran across two agent
sessions; the first landed the worker edits and the tests, the second finished
the backend, the updater and the report.

## Bugs fixed

1. Setup helper quotes `/DIR=` and `/LOG=` (`app_updater.py`,
   `_WINDOWS_SETUP_HELPER`). Test:
   `test_app_updater.py::test_the_setup_helper_hands_the_installer_paths_with_spaces_as_one_argument`
   (Windows only; runs the helper under Windows PowerShell against a stub
   installer that records its arguments).
2. `_await_response` honours the turn-over return from `_handle_event`. The
   new `_await_frame` sets `_ended` and returns; `_call`, `_wait_for_ready`
   and the slash path check `_ended` before reporting a failure. Test:
   `test_hermes_backend.py::test_a_turn_that_ends_before_the_reply_reports_one_terminal_callback`.
3. Held connection dropped after an abnormal end. `_clean_end` is set only by
   a completed `_turn_complete`, a slash command, compaction and replay
   success; `run()` requires it before handing the connection on. Tests:
   `test_hermes_backend.py::test_an_error_event_drops_the_held_connection`,
   `::test_a_refused_prompt_drops_the_held_connection`.
4. `StdioTransport.receive` waits its timeout on a closed stream instead of
   returning at once. Test:
   `test_hermes_backend.py::test_a_closed_stdio_transport_still_waits_on_receive`.
   The hand-rolled ready loop in `hermes_model_options` is now the shared
   `_ready_or_fail`, which also checks `connected()`. Test:
   `test_hermes_model_selection.py::test_a_gateway_that_dies_before_ready_is_reported_at_once`.
5. Remote TLS goes through `certificate_context()`, both for the password
   login's urllib opener and for `create_connection(sslopt=...)`. Tests:
   `test_hermes_backend.py::test_a_secure_remote_connection_uses_the_packaged_trust_store`,
   `::test_the_password_login_uses_the_packaged_trust_store`.
6. Reply waits are timed by the clock (`_await_frame` measures against
   `_now()`), for replies and for `gateway.ready`. Test:
   `test_hermes_backend.py::test_a_reply_wait_runs_out_by_the_clock_not_by_empty_reads`.
7. Idle-limit message says the turn went quiet, not that the connection
   closed. Test:
   `test_long_turn_connection.py::test_the_idle_limit_says_the_turn_went_quiet_not_that_the_connection_closed`.
8. `remote_ws_url("[::1]:9119")` keeps one port. Test:
   `test_hermes_backend.py::test_a_bracketed_ipv6_host_keeps_its_one_port`.
9. Status file written `-Encoding UTF8` by `Save-Failure` and read as
   `utf-8-sig`. Tests:
   `test_app_updater.py::test_a_failure_reason_with_accents_survives_the_status_file`
   (Windows only), `::test_a_status_file_with_a_byte_order_mark_reads_cleanly`.
10. `wsl_state_db` and `wsl_sqlite_query` pass `_no_window_kwargs()`. Test:
    `test_hermes_backend.py::test_the_wsl_history_probes_stay_off_the_screen`.
11. `StdioTransport.send` holds a lock around write and flush. No test; the
    interleaving is a race between two threads and is not reproducible
    deterministically.
12. `cancel()` no longer closes the transport while the worker thread is
    alive; `run()`'s `finally` does it on the worker thread as soon as the
    loop sees the cancellation (every wait loop checks `_cancelled` every
    0.5 s). The `finally` re-checks `_cancelled` after handing a connection to
    the holder, so a cancel that lands between the check and the hand-over
    still drops it. Test:
    `test_hermes_backend.py::test_cancel_leaves_the_close_to_a_running_worker_thread`.
    The three existing cancel tests, which call `cancel()` on a worker that
    was never started, still see a synchronous close.
13. A clarify with an id but no readable question is answered empty and a row
    says so. Test:
    `test_hermes_questions.py::test_a_clarify_with_no_readable_question_is_still_answered`.
14. Session catalog skips a row whose count or timestamp does not parse.
    Test: `test_hermes_sessions.py::test_catalog_keeps_the_good_rows_when_one_is_malformed`.

`_apply_live_selection` (maintainer decision): `/model` is sent only when the
pick differs from what the session last ran. `HeldConnection` remembers the
applied model; a fresh `session.create` records the pick, a resumed session
records nothing so the first reused turn asks. A refusal is announced and not
recorded as applied. Tests:
`test_hermes_model_selection.py::test_a_reused_session_already_on_the_picked_model_is_left_alone`,
`::test_a_changed_pick_moves_the_session_and_is_remembered`,
`::test_a_refused_switch_is_heard_and_not_recorded_as_applied`.

## Deletions and bloat

- `hermes_worker.py` `_REPLAY_READ_SETTINGS` and its comment: 7 lines.
- `hermes_backend.py` `TICKET_TTL_MARGIN`: 4 lines.
- `hermes_backend.py` misplaced credential-names paragraph above
  `REMOTE_CREDENTIALS`: 5 lines.
- `hermes_backend.py` duplicated ready loop in `hermes_model_options`
  replaced by `_ready_or_fail`: 12 lines became 4.
- `hermes_worker.py` `_message_text` redundant `isinstance`: 2 lines.
- `hermes_worker.py` `_as_slash_command` evaluates `split(None, 1)` once.
- `hermes_worker.py` send/await/report boilerplate collapsed into `_send`,
  `_await_frame` and `_call`. Measured by function size at HEAD against the
  working tree: `_ensure_session` 67 to 44, `_run_replay` 76 to 58,
  `_run_turn` 36 to 23, `_as_slash_command` 49 to 39, `_upload_attachments`
  63 to 45, `_run_compaction` 26 to 15, `_apply_live_selection` 45 to 40,
  against 60 new lines for the three helpers. Net about 75 lines once the
  comment trims inside those functions are set aside; the whole file is
  283 lines removed and 206 added. Behaviour kept, except that a dead
  transport during compaction now reports the transport's own reason before
  the generic text, as every other request already did.
- `app_updater.py` `TEMPORARY_PREFIX` moved up beside the other constants and
  used by the download's `mkstemp`, so `sweep_temporary_files` and the
  download cannot drift apart. The macOS helper's shell literal
  `BlindPilot-update-lock` is left as is; it is inside a shell script string.
- Change-history narrative cut to one WHY sentence each: `hermes_worker.py`
  (`on_question`, effort, `session_key`, cwd landing, provider split, title,
  `_consume_turn` clock, `_answer_clarify`), `hermes_backend.py`
  (`MODEL_ROW_SEPARATOR`, `WebSocketTransport.send` and reader docstrings,
  `_ready_or_fail`, `hermes_model_options`), `app_updater.py`
  (`Save-Failure`, `_windows_helper_flags`). About 90 lines.

## Stale docs

- `_model_rows` docstring no longer claims a row is what `/model` accepts.
- "Kept so the accessor below can answer honestly" replaced; there is no
  accessor.
- `approval.request` comment says what the code does: approved in yolo modes,
  denied elsewhere, a row either way.
- `_release_finished_sentences` docstring says it always emits and the window
  drops the rows.
- `tests/test_hermes_upstream_contract.py::test_hermes_never_calls_the_question_callback`
  deleted (vacuous, and false since the clarify and secret handlers).

## Changes in other files

- `tests/test_transport_contract.py`: registered
  `test_hermes_model_selection._ReplyTransport` in `FAKES` with
  `stream_ends=True` (its frames run out like a pipe). Done here at the
  coordinator's request; that registry is shared across slices.
- Nothing else is needed outside the three modules and their tests.

## Skipped, and open questions

- `approval.request` stays auto-denied outside the yolo modes, per the
  maintainer. Open question for the maintainer: should it go through
  `on_question` as a yes/no, the way clarify does, so Hermes is usable in
  "default" mode? The "approve"/"deny" decision values are pinned by a test
  but not verified against Hermes.
- `--session` on `/model` is still unverified against Hermes; the switch is
  best-effort and a refusal is spoken.
- A `_one_shot` helper for `hermes_session_catalog` and
  `hermes_model_options` was not written: the audit estimated about 50 lines
  saved, under the 60-line bar.
- The request loops in the catalog and model query still wait out their
  deadline on a transport that dies mid-request. They no longer spin (bug 4),
  so the cost is a slow answer, not a hot CPU. Left as is to keep the diff
  small.

## Commands run

- `python -m pytest tests/test_app_updater.py -q -p no:randomly`:
  24 passed, 1 skipped (the macOS helper test).
- `python -m pytest tests/test_hermes_backend.py tests/test_hermes_model_selection.py tests/test_hermes_questions.py tests/test_hermes_sessions.py tests/test_hermes_upstream_contract.py tests/test_long_turn_connection.py -q -p no:randomly`:
  157 passed.
- `python -m pytest tests/test_transport_contract.py -q -p no:randomly`:
  17 passed.
- `python -m ruff check hermes_worker.py hermes_backend.py app_updater.py tests/test_hermes_backend.py tests/test_hermes_model_selection.py tests/test_hermes_sessions.py tests/test_hermes_questions.py tests/test_hermes_upstream_contract.py tests/test_app_updater.py tests/test_long_turn_connection.py tests/test_transport_contract.py`:
  clean.
- `python -m ruff format` on the same files: no changes.
- `python -m mypy`: no issues in 14 source files.
