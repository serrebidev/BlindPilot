# Applied: accessible_ai, small modules, tests, CI and packaging

What was done with `accessible-ai-tests-ci.md` and the small-module items of
`app-part3-and-small-modules.md`, on branch `audit/2026-09-fixes` (base
730d183, v0.21.4). Each line names the test that pins it.

## Bugs fixed

1. Chat log path computed lazily and the chat data folder redirected in tests.
   `accessible_ai/logging_setup.py` now has `log_path()`; `LOG_PATH` and
   `configure_logging` are gone. `tests/conftest.py` gains an autouse fixture
   that points `accessible_ai.storage.paths.system_config_dir` at a temp dir
   and closes the chat log handler afterwards. Tests:
   `test_chat_integration.py::test_the_chat_log_path_is_not_fixed_at_import_time`,
   `::test_tests_never_touch_the_installed_apps_data_folder`.
2. The warning-clean sweep compiles only the repository's own sources. `docs`
   joined the prefix exclusions and anything `git check-ignore` hides is
   dropped (falls back to the old sweep when git is absent). Collection went
   from 694 cases to 141. Tests:
   `test_sources_are_warning_clean.py::test_the_sweep_skips_what_git_ignores`,
   `::test_the_sweep_ignores_a_virtualenv_however_it_is_named` (new docs case).
3. Legacy AccessibleAI import catches `sqlite3.Error`, unlinks a half-written
   target, and closes both connections (`chat_integration.py`). Tests:
   `test_chat_integration.py::test_a_damaged_accessibleai_database_is_skipped_and_leaves_no_half_copy`,
   `::test_a_sound_accessibleai_database_is_copied`.
4. `ChatPanel._last_assistant_message` reads one row through the new
   `Database.last_message` instead of every message and every attachment blob.
   Test: `test_chat_integration.py::test_the_last_message_is_read_without_its_attachments`.
5. Blank API key on an existing account is explicit: the label says "API key,
   blank keeps the stored key:" and `on_ok` carries a comment saying so
   (`accessible_ai/ui/accounts.py`). Test:
   `test_chat_integration.py::test_editing_an_account_says_a_blank_key_keeps_the_stored_one`.
6. `shell: bash` on the "Run static checks" step in `ci.yml` and `release.yml`
   (and on the Linux install step in `ci.yml`). Test:
   `test_ci_workflows.py::test_every_multi_command_step_fails_on_its_first_failing_command`.
7. `finish_reason: "length"` yields a recorded status event ("Response cut off
   at the model's length limit") and a per-choice `error` raises
   `ProviderError` (`accessible_ai/providers/protocols.py`, streaming and
   non-streaming). Tests:
   `test_openrouter_features.py::test_an_answer_cut_off_at_the_length_limit_says_so`,
   `::test_a_finished_answer_reports_no_cut_off`,
   `::test_an_error_openrouter_reports_inside_a_choice_is_raised_as_one`.
8. `markdown_rows.py` keeps `html_block` text as a prose row and enables the
   table rule, emitting one "Row: a, b" prose row per table row. Tests:
   `test_markdown_rows.py::test_html_block_text_is_kept_as_a_prose_row`,
   `::test_a_table_is_read_one_row_at_a_time_in_plain_words`,
   `::test_a_table_cell_holding_markup_reads_as_words`.
9. `session_history.py`: Hermes store opened via `path.absolute().as_uri()`;
   opencode `float(updated)` guarded; `clean_user_text` uses a single-pass tag
   stack (linear); `isCompactSummary` user records skipped. Tests:
   `test_session_history.py::test_a_hermes_home_with_uri_characters_in_it_is_still_read`,
   `::test_opencode_text_in_the_time_column_does_not_take_the_list_down`,
   `::test_unclosed_tags_are_cleaned_in_linear_time`,
   `::test_a_compaction_summary_is_not_the_persons_prompt`.
10. `tools/make_icon.py`: `ic11` is 32 (16@2x) and `ic12` is 64 (32@2x).
    Test: `test_make_icon.py::test_retina_icns_entries_carry_their_documented_sizes`.
11. `update_dialog.py` workers post through `_call_after`, which does nothing
    once `wx.GetApp()` is None. Test:
    `test_update_dialog.py::test_a_check_that_finishes_after_the_app_is_gone_stays_quiet`.

## Deletions (lines saved, re-verified by grep across the repo and tests)

| What | Where | Lines |
|---|---|---|
| `INSTALLED_MARKER`, `PORTABLE_CONFIG_DIR`, `executable_dir`, `is_portable`, `portable_config_dir` | `accessible_ai/storage/paths.py` | 54 |
| `configure_logging` and `LOG_PATH` | `accessible_ai/logging_setup.py` | 24 (6 added for `log_path`) |
| `touch_conversation`, `get_setting`, `set_setting`, `app_settings` table | `accessible_ai/storage/database.py` | 25. No migration: nothing read the table, and an existing database keeping it is harmless |
| `unsupported_mode` | `accessible_ai/providers/protocols.py` | 4 |
| `SERVER_TOOL_LABELS` | `accessible_ai/models.py` | 1 |
| `__version__` and its VERSION-file claim | `accessible_ai/__init__.py` | 5 |
| Nine provider dicts collapsed to `(base_url, api_mode)` plus one shared endpoint dict | `accessible_ai/providers/config.py` | 49 net. `BUILTIN_PROVIDER_DEFAULTS` keeps the same keys and values |
| "Compile source" `py_compile` step | `.github/workflows/release.yml` | 3 |
| `pexpect` hidden import moved under the non-Windows branch | `BlindPilot.spec` | 0 (a warning per Windows build) |
| `sys.path` hack, five `# noqa: E402`, "Run from the project root" docstring | `tests/test_openrouter_features.py` | 12 |
| Eight `assert not hasattr(...)` | `tests/test_chat_mode.py` | 8 |
| `test_tab_switcher_mirrors_the_session_pages` moved to `tests/test_tab_strip_focus.py` on that file's `_running_app` and `_frame` helpers | tests | 9 net |
| `claude_reader.py` shim; six tests import `blindpilot_app`; `mypy.ini` and `release.yml` references removed | root | 23 plus 2 references |
| `panel.imported_database` and its comment | `chat_integration.py` | 3 |
| `render()` | `tools/make_icon.py` | 11 |
| Duplicate `is_file()` and `import sqlite3` in `_hermes_connect`; duplicate branch in `describe_age` | `session_history.py` | 5 |

## Stale comments fixed

- `BlindPilot.spec` docstring: the build line no longer names
  `--additional-hooks-dir`; it says the hooks directory is `hookspath` in the spec.
- `accessible_ai/__init__.py`: the VERSION-file claim went with `__version__`.
- `accessible_ai/storage/paths.py`: the `AccessibleAI.app` sentence went with
  `executable_dir`.
- `accessible_ai/ui/accounts.py`: the two "Windows Credential Manager" strings
  read `CREDENTIAL_STORE_NAME`, which says "the system keychain" off Windows.
- `accessible_ai/ui/accounts.py`: the Claude note now says attachments are
  available on OpenRouter accounts only, which is what `chat_panel.py` enforces.
- `.github/workflows/release.yml`: the icon comment says the tool renders over
  the committed copies rather than that no binary is committed.
- Also: `diagnostics.py` "currently goes nowhere", `session_history.py` "three
  history stores", `tests/test_ci_workflows.py` docstring describing the time
  before `ci.yml`, `markdown_rows.py` comment on why tables were off.

## Changes needed in files outside this slice

- `README.md:188` still says `claude_reader.py` is a compatibility alias. Drop
  that sentence: "`blind_pilot.py` is the entry point; the code is in
  `blindpilot_app.py`."
- `tests/test_claude_launcher_repair.py:3` (another agent's new file): the
  module docstring holds `%APPDATA%\npm\claude.cmd`, and `\c` is an invalid
  escape, so `test_sources_are_warning_clean.py` fails on it and the release
  build's `pytest -W error` would too. Make the docstring raw (`r"""`) or
  write `\\npm\\claude.cmd`.

## Skipped, and why

- `paths.app_data_dir` docstring "BlindPilot's existing per-user settings
  folder" versus the `~/.blindpilot` fallback when `APPDATA` is unset
  (`agent_backends.blindpilot_config_dir` falls back to
  `~/AppData/Roaming/BlindPilot`). Fixing it means choosing a fallback, which
  is code behaviour in another agent's file, not a comment.
- `database.py::_json_column` comment about an older connection: harmless,
  not in the task list.
- `make_icon.py` `ic06` entry: the audit was unsure it is a documented type;
  only ic11/ic12 were in scope.
- The `providers/openai_compatible.py` and `chat_completions.py` duplication,
  `credentials.py` ctypes hoisting, and the other accessible_ai bloat rows not
  named in the task were left alone.
- DPI awareness: another phase.

## Open questions for the maintainer (left as they are)

1. Conversations are write-only (no list/open/delete in `Database` or the UI).
2. A cancelled or failed turn discards the partial answer but leaves it on
   screen until the next redraw.
3. Attachments on non-OpenRouter Chat Completions providers: the providers
   build the parts, only `chat_panel.py` refuses them.
4. `custom_headers_json` sits in plain text in `chat.sqlite3`.

## Commands run

- `python -m pytest tests/test_chat_mode.py tests/test_tab_strip_focus.py tests/test_openrouter_features.py tests/test_chat_integration.py tests/test_markdown_rows.py tests/test_session_history.py tests/test_update_dialog.py tests/test_make_icon.py tests/test_ci_workflows.py -q -p no:randomly`: 131 passed.
- `python -m pytest tests/test_live_rows.py tests/test_sources_are_warning_clean.py tests/test_diagnostics.py tests/test_certificate_trust.py tests/test_linux_accessibility.py tests/test_macos_directory_migration.py tests/test_claude_stream_resilience.py tests/test_cli_install.py tests/test_model_picker.py tests/test_permission_mode.py tests/test_tabs.py -q -p no:randomly`: 296 passed, 5 skipped, 1 failed (`tests/test_claude_launcher_repair.py`, see above; not this slice's file).
- `python -m ruff check <files in this slice>`: all checks passed (after removing an unused `pytest` import in `tests/test_chat_integration.py`).
- `python -m ruff format <files in this slice>`: 5 files reformatted, then clean.
- `python -m mypy`: Success, no issues found in 14 source files.
- `python -m pytest tests/test_sources_are_warning_clean.py --collect-only -q`: 141 tests collected (was 694).
