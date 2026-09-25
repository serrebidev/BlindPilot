# Changelog

Release history for BlindPilot, newest first. Entries are short by design. The reasoning behind each change is in the commit messages.

## v0.29.22 - 2026-09-24

- Streamed answers from Muse Code, Hermes and Command Code keep their Markdown shape. Each sentence was parsed as a row of its own, so numbered lists lost their numbers and lines split at their colons. Sentences are still spoken as they arrive, but a row is added only once its paragraph, list or heading is finished, and rows are only appended, so the list never rebuilds under the reader.
- Hermes keeps the spaces and paragraph breaks between streamed sentences instead of running paragraphs together.
- Muse tool rows name what they are about: search shows its pattern, and write_todos lists its items instead of reading out its bookkeeping JSON.

## v0.29.21 - 2026-09-24

- Muse Code no longer says it refused a permission you gave. Under load Muse 1.3.0 answers a permission choice with an internal error even though it has already saved the choice and the command runs, so BlindPilot read out a refusal that never happened. BlindPilot now sends the same choice once more with the same command id, which Muse answers with its first result, so you only hear a refusal if the second try fails as well. That also covers the rare case where Muse really did lose the choice, which would otherwise have left the turn waiting.
- Answering a Muse question in your own words works. A typed answer was sent as though it were one of the listed options, which Muse rejects, and the turn then waited on the question forever. It is now sent as your own text.
- Closing a Muse question without answering it, or pressing Stop while one is open, tells Muse the question was declined. Before, an empty answer was sent, Muse rejected it, and the turn stayed stuck.
- BlindPilot now sends Muse the short acknowledgement Muse's protocol requires when it shows a question or a permission request.
- Two requests sent at the same moment, for example pressing Stop while a turn is sending its own request, can no longer be given the same number, on Hermes or Muse.

## v0.29.20 - 2026-09-24

- Muse Code no longer hangs when it asks permission. Allow, Reject, Stop and steering were sent in a form Muse 1.3.0 silently ignores, so any turn that needed approval waited forever and Stop stopped nothing. They now reach Muse, and if Muse refuses one, the refusal is read out instead of lost.
- The Muse permission menu offers Muse's own choices. BlindPilot used to show a fixed Allow once, Allow for this conversation, Deny menu, but Muse 1.3.0 offers Allow once, Always allow in this workspace, and Reject, so two of the three old answers were invalid. The dialog now lists exactly what Muse offers, in its own words.
- Piped commands such as git status piped into head run under Muse. Muse approves each stage of a pipe separately, and only the first stage was ever answered, so the command stalled.
- Muse's thinking is read as thinking, not as its answer. Its reasoning summary was spoken and saved as though it were the reply.
- Muse answers are spoken once. Each message was read as it streamed in and then read again when it finished, and when a turn had more than one message only the last was kept as the answer.
- Muse's internal housekeeping is no longer read out. Every turn used to say "agentMessage" and "Reminder child session" twice.
- A model picked for a reopened Muse conversation is now used. The model was only ever sent when a conversation started, so changing it later did nothing. The picker also lists Muse's model ids, which is what gets sent back.
- Muse's max reasoning effort is offered now that Muse accepts it.
- A blank piece of a streamed answer no longer makes the screen reader say just the backend's name, on any backend.

## v0.29.19 - 2026-09-24

- The macOS download is built for Apple Silicon only. The release notes shipped with this version were still those of 0.29.18.

## v0.29.18 - 2026-09-22

- Each tab keeps its own backend. The backend was one app-wide setting, so choosing Codex for a new tab moved every other tab to Codex too, and the next message typed into a Claude tab silently started a Codex conversation. Model, Backend now changes the visible tab only; a new tab starts on the backend of the tab in front, the menu follows the tab you switch to, and resuming a past conversation puts only its own tab on that conversation's backend. While the open tabs use more than one backend, each says where it sends: the tab name starts with the backend, the prompt is called "Codex prompt" and so on, switching tabs announces it, and the send sound is pitched per backend. With a single backend nothing reads or sounds any different. Suggested by a user who sent a message to the wrong place.
- The version inside the app is right again. 0.29.17 was released still calling itself 0.29.16.

## v0.29.17 - 2026-09-22

- The three JSONL transcript formats (Claude Code, Codex, Command Code) are read by one reader instead of three copies. No behaviour change. Contributed by blindndangerous in #53.

## v0.29.16 - 2026-09-21

- FreeBuff's message divider is no longer read out as the answer. 0.0.180 draws each message in the transcript under a divider carrying the time it was sent - `[05:23 PM]` - so a message is two lines rather than one. The prompt's divider sits above the reading's boundary and went unread by accident; a steer's sits below it, so removing the steer's own line left the divider behind and it was spoken on its own, and joined onto the front of the next row. The divider above a matched line is now part of that message's span, recognised by shape so that a release which stops drawing dividers takes nothing with it. Found by running the backend against the installed FreeBuff 0.0.180 rather than by reading it: the steered turn put the person's own instruction in front of them as the model's words, copy marker and all, and repeated the same thinking paragraph four times as the section the reading compares against shifted underneath it. The same run confirmed what had only ever been assumed - the catalog scan reads all five of 0.0.180's models out of its executable, the boot hold, the reasoning split, session discovery and completion all work on a release the suite has never seen, and after the fix a steered turn reads `pong` and `kumquat`, one row each, with no echo of the message that asked for them. Also records the live measurements in `docs/code-audit/freebuff.md`.

## v0.29.15 - 2026-09-21

- A steered FreeBuff message is no longer read out as part of the answer. Steering types a second message into the same composer the prompt went through, and FreeBuff writes it into the transcript exactly as it writes the reply - plain text, with nothing to say whose words they are - which is why the reading cuts the transcript at the echo of what was typed. Only the prompt was ever looked for, so a steer's echo landed inside the section that gets spoken and the person heard their own instruction read back as though the model had said it. Moving the boundary to the newer echo would have cost the answer above it, and on the path where no saved chat can be found that is the first half of the turn's work, so the echoes are taken out instead: every message typed this turn is remembered, and the lines each one covers are skipped when the reading is built. By span rather than by the one matched line, because an echo the terminal had to wrap is taller than one line, which fixes the older half of the same bug - a long prompt's wrapped tail was read as the answer too. The last open item from `docs/code-audit/freebuff.md`.

## v0.29.14 - 2026-09-21

- The update dialog's worker test no longer calls GitHub. Its stand-in carried a `check` that nothing read, a leftover from the injectable check that went in 0.29.9: `UpdateDialog._check_worker` asks the module's own `fetch_latest_release`, so every run made an unauthenticated request to the GitHub API. On a machine that has spent its anonymous rate limit that answers 403, and the `HTTPError` holds an unopened response body that warns when it is collected - which `-W error`, the command CI and the release workflow both run, turns into a failure in a test about what the dialog does after `wx.App` is gone. The answer is stubbed at the seam the worker actually uses, the dead field is gone, and the test still fails if `_call_after`'s no-application guard is removed. No product change.

## v0.29.13 - 2026-09-21

- A FreeBuff turn that runs on a model you did not pick now says so out loud. FreeBuff drops models between releases, and when the chosen one is gone from the picker there is no card to walk to, so after five seconds BlindPilot presses Enter on whatever is highlighted and runs the turn on that. The row announcing the swap was filed as agent activity, and Narration, Keep up drops every kind but "assistant" and "notice" - so the one mode where an answer unlike the chosen model's is least likely to be noticed was the mode that never heard why. It is a notice now, the kind that is spoken whatever the narration mode, as the boot hold and the mid-turn drop beside it already were.
- A reopened FreeBuff conversation keeps its own id when its chat folder cannot be found. A chat deleted since the history list was drawn, or filed under a bucket this release no longer uses, leaves the turn in the state a brand-new conversation is in - no chat path - and the rule that learns a new conversation's id was armed on it: whatever appeared under FreeBuff's projects since the turn began. A folder created by another tab's FreeBuff turn is exactly that, so the id was overwritten and the next message resumed a conversation nobody had opened. It happened twice over, once in the watching loop and once where the turn decides what to prewarm for the next message; both now ask one helper, which answers only for a turn that began without an id. The watching loop also stopped walking every project bucket once a second for the whole of a resumed turn that could never succeed at it.
- Help, About BlindPilot names all seven backends again. The sentence listed five and had already been corrected by hand once, when opencode and Hermes were added; Muse Code and Command Code were both shipped without it, in text a screen reader reads aloud. It is built from the backend table now, so the next backend cannot go missing from it, and a test holds it there the way the README's own has since the same thing happened to the README.
- The FreeBuff surface was audited end to end; the report is `docs/code-audit/freebuff.md`. It also records what was checked and deliberately left alone - the prewarm lock held across a spawn, the `saw_busy` gate on the only completion reading that does not open the chat file, a `not self._cancelled` that reads as dead and is not - and one open item: a steered message may be read out as part of the answer, which needs a captured frame from a real steered turn to settle and was not guessed at.

## v0.29.12 - 2026-09-20

- A current Hermes' blocking prompts reach you now instead of the agent waiting them out. Its clarify, dangerous-command approval and password or secret prompts moved from events onto JSON-RPC server-to-client requests: the gateway writes a request carrying an id of its own and holds it open until a response frame with that id comes back, and it only asks a client that has announced it answers them - `client.capabilities {server_requests: true}`. BlindPilot announced nothing, so on a current Hermes the agent stopped on the first question it had, and sat out its whole deadline on one nobody had been shown. The worker announces the capability as it connects, answers all four kinds on the request's own id, and declines the seven it has no window to serve - reading Hermes' in-app terminal, its browser preview or a native window, driving a tour, and the password-manager prompts - with an error rather than silence, because the gateway settles an error response exactly as it settles an answer. A resume hands back the requests that were still open, so reopening the conversation parked on a question now answers it rather than attaching to a session this window could not unblock. Verified against the Hermes installed here (0.21.3) over BlindPilot's own local pipe: the greeting arrives, the capability is accepted, and the reply names all twelve request methods. A Hermes that predates this keeps working - the event-and-respond half of every prompt is still read and still answered.
- The sentence said when a bypass turn has a tool refused named things bypass does not actually refuse. It told the listener bypass still refuses "a destructive shell command", which bypass runs - the only removal that still stops for confirmation is one of the filesystem root or the home directory - and it recited the tools a headless run withholds, which the refusal itself already names, one tool at a time. It now says the two things a bypass turn is genuinely still held to, an ask rule and that removal, both measured at 1.58.1, and the withheld tools keep their naming where it applies.
- A Command Code turn that stops at a tool needing an approval no headless run can give no longer loses the work it had done. A denial Command Code can hand back to the model - a `permissions.deny` match, a mode gate, a tool name that does not exist - is a policy denial, and the turn carries on from it; a denial from anywhere else ends the whole run instead, which is what a prompt becomes when nobody can answer it: a `permissions.ask` rule (which prompts in bypass exactly as it does in default), the root and home deletion breaker, a hook, or a permission check that threw. Measured against the CLI installed here: a bypass run whose ask rule matched a call exits **0** and hands the text the turn had produced back on stdout, and BlindPilot threw that text away and reported the turn as a failure - so the files written and the tests run under it disappeared behind an error, and the reason the run stopped was on no stream BlindPilot kept at all. The answer is kept now and said to be cut off rather than finished, in a notice that is spoken whatever the narration mode - the treatment a turn out of turns already gets - and it names the tool that was refused, taken from the refusal the tool events already spelled out. Only a stop with nothing to hand back is still reported as a failure.

## v0.29.11 - 2026-09-20

- A Hermes on another computer behind a load balancer or an authenticating proxy is no longer refused the WebSocket for want of a cookie. The login bought its ticket with a throwaway cookie jar that was dropped the moment the ticket came back, so the upgrade that followed carried no cookie at all - while every client that works (the dashboard in a browser, Hermes' own desktop app, the other apps reaching the same address) sends the session it was just given. A ticket is known only to the process that minted it, so a load balancer that places the upgrade by an affinity cookie, or a proxy that decides by its own session cookie, refused the connection it had just let through for the browser. One jar now spans the login, the ticket and the upgrade, and the cookies are matched the way a browser matches them: another host's cookie is never handed over, an expired one is dropped, and a Secure one is withheld from an unencrypted `ws://`. Verified against a real gated `hermes serve`: the upgrade carries Hermes' session cookies and is accepted.
- A refused upgrade now says which layer refused it, instead of repeating the one thing the reporter had already checked. Hermes answers an upgrade it turns down with an empty 403 and no body (measured against `hermes serve`), while anything else writes its own error page and names itself in a `Server` header; that difference is read and reported now. When the refusal is Hermes' own - which is what a ticket it never issued looks like - the message says the WebSocket has to reach the same Hermes that signed the user in, the case a load balancer in front of several needs session affinity for. The README's remote-Hermes section says the same.
- A Command Code turn that runs out of turns keeps what it produced. Print mode caps its loop at 100 turns, which is the budget for a script that pipes a question in and reads an answer out, and nothing asked it for more - so a piece of work that spends a turn on each of its steps (read, edit, run the tests, read again) stopped mid-task, where the same task in a terminal runs to the end. Worse, when the cap was reached Command Code returned the partial answer and exited 8, and BlindPilot threw that answer away and reported a failure, so the files written and the tests run in that turn disappeared behind an error. The limit is 500 turns now, a runaway guard rather than the budget for real work, and a turn that reaches it keeps its answer and says so in a notice, which is the kind that is spoken whatever the narration mode - the treatment a FreeBuff turn cut off at its hour already got.

## v0.29.10 - 2026-09-20

- Muse Code now reaches its host through the same stdio transport Hermes uses instead of a private copy of it (PR #52). `MuseTransport` is gone, and so is the third stdio client that lived inside `muse_model_catalog`; both build a `StdioTransport` over `muse_command(cwd)`, and the request numbering and frame shapes the two workers duplicated are now one `JsonRpcCalls` mixin. Hermes' launch branches, timeouts, frames and user-facing sentences are unchanged. Two Muse behaviours change deliberately: on macOS and Linux `muse serve` now starts in the conversation's folder rather than wherever BlindPilot was launched, which is what Windows already got through WSL's `--cd` and what the catalog already did; and a Muse turn that fails quotes the last six stderr lines one per line as Hermes does, rather than the last three joined with semicolons. One latent defect goes with the copy: the old transport reported itself connected until its pipe closed on its own, so a `muse serve` that died was waited on until the deadline ran out - the shared transport polls the process.
- The suite's one `wx.App` now lives for the whole run. A per-module fixture re-made the app each time one was collected, and two New Session fixtures created and destroyed an app of their own; a second `wx.App` replaces the first and collecting it leaves no current app at all, so a later dialog could be built without one. wxGTK reports that as `PyNoAppError` while wxMSW tolerates it, which is why four unrelated tests in `test_preferences_dialog` went red on the Linux runner only. The fixture is session-scoped now, and the two New Session fixtures destroy only an app they made. No product change.
- The Codex, FreeBuff and opencode turn workers share one `_TurnWorker` base instead of three written-out copies of the same 14-keyword constructor, callback block, accepting-input test, one-failure-only guard, and run/teardown order (PR #54). Each keeps its own `_do_run`, a `_setup` hook for its own fields, and its own `_teardown` - Codex releasing its place in the app server's routing tables, FreeBuff closing its terminal, opencode closing its event stream - in the order the copies ran them. `_fail` reads the backend from a class attribute, so the diagnostics record and the crash sentence still name the right provider, and FreeBuff, which took a permission mode and an effort and stored neither, records the same n/a its old `_fail` hard-coded. The four question builders (Codex, opencode, Claude Code, and the shared option helper) each walked a list of dicts into `Question` objects by hand; one `_questions` takes the provider's flag names, and each provider's vocabulary stays declared in one line. The three opencode auth writes share `_opencode_write` (invalidation included, so a write cannot forget to drop the stale provider cache); the two FreeBuff queue pumps are one `_drain_to_queue`; `CodexServer.expect` takes `binds_thread` instead of hiding it behind two methods; and the finished-sentence release Muse and Command Code each carried is `release_finished`/`release_remainder` beside `complete_sentences` in `markdown_rows`. The Hermes, Muse and Command Code workers stay as they are - none files a diagnostics record, Hermes swallows crashes its own run() would report, and sharing Hermes' sentence release would change what Muse and Command Code speak. Behaviour, callback order, and every user-facing string are unchanged.

## v0.29.9 - 2026-09-19

- Thirty-four pieces of code nothing called are gone, from a whole-repo over-engineering audit (PR #50). `BACKEND_IDS` and `BACKEND_LABELS` were hand-written copies of what `BACKENDS` already holds and are now derived from it, with the same names and order; `BackendInfo.supports_steering` was `True` for all seven backends and read nowhere, so the field and its seven values are gone. Parameters no caller varies went too: `invalidate_backend_cache` and `invalidate_model_options` no longer accept a missing backend meaning "every backend", `freebuff_model_options` returns three values instead of a five-tuple whose last two were always empty, and a tab-title limit, a fetch timeout, a mode-picker `speak`, an account-editor password flag, a conversation-list limit and the three generate methods' `endpoint`/`extra_headers` are gone. Dead code followed: `MUSE_CREDENTIAL_RELPATH`, `SERVER_TOOL_SUBAGENT`, `_OPENCODE_AGENTS`, `ChatPanel.regenerate_item`, `CodexServer.inbox()` and `stderr_lines()`, `backend_pool.stop_reaper`, `muse_popen_wrapper`, the `end_hidden_terminal`/`_kill_pty` pair under one name, and three function-local re-imports in `muse_backend`. The three settings toggles in the main window and the two enable/disable loops in Accounts share one helper each, and every announced sentence is byte-identical. The update dialog's injectable check and its unannounced-state flag went with them. `CREDITS.md` now names all seven backends it drives.
- The test suite's stand-ins live in one place instead of a copy per file (PR #51). `tests/doubles.py` holds the Earcons recorder, `Button`, `Prompt`, `KeyEvent` and the SessionPanel stub nine files had each built by hand; `tests/conftest.py` gains the module-scoped `wx_app` fixture ten files repeated and the `frame` fixture six repeated; the twelve `log_dir` patches in the diagnostics tests and the fourteen `Path.home` patches in the backends tests are a requested fixture each; and the two dialog-key files that differed only in the dialog class became one parametrised file. 303 lines fewer across 35 files, with the same tests. No behaviour change.

## v0.29.8 - 2026-09-19

- The working design and task plan for the held Claude Code process are gone from `docs/claude-session/`, now that the work they describe shipped in 0.22.1 (PR #39). `01-design.md` was the design approved 2026-09-05, and `02-plan.md` was the eight-task plan an agent followed, with every test body written out ahead of the code; both describe an intention the code now realises, and `applied.md` already keeps the record of what shipped and what was left out. Git history keeps both files. No code, spoken wording, or key changed.

## v0.29.7 - 2026-09-19

- A turn that finished its work and signed off by offering the next step no longer opens the question dialog. Since 0.29.4 a question written into an answer opens the same dialog a question tool does, which on Command Code is the only way a question can arrive at all - but the judgement was "the turn ends on a question mark", and that is how most turns end. "Want me to run the tests too?", "Should I commit this?", "Anything else?": every one of them put a modal over an answer that was still being read, for a question that was holding nothing up, and whose reply would have been the next message whenever it was typed. `is_an_offer_to_carry_on` now reads the question alone and literally - the phrasing an offer or a closing courtesy is put in - rather than guessing at intent, because a dialog opened over a finished turn is exactly the interruption this is meant to spare.
- A question only you can settle still opens the dialog, on every backend: "Which name do you prefer?", "What should the config file be called?", "How many retries do you want?" So does an offer that names a fork - "Should I use tabs or spaces?" is an offer by its grammar and a decision by its content, and which way the work goes from there is not something a turn can pick for itself. Questions a backend asks through its own question tool are untouched, and the Options switch that turns written questions off altogether is unchanged.

## v0.29.6 - 2026-09-19

- `HERMES_HOME` is now resolved one way, so a tilde in it names one directory. Three parts of BlindPilot read that setting and two of them disagreed: the Hermes launcher and the session history stripped the value and expanded a leading `~`, while the status report and the Settings menu took it exactly as written. With `HERMES_HOME=~/alt`, the launcher and the history used the real directory while the other two looked for `auth.json` and `config.yaml` under a folder literally named `~`, so a Hermes that was signed in could be reported as signed out of it. All three now resolve it through `hermes_backend.hermes_home`. Every test until now set an absolute path, which is why the two halves never disagreed in the suite; the new one sets a tilde.
- Dead code and duplicate helpers found by a whole-repo audit are gone, with no change for any caller that exists: a `certificates` import in `muse_backend` kept only "for symmetry" (Muse is local-only and makes no TLS call), an unused `_no_window()` alias and `_wsl_available()`, two Chat-panel history-view handlers that nothing bound, and two helpers in the main window that were copies of `agent_backends.no_window_kwargs` and `app_updater.version_tuple`, both of which the module already imports. The one parser difference is in BlindPilot's favour: the kept `version_tuple` reads `v1.2.3` as `(1, 2, 3)` where the deleted copy read `(2, 3)`, and every caller passes either a bare version or a versions-directory name, where the two agreed.
- A bare `pytest` now collects only `tests/`. It used to walk the whole tree, and an untracked scratch folder under `docs/` holding a `uv` cache took collection into a `RecursionError` before a single test ran - invisible to CI, which never sees a gitignored folder, and worked around by hand with `--ignore=docs` in the pre-commit hook. That flag goes with its cause, and explicit paths still win, so `pytest tests/test_foo.py` and CI's bare invocation are unchanged.
- The setup-helper test's sentinel now means what its own comment claims. Its stand-in app writes two trace files and the test waits for the second, on the reasoning that a file closed before the next one exists has been released - which covered the first file but not the second, because a file exists from `CreateTextFile` onward, not from `Close`. Under load the teardown could then collide with Windows Script Host still holding it. The sentinel is written under a temporary name and renamed into place once closed, so its appearance means the handle is gone. Found while running the suite for the audit, not by the audit.

## v0.29.5 - 2026-09-18

- A Hermes on another computer now says why it refused the connection instead of always blaming the password. Every refused WebSocket upgrade was reported as "refused the connection key. Check the key", so a Hermes that refused for a reason of its own sent the user back to re-type a password it had just accepted - and the user could see it was correct, because it was. The HTTP status Hermes refused with is now read, and it decides the message: a sign-in that already succeeded is never reported as a wrong key, a 404 or a 502 is named as the address or the proxy rather than the key, and a Hermes given the wrong kind of credential says which one it wants.
- Hermes never announces which credential arrangement it is running, and the two take different credentials: a Hermes bound to a public address refuses a session token outright, and one bound to localhost has no login to offer and refuses a ticket. BlindPilot asks the provider route which one it is talking to, so a token refused by a server that requires a sign-in is answered with "choose Username and password" and a password refused by a server that wants a token with "choose Session token", instead of both arriving as a wrong key.
- A 400 from the login is reported as the address fault it is. Hermes answers 400 to a Host it was not told about, which is exactly what a reverse proxy sending its own Host produces; it was reported as a bare "HTTP 400" that read like a credential problem. The message now names `dashboard.public_url`, which is the setting that fixes it.
- Verified against a real `hermes serve`, bound both ways and reached over plain `ws` and over TLS through a reverse proxy: the working arrangements still connect, and each refusal now names its own cause.

## v0.29.4 - 2026-09-18

- A turn that ends by asking you something now opens the question dialog, even when it never used a question tool. Every backend's dialog was opened by a structured event and nothing else - Claude Code's `AskUserQuestion`, Codex's `request_user_input`, opencode's question, Hermes' clarify, Muse's userInput - so a model that wrote its question into its answer instead announced nothing and showed nothing: the turn just ended, with no sign an answer was wanted. This is what a skill that interviews you does as a matter of course, "grill me" among them, and on Command Code, which withholds `ask_user_question` from headless runs, it was the only way a question ever arrived. The end of each answer is now read, and a turn that ends on a question opens the same dialog on every backend; what you type is sent as your next message. Under Options, and on by default.
- The reading is deliberately narrow, because a dialog nobody asked for interrupts for nothing: the question mark has to be near the end, code blocks are not read, and a question the answer then goes on to answer itself is left alone.
- Claude Code and Codex are now told to ask through their question tool rather than writing the question out. Claude Code takes it as `--append-system-prompt`; Codex takes it as `developer_instructions`, added behind your own rather than replacing them, since `-c` overwrites a key rather than adding to it.
- Answering a written question sends your answer through the message box, and anything you had typed there while the turn was running is put back afterwards rather than lost. Your own Codex `developer_instructions` reach Codex intact even when they contain an emoji: the value is no longer written with escapes that Codex's TOML parser refuses.

## v0.29.3 - 2026-09-16

- The README now names Muse Code wherever it names the other backends: the opening sentence, the agent count (seven, not six), the wizard paragraph, the by-hand setup block with its one-line install and `muse login`, the Backends table with its columns read off Muse's BackendInfo and the compaction map, and a paragraph on how BlindPilot reaches it over MSP through WSL on Windows. Muse Code became the sixth backend in 0.26 and the README was never updated for it; Command Code in 0.28 was, which is why the prose said six while the Backend menu offered seven.
- A new test reads the README the way a person does and fails the moment a backend or Chat provider exists in the code without a mention: it checks every backend label against the opening sentence, the by-hand setup block and the Backends table, checks the stated count in words against the number of backends the code ships, and checks every Chat provider label against the supported-providers sentence. Written against main first, it named all five places Muse Code was missing.
- The sound-cue tests no longer read the developer's own config.json. Their Earcons fixture pins the master switch, every cue, and the working-loop mode, so a machine with the working cue switched off can no longer make the loop-starts test fail and the loop-stays-off test pass for the wrong reason.

## v0.29.2 - 2026-09-15

- A tool Claude Code refused is no longer read out as though it were the tool's output. A `permissions.deny` rule, a disabled tool and a PreToolUse hook all refuse in `bypassPermissions` exactly as they do in any other mode, and each one comes back as an ordinary tool result carrying `is_error` — which was announced as "Result: Permission to use Bash ... has been denied", indistinguishable by ear from the command output it never produced. A refusal now names the tool and the reason, a call that merely failed is told apart from one that was refused, and the full text still gets its own row. In bypass, one sentence follows the first refusal saying what bypass does not cover.
- Verified against the installed Claude Code rather than assumed: in `bypassPermissions` the CLI sends no permission prompt at all and Read, Write, Edit, Bash, Glob and Grep all run, so BlindPilot's own refusal of anything left to a prompt never fires in that mode. The refusals that do get through come from deny rules, disabled tools and hooks.

## v0.29.1 - 2026-09-15

- Command Code's bypass mode now actually bypasses what it can, and names what it cannot. `-p` withholds nine tools from the model outright, so a call to one came back "No tool named ... exists" — a refusal no permission mode lifts, bypass included. `todo_write` and `taste` are now asked back with `--tools-enable`; the tools that would answer a question or approve a plan with nobody watching stay withheld on purpose.
- A refused tool is said out loud. `tool_denied`, `tool_errored` and `tool_hook_blocked` arrived through the unknown-event path as the bare words "tool_denied" and "tool_hook_blocked", naming neither the tool nor the reason. Each now names the tool, its subject, and the reason the hook or gate gave, and a bypass turn adds one sentence saying what bypass does not cover: a `permissions.deny` match, a `permissions.ask` match and a destructive shell command are all checked before the mode is.
- A turn Command Code stopped over a permission is reported as that. A refusal that did not come from a permission rule ends the whole turn, and a headless run has nobody to approve it; the turn used to finish with "Finished with nothing to say."
- A bypass turn says up front what the settings will still refuse. `permissions.disableBypass` switches `--yolo` off with one line on stderr that a windowed run never shows anybody, and `permissions.deny` and `permissions.ask` rules apply in bypass exactly as they do in default. All three are now read out of Command Code's own settings layers and announced before the turn starts.

## v0.29.0 - 2026-09-14

- Chat mode has a new built-in provider: Command Code's Provider API. One account reaches every model Command Code sells - Claude, GPT, Gemini and the open models - at their underlying rates. Claude models are sent to the Anthropic Messages protocol and every other model to Chat Completions, chosen per model as the request is built, because the service rejects a model sent to the wrong protocol. Enter a name and the API key from Command Code Studio; the addresses are built in.
- File attachments now work on every chat account, not only OpenRouter's. The panel no longer refuses to send them for other providers: images and PDFs travel as the protocol's own content blocks on the Messages protocol (which also gains them on OpenAI accounts set to Messages), and text files go in as their text everywhere, as they already did on Chat Completions. Accounts left on OpenAI's Responses API still decline attachments with a clear sentence, because that protocol has no file block BlindPilot can serve.

## v0.28.4 - 2026-09-14

- Command Code can now be steered and queued. `-p` answers one query and exits, so a message typed while a turn was running used to be refused with "still finishing"; it is now queued and sent the moment that turn drains, in order and with its own attachments. Steer stops the running turn and resumes the conversation with the new instruction, Stop pauses the queue, and `/queue list`, `/queue clear` and `/queue resume` manage it. `/help` explains the flow.
- Command Code's own commands now have frontend equivalents: `/effort`, `/mode` and the `/mode:name` forms, `/plan [task]`, `/add-dir`, `/copy`, `/sessions`, `/quit`, `/config`, `/login`, `/connect`, `/update`, `/init`, `/review` and `/pr-comments`. A command that only exists in its terminal UI is explained when typed instead of being sent to the model as ordinary text.
- `/compact` works for Command Code: it summarizes the conversation into a new saved session, and the original stays selected unless the replacement session is saved first.
- Command Code no longer starts its own background updater. Even `--version`, `status` and `--list-models` could spawn its detached updater, which put a console window on screen mid-use. Every Command Code process BlindPilot starts now runs with `COMMANDCODE_SKIP_UPDATES` set, and npm installs and updates run hidden and non-interactively with their output logged.

## v0.28.3 - 2026-09-13

- Command Code's sign-in no longer puts a console window on screen. v0.28.2 ran it in the same off-screen pseudo-terminal FreeBuff uses, but making that terminal calls `AllocConsole`, which hands back a console that has *already* appeared — hiding it is the next thing that happens, and that frame of a window, titled with BlindPilot's own executable, is what was still being seen. Windows now creates the sign-in's console hidden from the start (`CREATE_NEW_CONSOLE` with the startup show flag set), so there is no window to hide and nothing to flash; the same watcher still hides any console Windows raises anyway. Verified end to end on Windows: the CLI runs in that console, exits 0, and no console window in the process tree is ever visible.
- The rest of v0.28.2 stands: the wizard waits for the sign-in to finish and reports whether it landed.

## v0.28.2 - 2026-09-13

- Command Code's sign-in no longer opens a console window. The wizard now runs `command-code login` in an off-screen terminal — the same hidden pseudo-terminal FreeBuff runs in — because the CLI's Ink UI needs a real terminal but has nothing in it for anyone to read or answer: the sign-in is the browser page it opens. The wizard then waits for the command to end and asks the CLI whether the sign-in landed, so it reports the result itself rather than leaving a window to tab past and an "Already Signed In" to choose by hand.
- Fixed `test_pool_contract.py` failing with `ModuleNotFoundError: No module named 'tests.test_claude_session'` on any machine whose Python has another `tests` package installed. argostranslate ships one into site-packages, and a directory without an `__init__.py` is only a namespace package, which loses to a regular package of the same name later on `sys.path`. The import uses the bare sibling name every other test in the suite already uses.

## v0.28.1 - 2026-09-13

- Command Code's sign-in from the setup wizard now opens a real terminal window instead of running the CLI hidden. `cmd login` mounts an Ink terminal UI for its authentication spinner, and Ink refuses to start when its input is not a terminal ("Raw mode is not supported on the current process.stdin, which Ink uses as input stream by default"), so the hidden attempt died on that mount before it reached the browser — no sign-in page ever opened, and the crash's own Ink documentation URL was read out as the address to sign in at. A console gives Ink the terminal it needs, which is how Hermes' setup already runs.
- The wizard's sign-in page for Command Code no longer says BlindPilot needs it "to have a provider and model configured" — that is Hermes' wording for a different thing. Command Code signs in to an account, and its page now says so.

## v0.28.0 - 2026-09-13

- Command Code is now a backend BlindPilot can drive. It is installed from npm and driven through its non-interactive mode: `command-code -p --output-format json` writes one event per line, the answer is spoken a sentence at a time with tool calls and results shown as they happen, and the next message resumes the conversation by the session id the CLI reports.
- The model picker reads `command-code --list-models` and opens on the model and effort Command Code's own config records. Permission modes map onto its `default`, `auto-accept`, `plan`, `dont-ask`, and launch-only `--yolo`; the setup wizard installs, updates, and signs it in; `/status` names the account; and past conversations are listed from `~/.commandcode/projects` by the id they resume with.
- Compaction is not offered, and a headless prompt of `/compact` is treated as text by the CLI (measured at 1.53.1) — the backend reports that instead of pretending. A running turn cannot be steered either, because `-p` answers one query and exits.
- Codex sign-in now opens the sign-in page from BlindPilot itself, because the CLI's own browser launch does not reliably arrive when `codex login` runs hidden. Codex's `http://localhost:1455` callback line is no longer read out as the address to visit, which had sent at least one person to a local port that cannot sign them in.

## v0.27.5 - 2026-09-11

- Muse Code now detects Meta's HTTP 402 Spark-inference refusal in its live session log and reports that the signed-in account needs Muse Spark access. Previously Muse retried internally without publishing an MSP error, leaving a BlindPilot turn apparently stuck.

## v0.27.4 - 2026-09-11

- FreeBuff's Windows pseudo-terminal console is now disabled and marked as a no-activate tool window before it is hidden and moved off-screen. It cannot become an interactive BlindPilot.exe window or take focus while FreeBuff starts or stops.

## v0.27.3 - 2026-09-11

- Chat's Newest first order now uses the release timestamp OpenRouter supplies for each model, rather than comparing version numbers across unrelated model families. Meta Muse Voice Transcribe 1.0, for example, correctly appears ahead of older 5.x models when OpenRouter lists it as newer.

## v0.27.2 - 2026-09-11

- Codex now interrupts a turn whose id arrives just after the Stop wait expired. The id was recorded as abandoned but no interrupt was sent in that timing window, leaving the turn running; this was exposed by the Intel macOS release test.

## v0.27.1 - 2026-09-11

- Chat's Newest first and Oldest first orders now sort the generation, revision, and dated build numbers in model IDs before any catalog tie-breaker. The first release gave all models discovered in one refresh the same timestamp, so most of a catalog still fell back to alphabetic order.

## v0.27.0 - 2026-09-11

- Chat's editable Model field is now a native list. It only offers models in the selected account's catalog, so a mistyped or retired model cannot be sent by accident. Chat, Model order lets you choose newest first (the default), oldest first, A to Z, or Z to A; the order is remembered and changing it keeps the selected model. Newest and oldest use the first refresh on which each model appeared in that account's catalog, because providers do not consistently publish model release dates.

## v0.26.1 - 2026-09-08

- The Muse backend starts sessions again. MSP validates `session/start`'s command id as UUIDv7 — a random v4, which every other command tolerated, is refused with `invalid session/start commandId: expected UUIDv7` before any conversation begins. Ids are v7 now.
- The same live probing found the approval mode the wire advertises is sealed by the host at startup and cannot be lifted from the client, so a default host only ever accepts `promptUnmatched` and `denyUnmatched` — the bypass mode BlindPilot asked for was rejected outright, killing the session before it began. The worker starts in `promptUnmatched` and, when the window is in bypass, answers each approval itself instead of asking; a decision must also pick from the approval's own `availableChoices` ids rather than a fixed word, and a cancel pressed while the dialog is up beats a stale answer.
- `workspaceRoot` must be absolute on the wire too; a relative one is refused. It is already translated and absolutised in the WSL bridge, so only the fallback path needed it.

## v0.26.0 - 2026-09-08

- Muse Code, Meta's terminal coding agent, is now a backend BlindPilot can drive. Its CLI ships for macOS and Linux, so on Windows it is reached inside WSL through the same bridge Hermes uses: the working directory is translated on the way over and always handed over absolute, because WSL's own `--cd` rejects a relative one (measured: `Wsl/E_INVALIDARG`, printed on the stream the protocol reads). Turns ride Muse's own host protocol, MSP — JSON-RPC 2.0 over the stdio of `muse serve` — with the answer streamed a fragment at a time and spoken only in whole sentences, steering and cancelling mid-turn, tool approvals and the agent's own clarifying questions put in front of the person, compaction on request, and past conversations reopened by session id.
- The setup wizard installs Muse through its official script (inside WSL on Windows, where the script cannot run natively) and signs you in through its device flow: the sign-in page is opened in your browser and the wizard reports whether the CLI came back signed in. `/status` names the launcher, its version, and the account Muse stored at sign-in, and the settings dialog points at Muse's own credential file.
- The model picker reads Muse's live catalog from a real `muse serve` host — the same request a mid-conversation model switch is served by — and offers the reasoning-effort levels the CLI documents, filtered to the vocabulary MSP itself accepts so a tier the protocol would refuse is never offered.

## v0.25.0 - 2026-09-07

- A Chat conversation belongs to the profile it was started on. A profile restored as the default was shown but never applied, so the first conversation of every session was created on the wrong account and model while being recorded against a profile naming another; and a conversation re-read its temperature, token limit and OpenRouter tools from the profile on every request while keeping the system prompt it started with, so editing a profile changed a conversation half way through. Both fixed (PR #43).
- Chat mode can open a past conversation again. The conversations table had been written on every conversation since Chat mode shipped and never read: no list, no get, and nothing in the window to reach one. Chat menu, Recent conversations, with a filter, message counts, the profile and account each ran on, and Delete. Opening one restores the profile, system prompt, account and model it was started on.

## v0.24.0 - 2026-09-07

- Chat mode opens on the account and conversation profile you chose. A Use as default checkbox sits under the list in Accounts and in Conversation profiles; each row says "default" in its own text, and unticking leaves none marked, which opens on the first account and no profile as before (PR #42).
- The Chat menu could not be opened from the keyboard: it shared Alt+C with the Conversation menu, and Windows gives a shared access key to the first menu only. It is Alt+T now. Three chat buttons that were shadowed the same way move too - Stop generation to Alt+G, Clear all to Alt+L, Remove selected to Alt+E.
- An empty History list announced "unknown" and answered no arrow key, having no item for focus to land on. It holds one "No messages yet" row now, which is not an entry and goes when a message arrives.
- Chat mode no longer starts with focus inside the hidden Agent page, where Tab and Shift+Tab moved around a panel that was not on screen until something later moved focus back.

## v0.23.0 - 2026-09-07

- Session Status reports how much of the account's allowance is left and when it comes back, for every backend that meters one: Claude Code and Codex in the five-hour and weekly windows they report, FreeBuff in the credits it counts off a balance, and Hermes in the pooled provider credentials it has stopped spending. opencode meters nothing of its own and reports nothing (PR #41).
- A Hermes tab's status named opencode's connected providers as its own, having no branch of its own in the report. It now names the providers Hermes holds credentials for, and the one it is set to use.

## v0.22.1 - 2026-09-06

- Claude Code keeps one process per tab between turns instead of starting a fresh one for every message, so the agents a turn leaves running survive the turn's end (PR #39).

## v0.22.0 - 2026-09-06

- Chat mode can start new conversations again. The menu's Start New Conversation item was built as an agent-only command, so switching to Chat mode greyed it out and left its Ctrl+Shift+N chord dead; every message sent afterwards kept landing in the same conversation. The item is now enabled in both modes, and the handler routes to whichever mode is showing.
- The Responses list wraps long rows to the window's width again, drawn by a list that carries its own accessible object on Windows; on Linux and macOS, where the toolkit has no accessible object to give, a native list stands in so the screen reader still reads it (PR #37).
- A pre-commit configuration runs the same checks CI does - ruff, the formatter and mypy on every commit, the test suite on push - so a lint error is caught before a run is spent on it (PR #38).

## v0.21.6 - 2026-09-05

- Bypass permissions now works on Hermes. The gateway has no yolo parameter on session.create, so the one BlindPilot sent was silently ignored; the bypass is applied the way Hermes' own /yolo command does it, per session, on every turn, and a mode picked between messages takes effect.
- A Hermes approval request is now answerable. The reply was sent with a key and values the gateway does not read, so every answer — including the automatic ones in bypass mode — landed as a denial and the command could not be run. In the asking modes the request is put in front of the person with the gateway's own once, session, always, and deny choices instead of being denied unheard.
- Visual pass 1 from a sighted contributor: a real app icon with display-scaling awareness, menu layouts that match what they announce, the error cue and update dialog made presentable, packaging checks for the icon and manifest, and ruff's formatter scoped out of the docs' code samples.
- Visual pass 2: the windows follow the system's dark mode, or light or dark can be chosen in Preferences. wxWidgets applies the choice before the first window exists, so the dialog says it takes effect at the next start, and a wxPython without the appearance API carries on as it was.

## v0.21.5 - 2026-09-05

- Stopping Codex or opencode on Windows ends their whole process tree instead of leaving every MCP child orphaned, and the taskkill path is built for Windows separators so the tree kill works wherever it runs.
- A Codex that cannot resume a conversation costs that tab its session, and the next message starts a new conversation, instead of taking the shared app-server down with it and breaking every other tab.
- Installing or updating Codex drops the held app-server first, because Windows refuses to overwrite a running exe.
- A prewarmed FreeBuff terminal nobody claims is closed when its fifteen-minute TTL runs out.
- A Hermes question with no preset options can now be answered; the text box is offered from the start.
- Enter on a Hermes conversation dialog's Cancel button opened that conversation instead of doing nothing.
- A Chat mode that cannot open falls back to Agent mode completely instead of leaving the window half switched, and agent-only commands are greyed out in Chat mode instead of acting on the hidden notebook.
- A resumed Claude CLI can emit a result for a leftover turn before ours; the worker took that as the end of our turn, closed stdin with the prompt still queued, then killed the CLI after thirty seconds and reported the kill as the answer. It now reads on while turns are still queued.
- The silent updater quotes the installer's /DIR= and /LOG= paths, so an account name with a space no longer breaks updates, and a failed update's status file survives accented characters.
- Remote Hermes connections verify certificates through the packaged trust store, and a held connection is dropped after an abnormal end instead of being reused.
- Chat answers cut off at the model's length limit say so, per-choice OpenRouter errors are raised instead of swallowed, and HTML blocks and markdown tables are read as rows.
- Tests no longer write into the real %APPDATA%, damaged AccessibleAI databases are skipped without leaving a half copy, and the warning-clean sweep compiles only the repository's own sources.
- README rewritten without stale claims, CHANGELOG cut from 19,000 words to 4,400 keeping every version, the macOS icon's retina sizes corrected, and the September audit reports kept in docs/code-audit/.

## v0.21.4 - 2026-09-04

- Codex keeps one app server running for the whole window instead of starting a new process for every message. The first message starts it, later messages and other tabs reuse it, and it is stopped when BlindPilot quits.
- Stop interrupts only the current Codex turn instead of killing the shared process. If Codex does not confirm the interrupt, only this tab's conversation is dropped, and the next message resumes it from Codex's own record. Escape pressed right after Send now prevents the turn from starting at all.
- A Codex idle for fifteen minutes is closed to free memory, with the announcement "Codex was idle and has been closed. The next message will restart it." A Codex that crashed or was killed is restarted with "Codex had stopped running. Restarting it, which takes a moment." Idle means no turn running or waiting on a question.
- A FreeBuff turn no longer dies with "string index out of range". pyte, the terminal emulator FreeBuff is read through, left an empty cell after a redraw over an emoji or CJK character, and reading it raised IndexError. Those cells are repaired before the screen is read.

## v0.21.3 - 2026-09-04

- FreeBuff messages sent during or just after startup no longer get stuck. On macOS and Linux the terminal is read from launch, so an unread output buffer cannot stall startup. Sending adopts the terminal already being started and cancels stale delayed starts, so two FreeBuff processes cannot compete for one message.
- Turn completion and session drops are detected from FreeBuff's log event names rather than by searching the log text, so a word in your prompt or its answer can no longer end a turn early or release a held message.

## v0.21.2 - 2026-09-04

- FreeBuff's normal startup line, "session over; holding queued messages until rejoin", no longer fails the turn. 0.21.1 treated it as a dropped session, which failed every first message after launch one second in. The message is now held until the log shows FreeBuff has reconnected, with a one-time "FreeBuff is still starting; holding the message until it is ready".
- A session drop seen mid-turn is watched for thirty seconds for FreeBuff's automatic rejoin before the turn is failed.

## v0.21.1 - 2026-09-04

- FreeBuff 0.0.168 changed its welcome screen, so the model picker chose the wrong card and ran GPT-5.6 Luna when GLM 5.3 Flash was selected. The picker is now read by position and navigation counts real steps.
- A FreeBuff session that logs "session over" and never answers is reported with the remedy (quit and reopen FreeBuff, then resend) instead of being waited out for an hour.
- Composer readiness is recognised from the "Describe your task" placeholder, so a message is no longer held through a two-minute silence.

## v0.21.0 - 2026-09-04

- Shortcuts that collided with macOS changed everywhere. Recent Conversations is Ctrl+Shift+H (Ctrl+H was Hide), Model and Effort is Ctrl+Shift+E (Ctrl+M was Minimize), and Next and Previous Session are Cmd+Shift+] and Cmd+Shift+[ on macOS (Cmd+Tab is the application switcher). Menu notes say Cmd on macOS.
- macOS settings move from `~/.config/blindpilot` and `~/.local/share/blindpilot` to `~/Library/Application Support/BlindPilot` on first launch. Nothing already there is overwritten, and a failed move does not stop the launch.
- Preferences (Cmd+,) opens every Options-menu setting in one dialog. About uses the native macOS panel, Create Desktop Shortcut works on macOS, and the build ships a real icon, bundle identifier, and minimum macOS version (10.15) from `BlindPilot.spec`.

## v0.20.9 - 2026-09-04

- HTTPS requests from the Mac build can verify certificates again. PyInstaller froze a certificate path that exists only on the build machine, so update checks, Node.js installs, and npm-based backend installs all failed with CERTIFICATE_VERIFY_FAILED. certifi's root list is used only when OpenSSL's own store is empty; `SSL_CERT_FILE` and system stores still win.

## v0.20.8 - 2026-09-03

- Hermes slash commands are run as commands instead of being sent to the model as text. The picker lists Hermes' own commands, asks Hermes which it recognises, and reads the output back.
- A Hermes turn that asks a question no longer ends there. Hermes' `clarify`, `sudo`, and `secret` requests are shown in the question dialog and answered by id. Passwords and secrets are never echoed into the transcript.

## v0.20.7 - 2026-09-02

- A Hermes turn that is waiting says why. Hermes' own "Still starting the agent" notice is shown and spoken, a turn silent for two minutes gets a one-time diagnosis naming the likely cause (a rate-limited or out-of-credit provider) and the remedy (pick another model with /model), and terminal decorations such as the warning emoji are stripped from fallback notices.

## v0.20.6 - 2026-09-02

- The New Session dialog says what it is doing, once. The name-field help reads "Leave it empty to let the first message name it", the remote-mode explanation is spoken when the dialog opens, and a refused folder is announced once instead of twice.
- The test suite no longer reads the machine's own Hermes history when Hermes is installed, and an unclosed response body in `mint_ws_ticket` is closed. The suite is green under `-W error`.

## v0.20.5 - 2026-09-02

- A session on a remote Hermes can be named. New Session in remote mode asks for a name and an optional folder on that computer as free text, sends the path as typed, and says so when Hermes could not use the folder and ran the conversation elsewhere.
- A session keeps the name it was given instead of being renamed after its first message. The name is dropped only when the tab becomes a different conversation.
- Transport fakes in the test suite follow the real `Transport` rules, so a closed stream reports disconnected and a closed transport refuses writes.

## v0.20.4 - 2026-09-02

- Hermes is found after its own installer installs it. Discovery checks `%LOCALAPPDATA%\hermes\bin`, the Windows venv layout (`Scripts\hermes.exe`), and the default `HERMES_HOME` on disk when the environment variable is stale.

## v0.20.3 - 2026-09-02

- The setup wizard installs and updates Hermes on Windows, macOS, and Linux through Hermes' official installers (PowerShell on Windows, curl on the others). No administrator rights or Node.js are needed, the installer's output streams into the log, and the install folder is added to PATH. Update re-runs the installer instead of falling through to npm.

## v0.20.2 - 2026-09-02

- The wizard no longer offers to install a backend that is not on npm, and no longer reports "npm could not be installed" on a machine that has npm. A backend BlindPilot cannot install shows where its instructions are and a Check Again button. Failure messages are built from whole sentences.

## v0.20.1 - 2026-09-02

- The warning-clean sweep test excludes virtualenv and build folders by name prefix rather than a fixed list, so a virtualenv called `.venv-win` or a folder called `dist_new` is no longer compiled as thousands of test cases. Diagnosed by michaldziwisz.

## v0.20.0 - 2026-09-02

- Hermes Agent is a fifth backend, chosen from Model, Backend. It streams answers a sentence at a time, shows its reasoning and tool calls as rows, reopens past conversations, compacts in place, can be steered or stopped, and receives attached files as uploads so a Hermes in WSL or on another machine gets the file itself.
- Options, Remote Hermes drives a Hermes on another computer with a host, port, and a session token or username and password, with a Test connection button. Hermes Conversations (Ctrl+G) lists every conversation that Hermes knows, including running ones, and joins a running turn. A Hermes installed in WSL is found and run from Windows.
- One Hermes connection is kept per conversation and read continuously, so a quiet stretch of several minutes no longer drops it, a dead connection is noticed within seconds, and a quiet turn reports what it is doing about once a minute. Steer and Stop on a remote Hermes reach the live session.
- The working sound can be continuous, every few seconds (default ten, adjustable from two to a hundred and twenty), or off, from Options.
- Up in the prompt moves the caret instead of leaving the field. The responses are reached by Ctrl+R, Shift+Tab, or Ctrl+Up.
- The permission picker, effort levels, compaction, and the wizard summary are decided by what each backend reports it supports rather than by backend name. A backend whose sign-in needs a keyboard opens a real terminal window instead of failing hidden.

## v0.19.2 - 2026-09-02

- Shift+Tab into the session tab strip works. Arrowing along the strip keeps focus on it and the native control announces the tab. A Tab that BlindPilot routes but cannot move is handed back to Windows instead of being swallowed, and Tab out of the strip lands in the Prompt when the responses list cannot take focus.

## v0.19.1 - 2026-09-02

- Choosing a backend in the setup wizard is announced once, by the control itself, instead of a second time by BlindPilot.

## v0.19.0 - 2026-09-02

- The first-run wizard no longer repeats on every launch when the settings file cannot be written. The failure is logged and the wizard says its settings were not saved. The settings write is atomic, so an interrupted write cannot reset every setting.
- A CLI started in a project folder can no longer run a program committed to that folder. `NoDefaultCurrentDirectoryInExePath` is set for every CLI and everything it starts. The release workflow's write token is held only by the job that publishes.
- Closing a tab mid-turn no longer freezes the window, and the working sound stops when the tab closes. A FreeBuff turn cut off at its hour says so instead of presenting a partial answer as the whole. A failing Codex turn waits up to one second for stderr's last line so the reason is kept.
- Claude Code edits are narrated with their size, such as "Editing server.py, 3 lines added, 1 removed". Model, Session Status runs `/status` from the menu bar. Model, Backend Settings lists the settings files each CLI reads for the current folder and opens the chosen one in your editor.

## v0.18.0 - 2026-09-02

- Four things spoken at the wrong moment, from an accessibility audit (PR #23). The row being read is no longer re-announced on every streamed batch, a dictated prompt is read back once and typed text is not, a search with no hits is announced instead of going to the status bar, and Enter on Cancel in Recent Conversations cancels instead of opening.
- The append fast path from that rewrite falls back to a full rebuild whenever the control's row count disagrees with the record, so Start New Conversation clears the screen and switching to the text view is not blank.

## v0.17.0 - 2026-09-02

- BlindPilot no longer kills a Claude Code that has already answered and then reports "it had not finished shutting down 30 seconds after it went quiet". A finished turn leaves the process to exit on its own and a reaper thread collects it, so session files and MCP servers shut down cleanly. The thirty-second wait remains on the failure path only.

## v0.16.0 - 2026-09-02

- FreeBuff no longer cuts a word at the wrong letter when reading the end of an answer. `casefold()` changes string length for characters such as German ß, and the position list did not account for it. Found with property-based tests, which are kept for this file.

## v0.15.0 - 2026-09-02

- Tests time out at 60 seconds each and CI jobs at 20 minutes, so a hung test fails in a minute instead of six hours. Test order is shuffled with `pytest-randomly` to expose order coupling; `-p no:randomly` restores fixed order. ruff moves from 0.15.10 to 0.16.5.

## v0.14.0 - 2026-09-02

- mypy runs in CI, pinned, with `platform = win32` so both halves of every platform split are checked consistently. It found that `steer` was missing from the `AgentWorker` Protocol even though the window calls it, and a test now holds every worker to the whole contract. The remaining 21 errors were fixed or ignored with a reason beside each.

## v0.13.0 - 2026-09-02

- Every dependency in `requirements.txt` has an upper bound at the next major version, because pywinpty and markdown-it-py had each drifted a major version under an open floor and a release build resolves fresh.
- Keep up narration is measured by a test. Five agents at eight steps each is 85 spoken lines in Follow everything and one in Keep up, and every step is still a row.

## v0.12.0 - 2026-09-02

- Options, Narration has two modes. Follow everything, the default, speaks every tool call, result, and subagent line. Keep up speaks your message, the answer, and BlindPilot's own notices, and leaves the steps in the list unspoken.
- A failed turn has a sound, using the platform's own error sound (MessageBeep on Windows, Basso on macOS, none on Linux), as a fourth switch under Options, Sounds. Narration stops when Stop is pressed.
- macOS announcements no longer all post at high priority, which cut off the previous line; only errors do now. Verified with a real FreeBuff that ending a turn on "Main prompt finished" does not cut agents short.

## v0.11.1 - 2026-09-02

- BlindPilot no longer kills Claude Code five seconds after a turn and then reports "Claude Code exited with code 1", an exit code it caused itself. The wait watches for thirty seconds of silence instead, and when BlindPilot does stop the CLI it says so in those words.
- A result event with no `subagent_stats` no longer ends a run with background agents still working, a `started_in_background: true` no longer counts as an agent, and a late error result keeps the answer that already arrived.

## v0.11.0 - 2026-09-01

- BlindPilot keeps talking after the screen reader connection drops or the reader starts after BlindPilot. A failed announcement rebuilds the speech output and repeats the line, throttled to once every five seconds. Startup logs when there is no speech output at all.
- A rotating log, `sys.excepthook`, `threading.excepthook`, and `faulthandler` record every crash and unfinished turn for every backend, in `%LOCALAPPDATA%\BlindPilot\Logs`, `~/Library/Logs/BlindPilot`, or `$XDG_STATE_HOME/blindpilot`, four files of a megabyte at most, with Help, Open Log Folder to reach them. Prompt text, answers, file contents, and credentials are never written at any level.
- The startup smoke check no longer shows a window or steals focus, and the hidden console is claimed only when FreeBuff is the backend.

## v0.10.0 - 2026-09-01

- A Model menu carries Backend, Model and Effort, Permission Mode, Manage Backends, and Connect a Provider. The File menu is split into File (sessions, tabs, the application) and Conversation (what happens inside one). No chord changed.

## v0.9.2 - 2026-09-01

- Each sound cue can be turned off on its own from a new Options, Sounds submenu, greyed out while Play sound cues is off. Turning off the working cue stops a loop that is already playing.

## v0.9.1 - 2026-09-01

- Enter no longer starts a turn while the last one's events are still being applied, which could write the old answer into the new turn or leave a backend process unstoppable. A run counts as in progress until the window has been told it ended.
- Tests run on every push and pull request, on Windows, macOS, and Linux (under xvfb), and CI starts the application through both startup smoke flags instead of only importing it.

## v0.9.0 - 2026-09-01

- `/status` is answered by every backend. Claude Code and Codex are asked through their own status commands, FreeBuff and opencode are read from their stored credentials, and the report says the model, effort, permission mode, folder, and whether the next message continues this conversation.
- Chat mode reaches OpenRouter's twelve server-side tools (web search, web fetch, date and time, image generation, apply patch, shell, bash, fusion, advisor, subagent, tool search, model search) as a checklist on the conversation profile, beside a thinking budget and a PDF reader. Tool calls are spoken as they run and cited pages become a numbered Sources list.
- A reasoning model's thinking arrives as its own History entry with a length line, is not saved with the conversation, and can be copied with Ctrl+C. Conversation profiles from earlier releases still open.

## v0.8.1 - 2026-09-01

- A Codex, FreeBuff, or opencode turn that crashes says so instead of ending silently. Every FreeBuff turn closes its pseudo-terminal handle. Ten error messages that went only to the status bar are spoken.
- Enter sends the answer in the question dialog's Other box. Sign-in addresses are opened only when they are `http` or `https`; anything else is spoken and shown for opening by hand.

## v0.8.0 - 2026-09-01

- A Mode combo box switches the window between Agent and a new Chat mode, which integrates AccessibleAI's chat stack (OpenRouter, OpenAI, Claude, Gemini, Z.AI, Moonshot AI, Kimi, DeepSeek, OpenCode Go, and OpenAI-compatible accounts, with stored keys, profiles, streaming, attachments, regeneration, and diagnostics). An existing AccessibleAI database is imported once. Chat's management row moved into a Chat menu.
- Sessions are a native tab strip instead of a combo box. Ctrl+Tab and Ctrl+Shift+Tab move between them, and the tab control announces the conversation name and position itself. Focus order in Agent mode is Mode, Session tabs, Responses, Prompt, actions, Permission mode, with no transient "tab control" or "unknown" announcements.
- The updater is one accessible dialog with readable release notes, a named progress gauge, 10-percent announcements, cancellation, checksum verification, and an explicit restart step. Startup checks can be turned off and no longer steal focus.

## v0.7.2 - 2026-09-01

- Claude Code runs stay open until every background agent has finished, with the count of remaining agents announced. Stderr is drained continuously, malformed UTF-8 is replaced, and a turn that exits without a result leaves its details in `claude-worker.log`.
- Sound cues can be turned off from Options, Play sound cues.

## v0.7.1 - 2026-08-31

- A FreeBuff that starts and then paints nothing is reported after two minutes of silence instead of an hour. FreeBuff's preferred model is GLM 5.3 Flash (`z-ai/glm-5.3-flash`), since FreeBuff dropped DeepSeek V4 Pro; a release without it falls back to a model FreeBuff offers.

## v0.7.0 - 2026-08-28

- Linux announcements reach Orca through an off-screen GTK accessible without stealing focus. macOS self-updates reject translocated or unwritable bundles, remove quarantine from verified updates, restore the previous copy if replacement fails, and reopen the result. The opencode backend works on Python 3.13.

## v0.6.3 - 2026-08-28

- Every backend starts with the PATH a login shell would have, so a CLI launched from the macOS Dock can find Node. FreeBuff, which passed no environment at all, runs on a Mac for the first time.
- CLIs that are npm launchers start in their own process group and are stopped as one, so the real agent does not outlive the launcher. FreeBuff's model picker no longer selects the wrong model when a remembered model has been dropped from the catalogue. The progress earcon plays once instead of overlapping.

## v0.6.2 - 2026-08-28

- Sign In works for every backend. Claude Code is signed in with `claude auth login` instead of the slash command, the CLI's output is read as it arrives so the sign-in address is found, spoken, and opened, and an Open Sign-in Page button reopens it. A code prompt written with no trailing newline is detected and a box pastes the code to the CLI.
- Whether a sign-in worked is checked by asking the backend afterwards rather than trusting the exit code. Closing the wizard, pressing Escape, or switching backends stops a running sign-in.

## v0.6.1 - 2026-08-27

- An opencode conversation survives the questions it asked. When the provider refuses the stored question step with "Invalid assistant message", the broken step is deleted and the message is resent, once per turn, and the transcript row stays.

## v0.6.0 - 2026-08-27

- A backend that stops to ask a multiple-choice question gets a dialog with one radio button per answer, checkboxes where several are allowed, and an Other box. Claude Code's AskUserQuestion, Codex's `request_user_input`, opencode's `question.asked`, and FreeBuff's terminal question box are all answered natively. Closing the dialog declines the question so the turn is never left waiting.
- FreeBuff reaches its composer again after FreeBuff stopped labelling the start-screen model "RECOMMENDED".

## v0.5.1 - 2026-08-20

- A clean computer can install every backend from the setup wizard. BlindPilot downloads Node.js LTS, verifies its SHA-256, installs the CLI into a per-user prefix, adds both to PATH, and checks that the CLI starts.
- FreeBuff sign-in opens the URL its CLI prints. FreeBuff's `off_peak_only` availability marker keeps a model in the picker, and a partial FreeBuff credential file no longer counts as signed in.

## v0.5.0 - 2026-08-20

- Sessions are real tabs, announced as "tab 2 of 4" with the conversation's name, and Ctrl+Tab and Ctrl+Shift+Tab move between them. A tab is named after its conversation's first message.
- Every backend starts in Bypass permissions mode, so a run never stops to ask. Existing installations are moved onto it once. Background tabs no longer speak over the tab being read, and the permission picker is greyed out from what a backend reports it supports.

## v0.4.0 - 2026-08-19

- opencode is a backend with streaming answers, steering, stopping, permission modes, compaction, and reopening past conversations. BlindPilot drives it through its own headless server, one per run shared by every tab, on loopback behind a generated password.
- `/model` lists every model opencode can reach with its reasoning variants, `/connect` and Connect a Provider connect providers by key or browser sign-in, and opencode's own slash commands run as commands. Past conversations are read from opencode's SQLite database, read-only.
- Permission modes reach opencode as rules it enforces. Plan mode selects opencode's plan agent and denies edits, and accept-edits allows edits while shell commands keep their safeguard.

## v0.3.14 - 2026-08-12

- A type checker was run over the shipped modules and 32 findings were settled. No behaviour changed.

## v0.3.13 - 2026-08-12

- The setup program run by hand closes programs that refuse to close, so a manual upgrade no longer stops on "DeleteFile failed; code 5".

## v0.3.12 - 2026-08-12

- Installed updates work again after 0.3.10 broke them with "code 5". BlindPilot no longer hands its library folder to child processes, closes programs that have its libraries loaded before the installer runs, checks every file can be opened first, and reports what went wrong with the installer's log beside it. Two BlindPilot windows no longer race to update the same folder.

## v0.3.11 - 2026-08-12

- Down on the newest row stays in the responses; Tab is the way to the prompt. The row being read is kept when streamed output rebuilds the list, and output is applied in small batches so arrow keys stay responsive.
- FreeBuff stays on DeepSeek V4 Pro after FreeBuff dated the model's display name, and the chosen model is re-applied before a terminal is replaced mid-message.

## v0.3.10 - 2026-08-10

- Updates install at all. No update since 0.3.0 had been applied, because the helper was started in a way that made PowerShell exit without running it. The installed folder's contents are replaced in place, every running process from that folder is waited for, moves are retried, the previous version is restored on failure, and the reason for a failed update is read out at the next start.

## v0.3.9 - 2026-08-10

- Reopen a past conversation from any backend and carry on, from File, Recent Conversations (Ctrl+H), filtered as you type and titled by the first message. Compact a conversation in place (Ctrl+Shift+K) and start a fresh one in the current tab (Ctrl+Shift+N). FreeBuff says plainly that it cannot compact.

## v0.3.8 - 2026-08-09

- A FreeBuff answer is read off its own screen as it is written, a sentence at a time, and anything that scrolled away is read from the saved chat once. A FreeBuff terminal is kept waiting so a message reaches it in under a second.

## v0.3.7 - 2026-08-09

- BlindPilot claims its console at startup, hidden and off screen, so creating a terminal has none left to show.

## v0.3.6 - 2026-08-09

- The console a pseudo-terminal attaches is hidden for the whole run. Updates run the new installer silently instead of replacing the program directory. The installer offers a desktop shortcut, and File, Create Desktop Shortcut does the same for unpacked copies. Reasoning is read without "Thinking" in front.

## v0.3.5 - 2026-08-09

- The console host FreeBuff needs ships in the packaged build, so FreeBuff runs and no stray command windows appear. A terminal that closes before FreeBuff is ready is reported. The backend's reasoning is left out of the activity by default, with an Options setting to include it.

## v0.3.4 - 2026-08-09

- Claude Code and every backend helper run without a terminal window. A Stop button and File, Stop Task (Ctrl+period) end a task and keep what it produced. FreeBuff's second and later answers are shown, its model stays on DeepSeek 4 Pro, and its interruption marker is not read out.

## v0.3.3 - 2026-08-09

- After an update is verified, the main window shuts down normally so its files can be replaced. A complete new installation is staged, swapped in after the old process exits, and rolled back if replacement or startup fails.

## v0.3.2 - 2026-08-09

- The selected FreeBuff model is kept by navigating FreeBuff's runtime picker to it, instead of accepting the Flash model it recommends.

## v0.3.1 - 2026-08-09

- Agent and tool activity and a heartbeat every thirty seconds are reported, so a long FreeBuff task does not appear frozen. FreeBuff's structured chat state is read for reasoning and answers, completion is detected from its per-chat log, a new session id is saved at once, and advertisements and tool cards are no longer mistaken for the answer.

## v0.3.0 - 2026-08-09

- Claude Code, Codex, and FreeBuff backends with matching conversation features, runtime model discovery, automatic narration after a message is sent, a silent-until-response mode, and a GitHub release updater with SHA-256 verification.
