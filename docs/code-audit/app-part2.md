# Audit: blindpilot_app.py lines 5135-8735

Scope: SessionPanel, BackendLogin (falls inside the line range), SetupWizard,
RemoteHermesDialog, PreferencesDialog. Read-only. Referenced modules checked:
agent_backends.py, backend_pool.py, hermes_worker.py, hermes_backend.py,
markdown_rows.py. Coverage checked in tests/. `ruff check blindpilot_app.py`
is clean.

## Bugs

Ranked by severity, then confidence.

### B1. Text view stays empty when it becomes visible with exactly one row (medium, high)

- blindpilot_app.py:6855-6869 (`_refresh_list`), reached from `apply_view_mode` 5329.
- `_refresh_list` decides between append and rebuild with
  `trustworthy = shown == len(previous)`, where `shown` is
  `responses_text.GetNumberOfLines()`. On Windows an empty `TE_MULTILINE`
  control reports 1 line (verified on this machine: empty -> 1, "one" -> 1,
  "a\nb" -> 2). With one displayed row and a never-filled text control,
  `shown` (1) equals `len(previous)` (1), the first-N compare passes, `added`
  is empty, and the method returns without writing anything. The text view is
  shown blank while the list holds a row.
- Trigger: one row in the list (a "You:" row mid-turn, or a search matching one
  row), then Options -> "Responses as a read-only text field".
- Fix: in the text-view branch treat an empty control as zero lines:
  `shown = n if self.responses_text.GetLastPosition() else 0`. Or compare
  `GetValue()` against the joined labels instead of counting lines.
- Test: tests/test_reading_while_streaming.py `_TextView` stub already
  mirrors the 1-line-when-empty behaviour; add a case with `_rows("one")` and
  `_TextView("")`, assert `control.text == "one"`. It fails today.

### B2. RemoteHermesDialog: test thread reports into a destroyed dialog (medium, high)

- blindpilot_app.py:8521 `_test_done`, scheduled from `_run_test` 8492-8519 via
  `wx.CallAfter`. No `if not self` guard.
- `_run_test` can run for up to `REMOTE_CONNECT_TIMEOUT` (connect) plus 30 s
  (waiting for `gateway.ready`). The dialog is `ShowModal`ed then `Destroy`ed
  by `_configure_remote_hermes` (10402-10409). Pressing Cancel or Escape while
  the test runs leaves the CallAfter to call `self._test_btn.Enable()` on a
  deleted C++ object: `RuntimeError: wrapped C/C++ object of type Button has
  been deleted`, printed as an unhandled wx callback error.
- Trigger: Test connection against an unreachable address, press Escape.
- Fix: first line of `_test_done`: `if not self: return`. Same guard the
  panel's `_show_status` / `_remember_cli_model` already have.
- Test: construct the dialog with a stub REMOTE_HERMES, `Destroy()`, call
  `_test_done("")`; must not raise.

### B3. Reopening a finished Hermes conversation leaves `_stream_response` set (medium, high)

- blindpilot_app.py:6770-6772 (`_on_response_complete` early return), fed by
  hermes_worker.py:912-923 (`_run_replay` emits activity rows then
  `_on_complete("")`).
- The replay rows go through `_on_activity` -> `_begin_stream_response`, so
  `_stream_response` is the replay's response number. The early return
  neither clears it nor does `_on_worker_finished`. The next message the user
  sends then: `_add_your_message` (6260) numbers the "You:" row with the old
  number, `_begin_stream_response` opens no new header, and the answer's rows
  and header payload land under the replayed "Response 1". Ctrl+R cannot
  reach the new answer as its own response, and Shift+C / "Copy whole
  response" copies the replay plus the answer.
- Trigger: Hermes backend, Ctrl+G, open a finished conversation, send one
  message.
- Fix: set `self._stream_response = None` before the early return at 6771.
- Test: stub panel with `_turns=[]`, `_stream_response=1`, `_rows` non-empty;
  call `_on_response_complete("")`; assert `_stream_response is None`.
  tests/test_hermes_sessions.py covers the worker side only.

### B4. Same reopen shows nothing when "Show live activity" is off (medium, high)

- blindpilot_app.py:6650 (`_on_activity` returns when `not SETTINGS.live_rows`)
  plus 6770 (empty complete returns without parsing).
- Replay rows are the only way a reopened Hermes transcript reaches the list,
  and they are dropped in silent-until-response mode. The completion carries
  no text, so nothing is ever displayed; status says "Reopened, 0 rows".
- Trigger: uncheck Options -> Show live activity, then Ctrl+G, open any
  finished conversation.
- Fix (product decision, see Q1): in `_on_activity`, let `kind == "you"` and
  a replay bypass the `live_rows` gate, or have `open_hermes_session` force
  the rows on for the resume-only worker.

### B5. Wizard blocks its own event loop on the sign-in check (medium, medium)

- blindpilot_app.py:8132 `_check_signin` calls `backend_auth_ok(self.backend)`
  on the GUI thread. agent_backends.py:627-660 runs `subprocess.run` with a
  12 s timeout (Claude, Codex, FreeBuff) or `hermes_auth_ok(timeout=25)`.
- `_check_signin` is queued whenever step 2 is shown (7757) and after the
  opencode Connect dialog closes (8152). For the duration the wizard does not
  repaint or answer NVDA; a slow or hung CLI stalls it for up to 12-25 s with
  no announcement. `open_status_dialog` (5620) already does the same probe on
  a thread for exactly this reason.
- Fix: run `backend_auth_ok` on a daemon thread and `wx.CallAfter` a
  `_show_signin_status(label)` that keeps the existing `if not self` guard.
- Test: tests/test_wizard_outlives_its_callbacks.py:77 calls `_check_signin`
  on a dead stub; extend it to assert the auth probe is not called
  synchronously (monkeypatch `backend_auth_ok` to record the thread).

### B6. Stop that fails to kill leaves the tab with neither Stop nor narration (low, medium)

- blindpilot_app.py:6305-6311 `_on_stop` disables Stop and Steer and sets
  `_stopping = True` before `worker.cancel()` has run, and nothing re-enables
  them if the worker survives. `_say` (6738) then mutes all narration while
  `_stopping` is set, and Send stays disabled because `_worker` is not None.
- Trigger: any backend whose cancel does not end the turn (opencode abort
  POST failing, a Hermes interrupt the gateway ignores). The turn runs on in
  silence until it finishes by itself.
- Fix: after the cancel thread returns, `wx.CallAfter` a check: if
  `worker.is_alive()` still, clear `_stopping`, re-enable Stop, announce
  "Could not stop the task; it is still running".

### B7. Failed turn's "You:" row is grouped into the next response (low, high)

- blindpilot_app.py:6816-6819 `_on_failed` pops the turn but leaves the
  "You:" row, numbered `_response_count + 1` by `_add_your_message` (6260),
  in `_rows`. When the failure happened before any streamed output the
  counter never advanced, so the next turn reuses the same number: the
  failed prompt, the new prompt and the new header all share it, and
  `reassemble` / "Copy whole response" returns both prompts.
- Fix: in `_on_failed`, when `_stream_response is None`, either drop the
  trailing "you" row with that number or advance `_response_count` so the
  orphan keeps a number of its own.
- Test: stub panel, `_add_your_message("a")`, `_on_failed("x")`,
  `_add_your_message("b")`; assert the two rows have different numbers.

### B8. Programmatic prompt fills are read back as if dictated (low, medium)

- blindpilot_app.py:5743 (`_pick_slash_command`) and 7078 (`_action_insert`)
  use `prompt.SetValue`, which fires `EVT_TEXT`. `_on_prompt_text_changed`
  (5821) treats any growth over one character as dictation and schedules
  `_read_prompt_text` 1.5 s later.
- Result: "Slash command: /model. Edit if needed..." is followed 1.5 s later by
  "/model" again. "Insert into prompt" on a tool-result row announces
  "Inserted into prompt" and then reads the whole payload (hundreds of lines).
- Fix: use `ChangeValue` (no EVT_TEXT) in both places and update
  `self._prompt_text` to match. tests/test_prompt_readback.py covers typing
  and dictation only.

### B9. Wizard advances a step on a dialog that may be gone (low, medium)

- blindpilot_app.py:8339 `_on_login_done` queues `wx.CallAfter(self._go, +1)`;
  `_go` -> `_show_step` touches `self._book` with no `if not self` guard,
  unlike every other deferred wizard callback.
- Trigger: Escape between `_on_login_done` and the next event-loop turn. Rare.
- Fix: guard `_go` or call `self._go(+1)` directly (it is already on the GUI
  thread; the CallAfter buys nothing).

## Bloat and dead code

- 7497-7508, 7569-7578, 7627-7638: `_make_welcome`, `_make_signin`,
  `_make_done` set long literal labels that `_refresh_backend_copy` (called
  unconditionally at 7472) overwrites before the dialog is shown. The welcome
  text still says "choose Codex or FreeBuff later". Replace each with
  `label=""`. About 28 lines.
- 7448, 8173-8174: `_login_thread` is assigned and never read anywhere in the
  repo (grep including tests). Start the thread inline. 3 lines.
- 7430 `_STEPS[1] = "Coding Agent CLI"` is never displayed; `_show_step`
  substitutes `f"{backend} CLI"`. Cosmetic.
- 7002-7010 `_copy_response(sel)` and 7083-7089 `_action_copy_response(row)`
  are the same body; the first can be `self._action_copy_response(
  self._displayed[sel])`. 6 lines. Both are exercised by
  tests/test_errors_are_spoken.py, so keep the names.
- 7141-7167 `cancel_worker` docstring (27 lines) and 7176-7185 comment are a
  changelog. Two sentences carry the rule ("cancel waits on the process, so
  wait=False hands it to a thread; stop the shared progress loop here because
  the mailbox drops `done` once the panel is gone"). About 25 lines.
- 6173-6199: 26 comment lines to justify one `if`. Keep the defect note
  (named session must not be renamed by its first message) and drop the
  Hermes title_source and getattr paragraphs; the getattr itself is needed
  only by stub panels in tests. About 18 lines.
- 6046-6065 `_run_in_progress` docstring: 14 lines to say "`_worker` is
  cleared on the GUI thread when `done` drains, `is_alive()` is not". 8 lines.
- 6837-6850 `_refresh_list` opening comment restates the branch below. 6 lines.
- 5965-5975 `_close_question_dialog` and the `_stopping` poll in
  `_ask_questions` (5947-5951): the question dialog is modal on the GUI
  thread, which disables the frame, its accelerators and the Stop button, so
  `_on_stop` cannot run while it is open. The only live caller is
  `cancel_worker` from a frame close. Not dead, but the docstring's
  "stopping a run happens ... while the worker waits" describes a path the UI
  does not offer (see Q2).
- 8166 `_do_login` builds a `BackendLogin` for `login_needs_terminal`
  backends that `_run_login` never uses (8177-8187 takes the terminal branch
  first). Move the construction below that branch. 2 lines, clearer intent.
- 5794-5803 `_save_clipboard_image` writes `blindpilot-paste-*.png` to the
  temp directory and nothing ever removes them. Not a bug, but a slow leak;
  delete after the send completes or on app exit.

## Stale comments

- 5136-5142 SessionPanel docstring: "Up from the prompt enters the newest
  row". `_on_prompt_key` 5878 requires Ctrl/Alt+Up and the comment there
  explains why plain Up was removed.
- 5253-5256 prompt hint (user-facing, spoken by NVDA on focus): "Up to enter
  responses". Same staleness; should say "Ctrl+Up".
- 6636-6648 `_on_activity` docstring lists assistant/thinking/tool/result but
  the body also handles `you` (replay) and `subagent`.
- 6788 "yields Claude's full answer text": every backend goes through here.
- 7701-7705 `_refresh_backend_copy` done-page text hard-codes "Ctrl+R",
  "Ctrl+/", "Ctrl+period", "Ctrl+Shift+M". `_chord` (8540) exists to say Cmd
  on macOS and is not used here.
- 8376-8382 RemoteHermesDialog docstring: "a checkbox, an address, a port, a
  key, and a button". The dialog also has TLS, credential type and username.
- 6252-6256 `_add_your_message` docstring says "Skipped in
  silent-until-response mode"; true, but the skipped row is never added
  later either, so a silent-mode transcript has answers with no prompts.
  Either the comment should say so or `_on_response_complete`'s silent path
  should add it.

## Questions for the maintainer

- Q1 (B4): in silent-until-response mode, should a reopened Hermes
  conversation still display its transcript? The replay is the transcript,
  not live activity, so bypassing the gate seems right, but it changes what
  the option means.
- Q2: is Stop meant to be reachable while a QuestionDialog is open (a Stop
  button inside the dialog, or `wx.Dialog.ShowWindowModal`)? If yes,
  `_close_question_dialog` needs a caller; if no, its docstring and the
  `_stopping` poll in `_ask_questions` should say they exist for tab close
  and quit only.
- Q3: `_drain_worker_events` announces "Receiving response" (6024) with
  `_announce`, which speaks regardless of which tab is visible, while all
  other streaming narration goes through `_say` and is muted for background
  tabs. Intentional cue, or should it use `_say`?
