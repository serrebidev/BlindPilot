# blindpilot_app.py lines 1-5134: bug and bloat audit

Scope: module setup, config, CLI discovery and install, model probe, slash
commands, helpers, `ClaudeWorker`, and the dialogs `SettingsFilesDialog`,
`ReadView`, `ModelDialog`, `QuestionDialog`, `ConnectDialog`,
`NewSessionDialog`, `HistoryDialog`, `HermesSessionsDialog`. Read-only; no
files changed. `ruff check blindpilot_app.py` is clean.

## Bugs

Ranked by severity, then confidence.

### 1. A free-text question cannot be answered (high, confidence high)

`blindpilot_app.py:4047-4112` (`QuestionDialog.__init__`),
`:4128-4131` (`_wants_custom`), `:4133-4151` (`_on_choice`).

A `Question` with no options and `allow_custom=True` gets a `wx.RadioBox`
whose only entry is "Other". A RadioBox always starts with item 0 selected,
so `_wants_custom(0)` is already True when the dialog opens, but the text box
is only shown from `_on_choice`, which fires on `EVT_RADIOBOX`; with one entry
the selection can never change, so it never fires. `_answered()` then
announces "type your own answer" and calls `SetFocus()` on a hidden control.
OK and Enter never close the dialog; only Cancel does.

Verified with wx: `QuestionDialog(frame, "hermes", [Question("API key?",
secret=True)])` gives `wants_custom=True`, `text shown=False`,
`_answered()=False`.

Triggers: every Hermes `secret.request` / `sudo.request`
(`hermes_worker.py:1585` builds `Question(question=asked, secret=True)`),
any Hermes clarify question without `choices` (`hermes_worker.py:276`), and
any Codex question with an empty option list (`agent_backends.py:3377`).
The sudo password prompt is therefore unanswerable, so the turn cannot
proceed.

Fix: factor the show/hide loop out of `_on_choice` into `_sync_custom_boxes()`
and call it at the end of `__init__`; when the first question has no options,
focus its text box instead of the picker.

Test: `tests/test_question_dialog.py` has no zero-option case. Add one that
constructs the dialog as above and asserts `dialog._texts[0].IsShown()` and
that `_answered()` is True after `SetValue("x")`.

### 2. Enter on Cancel opens a Hermes conversation (medium, confidence high)

`blindpilot_app.py:5121-5123` (`HermesSessionsDialog._on_key`).

`EVT_CHAR_HOOK` sees Enter before the focused button does. When Cancel (or
Open) has focus and the list is non-empty, `_accept()` runs and the selected
conversation opens, possibly attaching to a live turn. `HistoryDialog._on_key`
(`:4874-4885`) and `SettingsFilesDialog._on_key` (`:3852-3860`) fixed exactly
this and say so in comments; the newer dialog repeats the bug.

Fix: before `self._accept()`, add
`if isinstance(self.FindFocus(), wx.Button): event.Skip(); return`.

Test: copy `tests/test_history_dialog_keys.py::test_enter_on_a_button_does_not_open_a_conversation`
for `HermesSessionsDialog._on_key`. `tests/test_hermes_sessions_ui.py` covers
labels, filtering and `_accept()` only, not key handling.

### 3. zsh PATH check and PATH write use different startup files (medium, confidence medium-high)

`blindpilot_app.py:351-376` (`_login_shell_which`), `:588-621`
(`_posix_persistent_path_dirs`), `:703-722` (`_shell_profile_file`).

Both probes run `zsh -l -c ...`: a login, non-interactive shell, which reads
`.zshenv`, `.zprofile` and `.zlogin` but not `.zshrc`. `_shell_profile_file`
writes the export line to `.zshrc` for zsh. So on macOS:

- After "Add to PATH" succeeds, `_is_on_persistent_path` still answers False
  on the next launch, and the wizard (`:7809-7827`) keeps saying "`folder` is
  not on your PATH ... will not work" although a real Terminal finds it.
  `ensure_on_posix_path` only avoids a duplicate stanza because of the
  `line in existing` check at `:764`.
- A `claude` installed through nvm (whose init lines live in `.zshrc`) is not
  found by `_find_claude` from a Dock launch, contradicting the
  `_login_shell_which` docstring ("how we tell whether their shell startup
  files would find it").

Fix options (see Questions): write to `.zprofile` for zsh, which Terminal and
iTerm read (they open login shells) and which `zsh -l -c` also reads; or keep
`.zshrc` and run the probes with `-i -l -c` (interactive, needs a tty and can
trip on prompts). The first is smaller and consistent.

Test: `tests/test_cli_install.py:308` asserts `.zshrc` for Darwin/zsh, so it
has to change with the fix. Add a test asserting the file written by
`_shell_profile_file` is one the `_posix_persistent_path_dirs` command would
source.

### 4. A non-string `narration` value crashes at import (medium, confidence high)

`blindpilot_app.py:2562-2567`, executed at import by `SETTINGS = _Settings()`
(`:2604`).

`narration in {mode for ...}` raises `TypeError: unhashable type` when
config.json holds `"narration": []` or `{}`. Every neighbouring field is
guarded (`_valid_progress_cue`, `sound_cues` isinstance check), and the
`_valid_progress_cue` docstring says a hand-edited config "must not stop the
app from starting". Trigger: hand-edit or a corrupt write of config.json.

Fix: `isinstance(narration, str) and narration in {...}`.
Test: extend `tests/test_narration_modes.py:65` with `{"narration": []}`.

### 5. `cached_model_options` can block the GUI thread (low, confidence medium)

`blindpilot_app.py:1718-1731`; called on the GUI thread at `:5560`
(`open_model_dialog`). Docstring says "Never blocks", but it calls
`_find_claude()`, which on POSIX falls through to `_login_shell_which`
(a login-shell subprocess, 8 s timeout) whenever `claude` is not on PATH or in
the fallback list, and `find_backend_cli` has the same login-shell tail
(`agent_backends.py:~615-625`). Trigger: `/model` on macOS with the backend
missing. Fix: pass the binary in, or resolve it inside the worker thread and
skip the cache lookup when no binary is known. Test: monkeypatch
`_login_shell_which` to record calls and assert it is not called from
`open_model_dialog`.

### 6. `_repair_claude_native_update` can overwrite an npm shim (low, confidence medium)

`blindpilot_app.py:1425-1453`. `binary` is whatever `_find_claude()` found,
which can be `%APPDATA%\npm\claude.cmd` when an npm install shadows a native
one. If `~/.local/share/claude/versions` also exists, `shutil.copy2(newest,
binary)` writes a PE image over the `.cmd` shim, breaking it. Fix: only repair
when `Path(binary).resolve().parent == _native_bin_dir()` (or suffix is
`.exe`). Test: fake a `.cmd` binary plus a versions folder and assert no copy.

### 7. `ConnectDialog._choose_method` IndexError on an empty method list (low, confidence medium)

`blindpilot_app.py:4402-4417`. `opencode_auth_methods` returns the filtered
list `[m for m in methods if isinstance(m, dict)]`, which is `[]` when the
server's entries are not dicts. Then `len(methods) != 1`, a
`SingleChoiceDialog` with no choices opens, and `methods[index]` raises on the
GUI thread. Fix: treat an empty list like the fallback (`[{"type": "api",
"label": "Manually enter API key"}]`) either in `opencode_auth_methods` or
before `methods[index]`.

### 8. Remote-Hermes key is world-readable for an instant (low, confidence high)

`blindpilot_app.py:2650-2663`. `write_text` creates the file with the umask
default, then `os.chmod(0o600)`. Fix: `os.open(path, O_WRONLY|O_CREAT|O_TRUNC,
0o600)` and write through the fd (Windows unchanged).

### 9. Minor

- `:443-457` `_open_path` on POSIX drops the `Popen` without waiting: a zombie
  until GC, and `Popen.__del__` raises a `ResourceWarning` that the repo's
  `-W error` CI turns into a failure if a test ever reaches it (the Earcons
  code at `:2736-2740` documents exactly this hazard). Fix: reap it on a
  daemon thread the way `Earcons._spawn` does; do not `wait()` inline.
- `:5124-5127` F5 in `HermesSessionsDialog` announces "Refreshed" even when
  `_reload` just announced an error. Guard on the error path.
- `:3960` `ModelDialog`: `SetStringSelection(selected_effort)` does nothing
  when the saved effort is not in the current list, and `selection()` then
  returns `""`, silently clearing the tab's override on OK. Announce it or add
  the missing value to the choices.
- `:1481`, `:1395`, `:1527` `_run_logged_process` returns `None` when Popen
  fails; the callers print "exited with code None". Say "could not be
  started".
- `:1402-1417` `_executable_version` has no `stdin=DEVNULL`; a CLI that reads
  stdin hangs for the full 30 s timeout.

## Bloat and dead code

Verified with ripgrep across the repo including `tests/`.

- `:1570-1587` `_check_auth_quick`: only the definition exists (1 hit
  repo-wide). It would also spend a real `-p x` turn if ever used. Delete:
  18 lines. `AUTH_ERROR_MARKERS` and `AUTH_HINT` stay; they are used by
  `_looks_like_auth_error` and `ClaudeWorker`.
- `:726` `LEGACY_PATH_STANZA_MARKER`: never read. Delete: 1 line.
- `:997-1017` `install_claude` re-implements `_run_logged_process` (Popen,
  line loop, `wait`) in place. Replace with `rc = _run_logged_process(argv,
  log)`; `_run_logged_process` already logs "could not be started".
  `tests/test_cli_install.py` patches `subprocess.Popen` on the module, which
  `_run_logged_process` also uses, so tests keep working. Saves about 15
  lines.
- `:838-864` `_install_argv` and `:879-905` `_hermes_install_argv`, plus
  `:867-876` and `:908-917` the two prerequisite messages, differ only in URL
  and product name. One `_script_installer_argv(url)` and one
  `_missing_prereq_message(product)` with two thin wrappers (tests call
  `_install_argv` and `_hermes_install_argv` by name) saves about 35 lines.
- `:1273-1302` `_npm_install_argv` and `_npm_update_argv` differ by
  `@latest`. One function with a `latest: bool` flag saves about 12 lines.
- `:781` and `:797`: `ensure_on_path` calls `_add_to_process_path` and then
  `ensure_on_windows_path` calls it again. Drop one call: 1 line.
- `:2094-2109` `_default_permission_mode(cwd, backend)` ignores both
  arguments; the docstring admits it. Tests call it with both, so leave it,
  but the docstring paragraph (4 lines) could go.
- History-narrative docstrings that restate what the code now does and what
  it used to do: `_save_config` (`:2432-2445`, 13 lines), `_wait_for_shutdown`
  (`:3160-3173`, 14 lines), `_log_unfinished_turn` (`:3281-3291`), the Hermes
  update comment (`:1470-1474`), `announce` retry comments (`:209-243`). Each
  could keep the first sentence. About 40 lines total, optional.

Not dead, checked: `_LANG_EXT`, `_automatic_npm_install_available`,
`activate_managed_cli_paths`, `_WORKER_EVENT_*`, `ORIGINAL_APP_CREDIT`,
`PROBE_TTL_SECONDS`, `cached_model_options`, `_CYCLE_VALUES`, `Turn`,
`_flatten`, `_one_line`, `_result_label`, `_copy_to_clipboard`,
`_legacy_config_path`, `adopt_full_auto_default`, `ClaudeWorker` (used via
`worker_class(..., ClaudeWorker)` and in tests).

## Stale comments

- `:1-5` module docstring: "pluggable Claude Code, Codex, and FreeBuff
  backends" omits opencode and Hermes.
- `:1895-1896` "The quick-cycle chord steps through the everyday subset..."
  sits above `_LANG_EXT`; it describes `_CYCLE_VALUES` at `:2087`. Move it.
- `:2708-2716` `Earcons` docstring: "Three cues" (there are four; the error
  cue uses the system sound) and only names `winsound` and `afplay`, while
  `_unix_player` also uses paplay/aplay/ffplay on Linux.
- `:1613` `ModelOptions` "What `claude` reports" and `:3988`
  `ModelDialog.selection` "as Claude Code has it": both are per-backend now.
- `:921-926` `_hermes_binary_after_install` docstring says the Windows
  launcher lands in `LOCALAPPDATA\Programs\hermes`; the body (`:933-939`) and
  `hermes_backend.find_hermes_cli` say `LOCALAPPDATA\hermes\bin` was measured.
- `:1721` `cached_model_options` "Never blocks": see bug 5.
- `:2441-2444` `_save_config` docstring: "`_load_config` ... starts again from
  empty". `_load_config` (`:2420-2428`) falls through to the legacy
  `claude-reader/config.json` when the current file is unreadable, so a
  corrupt config revives years-old settings rather than empty ones.
- `:352-358` `_login_shell_which` docstring: "covers zsh, bash and fish
  alike" and "whether their shell startup files would find it" is not true for
  `.zshrc` (bug 3).

## Questions for the maintainer

1. Bug 3: should the zsh PATH stanza move to `.zprofile` (matches what the
   probes read; Terminal and iTerm source it), or should the probes become
   interactive shells to keep `.zshrc`? Existing `.zshrc` stanzas written by
   older builds would need the check to also accept them.
2. Bug 1: for a secret/sudo question should the dialog skip the RadioBox
   entirely and show only the masked text box, or keep the one-entry "Other"
   radio for consistency with option questions?
3. `_load_config` reading the legacy `claude-reader` config whenever the
   current file is missing or corrupt: still wanted, or should the legacy
   read happen once at first run and the fallback on corruption be removed?
