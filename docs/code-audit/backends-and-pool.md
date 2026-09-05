# Audit: agent_backends.py and backend_pool.py

Scope: `agent_backends.py` (6314 lines), `backend_pool.py` (411 lines), the
spec `docs/superpowers/specs/2026-09-02-held-backend-processes-design.md`, and
the pool's call sites in `blindpilot_app.py`. Read-only. Coverage checked in
tests/. `ruff check` on both files is clean. Checked on this machine: pywinpty
3.0.5 (`PtyProcess.spawn(argv, cwd, env, dimensions)`), the npm Codex layout
matches the `codex.exe` glob, `claude auth status` exits 1 when logged out.

## Bugs

Ranked by severity, then confidence.

### B1. Windows: stopping a held server orphans its whole child tree (high, high)

- `agent_backends.py:62` `own_group_kwargs` returns `{}` on Windows;
  `:64-105` `end_process_group` then only calls `proc.kill()`. Used by
  `CodexServer.stop` (`:2154`) and `OpencodeServer.stop` (`:5161-5172`).
- `TerminateProcess` on `codex.exe` does not touch what it spawned. The spec
  measured 17-29 MCP descendants per app-server; on idle reap, `drop`, or quit
  they all survive with no parent. If the `codex.exe` glob (`:1030-1037`)
  misses and the `.cmd` launcher is used, even the app-server survives.
- Trigger: Windows, Codex with MCP servers configured, wait 15 minutes or
  quit; `node`/`uvx` children remain in Task Manager.
- Fix: on Windows, in `end_process_group`, terminate the tree. Cheapest:
  `subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], **no_window_kwargs())`.
  Better: assign a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` at spawn.
  `_descendant_pids` (`:163`) already walks the process table and could drive
  `OpenProcess`/`TerminateProcess` per pid.
- Test: spawn `python -c` that spawns a sleeping grandchild and prints its pid;
  call `end_process_group`; assert `tasklist /FI "PID eq N"` finds nothing.
  Windows-only, mark `skipif`.

### B2. Prewarmed FreeBuff terminal's TTL never fires (high, high)

- `agent_backends.py:3965-3967` `_FREEBUFF_PREWARM_TTL`, set at `:4059`,
  read only at `:4080` inside `_take_freebuff_prewarm`.
- Nothing runs on the clock. A terminal prewarmed by picking FreeBuff
  (`blindpilot_app.py:5494`) or at turn end (`:4693`) whose message never
  comes lives until quit: `freebuff.exe`, its node launcher, the pump thread,
  and a `hide_terminal` thread (`:3853-3877`) calling `EnumWindows` four
  times a second. The comment "short enough that an abandoned terminal does
  not sit there all day" is false.
- Fix: a `threading.Timer(_FREEBUFF_PREWARM_TTL, ...)` in `launch()` that
  re-takes the lock, checks `_freebuff_prewarm is holding`, and discards. Or
  express prewarm as a pool key (spec plan 5) so the reaper does it.
- Test: monkeypatch `_FREEBUFF_PREWARM_TTL` to 0.05 with a fake pty, call
  `prewarm_freebuff`, poll with `_wait_for` until `_freebuff_prewarm is None`
  and the fake was terminated.

### B3. Windows: the console-hiding thread leaks when the PTY spawn raises (medium, high)

- `agent_backends.py:3877` starts `hide_terminal` before `:3880`
  `PtyProcess.spawn`. If spawn raises, `pump` never starts, `stream_ended` is
  never set, and the thread polls `hide_console_windows` every 250 ms for the
  life of the app. Callers swallow the exception (`:4051-4052`, `:4269-4271`),
  so each failed launch adds one thread. `hide_console_windows` does an
  `EnumWindows` plus `GetClassNameW` per visible window on every call.
- Trigger: FreeBuff binary removed after discovery, or an unreadable cwd.
- Fix: wrap the spawn in `try/except: stream_ended.set(); raise`, or start
  the thread after spawn succeeds.
- Test (Windows): monkeypatch `winpty.PtyProcess.spawn` to raise; assert the
  passed `stream_ended` is set after the exception propagates.

### B4. Windows: FreeBuff is spawned without `subprocess_env` (medium, high)

- `agent_backends.py:3880` `PtyProcess.spawn(args, dimensions, cwd=cwd)` vs
  the POSIX path `:3923` `env=subprocess_env(args[0])`.
- `NoDefaultCurrentDirectoryInExePath=1` (`:1010-1022`) is therefore not set
  for FreeBuff or anything it runs, so a `git.exe` committed to the project
  folder is what runs when the agent asks for git -- the hazard that comment
  describes. The shim directory is also not prepended, so a managed
  `freebuff.cmd` whose `node` is not on the GUI's PATH fails to start.
  `tests/test_exe_search_path.py` only checks `subprocess_env` itself.
- Fix: `PtyProcess.spawn(args, cwd=cwd, env=subprocess_env(args[0]), dimensions=(60, 180))`.
- Test: monkeypatch `winpty.PtyProcess.spawn` to record kwargs; assert
  `env["NoDefaultCurrentDirectoryInExePath"] == "1"`.

### B5. Updating Codex does not stop the held app-server first (medium, high)

- `blindpilot_app.py:1493-1503` stops only opencode before `npm update`;
  nothing drops the Codex key. `backend_pool.py:279` and `:374` both say
  "before an update replaces a CLI", and nothing calls either from the update
  path.
- On Windows npm cannot overwrite a running `codex.exe`: the update exits
  non-zero or leaves `codex.cmd` pointing at a half-replaced package, while
  the held server keeps running the old binary for up to 15 minutes.
- Fix: in `update_backend` and `install_backend`, for `BACKEND_CODEX` call
  `backend_pool.pool().drop(backend_pool.pool_key(BACKEND_CODEX))` and log it,
  as the opencode branch does.
- Test: keep a fake `HeldProcess` under the codex key, stub
  `_run_logged_process`, run `update_backend("codex", log)`, assert
  `pool().take(key) is None`.

### B6. Server requests for an unbound thread are dropped, not declined (medium, medium)

- `agent_backends.py:2079-2081` and `:2091-2092`: `_route` returns when no
  listener is bound. A message with `method` and `id` is a request Codex
  holds open until answered (the code says so at `:2798-2799` and `:3105`).
  `_decline_server_request` (`:3102`) only runs inside a worker's loop.
- Trigger: default mode (`on-request`), Escape as the model is about to run a
  command; interrupt unconfirmed, thread abandoned, then
  `item/commandExecution/requestApproval` arrives for a thread nobody reads.
  That turn waits forever; the next `turn/start` after `thread/resume` meets
  a turn still in progress.
- Fix: in `_route`, when `method and "id" in message` and no listener, send a
  decline (move `_decline_server_request`'s payloads onto `CodexServer`).
- Test: `server._route({"method": "item/commandExecution/requestApproval",
  "id": 5, "params": {"threadId": "t", "turnId": "x"}})` with nothing
  attached; assert `proc.stdin.written` holds `{"id": 5, "result": {"decision": "decline"}}`.

### B7. A thread/resume error kills the shared server (medium, medium)

- `agent_backends.py:2700`, `:2706` call `_discard_server` on any error reply
  to `thread/start` or `thread/resume`. With one tab open, a stale session id
  ("thread not found", rollout rotated or deleted) drops the app-server and
  every MCP child. `drop` does not call `on_reap`, so the next prompt pays a
  cold start with nothing said.
- Spec: "The pool only drops a process-wide process when `alive()` says it is
  already gone, or on an explicit user-driven restart."
- Fix: keep the server on `thread/resume` errors (clear the session id and
  report), discard only when `alive()` is False or on `thread/start` errors,
  and announce via `on_reap` when it does drop. Product call: see Q1.
- Test: `_RefusesToStart`-style script answering `thread/resume` with an
  error; assert `pool().take(key)` is still the server.

### B8. Prewarm key never matches on a fresh install (low, high)

- `agent_backends.py:4008` key model is `model or _read_freebuff_choice()`,
  which is `""` before any choice is recorded; the worker resolves to
  `FREEBUFF_PREFERRED_MODEL` (`:4228`) and takes with that (`:4245`). The
  prewarmed terminal is killed at take. `launch()` also calls
  `set_freebuff_model("")` (`:4036`), which may scan the 125 MB binary under
  `_FREEBUFF_PREWARM_LOCK`, blocking the GUI-thread `discard_freebuff_prewarm`
  (`blindpilot_app.py:5676`) for seconds.
- Fix: `model or _read_freebuff_choice() or FREEBUFF_PREFERRED_MODEL` at `:4008`.
- Test: monkeypatch `_read_freebuff_choice` to `""` and `_spawn_freebuff_pty`
  to a fake; `prewarm_freebuff(".", None, "")`; assert
  `_take_freebuff_prewarm(".", None, FREEBUFF_PREFERRED_MODEL)` returns it.

### B9. Windows pump drops FreeBuff's last words (low, medium)

- `agent_backends.py:3889-3895` loops on `pty.isalive()`; when the process
  exits, buffered output is left unread. `_freebuff_launch_failure` (`:4406`)
  then quotes an earlier frame, not the "node not found" line the user needs.
- Fix: loop on `read()` until `EOFError` (pywinpty raises
  `EOFError('Pty is closed')`), not on `isalive()`.
- Test: fake pty whose `isalive()` is False while `read` still returns data;
  assert the data reaches the queue.

### B10. `.ps1` is offered as a runnable CLI (low, high on effect, low on trigger)

- `agent_backends.py:787` and `:823` include `.ps1` in the candidate
  suffixes. `Popen([".../codex.ps1", ...])` fails with WinError 193. Only
  reached when `.exe` and `.cmd` are both absent.
- Fix: drop `.ps1` from both tuples. Test: tmp dir holding only `codex.ps1`;
  `find_backend_cli` returns None.

## Bloat and dead code

- `backend_pool.py:71` `Adapter.start` is never called by the pool (only
  `_reaper.start()` at `:400`); `_borrow_server` calls `_start_codex_server`
  directly (`:2566`). One test uses it (`test_codex_pool.py:347`). Remove the
  field or make `take`-or-start a pool method. ~2 lines, plus the spec line.
- `agent_backends.py:1666-1685` `_offer`: every inbox is an unbounded
  `queue.Queue()` (`:1792`), so both `queue.Full` branches cannot run.
  Reduce to `listener.put_nowait(message)`. ~14 lines.
- `:2304`, `:2345-2346` `CodexWorker._request_id` fallback in `_next_id`:
  every caller runs after `_borrow_server` set `_server` (`:2571` precedes
  `_handshake`; `steer` needs `accepting_input`). Dead. 4 lines.
- `:1847-1855` `CodexServer.detach`: test-only (`test_codex_pool.py:169`);
  the test can use `detach_listener`. 9 lines.
- `:4940-4942` `FreebuffWorker._clean_freebuff_screen`: no callers in the
  repo or tests. 3 lines.
- `:3411-3420` `_SENTENCE_END_RE` alias is assigned and never read;
  `_complete_sentences` is a one-line wrapper. Import `complete_sentences`
  directly (tests reference `agent_backends._complete_sentences` at
  `test_backends.py:827` and `test_screen_text_properties.py:26`, so keep the
  name or update them). ~8 lines.
- Duplicated helpers inside the file (each 5-12 lines, one shared helper each):
  `_handle_server_request` else (`:3091-3100`) = `_decline_server_request`
  else (`:3119-3128`); `_codex_questions` (`:1571-1576`) = `_opencode_questions`
  (`:5557-5562`) option parsing; `backend_auth_ok` FreeBuff branch
  (`:644-655`) = `_freebuff_account_lines` (`:766-772`); `_opencode_auth_ok`
  (`:680-684`) and `_opencode_account_lines` (`:792-796`) both parse
  `auth.json`; `_freebuff_chat_dirs` / `_freebuff_chat_path` candidate lists
  (`:3218-3223` / `:3239-3244`); `_freebuff_boot_ready` new-chat branch
  (`:3726-3739`) is `_freebuff_dropped_new_chat` (`:3751-3761`) negated;
  `_freebuff_launch_failure` / `_freebuff_startup_silence` last-line
  extraction (`:3772-3775` / `:3802-3805`). ~50 lines total.
- Duplicated across modules: `no_window_kwargs` (`:57`) vs
  `blindpilot_app._no_window_kwargs` (`:419`) vs `hermes_backend.py:58`;
  the login-shell `command -v` block in `find_backend_cli` (`:805-820`) vs
  `blindpilot_app._login_shell_which` (`:351-375`); `_codex_home` (`:895`)
  vs the inline `CODEX_HOME` read at `:1088`. ~35 lines.
- `_do_run` calls `find_backend_cli` (`:2629`) and `_start_codex_server` calls
  it again (`:2161`); on POSIX with the CLI off PATH each call may run a login
  shell (8 s timeout), the second under `_CODEX_START_LOCK`. Pass the binary
  in or drop the first call. 4 lines.
- Prose that restates the tests: `Adapter` docstring (`backend_pool.py:47-69`);
  `HeldProcess.interrupt` (`:111-114`) and `binding` (`:89-95`) repeat its "no
  production caller"; `CodexWorker.__init__` field comments
  (`agent_backends.py:2253-2303`, ~40 lines for 15 fields); `_is_this_turn`
  (`:3002-3023`); `_release` (`:2536-2544`) repeats `keep` (`backend_pool.py:248-251`).
  One sentence each saves ~60 lines; the reasoning is already in the tests.

## Stale comments and spec drift

- `backend_pool.py:170-180` `pool_key` docstring describes Codex and opencode
  as sharing, and Claude/Hermes/FreeBuff as per-panel, on the pool. Only Codex
  is on the pool; opencode still uses `_opencode_server` (`agent_backends.py:4959`),
  Hermes uses `HeldConnection`, FreeBuff uses `_freebuff_prewarm`.
- `backend_pool.py:279`, `:374` "before an update replaces a CLI": no update
  path calls `drop_all` or `stop_all_held_processes` (B5).
- Spec vs code: keys are `("codex", None)` not `("codex",)`; `Adapter` has
  five callables (`busy`) not four; the spec puts the
  `own_group_kwargs`/`end_process_group` pairing "once" in `HeldProcess.stop`,
  the code has it per server class (`:2154`, `:5161`); the spec calls the idle
  timeout "settable" and lists it as the one new user-facing setting -- there
  is no setting, `_HELD_IDLE_SECONDS` is a constant.
- `agent_backends.py:3412-3414` says the sentence regex lives in
  `markdown_rows` "so the Hermes worker can share it without importing this
  module". Hermes imports `markdown_rows` directly (`hermes_worker.py:31`);
  nothing imports the alias from here.
- `:3965-3966` TTL comment claims an abandoned terminal is cleaned up (B2).
- `:3389-3401` the paragraph explaining `_FREEBUFF_STARTUP_SILENCE_SECONDS`
  sits above `_FREEBUFF_TURN_SECONDS`; the two-line note for the turn limit
  is glued to its end. Swap them.
- `:2893` `clientInfo.version` sent to Codex is `"0.3.0"`; `APP_VERSION` is
  `"0.21.3"` (`blindpilot_app.py:285`). Import it or drop the field.
- `:1699-1704` `_state_lock` comment refers to "Task 7 registers a reply
  queue" -- plan-task jargon that means nothing in the code.
- `:1023-1028` `_codex_app_server_binary` docstring says launching the
  packaged exe "gives BlindPilot reliable ownership"; it gives ownership of
  one process, not its children (B1).

## Questions for the maintainer

- Q1. Should a `thread/resume` error drop the shared app-server (current), or
  only clear the tab's session id and keep the server for the other tabs
  (spec)? B7 depends on this.
- Q2. Windows child-tree teardown for B1: `taskkill /T` (simple, needs the
  exe on PATH), a Job Object at spawn (robust, more ctypes), or a Toolhelp
  walk reusing `_descendant_pids`?
- Q3. Keep `Adapter.start`, `Adapter.interrupt`, and `HeldProcess.binding`
  unused until plans 2-5 land, or remove them now and add them with their
  first caller? The contract tests exercise `interrupt`, so removing it
  touches `tests/pool_contract.py`.
- Q4. Is the 15-minute idle limit meant to become a preference, as the spec
  says, or is the constant final? The spec and the docstrings disagree.
