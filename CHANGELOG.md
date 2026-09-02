# Changelog

Readable release history for BlindPilot. When adjacent releases were part of the
same fix stream, they are combined with a version range such as
`v0.3.6-v0.3.7`.

## v0.11.0 - 2026-09-01

- BlindPilot keeps talking when the screen reader connection drops. The Windows speech output was built once, at import, and `announce()` swallowed every failure with a bare `except Exception: pass` — so when the connection to the reader went away, which is what NVDA restarting or a JAWS COM object disconnecting looks like from here, the object was dead for the rest of the session. Every later announcement raised, was swallowed, and said nothing, while the Options menu went on reporting narration as enabled; restarting BlindPilot was the only way back. On an application driven entirely by ear that is not a degraded state, it is the whole thing failing silently while claiming to work. A failed announcement now rebuilds the output and says that same line again, so the line that discovers the drop is not the one that is lost, and if the rebuild cannot speak either it is let go of rather than tried first on every line thereafter. The same rebuild covers the case that was equally permanent the other way round — a reader started *after* BlindPilot, which used to mean silence for good. Looking again is throttled to once every five seconds on both paths, because building the output scans for a reader and doing that per narration line during a fan-out would cost more than the speech does.
- Startup says so, in the log, when there is no speech output at all. With accessible-output2 missing — it is in `requirements.txt`, so that means an incomplete install — BlindPilot on Windows runs in total silence while its menus report narration as on.
- An automated startup check no longer puts a window on somebody's screen or takes their focus. `--startup-gui-smoke` built the real window and closed it a second and a half later, running the whole of a real launch to get there: it allocated a console (which Windows hands back already visible, one frame of a window appearing and vanishing), showed and raised the frame and asked for the foreground, and put focus into the prompt — which dropped whoever was working in another window into BlindPilot's prompt field, and, since Windows has to show a window to give it focus, dragged the hidden window onto their screen. Building the window is the point of the check — every menu, control and binding made, and the sizers able to lay them out — and `Layout()` gives all of that without it ever being displayed. The focus guard sits inside `focus_prompt` rather than at the four call sites, because a fifth would not know to guard itself. The cost, stated plainly: the check no longer exercises `reserve_hidden_console`.
- The console is claimed only for the backend that needs one. FreeBuff is driven through a pseudo-terminal, and creating one gives a windowed application a console whether it wants one or not, so claiming one up front and hiding it is right and stays — it means the console never arrives in the middle of somebody's first message. What was wrong is that it happened on every launch: Claude Code, Codex and opencode are ordinary subprocesses spawned with `CREATE_NO_WINDOW` and never need a console at all, so three quarters of launches paid one frame of a visible console for something they would never use. It is now claimed when FreeBuff is the selected backend and when the backend is switched to FreeBuff, so switching mid-session cannot push that frame into the middle of a turn either. `_spawn_freebuff_pty` reserves one itself in any case, so this decides *when* rather than whether.
- There is a record when a turn dies, for every backend and every crash. Only Claude Code left any account of a turn that ended badly, and it wrote its own file by hand; Codex, FreeBuff and opencode left nothing. The packaged build is windowed, which on Windows means it has no stderr at all, so an uncaught exception or a native crash in wxPython, pywinpty or ConPTY went nowhere — no console, no message, no file. One diagnostics module now provides a rotating log, `sys.excepthook`, `threading.excepthook` (the workers are threads, and an exception escaping one is exactly the failure that used to be invisible) and `faulthandler` for native crashes, writing to a file of its own so rotation cannot leave it holding one nobody reads again. All four backends record an unfinished turn through it. **What is never written, at any level, for any reason: the text of a prompt, the text of an answer, the contents of a file, or a credential.** This application's content is somebody's source code and the questions they asked about it, so the line is enforced rather than trusted — `log_unfinished_turn` takes a fixed list of fields and raises on anything else, which means widening it has to be a decision somebody makes on purpose.
- The log lives where logs belong, and is capped. The hand-written version wrote to the roaming settings folder — a roaming profile syncs between machines, and a log is about one machine and grows — with no size limit at all. It now goes to `%LOCALAPPDATA%\BlindPilot\Logs`, `~/Library/Logs/BlindPilot` or `$XDG_STATE_HOME/blindpilot`, four files of a megabyte at the most. BlindPilot's own records are written at INFO, raised with `BLINDPILOT_LOG_LEVEL` for a bug report; everything else stays at WARNING, because a library that logs a line per HTTP request would roll the records this exists to keep straight out of a capped file. Help gains an **Open Log Folder** item, because reading a path out loud and leaving somebody to navigate to it is not a way in.

## v0.10.0 - 2026-09-01

- The model picker has a way in from the menu bar. It already existed and worked, but typing `/model` into the prompt was the only way to reach it, and nothing in the menu bar said the word "model" at all — which is where a command is discovered, and where the shortcut for it is printed. A **Model** menu now carries the backend, **Model and Effort** on Ctrl+M, the permission mode, **Manage Backends** and **Connect a Provider**; Backend and Manage Backends move there out of File. The permission mode belongs to the conversation rather than to the window, so the menu's mark follows whichever tab is visible, and Connect is greyed with a reason on a backend that has no providers to connect rather than being offered and then refused.
- The File menu is split in two. It had grown into everything: sessions, tabs, compaction, stop, find, the projects folder and the desktop shortcut. It is now **File** for sessions, tabs and the application, and **Conversation** for what happens inside one. Every item is appended and bound through a single helper, so neither half can be added without the other — a menu item that does nothing is worse than no menu item. No button was removed and no chord changed, so existing muscle memory is untouched.

## v0.9.2 - 2026-09-01

- Each sound cue can be turned off on its own. **Play sound cues** turns all three off together, which is the right master switch and is unchanged, but the three are not interchangeable: sent and answer received are one-shots that confirm something happened, while working is a loop that runs for the whole turn — so it is both the one most likely to wear thin over a long fan-out and the only one that says a turn is still alive without being asked. Wanting the loop gone is not the same wish as wanting silence, and with one switch, stopping it cost both confirmations too. Options now carries a **Sounds** submenu with one check item per cue, greyed out while the master switch is off, because three live switches under something that mutes all three would describe a choice that is not there. A configuration written before this reads exactly as it always did, and a key this version does not know is dropped rather than carried. The progress loop still stops unconditionally when an answer arrives, because it has to end when the turn does; switching the working cue off stops a loop that is already playing rather than waiting for the turn to end.

## v0.9.1 - 2026-09-01

- Enter no longer starts a turn while the last one is still being applied. Whether a run was in progress was decided by `worker.is_alive()`, and a worker thread dies the moment it has *queued* its last event rather than when the window has acted on it — and the event mailbox empties sixteen at a time and then yields to the native queue on purpose, so a waiting Enter is dispatched inside that gap by design rather than by bad luck. The turn it started then had the previous turn's events applied to it: the pending `complete` wrote the old answer into the new turn, `failed` deleted the new turn outright, and `done` cleared the worker reference while the new backend was still working — which is how Stop, Steer and the tab-close cleanup reach a running backend, so the turn became unstoppable, its process leaked on exit, and Send came back on ready to start a third. A run is now in progress until the window has been told it ended, which is the only answer true at the same moment as the rest of the state these handlers read. Starting a new conversation and compacting ask the same question, and a worker whose thread fails to start clears the reference by hand so Send is never refused for good.
- The tests run on every commit instead of only on release day. `release.yml` triggers on a `v*` tag, so a broken commit was found with the release half built — artifacts uploaded for three platforms and the job failing on something that had been true for days. A Tests workflow now runs the same tests and static checks on every branch push and every pull request.
- The tests run on Linux for the first time. `linux_accessibility.py`, the pexpect pseudo-terminal and the POSIX process-group handling all ship, and several tests skip themselves on Windows and macOS, so they had never run anywhere. wxPython has no Linux wheel on PyPI, so the job installs Ubuntu's prebuilt package and runs under xvfb.
- CI starts the application rather than only importing it. The unit tests drive the window's handlers on stub objects, and a stub has whatever the test hands it, so a menu built before the notebook it describes cannot fail one. Both startup smoke flags the release build already relied on now run on every commit.
- The linter can see a warning that had already reached the repository. Ruff's select list had no `W`, so an invalid escape sequence — a Windows UNC path written without an `r` prefix — compiled to a `SyntaxWarning` nobody reads and would have failed a later release under `pytest -W error`. `W` is selected now, along with `B`, `C4`, `A`, `RET` and `PIE`; what is left out was measured rather than assumed and the reasoning is recorded in `ruff.toml`. A test also compiles every module and fails on any warning, so the guard does not depend on the linter's configuration.

## v0.9.0 - 2026-09-01

- `/status` is answered by every backend instead of one, and it now answers at all. It was listed under Claude Code alone, where it did not work either: Claude Code's own `/status` is interactive-only and replies "/status isn't available in this environment" when it arrives as a message through the headless mode BlindPilot drives it in, and Codex, FreeBuff and opencode have no status command whatever. Each is now asked in the way it can answer — `claude auth status`, which replies in JSON carrying the account, subscription, sign-in method and organisation; `codex login status`, which replies in a sentence; and, for the two with no command to ask, the credentials they stored, which is FreeBuff's account name and email and the providers opencode has connected. opencode's are read off disk rather than from its server, because `/status` should not be the thing that starts one, and a CLI that will not run at all is reported as that rather than as signed out. FreeBuff's stored token never reaches the report. The report then says what the tab will do with the next message: model, effort, permission mode, folder, and whether the next message continues this conversation or begins one.
- Chat mode reaches the tools OpenRouter runs itself. There are twelve — web search, web fetch, date and time, image generation, apply patch, shell, bash, fusion, advisor, subagent, tool search and model search — and a model that calls one has it executed at OpenRouter and the result handed back mid-answer, so nothing runs on this computer and nothing stops to ask permission. They are a checklist on the conversation profile, beside the thinking budget a reasoning model gets and a reader that turns an attached PDF into text any model can read rather than only the models that read one themselves.
- The thinking a reasoning model does is kept out of the answer it precedes. It arrives as its own History entry, and because that entry's line is read out whenever the arrow keys pass over it, the line says how many words there are rather than being the words; "Thinking" is spoken once when it starts, the text view holds it in full, and Ctrl+C copies it. It is not saved with the conversation, because only the answer is a message.
- A tool the model calls is spoken as it runs and left in History, down the same path a batch's progress already used, and the pages a searching answer cites are collected, deduplicated and written into the end of the answer as a numbered Sources list. A turn that ends asking for a tool the chat window cannot run names the tool and points at the matching OpenRouter one, rather than reporting that the provider returned no text.
- A conversation profile saved by an earlier release still opens. The chat database schema is written with `CREATE TABLE IF NOT EXISTS`, which leaves an existing table exactly as it is, so the column these settings live in is added with an `ALTER TABLE` on the way past; without it every read of the profiles table would have failed. A profile written by a newer release, or edited by hand, falls back field by field rather than refusing to open.

## v0.8.1 - 2026-09-01

- A Codex, FreeBuff, or opencode turn that crashes now says so. Their run loops caught nothing, and the `finally` that re-enables Send and stops the progress earcon ran either way, so a crashed turn was indistinguishable from a finished one except that the answer never arrived; the traceback went to a standard error the packaged windowed build does not have. Codex and FreeBuff also gained the once-guard opencode already had, so a turn that already explained itself and then failed on the way out does not speak a second error over the first.
- Every FreeBuff turn gave its pseudo-terminal back. Teardown was written as a fallback chain — terminate, and close only if terminate raised — so the close never ran and the handle FreeBuff was reached through, a ConPTY on Windows and pexpect's master file descriptor elsewhere, stayed open. Because this runs at the end of every turn, the handles accumulated for as long as the session lasted.
- Ten error messages are spoken instead of only written. They went to the status bar, which neither NVDA nor JAWS reads on its own, so a copy that failed was silent while a copy that worked announced itself — the only difference being a clipboard that still held what was there before. Steering with nothing running, stopping with nothing running, clipboard failures, and a failed code save all speak now, and still appear in the status bar.
- Enter sends the answer in the question dialog. The "type your own answer" box is built with `TE_PROCESS_ENTER`, which takes Enter from the dialog's default button and gives it to the box, where nothing was listening. This is the dialog that opens mid-run and holds the turn until it is answered, and the box is where focus lands after choosing "Other". Enter now applies the same validation the button does.
- A sign-in address is checked before it is opened. opencode hands back an address for the client to open, and it comes from a provider catalogue describing close to two hundred providers rather than from opencode itself. On Windows the platform opener is the default protocol handler, so `file:` would open whatever is at that path including one on a network share, and `search-ms:` or `ms-msdt:` would be handed to a separate program. Both sign-in paths now go through one opener that accepts only `http` and `https`, and an address that is refused is still spoken and shown so it can be opened by hand.

## v0.8.0 - 2026-09-01

- Added a screen-reader-named Mode combo box that switches the main window between the existing multi-session Agent experience and a new provider Chat experience.
- Removed Chat mode's crowded management row. Accounts, Conversation profiles, Refresh models, History view, and Diagnostics now live together in the Chat menu.
- Removed the Session combo box. Sessions are now navigated from a real tab strip: **Ctrl+Tab** and **Ctrl+Shift+Tab** move between them from anywhere in the window, Shift+Tab from the responses lands on the strip, and the arrow keys walk it. The strip is a native tab control, so NVDA announces the conversation name and "tab 2 of 4" itself instead of BlindPilot speaking a second, redundant description over it.
- Removed the same redundant custom focus speech from Permission mode. Prompt and read-only Responses focus hints are now VoiceOver-only, preserving their macOS workaround without duplicating NVDA's native Windows announcements.
- Separated the session tab strip from the pages it selects, and added pre-navigation boundary routing. The conversation pages live in a plain page container, so entering one no longer makes Windows announce "tab control"; the strip above stays a focusable native tab control. Agent mode now moves Mode → Session tabs → Responses → Prompt → actions → Permission mode → Mode (and the exact reverse with Shift+Tab), without transient "tab control" or "unknown" announcements. Empty Responses are skipped because they contain nothing to navigate. Prompt focus departure is briefly deferred so NVDA can finish its delayed formatting query while the native edit range is still valid.
- Integrated AccessibleAI's chat stack: OpenRouter, OpenAI, Claude, Gemini, Z.AI, Moonshot AI, Kimi, DeepSeek, OpenCode Go, and generic OpenAI-compatible accounts; securely stored API keys; account testing and model refresh; conversation profiles; streaming; editable history; file attachments; regeneration; OpenRouter cache controls and asynchronous batch models; and chat diagnostics. An existing AccessibleAI database and Credential Manager keys are imported once when Chat mode is first opened.
- Replaced the updater's sequence of prompts with one accessible, resizable dialog containing readable release notes, a named progress gauge, 10-percent progress announcements, cancellation with partial-file cleanup, checksum verification, and an explicit restart step. Startup checks can now be disabled and report an available update without stealing focus.

## v0.7.2 - 2026-09-01

- Claude Code runs stay open until every background agent started by the turn has finished. BlindPilot previously treated the parent turn's first result as the end of the whole run, closed Claude's input, and killed the CLI five seconds later, taking every helper agent and its unfinished work with it. The live view now announces how many agents remain and reminds the person that Stop Task can end the run immediately; helper narration remains visible while only Claude's own reply is collected as the final answer.
- Claude Code failures retain the information needed to understand them. Standard error is drained continuously so a full pipe cannot freeze the child, malformed UTF-8 is replaced instead of ending the reader, exceptions in the stream loop are reported, and an answer already produced is preserved when the CLI later exits non-zero. A turn that ends without a result leaves its exit details in `claude-worker.log` beside BlindPilot's settings.
- Sound cues can be turned off from **Options, Play sound cues**. The preference is remembered, muting stops a progress sound already playing, and cues remain enabled by default for existing behaviour.

## v0.7.1 - 2026-08-31

- A FreeBuff that starts and then paints nothing is reported in two minutes instead of being waited out for the whole hour a turn is allowed. FreeBuff 0.0.163 starts, connects, and then never draws a prompt to type into, on a bare terminal as readily as under BlindPilot; because the terminal neither died nor became ready, the message bought an hour of silence and was then reported unsent. Start-up is now bounded by how long FreeBuff goes without painting anything, so a first launch that is visibly downloading, unpacking, or offering the model picker is never cut off, while one that has stopped is reported in its own last words.
- FreeBuff's preferred model is now GLM 5.3 (`z-ai/glm-5.3-flash`). FreeBuff has dropped `deepseek/deepseek-v4-pro`, which BlindPilot preferred by default. This stays a preference rather than a requirement: the catalogue is still read out of the installed release at run time, a release without GLM 5.3 falls back to a model it does offer, and an explicit choice in BlindPilot still wins over both.

## v0.7.0 - 2026-08-28

- Linux announcements now reach Orca without stealing keyboard focus. BlindPilot creates a real off-screen GTK accessible through GTK's native API and emits ATK announcements through it, avoiding duplicate GTK initialization warnings while keeping status-bar text as the fallback when GTK is unavailable.
- macOS self-updates are recoverable. BlindPilot now rejects translocated or unwritable application bundles before closing, prevents concurrent updaters, records failures for the next launch, removes quarantine from verified updates, restores the previous application if replacement fails, and reliably reopens either the updated or restored copy.
- The opencode backend works on Python 3.13. Its event handler no longer collides with the private `_handle` attribute added to `threading.Thread`.
- POSIX login-shell PATH discovery is covered by a deterministic regression test, and release builds now compile the Linux accessibility bridge explicitly.

## v0.6.3 - 2026-08-28

- Every backend now starts with a PATH that can reach Node. An application opened from the macOS Dock inherits launchd's PATH and nothing else, and every provider CLI npm installs is a `#!/usr/bin/env node` shim, so a child handed that PATH died on "env: node: No such file or directory" before printing anything anyone could act on. FreeBuff's pseudo-terminal was the one place that passed no environment at all, which is why FreeBuff never ran on a Mac while the others did. The environment is now built once and carries the PATH a login shell would have given, and everything that starts a CLI uses it: the sign-in, the auth probes, the version checks, and npm itself.
- What we start is now stopped. Half of these CLIs are launchers rather than programs - npm's codex and freebuff are Node scripts running the real agent as a child - and killing the launcher left that child running, still holding its lock and still waiting on a sign-in nobody was completing. Children now start in a process group of their own and are stopped as one, and the group is only signalled when the child is demonstrably its leader, because a child still sitting in our own group would have us signal ourselves.
- A terminal that died during start-up reported a guess. It now reports its own last words, which is the only part of it anyone can act on.
- FreeBuff's model picker selected the right model again. FreeBuff has dropped deepseek-v4-pro, and a remembered model was offered whether or not the installed release still had it, so the picker was driven looking for a row that never appears and the message was lost after five seconds. Worse, the picker parsed three of five rows, because FreeBuff's display names disagree with its ids about where a version letter goes - `mimo/mimo-v2.5` is drawn "MiMo 2.5" - and navigation counts arrow presses as the distance between two positions in that list, so a missing row silently selected the wrong model for every model below it. A dropped model is no longer offered, and one that goes missing mid-run falls back audibly instead of costing the turn.
- The progress earcon plays once. It watched an event that the next turn cleared while the previous thread was still inside `wait()`, so one cue became several playing over each other - and a player that could not play the file at all spun spawning processes as fast as the machine allowed.
- The packaged smoke test now fails on macOS if AppKit did not make it into the bundle. AppKit is how anything is said to VoiceOver, and a build that packaged everything else and dropped it starts, runs, and is silent.
- Windows behaviour is unchanged: the process-group flags are empty there, the login shell is never asked, and the pywinpty spawn is untouched.

## v0.6.2 - 2026-08-28

- Sign In gets you signed in. BlindPilot ran `claude /login`, which is a slash command typed inside a session and not a command line: with no console to draw in it opened nothing, said nothing, and timed out five minutes later. Claude Code is now signed in with `claude auth login`. Codex's output, which carries the address to sign in on, was thrown away entirely; only FreeBuff was ever handled, and only as a special case. All of them now run through one sign-in that reads the CLI's output as it arrives, finds the address, speaks it, and makes sure it reaches the default browser — the CLI opens the page where it can, BlindPilot opens it where the CLI will not, and a new **Open Sign-in Page** button opens it again when the browser never appeared or was closed.
- Claude Code's prompt for the code the sign-in page hands back is written with no newline after it, so a line-by-line reader never sees it. Output is now read a character at a time, and the prompt opens a box to paste the code into that is passed straight to the CLI — and closes by itself when the browser finishes the sign-in without it.
- Codex announces its own callback server, `http://localhost:1455`, before it prints the page to visit, so the first address in its output was never the one to open. Loopback addresses are no longer mistaken for the sign-in page.
- Whether a sign-in worked is no longer taken on trust: when the CLI stops, the backend is asked whether it is signed in, so a success without a tidy exit code is recognised and a failure is not reported as success. Every line the CLI says on the way is spoken with colour codes and undecodable bytes stripped out, and the reason a sign-in failed is repeated word for word instead of "it did not complete".
- Closing the wizard, pressing Escape, or switching backends now stops a sign-in that is still running instead of leaving the process and its half-open browser behind. opencode's Connect a Provider says so out loud when a browser could not be opened, and hands over the address to open by hand.
- Fifteen tests drive the sign-in against transcripts taken verbatim from all three CLIs: the missing newline, the loopback address, the code written back to the CLI, the failure repeated word for word, the CLI that never finishes, and the one that asks for a code forever.

## v0.6.1 - 2026-08-27

- A conversation survives the questions it asked. Answering a question on the opencode backend could leave the conversation permanently broken: the provider refused the stored question step on every later request with "Invalid assistant message: content or tool_calls must be set", so every following message failed with the same 400 and no amount of retrying got past it. BlindPilot now recognises that refusal, deletes the broken step and everything after it, and sends the message again — the transcript row saying what was asked and answered stays, so nothing said is lost, and the conversation carries on instead of ending. A refusal with no question in the turn, or where the question was dismissed rather than answered, is still reported as the failure it is, and only one repair is attempted per turn.
- Four regression tests cover the repair: the broken step and the empty step it died on are deleted and nothing else, the same refusal with no question is only reported, a second refusal is reported rather than looped, and a dismissed question never triggers the surgery.

## v0.6.0 - 2026-08-27

- A backend that stops to ask a question is now answered rather than turned down. All four can pause a turn to put a multiple-choice question to the person driving them, and BlindPilot now opens a dialog for it: one radio button per answer where a single answer is wanted, checkboxes where several are, and an "Other" choice that opens a box to type an answer of your own. The answer goes back the way each backend expects it and the turn carries on; the transcript keeps a row saying what was asked and what was said.
- Each backend's own question format is handled natively. Claude Code's AskUserQuestion arrives on the permission channel of the stream BlindPilot is already reading, and headless Claude Code is now told the app can show a prompt - without that the tool is not offered at all, which is why Claude could never ask before. Codex's `request_user_input` is switched on for the app server BlindPilot starts and answered by question id. opencode's `question.asked` event is replied to instead of rejected. FreeBuff, which has no API, has its own question box read off the terminal and driven with the keys it understands.
- A question nobody answers is declined rather than left open. Closing the dialog tells the backend the question went unanswered, so a turn is never left waiting for an answer that is not coming - which sounds exactly like a model that has stopped thinking. Stopping a run closes an open question with it.
- FreeBuff reaches its composer again. FreeBuff no longer labels the model on its start screen "RECOMMENDED", and BlindPilot waited for that word before answering the chooser - so on current FreeBuff every message sat behind a start screen nobody could see, and the turn never began.

## v0.5.1 - 2026-08-20

- A clean computer can now install every backend from the setup wizard. Codex, FreeBuff, and opencode no longer stop at "npm was not found": BlindPilot downloads the current official Node.js LTS for that user, verifies its published SHA-256, installs the CLI into a writable per-user prefix, puts both on PATH, and proves the CLI starts before calling the install complete. FreeBuff's thin npm launcher is forced to download and verify its native binary during this check rather than surprising the first conversation with it.
- FreeBuff sign-in now opens the URL its CLI prints. FreeBuff intentionally does not open a browser itself, while BlindPilot hid the terminal and all of its output; the old wizard therefore waited five minutes for a sign-in page the user had never been shown.
- FreeBuff's current DeepSeek V4 Pro remains in the model picker. FreeBuff 0.0.152 changed its availability marker from `always` to `off_peak_only`; the binary catalog reader treated that as no model at all and quietly dropped the documented default.
- A non-empty but malformed or partial FreeBuff credential file no longer counts as signed in. The check now requires the device token and both fingerprint fields FreeBuff itself needs.

## v0.5.0 - 2026-08-20

- Sessions are real tabs. The window's session book is a native tab control now, so a screen reader announces "tab 2 of 4" and the name of the conversation in it, and **Ctrl+Tab** and **Ctrl+Shift+Tab** move between them from anywhere in the window — including from inside the prompt box. The session dropdown stays, for reaching tab 9 of 12 without stepping through the eight in between.
- A tab is named after the conversation in it, from the moment there is one. The first message names the conversation — the same title Recent Conversations lists it under — and that name goes on the tab. A tab whose conversation has not started yet, or that has just been cleared, falls back to its folder. Two tabs open on the same folder are no longer two tabs with the same name.
- Every backend starts fully automatic. Bypass-permissions is where a new tab starts and where the quick-cycle chord returns to, so a run never stops mid-task to ask for something nobody was watching for. An existing installation is moved onto it once, on the first launch after upgrading; a mode chosen in the picker afterwards is left exactly where it was put.
- Activity from a background tab no longer speaks over the tab being read. The check for "is this the tab in front" tested for a control the window did not use, so it never matched and every tab narrated at once.
- Arrowing along the tab strip keeps focus on the strip. Changing page used to move focus into the prompt, which meant the second arrow press never reached the tabs.
- Adding a session no longer sets the session dropdown to a row it does not have yet.
- The permission picker is greyed out from what a backend actually supports rather than from FreeBuff's name, and says so in its own words.

## v0.4.0 - 2026-08-19

- opencode is a backend, and it does everything the others do: streaming answers, steering a turn while it runs, stopping one, permission modes, compaction, and reopening a past conversation to carry on with it. It is picked from File → Backend like the rest, and installed, updated, and signed into from the same wizard.
- It is driven through the headless server its own terminal interface talks to, rather than through one process per turn. BlindPilot starts one server, shared by every tab, on the loopback interface behind a password generated for that run, and reads each turn off its event stream. That is the only surface that exposes everything at once, which is why nothing about opencode had to be left out.
- `/model` covers every model opencode can reach, named `provider/model`, with the reasoning variants each one offers. The list is read per directory, because a project's own `opencode.json` can pin a model or turn providers off, and an effort a model does not offer is never sent with it.
- `/connect` is opencode's own command, carried over as a dialog: every provider it knows, the connected ones first, with an API key or a browser sign-in depending on what the provider offers. Providers that need an account id or a self-hosted address ask for it in opencode's own words. It is also the Connect a Provider step in the setup wizard, because opencode's command-line sign-in is a terminal prompt nobody using a screen reader can answer.
- Permission modes reach opencode as rules it enforces rather than as instructions to a model: plan mode selects opencode's own plan agent and denies edits outright, accept-edits allows edits while a shell command keeps the normal safeguard, and default leaves opencode's own configuration alone. A request that still needs answering is answered from the mode, and a question opencode stops to ask mid-turn is declined and reported — unanswered, it would hold the turn open for good.
- opencode's own slash commands are run as commands rather than typed at the model: the picker offers whichever ones the current directory has — its built-in `/init` and `/review`, plus anything the project defines — and a `/name` it does not recognise is left alone, so a sentence that happens to start with a slash is still a sentence.
- Past opencode conversations are read straight out of its SQLite database, read-only, titled by their first message where opencode has not titled them itself. Subagent conversations are left out of the list: nobody had those.
- Steering an opencode turn is answered from what the window already knows and delivered on a thread of its own. It used to wait on a request, and on the window's own thread — a window that stops answering is a window a screen reader cannot describe.
- Every conversation opencode has had in a directory is offered, not just the ones among the most recent few hundred. Its conversations for every directory share one database, so a limit on rows read was a limit on how far back this project's history went.
- opencode's sign-in check no longer reads any `*_API_KEY` in the environment as a signed-in opencode. Plenty of programs set one and have nothing to do with opencode; a confident yes that turns into a wall at the first message is worse than an unconfirmed you can walk past.

## v0.3.14 - 2026-08-12

- Nothing here changes what BlindPilot does. A type checker was run over the three modules that ship, and the 32 things it objected to were settled — most of them objects passed around unnamed because they come from more than one library, which now say what is asked of them.
- Two were worth the trouble on their own: the macOS announcement read names that exist only on macOS, with nothing on Windows saying so, and could take the application down with it if it failed; and the sign-in helper had a timeout handler that could be reached before the process it kills exists. Neither could happen as the code stood. Both now say so where they are written, rather than leaving it to be worked out.

## v0.3.13 - 2026-08-12

- Upgrade over an installed copy when the setup program is run by hand, which stopped on "DeleteFile failed; code 5" while the same upgrade run from inside BlindPilot went through. Only the in-app updater asked the installer to close a program that will not close on request; run by hand, the installer asked politely, and a background program holding one of our libraries — with no window to close and nobody watching it — never answered. The setup program now closes what refuses to close however it was started.

## v0.3.12 - 2026-08-12

- Install updates on an installed copy, which since 0.3.10 ended in the installer stopping and reporting nothing but "code 5". BlindPilot handed its own library folder to every program it started, and to everything those started in turn, so an agent CLI or a tool it left running went on holding BlindPilot's libraries open for hours. The installer found its files in use, asked those programs to close, and — with its message boxes suppressed, as a silent update requires — silently answered Abort and rolled the update back.
- Keep the packaged library folder off the environment BlindPilot hands its children, so nothing outside the application loads out of the install folder in the first place.
- Notice a program that has one of our libraries loaded, not only one that runs from the install folder, and close it before the installer has to. The old check looked at where a process runs from and so found none of the programs that were actually holding files.
- Let the installer close what refuses to close, so one stubborn program can no longer abort an update.
- Check that every file can be opened before starting the installer, as the portable update already did.
- Say what went wrong when an installed update fails, rather than reading out the installer's exit code, and keep the installer's own log beside the reason.
- Stand aside when a second BlindPilot window starts an update at the same time, instead of two installers racing over one folder.

## v0.3.11 - 2026-08-12

- Stay in the responses when Down is pressed on the newest row, in the list and in the read-only edit field alike, rather than dropping focus into the prompt. Tab is the way to the prompt.
- Keep the row you are reading when streamed output rebuilds the list, which on Windows clears the selection.
- Apply backend output in small batches that yield to keyboard and screen-reader events, so a long, chatty job no longer leaves arrow keys unanswered, and redraw the list once per batch.
- Keep FreeBuff on DeepSeek V4 Pro. FreeBuff dated the model's name ("DeepSeek V4 Pro 08/13") without dating its documentation, so BlindPilot dropped Pro from the model list and fell back to FreeBuff's own setting, which FreeBuff rewrites to Flash after every turn.
- Re-apply the chosen FreeBuff model before replacing a terminal mid-message, since the terminal being replaced rewrites that setting as it exits.

## v0.3.10 - 2026-08-10

- Install updates at all. No update since 0.3.0 has ever been applied: the helper that does the work was started in a way that made Windows PowerShell exit without running it, and report success while doing so.
- Replace the contents of the installed folder rather than renaming the folder, so a shortcut, a sync client, or the application's own working directory cannot block an update.
- Wait for everything running out of the installed folder, not just the one process that asked for the update, and confirm each file can be opened before replacing it.
- Retry a move that leaves files behind, which is what a virus scanner reading a freshly written file causes.
- Put the previous version back, by copy rather than by move, if anything goes wrong part way through, and keep the backup.
- Say why an update failed. An update finishes after BlindPilot has closed, so the reason is written down and read out at the next start, with the path to a log.
- Update an installed copy into its existing location, and restart it even when the installer reports a problem.
- Never restart BlindPilot with the installed folder as its working directory, which is what stopped the following update from replacing it.
- Delete abandoned update downloads at startup; a failed update used to leave tens of megabytes behind every time.

## v0.3.9 - 2026-08-10

- Reopen a past conversation from any backend and carry on with it, from File, Recent Conversations (Ctrl+H).
- Title every past conversation by the message that started it, so two in the same folder can be told apart when read aloud.
- Filter the conversation list as you type, and widen it to every folder or every backend.
- Rebuild a reopened conversation into the same navigable rows a live one produces, and name its tab after its first message.
- Leave out the context each CLI writes into its own transcript, so a plugin listing or an environment block never becomes a title.
- Compact a conversation in place from File, Compact Conversation (Ctrl+Shift+K), through Claude Code's own command and through Codex's app-server request.
- Start a fresh conversation in the current tab from File, Start New Conversation (Ctrl+Shift+N).
- Say plainly that FreeBuff cannot compact, and grey the command out, instead of failing silently.
- Correct the FreeBuff slash-command list to the commands FreeBuff actually has.

## v0.3.8 - 2026-08-09

- Read a FreeBuff answer off its own screen as it is written, a finished sentence at a time, instead of waiting out the whole reply in silence.
- Read out from the saved chat anything that scrolled away before it could be spoken, so a long answer is heard in full and never twice.
- Keep a FreeBuff terminal waiting so a message reaches it in well under a second rather than several.
- Remember FreeBuff's model catalogue between runs instead of reading it out of the installed application on the way to sending.

## v0.3.7 - 2026-08-09

- Claim BlindPilot's console at startup, hidden and parked off screen, so creating a terminal has none left to put on screen.

## v0.3.6 - 2026-08-09

- Hide the console a pseudo-terminal attaches to BlindPilot, for the whole run and its shutdown, so a FreeBuff turn puts nothing on screen.
- Update an installed copy by running the new installer silently, instead of replacing the program directory and deleting its own uninstaller.
- Offer a desktop shortcut in the installer, and add File, Create Desktop Shortcut for copies that were unpacked rather than installed.
- Read reasoning as ordinary text, without "Thinking" in front of it.

## v0.3.5 - 2026-08-09

- Ship the console host FreeBuff needs, so FreeBuff runs in the packaged application at all.
- Stop stray command windows appearing, which was Windows falling back to a real terminal when the console host was missing.
- Report a terminal that closes before FreeBuff is ready, instead of waiting for output that can never arrive.
- Leave the backend's own reasoning out of the activity by default, with an Options setting to put it back.

## v0.3.4 - 2026-08-09

- Run Claude Code without a terminal window, which had been appearing for the whole turn and taking focus from the screen reader.
- Run every backend helper without one too: sign-in checks, model and version queries, and the Codex app server.
- Add a Stop button beside Steer, and File, Stop Task (Ctrl+period), enabled only while a task is running.
- Keep what a stopped task already produced as that turn's response, and report "Stopped" rather than an error.
- Show a FreeBuff session's second and later answers, which had been replaced by the previous turn's answer.
- Keep FreeBuff on DeepSeek 4 Pro, which FreeBuff had been silently downgrading by rewriting its own settings after every turn.
- Drop the interruption marker FreeBuff leaves behind when a session closes, instead of reading it out as part of the answer.
- Add a finished answer to the list on completion when it never arrived as live activity, for every backend.

## v0.3.3 - 2026-08-09

- Force the main window through the normal shutdown path after an update is verified, so the application releases its files before they are replaced.
- Stage a complete new installation, wait for the old process to exit, swap directories, then launch and check the new version.
- Restore and reopen the previous version if replacement or startup fails, instead of leaving a partial installation.
- Remove obsolete files from older builds as part of the full-directory swap.
- Report installer startup failures through the accessible update error dialog.

## v0.3.2 - 2026-08-09

- Keep the selected FreeBuff model by navigating its runtime picker to it, rather than accepting the Flash model it recommends.

## v0.3.1 - 2026-08-09

- Report agent and tool activity, plus a heartbeat every thirty seconds, so a long FreeBuff task no longer appears frozen.
- Read FreeBuff's structured live chat state for accurate reasoning and answers, keeping terminal parsing as a fallback.
- Detect FreeBuff completion from its authoritative per-chat log rather than from ambiguous terminal redraws.
- Save a new FreeBuff session id immediately, so interrupted work can be resumed.
- Stop mistaking advertisements and terminal tool cards for the assistant's answer.
- Stop narrating text twice when switching from terminal fallback to structured data.

## v0.3.0 - 2026-08-09

- Claude Code, Codex, and FreeBuff backends, with matching conversation features across all three.
- Runtime model discovery for every backend, with FreeBuff preferring DeepSeek 4 Pro.
- Automatic screen-reader narration after a message is submitted.
- Silent-until-response mode, which stays quiet until the complete answer is ready.
- Secure GitHub release updater with SHA-256 verification.
- Lazy model discovery, avoiding a large CPU spike at startup.
