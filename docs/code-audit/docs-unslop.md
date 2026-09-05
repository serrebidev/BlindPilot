# Documentation audit: debloat and de-AI the prose

Read-only pass, 2026-09-04. Nothing outside this file was changed. Line numbers refer to the files as they stand at commit 8320abf. Every proposed rewrite below is a proposal; approve, edit, or reject each before anything is applied.

The short version. The README and CHANGELOG are written well sentence by sentence, but they are written like essays. Every fact is wrapped in the story of how it was found, and every second sentence carries an em dash. A screen reader user hears all of it. The CHANGELOG is 19,165 words for 238 entries, an average of 80 words per bullet; the four Unreleased bullets alone run 692 words. The README is 3,104 words and has drifted from the code in seven places. AGENTS.md is not contributor guidance at all; it is a memory index pointing at another user's home directory.

## 1. Verdict per file

| File | Verdict | Why | Lines now | Lines after |
|---|---|---|---|---|
| README.md | rewrite | Seven stale facts, 18 em dashes, duplicated Telegram and credit sections, three paragraphs that narrate reasoning instead of stating behaviour | 247 | about 150 |
| CHANGELOG.md | shrink | Entries average 80 words; the recent ones average 300. Target 30 to 60. No v0.21.3 section although APP_VERSION is 0.21.3 | 421 lines, 19.2k words | same line count, about 7k words if the whole history is compressed; about 12k if only v0.20.0 onward |
| RELEASE_NOTES.md | rewrite | Two of five paragraphs are process narration and license boilerplate | 19 | 9 |
| CREDITS.md | rewrite | Stale backend list (no opencode, Hermes, Chat), "gratefully credits", noun-pile lists | 32 | 20 |
| NOTICE | keep | Three plain sentences. Nothing to fix | 8 | 8 |
| AGENTS.md | delete or replace | A machine-generated memory index for `C:\Users\admin\.claude`, a path that does not exist on this machine. Not instructions for anyone | 15 | 0, or 20 if replaced with real contributor notes |
| docs/superpowers/specs/*.md | shrink or delete | Design rationale for the pool. The measurements and decisions (about 60 lines) are worth keeping; the other 270 are argument | 331 | 0 or 60 |
| docs/superpowers/plans/*.md | delete | A task-by-task build script for code that is now in `backend_pool.py` and `tests/test_backend_pool.py`. 0 of 66 checkboxes ticked, plans 2 to 5 never written | 1959 | 0 |
| installer/BlindPilot.iss | keep, trim one comment | User-visible strings are fine. The `CloseApplications` comment at lines 39 to 47 is a nine-line essay for a one-line setting | 72 | 66 |
| blindpilot_app.py strings | fix 20 | Five stale menu references, about 40 em dashes read aloud, one exclamation-mark wizard page. See section 5 | n/a | n/a |

A note on em dashes for this project in particular. NVDA at the default punctuation level says nothing for an em dash, so "Off — the Hermes backend runs the copy installed here" is heard as "Off the Hermes backend runs the copy installed here". At higher punctuation levels it says "em dash" out loud. Either way a period is better, and in the app strings this is not a style point but a comprehension one.

## 2. README.md

### Hits, with line numbers

Stale against the code. These are the important ones.

- README.md:131 "sign into any of the four" and README.md:161 "All four answer it". There are five backends (agent_backends.py:423-479). Line 33 already says five.
- README.md:98 and README.md:100 say Hermes never asks questions ("Questions: No", "Hermes' gateway protocol has no such request, so its turns never pause to ask"). v0.20.8 added `clarify` handling; see hermes_worker.py:240 `_clarify_questions` and CHANGELOG.md:37.
- README.md:71 "Up from the prompt's first line enters the newest response." The code requires Ctrl+Up or Alt+Up (blindpilot_app.py:5881) and the comment there says bare Up was removed on purpose. CHANGELOG.md:91 records the change.
- README.md:45 lists the Model menu without "Session Status…" or "Backend Settings…" (blindpilot_app.py:9707-9765; both added in v0.19.0).
- README.md:47 lists the Options menu without the three Working sound radio items, "Working sound interval…", or "Remote Hermes…" (blindpilot_app.py:8814-8840).
- README.md:41 lists the File menu without "Hermes Conversations… (Ctrl+G)" (blindpilot_app.py:9798), although line 61 mentions the chord.
- README.md:3 says "Windows and macOS", but lines 127 and 204 give paths for "elsewhere", `linux_accessibility.py` exists, and CI runs the suite under xvfb. Either Linux is supported and the first line should say so, or the Linux paths belong in a footnote. Question 3 below.
- README.md:25 "Runs every backend fully automatic by default. Nothing stops mid-task to ask permission." Line 26 then describes answering the questions a backend stops to ask, and the Permission Mode default is "Default". The two lines contradict each other as a reader hears them. Question 4.

Style hits.

- Em dashes on lines 19 (a hyphen used as one), 41, 43, 45, 47, 49, 51, 55, 84, 102, 106, 121, 152, 163, 168, 181, 194, 204, 241. Eighteen in total.
- Bold-label-dash lists at 41 to 51. Six menu paragraphs each open with a bold word and an em dash.
- Line 8, "the fastest place to get help", is promotional and unverifiable. Line 247 says the same thing without the pitch. Keep one.
- Line 10 and lines 241 to 243 both credit Claude Code Reader in full. Line 243 adds "The name changed and the backends multiplied, but the origin has not been erased", which is a sentence about a feeling.
- Line 55 "so this is a choice offered rather than a cleverness applied" and line 163 "reporting that as a failed sign-in would be a lie". Flourishes. The mechanism is already stated in the sentence before each.
- Line 102 is one paragraph of 190 words and eight clauses about FreeBuff, three of them explaining a bug that was fixed. A user needs two of those sentences.
- Line 104 "That is what gives opencode everything the others have" is a claim about a feeling of completeness, not a fact.
- Line 161 "even though none of them has a status command that works this way". Trivia about the implementation, not something the user acts on.
- Line 208 "at any level, for any reason. What BlindPilot did is recorded; what you said is not." The first sentence already said it.
- Line 225 is a 96-word sentence with a colon as a hinge. Split.
- Lines 110 to 111 and 245 to 247 repeat the Releases and Issues links.
- Line 3 "vibe-coded". That is the maintainer's voice, not a slop pattern, but it is an odd first word for a user who wants to know whether the app is safe to run. Question 1.

### Proposed replacement

Every fact a user needs is kept. Stale facts are corrected as noted above. Where I could not verify a claim cheaply I kept the original wording and flagged it in section 6.

````markdown
# BlindPilot

A screen-reader-first desktop front end for AI coding CLIs. It runs Claude Code, Codex, FreeBuff, opencode, and Hermes in native wxPython windows, so NVDA, JAWS, and VoiceOver read controls instead of a terminal. Windows and macOS.

[![Join SerrebiProjects on Telegram](https://img.shields.io/badge/Telegram-SerrebiProjects-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/SerrebiProjects)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

Questions, bugs, and release news go to the [SerrebiProjects Telegram group](https://t.me/SerrebiProjects). Bug reports and feature requests also go in [Issues](https://github.com/serrebidev/BlindPilot/issues).

BlindPilot started as a fork of [Claude Code Reader](https://github.com/doubletaponair/claude-code-reader) by doubletaponair and keeps its accessibility design. See [CREDITS.md](CREDITS.md).

## What it does

- Runs five coding agents, picked from Model, Backend. The choice is remembered.
- Splits every answer into rows you can arrow through, one per heading, paragraph, list item, quote, code block, thought, tool call, and tool result.
- Reads answers aloud as they stream, or stays silent until the whole answer is in.
- Speaks every step of a run, or only your message, the answer, and status changes. See Narration below.
- Reopens past conversations from any backend and continues them. Compacts a long one in place.
- Runs several sessions at once, one tab each, with its own folder, model, and permission mode.
- Answers the multiple-choice questions a backend stops to ask, in one dialog with radio buttons, checkboxes, and an Other box.
- Lets you steer a running task with a new message, or stop it and keep what it produced.
- Attaches files and pasted images.
- Searches responses and copies a code block, a response, or the whole conversation.
- Lists the models and effort levels the installed CLI reports.
- Plays optional sounds for sent, working, received, and failed.
- Installs, updates, adds to PATH, and signs in to any backend from a wizard.
- Has a Chat mode that talks to a provider API directly with no agent and no file access.
- Drives a Hermes on another computer over the network.
- Updates itself from GitHub Releases after checking the published SHA-256.
- Logs what it did, never what you or the model said.

## Install

Downloads are on the [Releases page](https://github.com/serrebidev/BlindPilot/releases). Version history is in [CHANGELOG.md](CHANGELOG.md).

Windows installer. Download `BlindPilot-Setup-x64.exe` and run it. It installs per user with no administrator prompt, adds a Start Menu entry, and closes a running copy before replacing it.

Windows portable. Download `BlindPilot-Windows-x64.zip`, extract it anywhere, run `BlindPilot.exe`.

macOS. Download `BlindPilot-macOS-arm64.zip` for Apple Silicon or `BlindPilot-macOS-x64.zip` for Intel. The builds are ad-hoc signed and not notarized, so the first launch may need approval in System Settings, Privacy & Security.

Settings live in `%APPDATA%\BlindPilot\config.json` on Windows, `~/Library/Application Support/BlindPilot` on macOS, and `~/.config/blindpilot` on Linux. On macOS, settings from an older version are moved to the new folder once; nothing already there is overwritten. An existing Claude Code Reader configuration is imported once and never modified.

## Set up a backend

The first-run wizard and Model, Manage Backends find, install, update, and sign in to any of the five backends. Claude Code and Hermes use their own installers. Codex, FreeBuff, and opencode come from npm; BlindPilot installs Node.js LTS if npm is missing, installs the CLI into a per-user folder, adds it to PATH, and checks that it starts. No administrator rights are needed.

To do it by hand:

```powershell
# Claude Code
claude --version
claude auth login

# OpenAI Codex
npm install -g @openai/codex
codex login

# FreeBuff
npm install -g freebuff
freebuff login

# opencode
npm install -g opencode-ai
opencode providers login

# Hermes Agent, see https://hermes-agent.nousresearch.com/docs
hermes status     # shows the provider and model it will use
hermes model      # pick one, if none is set yet
```

Sign In in the wizard runs the backend's own login, reads the sign-in address from its output, speaks it, and opens your browser. Open Sign-in Page opens it again. If the provider hands back a code, BlindPilot asks for it and passes it to the CLI. Hermes is different: its setup asks questions interactively, so Sign In opens a real terminal window for it. Answer the questions there, then choose Already Signed In.

opencode needs a provider connected to it. Use Model, Connect a Provider, or type `/connect`, or use the wizard. Pick a provider, paste a key or sign in through the browser.

Type `/status` (or Model, Session Status) to hear the backend, model and effort, permission mode, folder, whether the next message continues this conversation, and which account the backend is signed in as.

## Menus

Every action is in the menu bar. Only two things are keyboard-only: Ctrl+L focuses the prompt, and Ctrl+1 to Ctrl+9 jump to a tab.

File. New Session (Ctrl+T), Recent Conversations (Ctrl+Shift+H), Hermes Conversations (Ctrl+G, Hermes only), Side Chat in This Folder, Next and Previous Session, Set Projects Folder, Create Desktop Shortcut, Close Session (Ctrl+W), Quit (Ctrl+Q).

Conversation. Stop Task (Ctrl+.), Attach Files (Ctrl+Shift+A), Slash Command (Ctrl+/), Compact Conversation (Ctrl+Shift+K), Start New Conversation (Ctrl+Shift+N), Find in Responses (Ctrl+F), Jump to Latest Response (Ctrl+R).

Model. Backend (one radio item per CLI), Model and Effort (Ctrl+Shift+E), Permission Mode (Default, Accept edits, Plan, Auto, Don't ask, Bypass permissions), Session Status, Backend Settings, Manage Backends, Connect a Provider.

Options. Show live activity in the list, Speak activity aloud, Include the backend's reasoning, Play sound cues, Narration (Follow everything, Keep up), Sounds (Message sent, Working, Answer received, Something went wrong), Responses as a read-only text field, Silent until the response mode, Working sound (continuous, every few seconds, off) and its interval, Remote Hermes, Preferences. On macOS, Preferences is in the application menu on Cmd+, as in every Mac app.

Chat. Accounts, Conversation profiles, Refresh models, History view, Diagnostics. Enabled only when the Mode combo box is set to Chat.

Help. Check for Updates, Check for updates at startup, Open Log Folder, About BlindPilot.

Backend, Permission Mode, and Narration are radio items, so a screen reader reports them as exclusive choices. Compact Conversation and Connect a Provider are greyed out for backends that have no equivalent.

### Narration

Follow everything, the default, speaks every tool call, result, and subagent line in order. Keep up speaks your message, the answer, and BlindPilot's own status lines (why a run is waiting, how it ended). The tool steps still appear in the list; they are just not spoken. Use Keep up when a run fans out into many parallel steps and the speech queue falls behind. BlindPilot cannot shorten the screen reader's own queue, so this is the control it can offer.

## Keyboard

- Ctrl+L focus the prompt. Ctrl+T open a session. Ctrl+W close it.
- Ctrl+Shift+H reopen a past conversation. Ctrl+G list Hermes conversations, including running ones.
- Ctrl+Shift+K compact this conversation. Ctrl+Shift+N start a fresh one.
- Ctrl+F search responses. Ctrl+R jump to the latest.
- Ctrl+Shift+E choose model and reasoning effort.
- Ctrl+/ slash commands. Ctrl+. stop the running task.
- Ctrl+Shift+A attach files. Ctrl+Shift+M cycle permission modes.
- Ctrl+Tab and Ctrl+Shift+Tab move between tabs, as do Ctrl+Shift+] and Ctrl+Shift+[. Ctrl+1 to Ctrl+9 jump to a tab. On macOS use Cmd+Shift+] and Cmd+Shift+[, because Cmd+Tab belongs to the system.
- Ctrl+Up (or Alt+Up) from the prompt enters the newest response. Shift+Tab also reaches the responses. Inside the responses, arrow keys stay in the list; Tab returns to the prompt.

On macOS the Ctrl chords are Cmd. Two chords differ from what you might expect everywhere, so that macOS does not swallow them: Recent Conversations is Ctrl+Shift+H (Cmd+H is Hide), and Model and Effort is Ctrl+Shift+E (Cmd+M is Minimize).

## Backends

| Backend | How BlindPilot talks to it | Model and effort | Permission modes | Compaction | Asks questions |
|---|---|---|---|---|---|
| Claude Code | Streaming JSON CLI | Yes | Yes | Yes | Yes |
| Codex | app-server protocol, one shared process | Yes, with reasoning effort | Yes | Yes | Yes |
| FreeBuff | Hidden pseudo-terminal | Model yes, effort no. GLM 5.3 Flash by default | Managed by FreeBuff | No | Yes |
| opencode | Its headless HTTP server, one shared process | Yes, with per-model reasoning variants | Yes | Yes | Yes |
| Hermes | Gateway JSON-RPC over a local pipe or the network | Model yes, effort no | Yes | Yes | Yes |

FreeBuff has no JSON or headless API, so BlindPilot runs its terminal interface in a hidden pseudo-terminal and reads the answer off the screen a sentence at a time. Redraws and advertisements are filtered out. If you send a message before FreeBuff has finished starting, BlindPilot holds it and says so, then sends it when the session is live.

opencode runs as one server shared by every tab, on loopback, behind a password generated for the run. Past conversations are read from opencode's own database, read-only.

Hermes answers stream a sentence at a time. One connection is kept for the whole conversation. Hermes' reasoning channel carries a terminal spinner rather than reasoning, so that is filtered out.

### Hermes on another computer

With Options, Remote Hermes off, BlindPilot runs the Hermes installed here, including one installed in WSL.

For a Hermes on the same computer bound to localhost, a session token is enough:

```bash
HERMES_DASHBOARD_SESSION_TOKEN=pick-a-long-random-string hermes serve --port 9119
```

For a Hermes reachable from other machines, Hermes requires a login before it will bind to a public address. Configure one on the machine running Hermes:

```bash
hermes config set dashboard.basic_auth.username your-name
# Hermes prints the hash to store; run this from its own installation:
python -c "from plugins.dashboard_auth.basic import hash_password; print(hash_password('your-password'))"
hermes config set dashboard.basic_auth.password_hash 'the-hash-it-printed'
hermes serve --port 9119 --host 0.0.0.0
```

Then choose Username and password in Remote Hermes. Hermes issues a 30-second single-use ticket for each WebSocket connection; BlindPilot logs in and fetches one itself each time it connects. Test connection checks the address and credentials before anything is sent.

`websocket-client` is only needed for the remote path. If it is missing, BlindPilot names it as an installable package and keeps running.

## Chat mode

Chat talks to a provider's API directly. No CLI, no agent, no file access.

Set the Mode combo box to Chat, add a provider and key under Chat, Accounts, then Chat, Refresh models and pick one. Supported providers are OpenRouter, OpenAI, Claude, Gemini, Z.AI, Moonshot AI, Kimi, DeepSeek, OpenCode Go, and any OpenAI-compatible endpoint. Keys go in the OS credential store.

Conversation profiles hold a system prompt, default account and model, temperature, token limit, and streaming preference. History view switches between a native list and a read-only edit field. Provider logs are under Chat, Diagnostics.

OpenRouter accounts also get multiple attachments, cache-aware regeneration, `:batch` model ids, OpenRouter's server-side tools (web search, web fetch, date and time, image generation, apply patch, shell, bash, fusion, advisor, subagent, tool search, model search), and thinking controls. Tools run on OpenRouter's servers, not your computer. Thinking effort sets how long a reasoning model thinks; Send the thinking back decides whether the thinking text is returned. Thinking arrives as its own History entry with a length line first. Read attached PDFs with converts a PDF to text for models that cannot read PDFs.

Chat data lives in `chat.sqlite3` beside the config. An existing AccessibleAI database is imported once and left unmodified.

## Logs

BlindPilot writes a rotating `blindpilot.log` and a `blindpilot-crash.log` for native crashes. Help, Open Log Folder opens the folder: `%LOCALAPPDATA%\BlindPilot\Logs` on Windows, `~/Library/Logs/BlindPilot` on macOS, `$XDG_STATE_HOME/blindpilot` on Linux. At most four files of one megabyte.

The level is INFO. Set `BLINDPILOT_LOG_LEVEL=DEBUG` for a bug report. Prompts, answers, file contents, and credentials are never logged at any level. On Windows the crash log also records first-chance COM exceptions from screen-reader interop; those are noise, not crashes.

## Updates

Help, Check for Updates asks GitHub Releases for a newer version, downloads it, verifies the published SHA-256, and restarts into the installer. Check for updates at startup does the same quietly and only speaks when there is something new. Builds run from source open the release page instead.

## Run from source

1. Install Python 3.10 or newer. Releases are built with 3.12.
2. `pip install -r requirements.txt`
3. `python blind_pilot.py`

`blind_pilot.py` is the entry point; the code is in `blindpilot_app.py`. `claude_reader.py` is a compatibility alias for the original application's name.

## Build

```powershell
python -m pip install -r requirements-build.txt
pyinstaller BlindPilot.spec
```

`BlindPilot.spec` reads the version from `APP_VERSION` and carries the bundle identifier, minimum macOS version, and icon (`tools/make_icon.py` generates the icon files into `packaging/`). The one-directory layout is what lets the updater replace the app after it exits. The Windows installer is `installer/BlindPilot.iss`. Pushing a `v*` tag runs `.github/workflows/release.yml`, which runs the tests and a startup check, then publishes the Windows installer, the Windows zip, both macOS zips, and their SHA-256 files.

Before opening a pull request:

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

Pull requests are welcome.

## License and credits

MIT. See [LICENSE](LICENSE). Every source file carries an `SPDX-License-Identifier: MIT` header.

Copyright (c) 2026 doubletaponair and BlindPilot contributors. Claude Code Reader is credited in this README, the About dialog, the source headers, [CREDITS.md](CREDITS.md), and the original specification kept at [`original-claude-code-reader-spec.html`](original-claude-code-reader-spec.html).
````

Two things in that draft I changed beyond wording and want called out. The Build section now says `pyinstaller BlindPilot.spec` instead of the long command line at README.md:222, because line 225 says the spec is what both platforms build from; if the long form is still what the workflow runs, keep the original. And I added `python -m mypy` to the pre-PR checks because CI runs it (.github/workflows/ci.yml:118).

## 3. CHANGELOG.md

### Hits

- CHANGELOG.md:9, 11, 13, 15. The four Unreleased bullets are 692 words. Each opens with a good one-line summary and then explains the bug's history, the tradeoffs, and the exact wording of two announcements. The first sentence of each is the entry; the rest is a commit message.
- CHANGELOG.md:19 (286 words), 23 (352), 27 (344), 31 (304). Same shape. Line 23 ends with "which is its own kind of lesson", line 27 with "so Finder's Get Info describes a finished product". Both are about how the author felt.
- Em dashes on 96 of 421 lines.
- CHANGELOG.md:296 "That is the only surface that exposes everything at once". "Surface" as a metaphor noun.
- CHANGELOG.md:303, 319, 337. "not just", "not only", "not just".
- CHANGELOG.md:41 and 45 each open with a "live audit" narrative before the change. CHANGELOG.md:23 too.
- Test counts as closing lines ("Fifteen tests", "Eight tests", "Four tests, failing-first") at 19, 31, 41, 53, 55. Fine in a commit; noise in a changelog a user reads to decide whether to update.
- There is no v0.21.3 section. blindpilot_app.py:285 says `APP_VERSION = "0.21.3"`, RELEASE_NOTES.md is titled 0.21.3, and commit 211f601 is "Release 0.21.3". The Unreleased bullets (Codex pool) are newer work, so 0.21.3 needs its own section between them and v0.21.2.
- CHANGELOG.md:85 says Ctrl+H for Recent Conversations. Historically accurate for v0.20.0, and v0.21.0 records the change, so leave it.

### Target density

One to three sentences per entry. First sentence says what changed from the user's side. Second says why, only if the why is not obvious. Third, if any, names a caveat or a new setting. No test counts, no investigation story, no quoted announcements unless the wording is the change.

### Proposed rewrite: Unreleased and the newest sections

````markdown
## Unreleased

- Codex keeps one app server running for the whole window instead of starting a new process for every message. The first message starts it, later messages and other tabs reuse it, and it is stopped when BlindPilot quits. Each turn no longer waits for Codex and its tool servers to cold-start.
- Stop now interrupts only the current turn. Before, it killed the Codex process, which with a shared server would have ended every tab's conversation. If Codex does not confirm the interrupt, only this tab's conversation is dropped and the next message resumes it.
- Escape pressed right after Send, before Codex has named the conversation, now prevents the turn from starting instead of letting it run under a tab that says it stopped.
- A backend idle for fifteen minutes is closed to free memory, and BlindPilot says so: "Codex was idle and has been closed. The next message will restart it." Idle means no turn running or waiting on a question, and the clock starts when the last message finishes.
- A backend that crashed or was killed while BlindPilot was open is restarted with an announcement ("Codex had stopped running. Restarting it, which takes a moment.") instead of a silent several-second wait.

## v0.21.3 - 2026-09-04

- FreeBuff messages sent during or just after startup no longer get stuck. On macOS and Linux the terminal is read from launch, so an unread buffer cannot stall startup; sending adopts the terminal already being started and cancels stale delayed starts.
- Turn completion and session drops are now detected from FreeBuff's log events, so a word in your prompt or its answer can no longer end a turn early or release a held message.

## v0.21.2 - 2026-09-04

- FreeBuff's normal startup line ("session over; holding queued messages until rejoin") no longer fails the turn. 0.21.1 treated it as a dropped session, which made every first message after launch fail one second in. The message is now held until the log shows FreeBuff has reconnected, with a one-time "FreeBuff is still starting; holding the message until it is ready".
- A session drop seen mid-turn is watched for thirty seconds for FreeBuff's automatic rejoin before the turn is failed.

## v0.21.1 - 2026-09-04

- FreeBuff 0.0.168 changed its welcome screen, so BlindPilot's model picker was choosing the wrong card and running GPT-5.6 Luna when GLM 5.3 Flash was selected. The picker is now read by position and navigation counts real steps.
- A FreeBuff session that logs "session over" and never answers is now reported, with the remedy (quit and reopen FreeBuff, then resend), instead of being waited out for an hour.
- Composer readiness is recognised from the "Describe your task" placeholder, so a message is no longer held through a two-minute silence.

## v0.21.0 - 2026-09-04

- macOS shortcuts that collided with the system are changed everywhere: Recent Conversations is Ctrl+Shift+H (Ctrl+H was Hide), Model and Effort is Ctrl+Shift+E (Ctrl+M was Minimize), and Next/Previous Session are Cmd+Shift+] and Cmd+Shift+[ on macOS (Cmd+Tab is the application switcher). Menu notes say Cmd on macOS.
- macOS settings move from `~/.config/blindpilot` and `~/.local/share/blindpilot` to `~/Library/Application Support/BlindPilot` on first launch. Nothing already there is overwritten, and a failed move does not stop the launch.
- Preferences (Cmd+,) opens every Options-menu setting in one dialog. About uses the native macOS panel. Create Desktop Shortcut works on macOS.
- The build ships a real icon, bundle identifier, and minimum macOS version (10.15) from `BlindPilot.spec`. `tools/make_icon.py` generates the icon files with no third-party dependency.
````

That is 692 words of Unreleased down to about 150, and v0.21.0 from 344 to about 120. The v0.21.3 section is drafted from RELEASE_NOTES.md; check it against the actual commit.

### Older history

Everything from v0.4.0 (CHANGELOG.md:293) upward is written in the same essay style. The v0.3.x entries at the bottom (lines 306 to 421) are already one line each and need nothing.

My recommendation is to compress v0.20.0 onward now (lines 7 to 95, about 8,000 words, the worst offenders) and leave v0.4.0 to v0.19.2 alone unless there is appetite for a second pass. The long versions stay in git history, and the header at CHANGELOG.md:3 could say so in one sentence: "Older entries are longer; the reasoning behind each fix is in the commit messages." Compressing the whole file would take it to roughly 7,000 words and is mechanical work once the target density is agreed.

## 4. RELEASE_NOTES.md, CREDITS.md, AGENTS.md, NOTICE

### RELEASE_NOTES.md

Hits. Lines 14 to 16 narrate the verification process ("full regression suite, lint, formatting, type checks, and packaged macOS startup checks"). Lines 18 to 19 repeat the license and credit, which the About dialog and README already carry. Line 3 is good.

Proposed replacement:

```markdown
# BlindPilot 0.21.3

Fixes FreeBuff requests getting stuck when sent during or after startup.

- macOS and Linux read FreeBuff's terminal output from launch, so an unread buffer cannot stall startup.
- Sending a message adopts the terminal already being started and cancels stale delayed starts.
- Turn completion and session drops are detected from FreeBuff's log events, so words in your prompt or the answer cannot end a turn early or release a held message.

Tested against a real FreeBuff install: sending immediately and sending after background startup both completed.
```

If this file is the GitHub release body, the same shape works for every release: one line of what, bullets of how, one line of evidence.

### CREDITS.md

Hits. Line 22 to 27 is a six-noun list of what the original "established". Line 29 "gratefully credits doubletaponair and every original contributor" is a feeling. Lines 33 to 36 list what BlindPilot added and stop at FreeBuff; opencode, Hermes, Chat mode, and the updater are missing. Lines 38 to 42 name nine architectural nouns from AccessibleAI. Line 45 "FreeBuff is maintained by its respective project" says nothing; opencode and Hermes (Nous Research) are absent from the trademark paragraph.

Proposed replacement:

```markdown
# Credits

BlindPilot is a fork of [Claude Code Reader](https://github.com/doubletaponair/claude-code-reader) by [doubletaponair](https://github.com/doubletaponair). The original project built the accessible wxPython window, the navigable response rows, live activity narration, multi-session tabs, and the Claude Code integration. Its contributor history is in the upstream repository. The original specification is kept in this repository as `original-claude-code-reader-spec.html`.

BlindPilot contributors added the Codex, FreeBuff, opencode, and Hermes backends, the setup wizard and installer, the verified updater, Chat mode, remote Hermes, narration modes, sound cues, and the test suite.

Chat mode is adapted from AccessibleAI, a sibling project by the same publisher. Its accounts, profiles, provider protocols, credential storage, attachments, and streaming were moved into BlindPilot's main window.

Claude and Claude Code are Anthropic's. Codex is OpenAI's. Hermes Agent is Nous Research's. FreeBuff and opencode belong to their own projects. The names identify the command-line tools BlindPilot drives; no endorsement is implied.

BlindPilot is distributed under the MIT License (see [LICENSE](LICENSE)). The upstream snapshot did not contain a LICENSE file when it was forked, so this notice does not claim the upstream project was MIT-licensed.
```

### AGENTS.md

This is not documentation. It is a block generated by `sync-claude-memory.py` that indexes memory files under `C:\Users\admin\.claude\projects\C--Users-admin-git-BlindPilot\memory`. That path is another user account and another checkout location; on this machine (`C:\Users\blind\gitrepos\BlindPilot`) every link is dead. The `@` import lines at 11 to 14 would make any agent that expands them fail. One of the indexed memories (line 9) says AGENTS.md is committed on purpose "so verified contributor agent instructions become usable", but the file contains no instructions, only pointers.

Two options. Delete it, since the memory files themselves are not in the repo and the index is useless without them. Or replace it with actual contributor instructions, which the repo currently lacks:

```markdown
# Notes for contributors and coding agents

Run before committing:

    python -m pytest -q -W error
    python -m ruff check .
    python -m ruff format --check .
    python -m mypy
    python blind_pilot.py --startup-smoke

CI runs the suite with `-W error`, so an unreaped `Popen` or an unclosed response body fails the build. Tests run in random order (`pytest-randomly`); a test that changes a module global must restore it in `try/finally`. Threads must be daemons. Nothing may block the GUI thread.

Every user-facing string is heard, not seen. Keep announcements short, put the action first, and avoid em dashes (NVDA drops them at the default punctuation level).

Every source file carries the SPDX MIT header. Never log prompts, answers, file contents, or credentials at any level.

Design notes for the shared backend process pool are in `backend_pool.py`'s module docstring.
```

Question 2 below.

### NOTICE

Keep as is. Eight lines, three plain sentences, no patterns.

### installer/BlindPilot.iss

The strings a user sees (`Launch BlindPilot`, the desktop-icon task, the uninstall entry) are fine. The comment at lines 39 to 47 explaining `CloseApplications=force` is 110 words with two em dashes. Proposed:

```
; force, not yes. Anything Restart Manager finds here is holding a file we are
; about to overwrite and has already declined to close, so close it. This is
; what the in-app updater has always passed on the command line.
; /NOFORCECLOSEAPPLICATIONS restores the polite prompt.
```

### docs/superpowers

Both files are tracked (commits 6db696a and e8403a6). The plan (1,959 lines) is a step-by-step build script with 66 unticked checkboxes for code that now exists (`backend_pool.py`, `tests/test_backend_pool.py`). It says "This is plan 1 of 5" and plans 2 to 5 were never written; the Unreleased changelog shows only Codex moved onto the pool. Nobody will execute this plan again. Delete it.

The spec (331 lines) has two parts worth keeping: the timing table at lines 24 to 31 (cold spawn 4.4 s, warm 0.55 s, resume scales with rollout size, 17 to 29 child MCP processes per app-server) and the three decisions at lines 66 to 85 (one lifecycle with per-backend shapes, interrupt-verify-kill on cancel, 15-minute idle reap with announcement). The open question at lines 306 to 331 (does an authenticated Claude keep its stream open after `result`) is still open and belongs in an issue. The rest is argument and code pointers by line number, which are already stale (`agent_backends.py:1656`, `blindpilot_app.py:3409` have moved).

Recommendation: delete the plan; cut the spec to the table, the decisions, and the open question (about 60 lines) and move it to `docs/design/held-backend-processes.md`, or fold those 60 lines into `backend_pool.py`'s module docstring and delete the directory. Question 5.

`docs/visual-audit/` is untracked and was not in scope. Its README is a how-to with PowerShell; I did not review it.

## 5. The worst 20 in-app strings

Every one of these is spoken. Shorter wording keeps every fact a user needs to act. Line numbers are in `blindpilot_app.py`.

1. **7500-7504** (initial welcome text). "You can choose Codex or FreeBuff later from File, Backend." Stale twice: opencode and Hermes are missing, and Backend is under Model (9707). This label is overwritten at 7657 on the first refresh, so it may never be heard, but it should match. Proposed: delete the four-sentence literal and set the same text 7657 uses.

2. **7657-7662** (welcome, live). "Welcome to BlindPilot.\n\nChoose the coding-agent backend you want to use first. This wizard checks its CLI, helps install or update it, checks sign-in, and optionally points BlindPilot at your projects folder.\n\nYou can switch or manage backends later from the File menu." "File menu" is wrong (Model, 9707). Proposed: "Welcome to BlindPilot. Choose a backend. This wizard checks that it is installed and signed in, and can set your projects folder. You can change backends later under Model, Backend."

3. **7698-7706** (wizard done page). "All done! BlindPilot is ready to use {label}.\n\nType in the Prompt field and press Enter to send.\nPress Ctrl+R to jump to the latest response.\nPress Ctrl+/ to pick a slash command.\nPress Ctrl+period to stop a task that is running.\nPress Ctrl+Shift+M to cycle permission modes when supported.\nType /model to choose the model and effort level when supported.{limitations}\n\nChoose Finish to open the app." Seven "Press" lines. Proposed: "BlindPilot is ready to use {label}. Type in the Prompt and press Enter to send. Ctrl+R jumps to the latest response, Ctrl+/ lists slash commands, Ctrl+period stops a task.{limitations} Choose Finish." The 7627-7640 initial version of the same page hardcodes Cmd+ and is also overwritten; make it one string.

4. **7835-7842** (Claude not installed). "BlindPilot's default backend needs it. Click Install Claude Code and it will be installed for you — the {flavour}, no administrator rights and no Node.js needed. It is put on your PATH so 'claude' also works in {shells}.\n\nYou can also install it yourself from claude.com/claude-code and click Check Again. To use another backend instead, press Escape and choose it from File, Backend in the main window." Em dash, stale menu, 70 words. Proposed: "Choose Install Claude Code. It installs the {flavour} with no administrator rights and adds it to PATH. Or install it yourself from claude.com/claude-code and choose Check Again. To use another backend, go Back and pick one." Same fix at **7861-7868** ("File, Backend").

5. **7849-7853** (the spoken hint for the same page). "Tab to the Install Claude Code button to install it now. It needs no administrator rights and is put on your PATH. Or press Escape to use another backend." Proposed: "Tab to Install Claude Code, or go Back to pick another backend."

6. **7913-7918** (Hermes not installed). "Choose Install {label}. BlindPilot runs {label}'s official installer — no administrator rights and no Node.js needed — then checks that it starts and puts it on your PATH.\n\nYou can also run it yourself: {command}\n\nThen choose Check Again." Two em dashes. Proposed: "Choose Install {label} to run its official installer. No administrator rights are needed. Or run it yourself: {command}. Then choose Check Again."

7. **7572-7576** (Claude sign-in intro). "BlindPilot needs you to be signed in to use the Claude Code backend.\n\nIf you have already run 'claude auth login' in your terminal and it worked, click Already Signed In to skip this step.\n\nOtherwise click Sign In — your browser will open to complete authentication." Em dash; overwritten at 7682 anyway. Proposed: make it the same string as 7682-7687, and shorten that to: "Sign in to {label}. If you already ran '{login}' in a terminal, choose Already Signed In. Otherwise choose Sign In and finish in the browser or terminal that opens."

8. **7605-7611** (projects folder page). "Optionally choose the folder that contains all your projects (for example your 'development' or 'repos' folder). New Session starts its Browse button there.\n\nYou can skip this and set it later from the File menu." Proposed: "Choose the folder that holds your projects, if you have one. New Session browses from there. You can skip this and set it later under File, Set Projects Folder."

9. **8302-8306** (sign-in code dialog). "{prompt}\n\nIf the page gave you a code, paste it here and choose OK.\nIf it did not, leave this alone — it closes by itself once the browser has finished signing you in." Proposed: "{prompt} Paste the code and choose OK. If there was no code, leave this open; it closes when the browser finishes."

10. **8388-8391** (Remote Hermes intro). "By default the Hermes backend runs the copy installed on this computer. Turn this on to drive a Hermes running somewhere else instead — start it there with 'hermes serve'." Proposed: "Off runs the Hermes installed on this computer. On connects to a Hermes elsewhere; start it there with 'hermes serve'."

11. **8413-8414** (credential choice items). "Session token — a Hermes on this same computer" and "Username and password — a Hermes on another computer". NVDA drops the dash, so the first reads as one run-on phrase. Proposed: "Session token (same computer)" and "Username and password (another computer)".

12. **2674 and 2679** (Remote Hermes describe(), spoken at 10414). "Off — the Hermes backend runs the copy installed here." and "On — {url}, signing in with a {how}". Proposed: "Off. Hermes runs the copy installed here." and "On. {url}, signing in with a {how}."

13. **5510-5514** (permission mode tooltip). "{label} does not expose permission modes through its command-line interface — it never stops to ask, so there is nothing here to choose". Proposed: "{label} has no permission modes. It never stops to ask."

14. **5594** (/connect on the wrong backend). "Error: /connect belongs to opencode. Switch the backend from the File menu first". Stale menu. Proposed: "Error: /connect is an opencode command. Choose opencode under Model, Backend first."

15. **9756** (Manage Backends help text). "Install, update, or sign in to Claude Code, Codex, FreeBuff, or opencode". Hermes is missing. Proposed: "Install, update, or sign in to a backend".

16. **10014-10017 and 10434-10437** (disabled menu help). "{label} has no providers to connect — this one belongs to opencode" and "{label} cannot compact a conversation — start a new conversation instead". Proposed: "Only opencode connects providers." and "{label} cannot compact. Start a new conversation instead."

17. **2413-2415** (settings not saved). "Your settings could not be saved, so BlindPilot will ask you to set it up again next time it starts. Check that its settings folder is reachable and not full." Proposed: "Settings could not be saved. Setup will run again next launch. Check that the settings folder is writable and has space."

18. **5102-5103** (live Hermes conversation selected). "Running now. Opening this attaches to the turn in progress and moves its output here." Proposed: "Running now. Opening it moves the live turn to this window."

19. **10217** (silent mode on). "Silent until the response mode on. Nothing is shown or spoken until the whole response is ready." Proposed: "Silent until the response is on. Nothing is shown or spoken until the whole answer arrives."

20. **9566** (update from a source checkout). "The release page opened. Automatic installation is used by packaged builds." Proposed: "Opened the release page. Only packaged builds install updates automatically."

Runners-up, all em dashes in strings a screen reader speaks: 1567 (`AUTH_HINT`), 1608, 2005, 4480 ("Sign-in cancelled — no code was pasted."), 5488 (" — new conversation on next send"), 5724 (slash picker "{cmd}  —  {desc}"), 7817-7821, 7896-7899. And 8112 "{label} is up to date. Its model list will refresh at runtime." can lose its second sentence.

## 6. Questions for the maintainer

1. README.md:3 opens with "vibe-coded". Keep it as a deliberate disclosure, or drop it? The draft above drops it; put it back if you want it.
2. AGENTS.md: delete, or replace with the contributor notes drafted in section 4? If you keep the memory-sync workflow, the paths need to change to this machine's, and the file should probably be gitignored as the "Tracked AGENTS.md" memory note argues against.
3. Is Linux supported? The README says Windows and macOS, but Linux config and log paths are documented, `linux_accessibility.py` exists, and CI runs the suite on Linux under xvfb. The draft says "Windows and macOS" and lists Linux paths without comment. If Linux is unsupported, the paths should go; if it is, the first line should say so.
4. README.md:25 says "Runs every backend fully automatic by default. Nothing stops mid-task to ask permission." The Permission Mode default is called "Default", and the next bullet describes answering questions the backend stops to ask. Which is true? The draft leaves the claim out until you confirm it.
5. docs/superpowers: delete both files, or keep the spec's 60 lines of measurements and decisions somewhere? And should the Claude open question (does an authenticated Claude keep its stream open after `result`) become a GitHub issue?
6. CHANGELOG older history: compress v0.20.0 onward only, or the whole file? Both are mechanical once you approve the density in section 3.
7. CHANGELOG has no v0.21.3 section. Should I add one from RELEASE_NOTES.md, or was 0.21.3 meant to ship without a changelog entry?
8. Build section: is `pyinstaller BlindPilot.spec` the command, or the long `--onedir --windowed ...` line at README.md:222? The README currently gives one and describes the other.
9. Do you want the RELEASE_NOTES.md shape (one line of what, bullets, one line of evidence) as the template for future GitHub release bodies?
