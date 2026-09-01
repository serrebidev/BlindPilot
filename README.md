# BlindPilot

A vibe-coded, screen-reader-friendly desktop front end for AI coding agents on Windows and macOS, built so Claude Code, Codex, FreeBuff, opencode, and Hermes can be driven without reading a terminal.

[![Join SerrebiProjects on Telegram](https://img.shields.io/badge/Telegram-SerrebiProjects-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/SerrebiProjects)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

**Have a question, hit a bug, or want early word on new releases?** Join the [SerrebiProjects Telegram group](https://t.me/SerrebiProjects) — the community hub for BlindPilot and my other projects, and the fastest place to get help.

BlindPilot is based on the original **[Claude Code Reader](https://github.com/doubletaponair/claude-code-reader)** by [doubletaponair](https://github.com/doubletaponair). It keeps that project's accessibility-first design and adds a pluggable multi-backend system. See [CREDITS.md](CREDITS.md).

## Features

- Native wxPython controls throughout, so NVDA, JAWS, and VoiceOver read the app rather than interpreting a terminal.
- Switches between Agent and Chat from a named Mode combo box at the top of the same window.
- Chats directly through OpenRouter, OpenAI, Claude, Gemini, Z.AI, Moonshot AI, Kimi, DeepSeek, OpenCode Go, or a custom OpenAI-compatible service, with secure API-key storage, model discovery, profiles, streaming history, attachments, editing, and response regeneration.
- Runs Claude Code, Codex, FreeBuff, opencode, and Hermes, switchable from the File menu and remembered between launches.
- Drives a Hermes on another computer over the network, so a server elsewhere can be worked with from the desktop.
- Segments every answer into navigable rows: one per heading, paragraph, list item, quote, code block, thought, tool action, and tool result.
- Reads answers aloud as they arrive, or stays quiet until the whole answer is ready.
- Reopens past conversations from any backend, titled by the message that started them, and carries on where they left off.
- Compacts a long conversation in place so the backend has room to keep going.
- Runs several project sessions at once, each in its own tab named after the conversation in it, with its own folder, model, and permission mode. **Ctrl+Tab** moves between them.
- Runs every backend fully automatic by default: nothing stops mid-task to ask permission, because a question nobody is watching for is a task that never finishes.
- Answers the questions a backend stops to ask. All four can pause a turn to put a multiple-choice question to you; BlindPilot opens a dialog with one radio button per answer, checkboxes where several are allowed, and an "Other" choice that opens a box to type your own answer in.
- Steers a task while it is still running, or stops it outright and keeps what it produced.
- Attaches files and pasted clipboard images as explicit prompt paths.
- Searches responses, jumps between them, and copies a code block, a whole response, or the whole conversation.
- Picks the model and reasoning effort from whatever the installed CLI actually reports.
- Marks sent, working, and received with optional earcons, which can be turned off from the Options menu. The working cue can be left continuous, played every few seconds, or switched off (**Options → Working sound**).
- Installs, updates, adds to PATH, and signs into any of the backends from an accessible wizard.
- Updates itself from GitHub Releases after verifying the published SHA-256.

## Chat mode

Choose **Chat** in the **Mode** combo box. Use **Chat → Accounts** to add a provider and API key, then choose **Chat → Refresh models** and select a model. **Chat → Conversation profiles** can supply a system prompt, default account and model, temperature, token limit, and streaming preference. **Chat → History view** switches between a native list and a read-only edit field; list items can be copied or edited from their context menu. Provider logs are under **Chat → Diagnostics**.

OpenRouter accounts additionally support multiple file attachments, cache-aware regeneration, and model ids ending in `:batch`. Attachments are stored with the conversation. Chat configuration lives in `%APPDATA%\BlindPilot\chat.sqlite3` on Windows and alongside BlindPilot's other configuration elsewhere; API keys use the operating system credential store. The first time Chat mode opens, BlindPilot imports an existing AccessibleAI database and its saved keys when they are present, without modifying the original database.

## Backends

| Backend | Integration | Sessions | Model control | Permission modes | Compaction | Questions |
|---|---|---|---|---|---|---|
| Claude Code | Streaming JSON CLI | Yes | Yes | Yes | Yes | Yes |
| Codex | Official app-server protocol | Yes | Yes, including reasoning effort | Yes | Yes | Yes |
| FreeBuff | Pseudo-terminal adapter | Yes | Yes; DeepSeek V4 Pro by default | Managed by FreeBuff | No | Yes |
| opencode | Its own headless HTTP server | Yes | Yes, including per-model reasoning variants | Yes | Yes | Yes |
| Hermes | Gateway JSON-RPC, local pipe or network | Yes | Yes | Yes | Yes | No |

Four of the backends can stop a turn to ask you a multiple-choice question, and each asks in its own way: Claude Code sends its AskUserQuestion tool through the permission channel of the stream it is already writing, Codex sends a `request_user_input` request over the app-server protocol, opencode publishes a `question.asked` event, and FreeBuff draws its `ask_user` tool onto its terminal. All four end up in the same dialog, and the answer goes back the way that backend expects it. A question left unanswered is declined rather than ignored, because a turn waiting on an answer that is never coming sounds exactly like a model that has stopped thinking. Hermes' gateway protocol has no such request, so it never pauses this way.

FreeBuff ships no JSON or headless API, so BlindPilot runs its terminal interface in a hidden pseudo-terminal, reads the answer off its screen a finished sentence at a time, and captures its chat id so the conversation can be resumed. Terminal redraws and advertisements are filtered out before anything is spoken. Its permission picker and Compact Conversation are disabled because the FreeBuff CLI has no equivalent.

opencode is driven through the same headless server its own terminal interface uses. BlindPilot starts one, shared by every tab, on the loopback interface behind a password generated for the run, and reads the turn off its event stream. That is what gives opencode every feature the other backends have — streaming answers, steering a running turn, stopping one, permission modes, compaction, resuming a past conversation — plus `/connect` and a model picker covering every provider it can reach. Past conversations come out of opencode's own SQLite database, read-only.

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

Settings live in `%APPDATA%\BlindPilot\config.json` on Windows and `~/.config/blindpilot/config.json` elsewhere. Chat data is stored in `chat.sqlite3` in the same folder. An existing Claude Code Reader configuration is imported once and never modified.

## Set up a backend

BlindPilot's first-run wizard and **File → Manage Backends** can find, install, update, and sign into any of the four. Claude Code uses its official native installer. For Codex, FreeBuff, and opencode, BlindPilot installs a current Node.js LTS for the user automatically when npm is missing, installs the CLI into a writable per-user folder, adds it to PATH, and verifies that the CLI starts. FreeBuff's verification also downloads its native binary before setup continues. No administrator rights are required. A failed backend remains isolated from the others. To do it by hand:

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

**Sign In** does the same thing from inside BlindPilot, with no terminal. It runs the backend's own sign-in, reads the address out of its output, speaks it, and makes sure it reaches your default browser — the CLI opens it where it can, BlindPilot opens it where it will not, and **Open Sign-in Page** opens it again if the browser was closed or never arrived. When a provider hands the page back a code instead of finishing on its own, BlindPilot asks for the code and gives it to the CLI; that dialog closes by itself if the browser completes the sign-in first.

opencode reaches a model through a provider you connect to it, so BlindPilot carries opencode's `/connect` as a dialog of its own: type `/connect` in the prompt (or use **Connect a Provider** in the wizard) to pick from every provider opencode knows, give it an API key, or sign in through your browser. Nothing about that flow needs a terminal.

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

## Keyboard

- **Ctrl+L** focus the prompt, **Ctrl+T** open a session, **Ctrl+W** close it.
- **Ctrl+H** reopen a past conversation.
- **Ctrl+Shift+K** compact this conversation, **Ctrl+Shift+N** start a fresh one.
- **Ctrl+F** search responses, **Ctrl+R** jump to the latest.
- **Ctrl+/** slash commands, **Ctrl+.** stop the running task.
- **Ctrl+Shift+A** attach files, **Ctrl+Shift+M** cycle permission modes.
- **Ctrl+Tab** and **Ctrl+Shift+Tab** move between session tabs, as do **Ctrl+Shift+]** and
  **Ctrl+Shift+[**; **Ctrl+1** to **Ctrl+9** jump straight to one.
- **Up** from the prompt's first line enters the newest response. At either end,
  arrow keys stay in the responses; press **Tab** to move to the prompt.

On macOS the same accelerators map to Command where appropriate.

## Run from source (any OS)

1. Install Python 3.10 or newer.
2. Install dependencies: `pip install -r requirements.txt`
3. Launch it: `python blind_pilot.py`

`blind_pilot.py` is the stable entry point and the implementation lives in `blindpilot_app.py`. `claude_reader.py` is a compatibility alias for anything written against the original application.

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
