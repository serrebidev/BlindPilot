# BlindPilot

A vibe-coded, screen-reader-friendly desktop front end for AI coding agents on Windows and macOS, built so Claude Code, Codex, FreeBuff, opencode, and Hermes can be driven without reading a terminal.

[![Join SerrebiProjects on Telegram](https://img.shields.io/badge/Telegram-SerrebiProjects-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/SerrebiProjects)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

**Have a question, hit a bug, or want early word on new releases?** Join the [SerrebiProjects Telegram group](https://t.me/SerrebiProjects) — the community hub for BlindPilot and my other projects, and the fastest place to get help.

BlindPilot is based on the original **[Claude Code Reader](https://github.com/doubletaponair/claude-code-reader)** by [doubletaponair](https://github.com/doubletaponair). It keeps that project's accessibility-first design and adds a pluggable multi-backend system. See [CREDITS.md](CREDITS.md).

## Features

- Native wxPython controls throughout, so NVDA, JAWS, and VoiceOver read the app rather than interpreting a terminal.
- Runs Claude Code, Codex, FreeBuff, opencode, and Hermes, switchable from the Model menu and remembered between launches.
- Switches between Agent and Chat from a named Mode combo box at the top of the window.
- Chats directly through OpenRouter, OpenAI, Claude, Gemini, Z.AI, Moonshot AI, Kimi, DeepSeek, OpenCode Go, or any OpenAI-compatible service, with keys in the OS credential store.
- Drives a Hermes on another computer over the network, so a server elsewhere can be worked with from the desktop.
- Reopens any conversation a Hermes knows, including one running right now, from Hermes Conversations (Ctrl+G) - and joins that running turn as it happens.
- Segments every answer into navigable rows: one per heading, paragraph, list item, quote, code block, thought, tool action, and tool result.
- Reads answers aloud as they arrive, or stays quiet until the whole answer is ready.
- Reopens past conversations from any backend, titled by the message that started them, and carries on where they left off.
- Compacts a long conversation in place so the backend has room to keep going.
- Runs several project sessions at once, each in its own tab, with its own folder, model, and permission mode.
- Runs every backend fully automatic by default. Nothing stops mid-task to ask permission.
- Answers the questions a backend stops to ask, in one dialog: radio buttons per answer, checkboxes where several are allowed, and an Other box to type your own.
- Steers a task while it is still running, or stops it and keeps what it produced.
- Attaches files and pasted clipboard images as explicit prompt paths.
- Searches responses, jumps between them, and copies a code block, a response, or the whole conversation.
- Picks the model and reasoning effort from whatever the installed CLI actually reports.
- Marks sent, working, received, and failed with optional earcons, switchable together or one at a time.
- Speaks every step of a run, or just the message, the answer and anything important, with the steps still in the list.
- Installs, updates, adds to PATH, and signs into any of the five backends from an accessible wizard.
- Writes a rotating log of what it did, and a separate crash log, with no prompt or answer text in either.
- Updates itself from GitHub Releases after verifying the published SHA-256.

## Menus

Every action is in the menu bar. Only moving focus is chord-only: **Ctrl+L** to the prompt, **Ctrl+1** to **Ctrl+9** to a tab.

**File** — New Session (**Ctrl+T**), Recent Conversations (**Ctrl+H**), Side Chat in This Folder, Next and Previous Session, Set Projects Folder, Create Desktop Shortcut, Close Session (**Ctrl+W**), Quit (**Ctrl+Q**).

**Conversation** — Stop Task (**Ctrl+.**), Attach Files (**Ctrl+Shift+A**), Slash Command (**Ctrl+/**), Compact Conversation (**Ctrl+Shift+K**), Start New Conversation (**Ctrl+Shift+N**), Find in Responses (**Ctrl+F**), Jump to Latest Response (**Ctrl+R**).

**Model** — Backend (one radio item per CLI), Model and Effort (**Ctrl+M**), Permission Mode (Default, Accept edits, Plan, Auto, Don't ask, Bypass permissions), Manage Backends, Connect a Provider.

**Options** — Show live activity in the list, Speak activity aloud, Include the backend's reasoning, Play sound cues, Narration (Follow everything, Keep up), Sounds (Message sent, Working, Answer received, Something went wrong), Responses as a read-only text field, Silent until the response mode.

**Chat** — Accounts, Conversation profiles, Refresh models, History view, Diagnostics. Greyed out until you switch the Mode combo box to Chat.

**Help** — Check for Updates, Check for updates at startup, Open Log Folder, About BlindPilot.

Backend, Permission Mode and Narration are radio items because the choices are exclusive, and that is what a screen reader says about them. Compact Conversation and Connect a Provider grey out for a backend that has no equivalent, rather than being offered and then refused.

**Narration** decides how much of a run is spoken. *Follow everything* is the default and speaks every tool call, result and subagent line in order. *Keep up* speaks your message, the answer, and anything BlindPilot says for itself — why a run is waiting, how it ended — and leaves the steps in the list, where the review cursor still reaches them. Nothing is lost in Keep up, only unspoken. The backlog a fan-out creates sits in the screen reader's own queue, which BlindPilot cannot measure or shorten, so this is a choice offered rather than a cleverness applied.

## Keyboard

- **Ctrl+L** focus the prompt, **Ctrl+T** open a session, **Ctrl+W** close it.
- **Ctrl+H** reopen a past conversation.
- **Ctrl+G** list the conversations Hermes knows, and join one that is running.
- **Ctrl+Shift+K** compact this conversation, **Ctrl+Shift+N** start a fresh one.
- **Ctrl+F** search responses, **Ctrl+R** jump to the latest.
- **Ctrl+M** choose the model and reasoning effort for this conversation.
- **Ctrl+/** slash commands, **Ctrl+.** stop the running task.
- **Ctrl+Shift+A** attach files, **Ctrl+Shift+M** cycle permission modes.
- **Ctrl+Tab** and **Ctrl+Shift+Tab** move between session tabs, as do **Ctrl+Shift+]** and
  **Ctrl+Shift+[**; **Ctrl+1** to **Ctrl+9** jump straight to one.
- **Up** from the prompt's first line enters the newest response. At either end,
  arrow keys stay in the responses; press **Tab** to move to the prompt.

On macOS the same accelerators map to Command where appropriate.

## Chat mode

Chat talks to a provider's API directly. No CLI, no agent, no file access.

Choose **Chat** in the Mode combo box, add a provider and key under **Chat → Accounts**, then **Chat → Refresh models** and pick one. **Conversation profiles** hold a system prompt, default account and model, temperature, token limit, and streaming preference. **History view** switches between a native list and a read-only edit field. Provider logs are under **Chat → Diagnostics**.

OpenRouter accounts get more: multiple attachments, cache-aware regeneration, model ids ending in `:batch`, OpenRouter's own tools, and its thinking. Both of the last two are set per conversation in Conversation profiles.

**OpenRouter tools** lists every tool OpenRouter runs itself — web search, web fetch, date and time, image generation, apply patch, shell, bash, fusion, advisor, subagent, tool search, model search. Tick the ones a conversation may use. OpenRouter executes them, so nothing runs on your computer and nothing stops to ask. Each call is spoken as it happens and left in History, and answers that cite pages get a numbered **Sources** list.

**Thinking effort** sets how long a reasoning model thinks, from minimal to maximum, with an optional token budget. **Send the thinking back** decides whether the words come back at all; off still lets the model think. Thinking arrives as its own History entry with a line saying how long it is, so arrowing past it does not read the whole thing. **Read attached PDFs with** turns an attached PDF into text any model can read.

Attachments are stored with the conversation. Chat data lives in `chat.sqlite3` beside the config; keys use the OS credential store. An existing AccessibleAI database and its keys are imported once, and the original is never modified.

## Backends

| Backend | Integration | Sessions | Model control | Permission modes | Compaction | Questions |
|---|---|---|---|---|---|---|
| Claude Code | Streaming JSON CLI | Yes | Yes | Yes | Yes | Yes |
| Codex | Official app-server protocol | Yes | Yes, including reasoning effort | Yes | Yes | Yes |
| FreeBuff | Pseudo-terminal adapter | Yes | Yes; GLM 5.3 Flash by default | Managed by FreeBuff | No | Yes |
| opencode | Its own headless HTTP server | Yes | Yes, including per-model reasoning variants | Yes | Yes | Yes |
| Hermes | Gateway JSON-RPC, local pipe or network | Yes | Yes | Yes | Yes | No |

Four of the five backends can stop a turn to ask a multiple-choice question, and each asks differently: Claude Code sends its AskUserQuestion tool through the permission channel, Codex sends `request_user_input` over the app-server protocol, opencode publishes a `question.asked` event, FreeBuff draws its `ask_user` prompt as a text menu. One accessible dialog answers all four. Hermes' gateway protocol has no such request, so its turns never pause to ask.

FreeBuff ships no JSON or headless API, so BlindPilot runs its terminal interface in a hidden pseudo-terminal, reads the answer off its screen a sentence at a time, and captures its chat id to resume. Redraws and advertisements are filtered before anything is spoken. Its permission picker and Compact Conversation are disabled because the CLI has no equivalent. FreeBuff renames and drops models between releases, so the default is a preference: GLM 5.3 Flash when it is on offer, FreeBuff's own choice when it is not.

opencode is driven through the same headless server its own terminal interface uses. BlindPilot starts one, shared by every tab, on loopback behind a password generated for the run. That is what gives opencode everything the others have, plus `/connect` and a model picker covering every provider it reaches. Past conversations come out of opencode's own SQLite database, read-only.

Hermes speaks its gateway JSON-RPC, which runs over both a local pipe and a network socket — so a Hermes on another computer can be driven from the desktop without a terminal (**Options → Remote Hermes**). Its answers are streamed a finished sentence at a time, the way FreeBuff's are, so a long turn is read while it is still being written. One connection is kept for the whole conversation rather than opened per message, and it is read continuously: a Hermes bound to a public address pings every twenty seconds and closes a connection that does not answer. Its reasoning channel carries a terminal spinner rather than the model's reasoning, so that is filtered out and the real reasoning shown instead. Hermes exposes no per-turn reasoning effort on this protocol, so that picker stays empty.

## Download and install

Grab the latest build from the [Releases page](https://github.com/serrebidev/BlindPilot/releases).
For a version-by-version history, see the [changelog](CHANGELOG.md).

**Windows installer (recommended)**

1. Download `BlindPilot-Setup-x64.exe`.
2. Run it. It installs per user with no administrator prompt, adds a Start Menu entry, and closes a running copy before replacing it.

**Windows portable**

1. Download `BlindPilot-Windows-x64.zip`.
2. Extract it anywhere and run `BlindPilot.exe` — no installation required.

**macOS**

Download `BlindPilot-macOS-arm64.zip` for Apple Silicon or `BlindPilot-macOS-x64.zip` for Intel. The macOS builds are ad-hoc signed but not notarized, so first launch may need approval in System Settings under Privacy & Security.

Settings live in `%APPDATA%\BlindPilot\config.json` on Windows and `~/.config/blindpilot/config.json` elsewhere. An existing Claude Code Reader configuration is imported once and never modified.

## Set up a backend

The first-run wizard and **Model → Manage Backends** find, install, update, and sign into any of the four. Claude Code uses its official native installer. For Codex, FreeBuff, and opencode, BlindPilot installs a current Node.js LTS when npm is missing, installs the CLI into a writable per-user folder, adds it to PATH, and verifies that it starts. No administrator rights are required, and a failed backend stays isolated from the others.

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

# Hermes Agent — see https://hermes-agent.nousresearch.com/docs
hermes status     # shows the provider and model it will use
hermes model      # pick one, if none is set yet
```

**Sign In** does the same from inside BlindPilot, with no terminal. It runs the backend's own sign-in, reads the address out of its output, speaks it, and gets it to your default browser. **Open Sign-in Page** opens it again if the browser was closed or never arrived. When a provider hands back a code instead of finishing on its own, BlindPilot asks for the code and passes it to the CLI; that dialog closes itself if the browser finishes first.

opencode needs a provider connected to it, so BlindPilot carries its `/connect` as a dialog. Use **Model → Connect a Provider**, type `/connect` in the prompt, or use the wizard. Pick from every provider opencode knows, give it a key, or sign in through the browser. None of it needs a terminal.

Type `/status` in the prompt to hear what the tab will do and whose account it will do it on: backend, model and effort, permission mode, folder, whether the next message continues this conversation or starts one, and who that backend is signed in as. All four answer it, even though none of them has a status command that works this way.

Hermes is the exception to that browser flow: its setup is an interactive picker that asks which provider and model to use, so there is no address to open and nothing to watch for. **Sign In** opens a real terminal window for it and says to come back and choose **Already Signed In** once its questions are answered — running it hidden would fail instantly for want of a keyboard, and reporting that as a failed sign-in would be a lie.

### Driving a Hermes on another computer

Leave **Options → Remote Hermes** off and the Hermes backend runs the copy
installed here — including one installed in WSL, which is found and started
there without any network setup.

To use a Hermes running on a different machine, how you start it decides how you
sign in.

On the same computer, bound to localhost, a session token is enough:

```bash
HERMES_DASHBOARD_SESSION_TOKEN=pick-a-long-random-string hermes serve --port 9119
```

Reachable from elsewhere, it has to be bound to a public address, and Hermes
refuses that outright unless a login is configured — there is no unauthenticated
public bind. Configure a password once, on the machine running Hermes:

```bash
hermes config set dashboard.basic_auth.username your-name
# Hermes prints the hash to store; run this from its own installation:
python -c "from plugins.dashboard_auth.basic import hash_password; print(hash_password('your-password'))"
hermes config set dashboard.basic_auth.password_hash 'the-hash-it-printed'
hermes serve --port 9119 --host 0.0.0.0
```

Then choose **Username and password** in the settings. Hermes' WebSocket accepts
only a single-use ticket once that gate is up, and those tickets live thirty
seconds — far too short to paste into a field — so BlindPilot logs in and mints
one itself each time it connects. **Test connection** reports whether the address
and credentials work before anything is sent.

Note that `websocket-client` is only needed for this path. The local backend
uses a pipe, and a missing copy is reported as an installable package rather
than stopping the app.

## Logs

BlindPilot writes a rotating `blindpilot.log`, plus `blindpilot-crash.log` for native crashes. **Help → Open Log Folder** opens the folder. It is `%LOCALAPPDATA%\BlindPilot\Logs` on Windows, `~/Library/Logs/BlindPilot` on macOS, and `$XDG_STATE_HOME/blindpilot` elsewhere — logs, not settings, so they do not roam between machines. Four files of a megabyte is the most they can occupy.

INFO by default. Set `BLINDPILOT_LOG_LEVEL=DEBUG` for a bug report.

Prompts, answers, file contents, and credentials are never written to either file, at any level, for any reason. What BlindPilot did is recorded; what you said is not. On Windows the crash log also collects first-chance COM exceptions from screen-reader interop, which are noise rather than crashes.

## Run from source (any OS)

1. Install Python 3.10 or newer. The release builds use 3.12.
2. Install dependencies: `pip install -r requirements.txt`
3. Launch it: `python blind_pilot.py`

`blind_pilot.py` is the stable entry point; the implementation is in `blindpilot_app.py`. `claude_reader.py` is a compatibility alias for anything written against the original application.

## Building

```powershell
python -m pip install -r requirements-build.txt
pyinstaller --onedir --windowed --name BlindPilot --add-data "EarCons;EarCons" --additional-hooks-dir hooks blind_pilot.py
```

The one-directory layout is what lets the verified updater replace the application after it exits. The Windows installer is built from [`installer/BlindPilot.iss`](installer/BlindPilot.iss). Pushing a `v*` tag runs [.github/workflows/release.yml](.github/workflows/release.yml), which tests startup and the full suite, then publishes the Windows installer, the Windows x64 archive, and both macOS archives with SHA-256 files.

Before opening a pull request:

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

## Contributing

Pull requests are welcome. If BlindPilot has been useful to you, open a PR with a fix or feature and I'll review it.

## License

BlindPilot is under the [MIT license](LICENSE) — use it, change it, redistribute it, or package it, no permission needed. Every source file carries an `SPDX-License-Identifier: MIT` header so packaging tools pick the license up automatically.

Copyright (c) 2026 doubletaponair and BlindPilot contributors. The name changed and the backends multiplied, but the origin has not been erased: [Claude Code Reader](https://github.com/doubletaponair/claude-code-reader) is credited in this README, the About dialog, the source headers, [CREDITS.md](CREDITS.md), and the retained original specification at [`original-claude-code-reader-spec.html`](original-claude-code-reader-spec.html).

## Community and support

Report bugs and request features in [Issues](https://github.com/serrebidev/BlindPilot/issues). For questions, feedback, and release news, join the [SerrebiProjects Telegram group](https://t.me/SerrebiProjects).
