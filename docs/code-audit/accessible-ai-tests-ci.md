# Audit: accessible_ai, tests, CI and packaging

Read-only audit, 2026-09-04. Scope: `accessible_ai/**`, `chat_integration.py` (its only entry point), `tests/`, `conftest.py`, `pytest.ini`, `.github/workflows/*.yml`, `BlindPilot.spec`, `installer/BlindPilot.iss`, `requirements*.txt`, `ruff.toml`, `mypy.ini`.
Verified: `ruff check accessible_ai tests chat_integration.py` clean; `ruff format --check` clean; `tests/test_openrouter_features.py` 30 passed; `tests/test_sources_are_warning_clean.py` 694 passed (see Bug 2 for why that number is wrong).

Checked and found sound (no finding): every UI mutation from worker threads in `chat_panel.py` and `accounts.py` goes through `wx.CallAfter`; `Database.connect()` opens one sqlite connection per call and commits in the context manager, so no connection crosses a thread and no write is left uncommitted; no API key reaches a log (`headers()` values are never logged; `raise_for_status` echoes only the server body); Anthropic gets the system prompt as top-level `system` and `x-api-key`, not a bearer token; `iter_sse_json` skips OpenRouter's `: OPENROUTER PROCESSING` comment lines and raises on a top-level `error` object.

## Bugs

Ordered by severity, then confidence.

### 1. Tests write into the user's real chat log folder — medium / high
- `accessible_ai/logging_setup.py:9` computes `LOG_PATH = app_data_dir() / "blindpilot-chat.log"` at import time, and `app_data_dir()` (`storage/paths.py:97`) calls `mkdir`. Importing `chat_integration` therefore creates the real `%APPDATA%\BlindPilot` on any machine, including CI runners and developer boxes.
- `chat_integration.py:30-40` `_configure_chat_logging()` opens a `FileHandler` on that real path and never removes it. `tests/test_chat_mode.py:89` calls `frame._set_app_mode(APP_MODE_CHAT)`, which reaches it. The `conftest.py:15-44` fixture redirects only `diagnostics.log_dir`; it says the intent is that tests never touch the installed app's logs.
- Trigger: `python -m pytest tests/test_chat_mode.py -q`, then look at the mtime of `%APPDATA%\BlindPilot\blindpilot-chat.log`.
- Fix: make it `def log_path() -> Path` (no import-time side effect) and add an autouse fixture in `conftest.py` that monkeypatches `accessible_ai.storage.paths.system_config_dir` to a temp dir; or have `_configure_chat_logging` accept the path.
- Test: the fixture asserts the real path was not created/modified during the run.

### 2. The warning-clean sweep compiles 566 third-party files from a cache inside `docs/` — medium / high
- `tests/test_sources_are_warning_clean.py:24-43` excludes only `.venv*`, `venv*`, `build*`, `dist*`, `__pycache__`, `.test-tmp`. `docs/visual-audit/sandbox/Local/uv/cache/...` holds 566 `.py` files of fastmcp, uvicorn, rich_rst and friends (162 MB, not covered by `.gitignore`). Result: 694 parametrised cases, 128 of them ours. The test's own docstring (lines 51-56) describes exactly this failure.
- Trigger: `python -m pytest tests/test_sources_are_warning_clean.py --collect-only -q | wc -l`. A `SyntaxWarning` in any of those vendored files turns CI red for a package nobody here maintains.
- Fix: delete `docs/visual-audit/sandbox` (and add `docs/visual-audit/sandbox/` to `.gitignore`), and add `"docs"` or a `sandbox`/`cache` prefix to the exclusion tuple.
- Test: the collection count drops to ~130; `test_the_sweep_ignores_a_virtualenv_however_it_is_named` gains a `docs/x/cache/y.py` case.

### 3. Legacy-database import catches `OSError` only — medium / medium
- `chat_integration.py:63-68`: `sqlite3.connect(source).backup(target)` raises `sqlite3.OperationalError` (source locked because AccessibleAI is running; "file is not a database" for a damaged file). That is not an `OSError`, so it escapes `create_chat_panel`, and `blindpilot_app._set_app_mode` reports "Chat mode could not be opened" and snaps back to Agent mode on every attempt. A backup interrupted mid-way leaves a partial `target`, and `target.exists()` (line 56) means it is never retried; `Database(path)` then opens a possibly corrupt file.
- `with sqlite3.connect(...) as db` commits but does not close; both connections leak.
- Fix: `except (OSError, sqlite3.Error)`, wrap both connections in `contextlib.closing`, back up to `target.with_suffix(".tmp")` then `os.replace`.
- Test: put a zero-byte file at a candidate path, call `import_existing_accessible_ai_data(tmp)`; expect `None` and a working `Database(tmp)`.

### 4. Every turn reloads every attachment blob to check one role — medium / medium
- `Database.list_messages` (`storage/database.py:352-393`) always loads all `message_attachments.data` for the conversation (up to `MAX_TOTAL_ATTACHMENT_BYTES` = 100 MB per message, `chat_panel.py:32`).
- `ChatPanel._last_assistant_message` (`chat_panel.py:791-797`) calls it only to look at the last row's role, and `_update_regenerate_enabled` (`:799-802`) calls that after every finished turn and on every "New conversation". `_generation_settings` (`:761`) and `_render_conversation` (`:822`) also call it, so one send on a conversation carrying a 90 MB file reads the blob three times and re-sends it base64 encoded (the last is inherent).
- Fix: add `Database.last_message(conversation_id) -> Message | None` (one row, no attachments), or a `with_attachments: bool` parameter; use it in `_last_assistant_message`.
- Test: sqlite `set_trace_callback` counting `message_attachments` reads during `_update_regenerate_enabled`; expect 0.

### 5. Clearing the API key on an OpenAI-compatible account keeps the old key — low / high
- `accessible_ai/ui/accounts.py:400-401`: `if api_key: set_api_key(...)`; no `else`. Built-in providers reject a blank key at line 331, but a custom server may need none. Editing such an account, blanking the field and pressing OK silently keeps sending the previous `Authorization: Bearer`.
- Fix: `elif not is_builtin_provider(provider): self.credentials.delete_api_key(account_id)`.
- Test: fake `CredentialStore`, existing custom account with a key, blank field, `on_ok`; assert `delete_api_key` was called.

### 6. "Run static checks" cannot fail on the Windows runner — low / medium-high
- `.github/workflows/ci.yml:107-110` and `release.yml:57-60` run two commands in one `run: |` block with no `shell:`. On `windows-2025` the default is pwsh, which only propagates the exit code of the last command; a `ruff check` failure followed by a clean `ruff format --check` passes. Every other multi-line step in `ci.yml` sets `shell: bash` for this reason (line 72, 81, 93). Masked today because the macOS/Linux jobs use bash `-e`.
- Fix: add `shell: bash` to both steps. Optional: `tests/test_ci_workflows.py` asserts every `run: |` block with more than one command declares `shell: bash`.
- Test: push a branch with an unused import; before the fix the Windows step is green while Linux is red.

### 7. `finish_reason` is never read — low / medium
- `providers/protocols.py:275-298`: only `delta.content`, `reasoning`, `tool_calls`, `annotations` are read. `finish_reason: "length"` (hit `max_output_tokens`) or `"content_filter"` ends as "Response complete"; a screen-reader user hears a sentence stop mid-word with no explanation. A per-choice `error` (OpenRouter puts mid-stream errors in `choices[0].error` alongside `finish_reason: "error"`) becomes "completed without returning any text" (line 344).
- Fix: after the loop, if `finish_reason == "length"` yield a `status` event "Answer cut off at the output token limit"; if `first.get("error")` raise `ProviderError` with its message.
- Test: `_Recorder` in `test_openrouter_features.py` with a `finish_reason: "length"` chunk; assert a status event follows.

### 8. Windows Credential Manager blob limit is unreported — low / medium
- `storage/credentials.py:180-203`: the key is stored UTF-16 (2 bytes/char) and `CredWriteW` rejects blobs over 2560 bytes (`CRED_MAX_CREDENTIAL_BLOB_SIZE`). A key over 1280 characters (JWT-style tokens from some OpenAI-compatible gateways) fails with "write failed with error 87" and no hint.
- Fix: check `len(blob) > 2560` first and raise `CredentialStoreError("API key is too long for Windows Credential Manager (limit 1280 characters)")`; or store UTF-8.
- Test: `set_api_key(1, "x" * 1300)` on Windows raises the descriptive message.

## Bloat and dead code

| Location | Why safe | Lines |
|---|---|---|
| `accessible_ai/logging_setup.py:12-33` `configure_logging` | No caller (grep whole repo); docstring says so; `chat_integration._configure_chat_logging` does the job | 22 |
| `accessible_ai/storage/paths.py:10-16, 23-37, 52-81` `INSTALLED_MARKER`, `PORTABLE_CONFIG_DIR`, `executable_dir`, `is_portable`, `portable_config_dir` | No caller anywhere; `app_data_dir()` ignores portable mode (see Question 2). Only `bundle_dir` (used by `blindpilot_app.py:60`), `system_config_dir`, `app_data_dir`, `database_path` are live | 45 |
| `accessible_ai/storage/database.py:310-315, 412-423` `touch_conversation`, `get_setting`, `set_setting` and the `app_settings` table (`:84-87`) | No caller | 22 |
| `accessible_ai/providers/protocols.py:491-493` `unsupported_mode` | No caller | 3 |
| `accessible_ai/models.py:173` `SERVER_TOOL_LABELS` | No caller | 1 |
| `accessible_ai/__init__.py:3-6` `__version__` | Unused; the comment claims a VERSION file and a test keep it equal — neither exists | 4 |
| `accessible_ai/providers/config.py:20-101` | Nine dicts differ only in `base_url` and `api_mode`; the four endpoint paths repeat nine times and again in `accounts.py:97-100` and `models.py:50-53`. Collapse to `{provider: (base_url, api_mode)}` plus one shared endpoint dict | 55 |
| `providers/openai_compatible.py:31-37` vs `providers/chat_completions.py:26-32` | Identical `generate`; make `OpenAICompatibleProvider(ChatCompletionsProvider)` overriding only `headers`. Also `openai_compatible.py:18-22`: the `if self.account.id is not None else ""` branch is dead — `BaseProvider.__init__` rejects `id None` | 10 |
| `providers/chat_completions.py:51-57`, `providers/opencode_go.py:52-58` | Rebuild `GenerationSettings` field by field, silently dropping `reasoning`/`tools`/`plugins` and any future field; `dataclasses.replace(settings, model=...)` | 12 |
| `providers/protocols.py:434` `"2023-06-01"` literal | `ClaudeProvider.headers` already sets `anthropic-version` from `ANTHROPIC_VERSION`; the `setdefault` duplicates the constant | 1 |
| `storage/credentials.py:101-115, 159-173` | `CREDENTIALW` struct and ctypes prototypes defined twice, rebuilt on every call. Hoist to module level under `if os.name == "nt"` | 25 |
| `ui/chat_panel.py:451` `return f"{size} bytes"` | Unreachable: the loop always returns at `"GB"` | 1 |
| `chat_integration.py:91-93` `panel.imported_database` | Set, never read (grep) | 3 |
| `tests/test_openrouter_features.py:26` `sys.path.insert` + five `# noqa: E402`, docstring lines 10-12 | `pytest.ini` sets `pythonpath = .`; no other test does this | 8 |
| `tests/test_chat_mode.py:121-173` `test_tab_switcher_mirrors_the_session_pages` | Not about chat mode; belongs beside `test_tab_strip_focus.py`/`test_tabs.py` | move |
| `tests/test_chat_mode.py:49-50, 55, 97-101` | Eight `assert not hasattr(...)` for attributes removed in the past — tests that dead code stays dead; brittle and prove nothing about behaviour | 8 |
| `.github/workflows/release.yml:62-63` "Compile source" | `pytest` has just imported all of them and `test_sources_are_warning_clean` compiles every file; the module list is also stale (omits `certificates`, `backend_pool`, `diagnostics`, `update_dialog`, `chat_integration`, `hermes_*`) | 2 |
| `BlindPilot.spec:34` `hiddenimports = ["pexpect"]` | pexpect is not installed on Windows (`requirements.txt:25`); PyInstaller warns every Windows build. Move under the non-win32 branch | 0 |
| `docs/visual-audit/sandbox` (162 MB, 566 `.py`) | A uv cache checked into the source tree; feeds Bug 2 | tree |

Duplicated helpers across `tests/`: none found among the accessible_ai tests. `tests/pool_contract.py` and `tests/transport_contract.py` are shared clause modules imported by their `test_*` twins, not duplicates.

Dependencies: every entry in `requirements.txt` has an importer (`httpx` and `keyring` only via `accessible_ai`); `hypothesis` is used by `tests/test_screen_text_properties.py`. Nothing to remove.

Weak tests worth knowing about (keep, cheap): `tests/test_ci_workflows.py:77-94` passes if the word `ubuntu` appears anywhere in a testing workflow, including a comment; `:105-112` passes if any one job in the file has `timeout-minutes`. Neither would catch Bug 6.

## Stale comments and text

- `BlindPilot.spec:8-10` says the workflow builds with `--additional-hooks-dir hooks`; `release.yml:79,111` do not pass it, and `BlindPilot.spec:68-70` in the same file says PyInstaller refuses that flag with a spec.
- `accessible_ai/__init__.py:3-5`: "The VERSION file is kept equal to this by a test" — no `VERSION` file, no such test, and `APP_VERSION` in `blindpilot_app.py:285` is the version that ships.
- `accessible_ai/storage/paths.py:26`: "the executable lives at `AccessibleAI.app/Contents/MacOS`" — the bundle is `BlindPilot.app` (`BlindPilot.spec:105`).
- `accessible_ai/storage/paths.py:95` "BlindPilot's existing per-user settings folder": true in a normal session; with `APPDATA` unset it falls back to `~/.blindpilot` while `agent_backends.blindpilot_config_dir` (`:1109`) falls back to `~/AppData/Roaming/BlindPilot`, so chat data and settings would part ways.
- `accessible_ai/ui/accounts.py:226-227, 423`: "API keys are stored in Windows Credential Manager" is shown on macOS and Linux, where `keyring` is used.
- `accessible_ai/ui/accounts.py:60-63` says only Claude lacks file attachments; `chat_panel.py:896` refuses attachments for every non-OpenRouter account although `generate_chat_completions` (`protocols.py:235-298`) builds multimodal parts for any Chat Completions provider.
- `accessible_ai/logging_setup.py:13-20` is a history lesson about `basicConfig(force=True)` on a function nothing calls.
- `chat_integration.py:91`: "Keep the service objects discoverable for diagnostics and tests" — only `imported_database` is set and nothing reads it.
- `.github/workflows/release.yml:66-68`: "the build does not depend on a committed binary" — `packaging/BlindPilot.ico`, `.icns`, `-1024.png` are committed; the step regenerates them anyway.
- `tests/test_ci_workflows.py:3-6` describes the state before `ci.yml` existed as if it were current.
- `accessible_ai/storage/database.py:276-280`: "A row read back through an older connection may not carry the column at all" — `_add_missing_columns` runs before any row is read, so the `IndexError/KeyError` branch cannot trigger.
- `ruff.toml:1` `target-version = "py310"` vs `mypy.ini:2` `python_version = 3.12` vs CI on 3.12 and development on 3.13; pick one (3.12) so ruff can flag 3.10-only idioms.
- `mypy.ini:8-12` lists every root module except `blind_pilot.py`, the entry script.

## Questions for the maintainer

1. Conversations are write-only. `Database` has `create_conversation` but no list/open/delete; the UI has only "New conversation". Rows accumulate in `conversations`/`messages`/`message_attachments` forever (with blobs) and can never be reopened. Is a history browser planned, or should the schema shrink to a per-session log with a retention rule?
2. Portable mode for chat data: `paths.is_portable`/`portable_config_dir` exist but `app_data_dir()` never consults them, so an unpacked zip still writes `chat.sqlite3` and the chat log to `%APPDATA%`. Wire them (and `app_updater.INSTALL_PORTABLE` agrees a portable kind exists) or delete them?
3. A cancelled or failed turn discards the partial answer (`chat_panel.py:1181-1186`) but leaves it on screen until the next redraw, when it vanishes. Save it with a "[stopped]" marker, or drop it immediately so what is shown matches what is stored?
4. Should attachments be allowed on non-OpenRouter Chat Completions accounts (Gemini, DeepSeek, custom, OpenAI in chat mode)? The providers already support it; only `chat_panel.py:896` forbids it.
5. `custom_headers_json` is stored in plain text in `chat.sqlite3` (`database.py:35`). A custom server authenticated by a non-Bearer header puts its secret there. Acceptable, or should custom headers go through `CredentialStore`?
6. DPI awareness: no manifest or `SetProcessDpiAwareness` anywhere (grep of `.py`/`.spec`/`.iss`), so the frozen `BlindPilot.exe` relies on the PyInstaller bootloader default. Worth one check of the packaged build on a 150% display; if blurry, add `<dpiAware>` via the spec's `manifest=` or a `ctypes.windll.shcore.SetProcessDpiAwareness(2)` before `wx.App`.
