# Audit: blindpilot_app.py 8736-end (MainFrame, main) and small modules

Read-only audit, 2026-09-04. Scope: `MainFrame`, `main()`, `update_dialog.py`,
`chat_integration.py`, `markdown_rows.py`, `session_history.py`, `claude_reader.py`,
`diagnostics.py`, `certificates.py`, `linux_accessibility.py`, `blind_pilot.py`,
`hooks/hook-winpty.py`, `tools/make_icon.py`. `ruff check` on all of them: clean.
Claims below marked "reproduced" were checked with a throwaway Python snippet.

## Bugs

Ranked by severity, then confidence.

### B1. Chat-mode startup failure leaves the window half in Chat mode (medium, high)

`blindpilot_app.py:9253-9261` (`_set_app_mode`). When `_ensure_chat_panel()` raises,
the handler sets `_app_mode = AGENT`, shows a message box and returns early. Nothing
else is corrected:

- `cfg["app_mode"]` still says `chat`, so the error box appears on every launch.
- `_refresh_compact_item()` ran at `:8931` while `_app_mode` was still `chat`, so
  Compact stays greyed out in Agent mode until the backend is changed.
- `_refresh_hermes_sessions_item()` ran the same way, so with the Hermes backend the
  Hermes Conversations item and Ctrl+G are missing.
- No prompt receives focus.

Trigger: saved `app_mode: chat` plus any exception in `create_chat_panel` (see B4, an
unwritable data dir, a broken `accessible_ai` install).
Fix: on failure set `mode = APP_MODE_AGENT; chat_panel = None` and fall through to the
normal path instead of `return`, so the config write, item refreshes and focus run.
Test: build `MainFrame` with `_load_config` returning `{"app_mode": "chat"}` and
`chat_integration.create_chat_panel` patched to raise (patch `wx.MessageBox`); assert
`saved["app_mode"] == "agent"` and `frame._compact_item.IsEnabled()` for Claude.

### B2. Agent-only commands run against the hidden notebook in Chat mode (medium, high)

Only `_chat_menu_items` are toggled by `_set_app_mode` (`:9271`). Every Model-menu and
most File/Conversation commands still resolve `self.notebook.GetCurrentPage()`, which
returns the hidden `SessionPanel`:

- `_find_active` `:10552` (Ctrl+F): opens the search dialog, then `_focus_row(0)` on a
  hidden list.
- `_slash_active` `:10598`, `_model_active` `:10020`, `_status_active` `:10026`,
  `_set_mode_active` `:10049`, `_connect_active` `:10044`: dialogs for a tab nobody sees.
- `_new_session` `:10270` (Ctrl+T), `_open_history` `:10310`, `_side_chat_active`
  `:9905`, `_resume_history` `:10362`: add tabs to the hidden book and
  `wx.CallAfter(panel.focus_prompt)` puts keyboard focus into an invisible control;
  the "Resumed ..." announcement describes something not on screen.
- `_close_current_session` `:10458` (Ctrl+W), `_cycle_tab` `:10260` (Ctrl+Tab is
  gated at `:9211`, the menu items and Cmd+Shift+] are not).
- `_refresh_connect_item` `:10001` ignores the mode; `_refresh_compact_item` does not.

Fix: either disable the Model menu and the agent-only File/Conversation items in
`_set_app_mode` (the mirror of `_chat_menu_items`), or start each delegator with
`if self._app_mode != APP_MODE_AGENT: return`. Include the mode in
`_refresh_connect_item`.
Test: extend `tests/test_chat_mode.py`: after switching to Chat, patch
`wx.TextEntryDialog` to record construction and call `frame._find_active()`; assert
nothing was built and `frame.notebook.GetPageCount()` is unchanged after
`frame._add_session`-driven commands.

### B3. HTML blocks vanish from the row list (medium, high, reproduced)

`markdown_rows.py:265-296` (`_emit`). `MarkdownIt("commonmark")` has `html` on, so a
line starting with `<details>`, `<div>`, `<table>` etc. becomes an `html_block` token
with nesting 0 that is neither a code type nor a container, and the loop skips it.
Reproduced: `Intro.\n\n<details>\n<summary>More</summary>\nHidden text.\n</details>\n\nAfter.`
yields rows `Intro.`, `After.`; the summary and body are only in the header payload.
Fix: in `_emit`, `if tok.type == "html_block": text = _tidy_prose(re.sub(r"<[^>]+>", "", tok.content))`
and append a prose row when non-empty.
Test: add a `<details>` case to `tests/test_markdown_rows.py`.

### B4. Legacy chat import lets `sqlite3.Error` escape and can leave a partial DB (medium, medium)

`chat_integration.py:67-77`. `sqlite3.connect(source)` and `source_db.backup(target_db)`
raise `sqlite3.Error` (not `OSError`) on a corrupt or locked AccessibleAI database.
The exception propagates to `_set_app_mode` (see B1). If `backup` fails part-way, the
partially written `target` now exists, so the next launch skips the import at `:66`
and opens a truncated database. `with sqlite3.connect(...)` commits but does not close.
Fix: `except (OSError, sqlite3.Error)`; unlink `target` in the handler; close both
connections explicitly.
Test: monkeypatch `_legacy_database_candidates` to a file of random bytes; assert the
function returns `None` and `target` does not exist.

### B5. ICNS 16@2x / 32@2x entries carry the wrong sizes (low, medium-high)

`tools/make_icon.py:36-37`. `ic11` is 16x16@2x (32 px) and `ic12` is 32x32@2x (64 px);
the table writes `ic11: 64` and `ic12: 32`. Finder scales the wrong image at those two
retina sizes. `ic06` (`:30`) is not a documented icns type (low confidence on that).
Fix: swap the two values; drop `ic06`. Test: regenerate on a Mac and run
`iconutil -c iconset packaging/BlindPilot.icns`, or `sips -g pixelWidth` on the entries.

### B6. Hermes store URI built by string concatenation (low, high, reproduced)

`session_history.py:528`: `f"file:{path}?mode=ro&immutable=0"`. A `#`, `?` or `%` in
the path is parsed as URI syntax. Reproduced with `a b#c.db`: SQLite drops the fragment,
loses `mode=ro`, and opens (creates) `a b` read-write. `_hermes_query` then reports the
"no such table" as an empty history. `_opencode_connect` at `:795` already does it
right with `path.as_uri()`. `hermes_backend.py:349` (out of slice) concatenates too.
Fix: `sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5.0)`.
Test: point `HERMES_HOME` at a tmp dir containing `#`, seed a `state.db`, assert
`_hermes_entries(None)` finds the row.

### B7. `_opencode_entries` can raise off a non-numeric timestamp (low, medium)

`session_history.py:878`: `float(updated or 0)`. A text value raises `ValueError`,
which `list_history` (`:984-987`, catches `OSError` only) passes up to
`HistoryDialog._reload` on the GUI thread (`blindpilot_app.py:4828`). The Hermes branch
at `:566-571` already guards the same conversion.
Fix: wrap in `try/except (TypeError, ValueError)` and fall back to `0.0`.
Test: insert a session row with `time_updated = 'abc'` in the test DB.

### B8. `clean_user_text` is quadratic on unclosed tags (low, high, reproduced)

`session_history.py:124,176-178`. `<([A-Za-z][\w.:-]*)>[\s\S]*?</\1>` scans to the end
of the message for every `<word>` with no matching close. 3000 unclosed tags in 20 KB
took 2.0 s; a pasted Java/C# file (generics) or an HTML fragment is the realistic
trigger, run once per user record in `_claude_turns` and once per file in the listing
scan. Fix: only strip elements whose closing tag exists
(`names = set(re.findall(r"</([\w.:-]+)>", text))`) or cap the input length before the
loop. Test: time `clean_user_text("<a>" * 3000 + "x" * 20000)` under 50 ms.

### B9. Update check thread can outlive the app and log a critical line (low, medium)

`update_dialog.py:184-197`. `_check_worker` has no cancel; closing the dialog and
quitting within the 20 s HTTP timeout leaves the daemon thread calling `wx.CallAfter`
after `wx.App` is gone, which asserts. `diagnostics._log_uncaught_in_thread` records it
as `CRITICAL uncaught exception in thread`. Fix: `if wx.GetApp() is not None:` before
each `wx.CallAfter` in the two workers, or catch and drop. Test: hard to automate;
inspect `blindpilot.log` after cancel-then-quit with the network stalled.

Checked and found sound: `_on_close` ordering (chat shutdown, parallel cancel with one
join budget, `stop_all_held_processes` idempotent against its `atexit` twin, reaper is a
daemon); `UpdateDialog` cancel/close paths unlink the archive; `download_update`
SHA-256 and size checks; `fetch_latest_release` wraps every exception so
`_update_checking` cannot stick; `_select_session` focus refusal; CRLF in fences
(markdown-it normalises, payload has `\n` only).

## Bloat and dead code

1. Dead non-silent update flow, `blindpilot_app.py:9459-9478, 9531-9625`. Only
   `check_for_updates_silently` calls `_check_for_updates`; Help goes to
   `_show_update_dialog`. So the `silent=False` branches of `_on_update_checked`, all of
   `_download_release`, `_on_update_downloaded` and `_show_update_error` have no
   production caller. `tests/test_startup.py:178` tests `_on_update_downloaded` in
   isolation and would go with it. About 90 lines.
2. `_insert_hermes_sessions_item` `:9999` re-Binds on every re-insert. Bindings are by
   id and survive `menu.Remove`; verified a repeated `Bind` does not double-fire but
   does add a handler entry each time. 1 line; the test stub counts `bound` and never
   asserts it.
3. `_open_hermes_sessions` `:10331-10335`: the `backend != HERMES` branch is unreachable;
   the item and its Ctrl+G exist only while Hermes is the backend. 6 lines.
4. `_add_session` `:9648-9651`: comment about lazy model catalogs describes code that is
   no longer in the function. 4 lines.
5. `_toggle_text_view` `:10200-10203`, `_apply_preferences` `:9420-9423`, `_set_backend`
   `:9332-9335`, `_on_close` `:10611-10616`: four copies of "for each SessionPanel in the
   notebook". A `_session_panels()` generator saves ~8 lines.
6. `getattr(self, "_narration_items", {})` `:9404,9406,10192`: the attributes always
   exist after `__init__`. 3 tiny edits.
7. `_set_app_mode` `:9283-9286`: `if _STARTUP_CHECK: pass` folds into the elif chain.
8. `session_history.py:523-526`: `_hermes_connect` repeats the caller's `is_file()` and
   re-imports `sqlite3` (imported at `:49`). `describe_age` `:1031-1034`: two branches
   return the same string. 5 lines.
9. `markdown_rows.py:187-192, 240-243`: `_lang_display` and `_lang_token` both compute
   the first info word; the first can call the second. 3 lines.
10. `tools/make_icon.py:75-83`: `render()` has no caller in tools or tests. 9 lines.
11. `chat_integration.py:91-93`: `panel.imported_database` is written and never read
    anywhere. 3 lines including the comment.
12. `diagnostics.py:575`: `return subprocess.Popen(...) is not None` is always True.
13. `claude_reader.py` (23 lines): no production importer. Eight test files import it
    (`test_cli_install`, `test_live_rows`, `test_model_picker`, `test_permission_mode`,
    `test_tabs`, `test_claude_stream_resilience`, ...), plus `mypy.ini:9`,
    `.github/workflows/release.yml:63`, `README.md:216`. Removable only if the tests
    are pointed at `blindpilot_app` (see Questions).
14. `hooks/hook-winpty.py` replaces PyInstaller's suppression list rather than extending
    `dylib._warning_suppressions`. With PyInstaller 6.22.2 the default is only
    `api-ms-win-.*\.dll`, so nothing is lost today; a future default would be.

## Stale comments

- `blindpilot_app.py:9716` `_build_model_menu` docstring: "rather than inline in
  `_build_menubar`" -- no such method exists; menus are built in `MainFrame.__init__`,
  and the Options menu is still inline there (`:8765-8856`). The "hundred and fifty
  lines apart" sentence is changelog, not documentation.
- `blindpilot_app.py:9648-9651`: see bloat 4.
- `blindpilot_app.py:10706-10710` `main()`: the "nothing BlindPilot launches may inherit
  a PATH" paragraph sits above `diagnostics.start_logging()`; the code it describes is
  `keep_bundle_off_child_path()` two statements later, and the "First, so that anything
  below..." sentence belongs to logging. Reorder the two comments.
- `blindpilot_app.py:6899` `open_find` docstring (read while following B2): says "File
  menu / Cmd-Ctrl+F"; it is Conversation > Find in Responses, Ctrl+F.
- `session_history.py:132` `_home` docstring: "the three history stores" -- five backends.
- `markdown_rows.py:31-33`: "agent output is plain CommonMark and we want the fence
  handling to stay predictable" -- tables are routine in Claude and Codex output and
  today read as one prose row of pipes (reproduced: `| a | b | |---|---| | 1 | 2 |`).
  Either the comment or the parser needs to change (Questions).
- `chat_integration.py:91-92`: "Keep the service objects discoverable for diagnostics
  and tests" -- no service object is kept and nothing reads `imported_database`.
- `diagnostics.py:409-411`: "An uncaught exception there currently goes nowhere" -- it
  describes the state before the hooks this module installs.

## Questions for the maintainer

1. Tables: enable markdown-it's `table` rule and emit one prose row per table row
   ("Row: a, b"), or keep the single pipe-laden row? Decides whether the comment at
   `markdown_rows.py:31` or the parser is wrong.
2. `clean_user_text` removes any `<tag>...</tag>` the person typed: "make <b>this</b>
   bold" replays as "make bold". Acceptable for titles; is it acceptable in the replayed
   conversation (`_claude_turns`, `_codex_turns`, `_opencode_turns`, `_hermes_turns_for`)?
3. Claude Code writes an `isCompactSummary` user record ("This session is being
   continued from a previous conversation...") after compaction. It is replayed as a
   prompt and can title a session that was resumed after compaction. Skip it like
   `isMeta`? (No test or code mentions it today.)
4. B2: grey out the agent-only menu items in Chat mode (consistent with
   `_chat_menu_items`, and the disabled state is announced), or make the delegators
   silent no-ops?
5. `claude_reader.py`: keep as a public compatibility alias, or move the eight test
   modules to `blindpilot_app` and delete the shim plus its three config references?
