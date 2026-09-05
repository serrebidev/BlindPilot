# BlindPilot

A screen-reader-first desktop front end for AI coding CLIs. It runs Claude Code, Codex, FreeBuff, opencode, and Hermes in native wxPython windows, so NVDA, JAWS, and VoiceOver read controls instead of a terminal. It runs on Windows, macOS, and Linux. Linux is the least tested of the three.

[![Join SerrebiProjects on Telegram](https://img.shields.io/badge/Telegram-SerrebiProjects-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/SerrebiProjects)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

Questions, bugs, and release news go to the [SerrebiProjects Telegram group](https://t.me/SerrebiProjects). Bug reports and feature requests also go in [Issues](https://github.com/serrebidev/BlindPilot/issues).

BlindPilot started as a fork of [Claude Code Reader](https://github.com/doubletaponair/claude-code-reader) by doubletaponair and keeps its accessibility design. See [CREDITS.md](CREDITS.md).

## What it does

- Runs five coding agents, picked from Model, Backend. The choice is remembered.
- Runs every backend in Bypass permissions mode by default, so a task does not stop to ask for approval. Change this under Model, Permission Mode.
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

Linux. There is no packaged build. Run from source as described below.

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

Sign In in the wizard runs the backend's own login, reads the sign-in address from its output, speaks it, and opens your browser. Open Sign-in Page opens it again. If the provider hands back a code, BlindPilot asks for it and passes it to the CLI. Hermes is different. Its setup asks questions interactively, so Sign In opens a real terminal window for it. Answer the questions there, then choose Already Signed In.

opencode needs a provider connected to it. Use Model, Connect a Provider, or type `/connect`, or use the wizard. Pick a provider, then paste a key or sign in through the browser.

Type `/status` (or Model, Session Status) to hear the backend, model and effort, permission mode, folder, whether the next message continues this conversation, and which account the backend is signed in as.

## Menus

Every action is in the menu bar except three chords. Ctrl+L focuses the prompt, Ctrl+1 to Ctrl+9 jump to a tab, and Ctrl+Shift+M cycles permission modes.

File. New Session (Ctrl+T), Recent Conversations (Ctrl+Shift+H), Hermes Conversations (Ctrl+G, shown only when Hermes is the backend), Side Chat in This Folder, Next and Previous Session, Set Projects Folder, Create Desktop Shortcut, Close Session (Ctrl+W), Quit (Ctrl+Q).

Conversation. Stop Task (Ctrl+.), Attach Files (Ctrl+Shift+A), Slash Command (Ctrl+/), Compact Conversation (Ctrl+Shift+K), Start New Conversation (Ctrl+Shift+N), Find in Responses (Ctrl+F), Jump to Latest Response (Ctrl+R).

Model. Backend (one radio item per CLI), Model and Effort (Ctrl+Shift+E), Permission Mode (Default, Accept edits, Plan, Auto, Don't ask, Bypass permissions), Session Status, Backend Settings, Manage Backends, Connect a Provider.

Options. Show live activity in the list, Speak activity aloud, Include the backend's reasoning, Play sound cues, Narration (Follow everything, Keep up), Sounds (Message sent, Working, Answer received, Something went wrong), Responses as a read-only text field, Silent until the response mode, Working sound (continuous, every few seconds, off), Working sound interval, Remote Hermes, Preferences (Ctrl+,). On macOS, Preferences is in the application menu on Cmd+, as in every Mac app.

Chat. Accounts, Conversation profiles, Refresh models, History view (List, Read-only text), Diagnostics. Enabled only when the Mode combo box is set to Chat.

Help. Check for Updates, Check for updates at startup, Open Log Folder, About BlindPilot.

Backend, Permission Mode, Narration, and Working sound are radio items, so a screen reader reports them as exclusive choices. Compact Conversation and Connect a Provider are greyed out for backends that have no equivalent.

### Narration

Follow everything, the default, speaks every tool call, result, and subagent line in order. Keep up speaks your message, the answer, and BlindPilot's own status lines, such as why a run is waiting or how it ended. The tool steps still appear in the list; they are not spoken. Use Keep up when a run fans out into many parallel steps and the speech queue falls behind. BlindPilot cannot shorten the screen reader's own queue, so this is the control it offers instead.

## Keyboard

- Ctrl+L focus the prompt. Ctrl+T open a session. Ctrl+W close it.
- Ctrl+Shift+H reopen a past conversation. Ctrl+G list Hermes conversations, including running ones.
- Ctrl+Shift+K compact this conversation. Ctrl+Shift+N start a fresh one.
- Ctrl+F search responses. Ctrl+R jump to the latest.
- Ctrl+Shift+E choose model and reasoning effort.
- Ctrl+/ slash commands. Ctrl+. stop the running task.
- Ctrl+Shift+A attach files. Ctrl+Shift+M cycle permission modes.
- Ctrl+Tab and Ctrl+Shift+Tab move between tabs, as do Ctrl+Shift+] and Ctrl+Shift+[. Ctrl+1 to Ctrl+9 jump to a tab. On macOS use Cmd+Shift+] and Cmd+Shift+[, because Cmd+Tab belongs to the system.
- Ctrl+Up (or Alt+Up) from the prompt enters the newest response. Shift+Tab also reaches the responses. Inside the responses, Down on the last row stays there; Tab returns to the prompt.
- Enter sends the prompt. Shift+Enter inserts a new line.

On macOS the Ctrl chords are Cmd. Two chords differ from what you might expect, so that macOS does not swallow them. Recent Conversations is Ctrl+Shift+H (Cmd+H is Hide), and Model and Effort is Ctrl+Shift+E (Cmd+M is Minimize).

## Backends

| Backend | How BlindPilot talks to it | Model and effort | Permission modes | Compaction | Asks questions |
|---|---|---|---|---|---|
| Claude Code | Streaming JSON CLI | Yes | Yes | Yes | Yes |
| Codex | app-server protocol, one shared process | Yes, with reasoning effort | Yes | Yes | Yes |
| FreeBuff | Hidden pseudo-terminal | Model yes, effort no. GLM 5.3 Flash by default | Managed by FreeBuff | No | Yes |
| opencode | Its headless HTTP server, one shared process | Yes, with per-model reasoning variants | Yes | Yes | Yes |
| Hermes | Gateway JSON-RPC over a local pipe or the network | Yes | Yes | Yes | Yes |

FreeBuff has no JSON or headless API, so BlindPilot runs its terminal interface in a hidden pseudo-terminal and reads the answer off the screen a sentence at a time. Redraws and advertisements are filtered out. If you send a message before FreeBuff has finished starting, BlindPilot holds it and says so, then sends it when the session is live.

Codex runs as one app server shared by every tab. It starts with the first message, stays running between messages, and is closed after fifteen minutes with no turn. BlindPilot announces the close and the restart.

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

Then choose Username and password in Remote Hermes. Hermes issues a short-lived single-use ticket for each WebSocket connection; BlindPilot logs in and fetches one itself each time it connects. Test connection checks the address and credentials before anything is sent.

`websocket-client` is only needed for the remote path. If it is missing, BlindPilot names it as an installable package and keeps running.

## Chat mode

Chat talks to a provider's API directly. No CLI, no agent, no file access.

Set the Mode combo box to Chat, add a provider and key under Chat, Accounts, then Chat, Refresh models and pick one. Supported providers are OpenRouter, OpenAI, Claude, Gemini, Z.AI, Moonshot AI, Kimi, DeepSeek, OpenCode Go, and any OpenAI-compatible endpoint. Keys go in the OS credential store.

Conversation profiles hold a system prompt, default account and model, temperature, token limit, and streaming preference. History view switches between a native list and a read-only edit field. Provider logs are under Chat, Diagnostics.

OpenRouter accounts also get multiple attachments, cache-aware regeneration, `:batch` model ids, OpenRouter's server-side tools (web search, web fetch, date and time, image generation, apply patch, shell, bash, fusion, advisor, subagent, tool search, model search), and thinking controls. Tools run on OpenRouter's servers, not your computer. Thinking effort sets how long a reasoning model thinks. Send the thinking back decides whether the thinking text is returned. Thinking arrives as its own History entry with a length line first. Read attached PDFs with converts a PDF to text for models that cannot read PDFs.

Chat data lives in `chat.sqlite3` beside the config. An existing AccessibleAI database is imported once and left unmodified.

## Logs

BlindPilot writes a rotating `blindpilot.log` and a `blindpilot-crash.log` for native crashes. Help, Open Log Folder opens the folder. It is `%LOCALAPPDATA%\BlindPilot\Logs` on Windows, `~/Library/Logs/BlindPilot` on macOS, and `$XDG_STATE_HOME/blindpilot` on Linux. The log keeps at most four files of one megabyte.

The level is INFO. Set `BLINDPILOT_LOG_LEVEL=DEBUG` for a bug report. Prompts, answers, file contents, and credentials are never logged at any level. On Windows the crash log also records first-chance COM exceptions from screen-reader interop; those are noise, not crashes.

## Updates

Help, Check for Updates asks GitHub Releases for a newer version, downloads it, verifies the published SHA-256, and restarts into the installer. Check for updates at startup does the same quietly and only speaks when there is something new. Builds run from source open the release page instead.

## Run from source

1. Install Python 3.10 or newer. Releases are built with 3.12.
2. `pip install -r requirements.txt`
3. `python blind_pilot.py`

`blind_pilot.py` is the entry point; the code is in `blindpilot_app.py`.

## Build

```powershell
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean BlindPilot.spec
```

`BlindPilot.spec` reads the version from `APP_VERSION` and carries the bundle identifier, minimum macOS version, and icon (`tools/make_icon.py` generates the icon files into `packaging/`). The one-directory layout is what lets the updater replace the app after it exits. The Windows installer is `installer/BlindPilot.iss`. Pushing a `v*` tag runs `.github/workflows/release.yml`, which runs the tests and the packaged startup checks, then publishes the Windows installer, the Windows zip, both macOS zips, and their SHA-256 files.

Before opening a pull request:

```powershell
python -m pytest -q -W error
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

Pull requests are welcome.

## License and credits

MIT. See [LICENSE](LICENSE). Every source file carries an `SPDX-License-Identifier: MIT` header.

Copyright (c) 2026 doubletaponair and BlindPilot contributors. BlindPilot was written with AI coding assistance. Claude Code Reader is credited in this README, the About dialog, the source headers, [CREDITS.md](CREDITS.md), and the original specification kept at [`original-claude-code-reader-spec.html`](original-claude-code-reader-spec.html).
