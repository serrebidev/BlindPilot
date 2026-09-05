# Applied: blindpilot_app.py

What was done with the findings in `app-part1.md`, `app-part2.md`, the
blindpilot_app.py items of `app-part3-and-small-modules.md`, section 5 of
`docs-unslop.md`, and the two requests in `applied-backends.md`.
`blindpilot_app.py` went from 10789 lines to 10603.

## Bugs fixed

Part 1

- Bug 1, a question with no options could not be answered. No RadioBox is
  built; the text box (masked for secret questions) is shown from the start
  and takes focus. `test_question_dialog.py::test_a_question_with_no_choices_offers_only_the_text_box`,
  `::test_a_free_text_answer_is_sent_with_enter`.
- Bug 2, Enter on Cancel opened a Hermes conversation. Same guard as
  HistoryDialog. `test_hermes_sessions_dialog_keys.py` (five tests, modelled on
  `test_history_dialog_keys.py`).
- Bug 4, a non-string `narration` value crashed at import.
  `test_narration_modes.py::test_a_mode_this_version_does_not_know_falls_back[[]-{}-3]`.
- Bug 5, `cached_model_options` could block the GUI thread. The probe cache is
  keyed by backend and cwd, and each entry carries the stamp of the binary that
  answered, so the lookup never searches for the CLI.
  `test_model_picker.py::test_cached_lookup_never_searches_for_the_cli`,
  `::test_a_cached_catalog_is_dropped_when_the_cli_changes`.
- Bug 6, the native-update repair could overwrite an npm `claude.cmd` shim.
  Only a `.exe` is repaired. `test_claude_launcher_repair.py::test_an_npm_shim_is_never_overwritten`.
- Bug 7, ConnectDialog IndexError on an empty method list. Falls back to the
  API-key method. No test (needs a live opencode server stub).
- Bug 8, the remote Hermes key file was world-readable for an instant. Created
  with mode 0600 through `os.open`. No test.
- Bug 9 minor: `_open_path` reaps its Popen on a thread; F5 in
  HermesSessionsDialog says Refreshed only when the reload worked
  (`test_hermes_sessions_dialog_keys.py::test_f5_says_refreshed_only_when_the_reload_worked`);
  ModelDialog keeps a saved effort the backend no longer lists
  (`test_model_picker.py::test_the_effort_box_keeps_a_saved_effort_the_backend_no_longer_lists`);
  every `_run_logged_process` caller returns on None instead of reporting
  "exit code None"; `_executable_version` reads stdin from DEVNULL.

Part 2

- B1, the text view stayed empty with one row. An empty control counts as
  zero lines. `test_reading_while_streaming.py::test_a_single_row_still_fills_an_empty_text_view`.
- B2, the remote Hermes test reported into a destroyed dialog.
  `test_remote_hermes_dialog.py::test_the_result_of_a_test_arriving_after_escape_is_dropped`.
- B3, reopening a Hermes conversation left `_stream_response` set.
  `test_hermes_sessions_ui.py::test_reopening_a_finished_conversation_closes_the_replayed_response`.
- B4, a reopened conversation showed nothing in silent-until-response mode.
  Replay rows bypass the live-activity gate (`_replaying`). No test; the
  `_on_activity` path needs most of a panel.
- B5, the wizard ran the sign-in probe on the GUI thread. It runs on a thread
  and reports through `_show_signin_status`.
  `test_wizard_outlives_its_callbacks.py::test_the_sign_in_probe_runs_off_the_wizards_own_thread`,
  `::test_a_probe_for_a_backend_no_longer_chosen_is_ignored`.
- B6, a Stop the backend ignored left the tab silent with no Stop. After the
  cancel join, `_after_cancel` gives Stop back and says so. No test.
- B7, a failed turn's "You:" row was grouped into the next response.
  `test_error_cue.py::test_a_failed_turns_prompt_keeps_a_number_of_its_own`.
- B8, programmatic prompt fills were read back as dictation. `ChangeValue`
  in `_pick_slash_command` and `_action_insert`. No test.
- B9, `_on_login_done` advanced through a CallAfter on a dialog that may be
  gone. It advances directly. No test.

Part 3

- B1, a Chat mode that cannot open left the window half in Chat mode. The
  handler falls through as Agent mode.
  `test_chat_mode.py::test_a_chat_mode_that_cannot_open_falls_back_to_agent_mode_completely`.
- B2, agent-only commands ran against the hidden notebook in Chat mode. The
  File, Conversation and Model items that act on the visible tab are collected
  in `_agent_menu_items` and greyed out in Chat mode; `_refresh_connect_item`
  knows the mode. `test_chat_mode.py::test_agent_only_commands_are_greyed_out_in_chat_mode`.

From `applied-backends.md`

- B5, installing or updating Codex drops the held app-server first, since
  Windows will not overwrite a running executable (`_drop_codex_server`).
  `test_codex_replace_drops_server.py` (three tests).
- B7 window side, a worker that lost its session clears the tab's session id
  in `_on_failed`. `test_error_cue.py::test_a_worker_that_lost_its_session_clears_the_tabs_session_id`,
  `::test_an_ordinary_failure_keeps_the_session`.

## Deletions and merges (lines saved, approximate)

- Non-silent update flow: `_check_for_updates`, the silent branches of
  `_on_update_checked`, `_download_release`, `_on_update_downloaded`,
  `_show_update_error`, and the `download_update` and `schedule_install`
  imports. `check_for_updates_silently` does the check itself. Its test
  `test_startup.py::test_downloaded_update_schedules_before_forced_close`
  went with it. About 100 lines.
- `_check_auth_quick`: no caller. 20 lines.
- `LEGACY_PATH_STANZA_MARKER`: never read. 1 line.
- `_install_argv` and `_hermes_install_argv` share `_script_installer_argv`;
  `_missing_prereq_message(installer)` serves both. Names kept for the tests.
  About 38 lines.
- `_npm_update_argv` is `_npm_install_argv(backend, latest=True)`. 12 lines.
- `install_claude` uses `_run_logged_process`. 17 lines.
- `ensure_on_windows_path` no longer repeats `_add_to_process_path`. 1 line.
- `_make_welcome`, `_make_signin`, `_make_done` start with empty labels;
  `_refresh_backend_copy` sets the only text. About 30 lines.
- `_login_thread` never read. 2 lines.
- `_copy_response` delegates to `_action_copy_response`. 6 lines.
- `_open_hermes_sessions` unreachable backend switch. 5 lines.
- `_insert_hermes_sessions_item` no longer re-binds on every insert. 1 line.
- `_session_panels()` replaces four page-walking loops. About 8 lines.
- `getattr(self, "_narration_items", {})` and the `_sound_cue_items` twin in
  `_apply_preferences` read the attribute directly.
- `_set_app_mode`: the `if _STARTUP_CHECK: pass` branch is folded.
- `_add_session`: comment about code no longer there. 4 lines.
- History-narrative docstrings and comments cut to the rule they state:
  `announce` retry, `_default_permission_mode`, `_save_config`,
  `_wait_for_shutdown`, `_log_unfinished_turn`, the Hermes update comment,
  `_run_in_progress`, the first-message title block in `_on_send`,
  `_refresh_list`, `cancel_worker` and its held-backend comment,
  `_close_question_dialog`, `_run_login`. About 110 lines.

## Stale comments corrected

Module docstring (backends listed, Ctrl+Up), `_login_shell_which` (what a
login shell reads), `_hermes_binary_after_install` (Windows path), the
quick-cycle comment moved from `_LANG_EXT` to `_CYCLE_VALUES`, `ModelOptions`
and `ModelDialog.selection` (per backend), `Earcons` (four cues, Linux
players), `SessionPanel` docstring and the prompt hint (Ctrl+Up), the header
payload comment (every backend), `_add_your_message` (the skipped row never
arrives), `open_find` (Conversation menu), `RemoteHermesDialog` (its fields),
`_build_model_menu`, `main()` (each comment above its code),
`_close_question_dialog` (tab close and quit), `_save_config` (legacy
fallback), `_on_activity` (you and subagent kinds, replay).

## Spoken strings (docs-unslop section 5)

Items 1 to 19 applied as proposed, with these notes. Item 20 went with the
deleted update flow. The done page uses `_chord`, so it says Cmd on macOS.
Item 9's default prompt is "The sign-in page may give you a code." so the
sentence that follows does not repeat it. Runners-up: `AUTH_HINT`,
`_keep_choice` (test updated), the Hermes /fast description, "Sign-in
cancelled", the backend-changed suffix, the slash picker labels, the two
wizard comments, and "is up to date" lost its second sentence. Five "File"
references now say Model, Backend (welcome, Claude not installed twice,
/connect) or File, Set Projects Folder (projects page). Menu labels are
unchanged.

## Skipped

- Part 1 bug 3 (zsh PATH probe): maintainer decision, behaviour unchanged;
  the `_login_shell_which` docstring now says what the probe reads.
- Legacy claude-reader config fallback: kept, as decided.
- Part 2 `_save_clipboard_image` temp files: a behaviour change outside the
  audit's bug list; not done.
- Part 2 `_STEPS[1]` "Coding Agent CLI": never displayed, left.
- Part 2 Q3 "Receiving response" through `_announce`: left, as decided.
- Part 1 bug 7 and 8, part 2 B4, B6, B8, B9: fixed without a test, for the
  reasons above.
- `claude_reader.py`: another agent's.
- Em dashes in list labels that tests assert on (`hermes_session_label`,
  ConnectDialog provider rows), the "Resumed:" status line and the wizard
  title were not in the list and are unchanged.

## Commands run

- `python -m pytest tests/test_error_cue.py tests/test_hermes_sessions_ui.py tests/test_model_picker.py tests/test_chat_mode.py tests/test_codex_replace_drops_server.py tests/test_claude_launcher_repair.py tests/test_errors_are_spoken.py tests/test_remote_hermes_settings.py tests/test_startup.py tests/test_question_dialog.py tests/test_hermes_sessions_dialog_keys.py tests/test_remote_hermes_dialog.py tests/test_narration_modes.py tests/test_chat_integration.py tests/test_wizard_outlives_its_callbacks.py tests/test_reading_while_streaming.py tests/test_menu_layout.py tests/test_model_menu.py tests/test_update_dialog.py tests/test_history_dialog_keys.py tests/test_cli_install.py tests/test_hermes_install.py tests/test_wizard_backend_guidance.py tests/test_preferences_dialog.py tests/test_permission_mode.py tests/test_tabs.py tests/test_live_rows.py -q -p no:randomly`:
  328 passed, 1 skipped.
- `python -m ruff check blindpilot_app.py tests/test_error_cue.py tests/test_hermes_sessions_ui.py tests/test_model_picker.py tests/test_chat_mode.py tests/test_codex_replace_drops_server.py tests/test_claude_launcher_repair.py tests/test_errors_are_spoken.py tests/test_remote_hermes_settings.py tests/test_startup.py`:
  All checks passed.
- `python -m ruff format blindpilot_app.py tests/test_error_cue.py tests/test_hermes_sessions_ui.py tests/test_model_picker.py tests/test_chat_mode.py tests/test_codex_replace_drops_server.py tests/test_claude_launcher_repair.py`:
  7 files left unchanged (after an earlier pass reformatted blindpilot_app.py).
- `python -m mypy`: Success: no issues found in 14 source files.

Test files touched: test_question_dialog.py, test_narration_modes.py,
test_error_cue.py, test_menu_layout.py, test_model_menu.py,
test_model_picker.py, test_chat_mode.py, test_reading_while_streaming.py,
test_wizard_outlives_its_callbacks.py, test_hermes_sessions_ui.py,
test_errors_are_spoken.py (stub borrows `_action_copy_response`),
test_remote_hermes_settings.py (one string), test_startup.py (one test
removed). New: test_chat_integration.py, test_claude_launcher_repair.py,
test_hermes_sessions_dialog_keys.py, test_remote_hermes_dialog.py,
test_codex_replace_drops_server.py.
