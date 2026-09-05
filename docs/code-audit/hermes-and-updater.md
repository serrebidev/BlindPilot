# Audit: hermes_worker.py, hermes_backend.py, app_updater.py

Read-only audit, 2026-09-04. Line numbers are as of this date. Hermes' gateway
source is not on this machine, so anything about what Hermes sends or accepts
is marked medium confidence at best. Ruff is clean on all three files.

## Bugs (severity, then confidence)

### 1. Setup helper passes `/DIR=` and `/LOG=` unquoted -- HIGH / high
`app_updater.py:682-691`: `Start-Process -ArgumentList @(... "/LOG=" + $setupLog, "/DIR=" + $InstallDir)`.
Windows PowerShell 5.1 joins the list with spaces and does not quote elements.
Verified here on 5.1.26100: `/DIR=C:\Users\John Smith\...` reached the child as
two arguments. The installer's default dir is `{localappdata}\Programs\BlindPilot`
(`installer/BlindPilot.iss:27`), under the user profile, so any account name
with a space, or any user-chosen folder with one, gets a wrong `/DIR` and a
wrong `/LOG`. Inno then installs elsewhere or exits non-zero.
Fix: `('/DIR="' + $InstallDir + '"')` and `('/LOG="' + $setupLog + '"')`.
Test: run the helper against a stub installer `.cmd` that records `%*`, with
an install dir containing a space; assert one argument arrives.

### 2. `_await_response` drops the "turn is over" signal -- HIGH / high
`hermes_worker.py:709-711`. Frames that are not the awaited reply go to
`_handle_event`, whose `True` return (turn ended) is discarded. When the
ending event arrives before the reply -- a held connection with a queued
`message.complete` from the previous turn, attaching to a turn that finishes
during `session.resume`, a fast `slash.exec` -- the worker calls
`on_complete`, then either `on_failed` ("did not accept the prompt") when
the reply never comes, or falls into `_consume_turn` and waits 15 minutes for
a second completion. Reproduced with the test module's own helpers: held
connection plus one `message.complete` frame gives
`[('complete','hi'), ('failed','fake transport ended')]` with `on_started`
never called. `test_a_reused_connection_does_not_create_a_second_session`
(`tests/test_hermes_backend.py:1342`) runs exactly this and passes because it
only inspects the sent methods.
Fix: set `self._ended = True` when `_handle_event` returns `True`;
`_await_response` returns `None` at once when `_ended`; every caller checks
`_ended` before reporting a failure or entering `_consume_turn`.
Test: the reproduction above, asserting exactly one terminal callback.

### 3. Held connection is kept after an abnormal end -- MEDIUM / medium-high
`hermes_worker.py:669-677`. `keep` needs only "connected and has a live
session". That holds after the idle limit (1330), an `error` event
(1454-1458), a refused or unanswered `prompt.submit` (1050-1058) and a
`message.complete` with an error status. The server-side turn may still be
running; its late frames land in the next worker (see 2), and the next
`prompt.submit` hits a busy session.
Fix: add a `_clean_end` flag set only by a complete `_turn_complete`, slash,
compaction and replay success; require it in `keep`.
Test: scripted transport ending with an `error` event while still connected;
assert `held.take()` returns `(None, "")`.

### 4. Dead `StdioTransport` makes callers spin at 100% CPU -- MEDIUM / high
`hermes_backend.py:790-796`. `receive` skips the wait once `_closed` and
returns `None` immediately. Callers that do not check `connected()` on every
`None` busy-loop: `_consume_turn` for up to 15 s until `next_check`
(`hermes_worker.py:1296-1317`); `hermes_model_options` ready loop for up to
60 s (`hermes_backend.py:1319-1325`), the exact defect `_ready_or_fail`'s
docstring describes (1151-1162) and fixed only in the sibling function; both
request loops in `hermes_session_catalog` after a mid-request death
(1223-1225, 1259-1261).
Fix: one line -- in `receive`, wait `timeout` whenever there is no frame,
closed or not. Fixes every caller.
Test: close a `StdioTransport` (no process) and time ten `receive(0.05)`
calls; expect about 0.5 s, not microseconds.

### 5. Remote TLS bypasses the packaged trust store -- MEDIUM / medium
`hermes_backend.py:527` builds a plain urllib opener; `922` calls
`create_connection` with default `sslopt`. Both use OpenSSL's default store,
which `certificates.py` documents as empty in the frozen macOS build. A
`wss://` Hermes fails with CERTIFICATE_VERIFY_FAILED in the packaged app while
the updater (which uses `open_url`) works.
Fix: `build_opener(HTTPSHandler(context=certificate_context()), HTTPCookieProcessor(...))`
and `create_connection(..., sslopt={"context": certificate_context()})`.
Test: monkeypatch `create_connection`; assert the shared context is passed.

### 6. Reply waits count empty reads, not time -- MEDIUM / high
`hermes_worker.py:700-708` and `830-839`. `waited` advances only when a read
returns nothing, so a peer that streams frames but never answers (or never
says `gateway.ready`) is waited on forever. The docstring at 1286-1292 says
why this counting was wrong in `_consume_turn`; the fix was not applied here.
Trigger: attach to a turn emitting `message.delta` faster than every 0.5 s
while `session.resume` goes unanswered; the 120 s timeout never fires.
Fix: measure against `_now()` as `_consume_turn` does.
Test: fake transport returning a content frame on every read;
`_await_response(999, 0.2)` must return within 0.2 s of the patched clock.

### 7. Idle-limit failure text is false -- LOW-MEDIUM / high
`hermes_worker.py:1330-1332`. `failure_detail()` on a connected transport
says "closed the connection before the turn completed". Nothing closed; the
turn was silent for 15 minutes. Fix: a distinct message. Test: 900 s of quiet
under the patched clock; assert the wording.

### 8. Bracketed IPv6 with a port doubles the port -- LOW / high
`hermes_backend.py:583-586`. `remote_ws_url("[::1]:9119")` returns
`ws://[::1]:9119:9119/api/ws`. Fix: when the host starts with `[` and
contains `]:`, split on `]:` and re-append `]`. Test: equality.

### 9. Failure status file written ANSI, read UTF-8 -- LOW / medium
`app_updater.py:321` `Set-Content` (5.1 default ANSI) vs `952`
`read_text(encoding="utf-8")`. A non-ASCII reason or `%TEMP%` path (accented
user name) is spoken as mojibake. Fix: `Set-Content -Encoding UTF8`; read
with `utf-8-sig`. Test: write via the prelude function with "Zoë" in the path.

### 10. Two WSL probes flash a console window -- LOW / medium
`hermes_backend.py:307-319` `wsl_state_db` and `359-365` `wsl_sqlite_query`
omit `**_no_window_kwargs()`; the three sibling probes include it. In the
windowed build (`BlindPilot.spec:88 console=False`) each history query pops a
console. Fix: add the kwargs. Test: monkeypatch `subprocess.run`, assert
`creationflags` present.

### 11. `StdioTransport.send` has no lock -- LOW / medium
`hermes_backend.py:779-788`. `cancel()` and `steer()` run on the GUI thread
(`hermes_worker.py:640, 651`) while the worker may be mid-`send` of a 33 MB
`file.attach` frame (1184). Two text-mode writers can interleave and corrupt
a JSON line. Fix: a `threading.Lock` around write+flush.

### 12. `cancel()` blocks the GUI thread up to 2 s -- LOW / high
`hermes_worker.py:655-659` -> `HeldConnection.drop()` -> `StdioTransport.close()`
-> `proc.wait(timeout=2)`. Fix: let `run()`'s `finally` (worker thread) do
the close; `cancel()` only sets flags and sends the interrupt.

### 13. Unreadable clarify wedges the turn -- LOW / medium
`hermes_worker.py:1539-1542`. With a `request_id` but no question text the
handler returns without answering; Hermes blocks until its `clarify_timeout`
(never, when 0). Fix: still send `clarify.respond` with "" and say so in a
row. Test: `clarify.request` with `{"request_id": "r"}`; assert a respond.

### 14. Catalog aborts on one bad row -- LOW / medium
`hermes_backend.py:1247-1249`. `int()`/`float()` on a non-numeric field
raises out of `hermes_session_catalog`. Fix: per-row try/except.

### Checked and found sound
SHA-256: digest or sidecar required (212-213); https plus host allowlist on the
asset and on the redirect target (261, 273-275); size cap and exact-size check
(284, 292); temp file removed on every failure path including cancel
(297-302). `version_tuple` handles the `v` prefix and a `-beta` suffix;
`/releases/latest` never returns pre-releases and `APP_VERSION` is plain, so
the "1.3.0-rc1 equals 1.3.0" collision cannot bite today. Every BlindPilot-side
child in these files gets `stdin=DEVNULL` or `input=`
(`hermes_backend.py:224, 278, 315, 361, 433`; `app_updater.py:1022`); the
historical stdin-inheritance hang has no remaining instance here. The
WebSocket reader survives timeouts and dies on real errors, `send` refuses when
the reader is dead, `HeldConnection.take` replaces a dead one; there is no
auto-reconnect, so no duplicate-event path. Attachment names split on both
separators and the bytes travel base64 in a data URL with
`ensure_ascii=False` over UTF-8.

## Bloat and dead code

- `hermes_worker.py:389-393` `_REPLAY_READ_SETTINGS = True`: no reference in
  src or tests. Delete, 5 lines.
- `hermes_backend.py:500-502` `TICKET_TTL_MARGIN`: no reference anywhere.
  Delete, 3 lines.
- `hermes_backend.py:478-482`: a first comment paragraph describing the wire
  credential names sits above `REMOTE_CREDENTIALS` (settings kinds); the
  correct paragraph follows at 483-493. Delete the first, 5 lines.
- `hermes_backend.py:1316-1327`: hand-rolled copy of `_ready_or_fail` minus its
  `connected()` check (bug 4). Replace with the call: 10 lines and a bug.
- `hermes_backend.py:1176-1352`: `hermes_session_catalog` and
  `hermes_model_options` repeat connect / ready / send / poll-for-id /
  error-or-result. A `_one_shot(transport, method, params, deadline)` helper
  saves about 50 lines and gives bug 4 one place to live.
- `hermes_worker.py`: eight blocks send a request, await it, and report the
  two failure shapes (808-827, 871-889, 930-966, 1037-1058, 1086-1104,
  1121-1146, 1180-1208, 1240-1260). A `_call(method, params, timeout,
  failure_text) -> Optional[dict]` removes about 80 lines and makes fix 2 a
  single edit.
- `hermes_worker.py:1083`: `split(None, 1)` evaluated twice; bind it once.
- `hermes_worker.py:398-399`: `_message_text` re-checks `isinstance(dict)`
  that its only caller (419) already guarantees. 2 lines.
- `app_updater.py:266`: literal `"BlindPilot-update-"` duplicates
  `TEMPORARY_PREFIX` (936). `sweep_temporary_files` only works while they
  match. Move the constant up and use it.
- `hermes_worker.py:783-827` `_apply_live_selection`: sends `/model X
  --session` on every reused turn whether or not the model changed, with a
  60 s wait budget and a "Hermes refused" row each time on failure. No test
  covers it (`grep _apply_live_selection tests/` is empty) and the `--session`
  flag is unverified. Remember the applied model on `HeldConnection`; send
  only on change. See question 2.
- Change-history narrative in comments ("An earlier version...", "Before this,
  the turn simply stopped here...", "Measured: ..."). The point survives as one
  sentence each: `hermes_worker.py` 519-526 (repeats 1527-1537), 535-541,
  892-899, 977-984, 996-1003, 1013-1019, 1286-1292; `hermes_backend.py`
  1051-1069, 944-953, 984-989, 1153-1161; `app_updater.py` 993-997. About 95
  lines total.

## Stale comments

- `tests/test_hermes_upstream_contract.py:84-90`
  `test_hermes_never_calls_the_question_callback`: docstring says "Hermes'
  gateway protocol has no mid-turn question". False since `_answer_clarify`
  and `_answer_secret`; the test asserts only that nothing was asked at
  construction, so it passes vacuously. Delete it (test_hermes_questions.py
  covers the real behaviour) or reword.
- `hermes_worker.py:592` "Kept so the accessor below can answer honestly":
  there is no accessor; `_on_question` is called directly at 1543 and 1586.
- `hermes_worker.py:1437-1439` "answers approvals through its permission
  picker rather than a modal": the code auto-denies in non-yolo modes and
  shows nothing. Say that.
- `hermes_worker.py:1472-1473` `_release_finished_sentences` "Silent when live
  rows are switched off in Options": the function always emits; the window
  drops the rows. Reword.
- `hermes_backend.py:1112-1117` `_model_rows`: "which is also the form
  `/model` accepts, meaning a picked row can be sent back unchanged". False
  since the separator became " · " (1051-1070); `_apply_live_selection` splits
  the row before sending.
- `hermes_backend.py:478-482`: see bloat; misplaced paragraph.
- `hermes_worker.py:1330-1332`: the message itself is the stale text (bug 7).

## Questions for the maintainer

1. Approvals (`hermes_worker.py:1594-1609`) are auto-denied in every
   non-yolo mode with "switch the permission mode". Should `approval.request`
   go through `on_question` as a yes/no question, the way clarify does, so
   Hermes is usable in "default" mode? The decision values "approve"/"deny"
   are pinned by a test but not verified against Hermes.
2. `_apply_live_selection`: is a `/model` round trip on every reused turn
   intended, and is a refused switch really best-effort (the turn continues on
   the old model after announcing the refusal)? Is `--session` a real Hermes
   flag?
3. Bug 3: dropping the held connection after an abnormal end costs one login
   plus resume on the next turn. Acceptable, or should it reuse when the
   failure was a refused prompt rather than an idle limit?
