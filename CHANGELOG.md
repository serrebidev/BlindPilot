# Changelog

Release history for BlindPilot, newest first. Entries are short by design. The reasoning behind each change is in the commit messages.

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
