# BlindPilot

A screen-reader-first desktop app for AI coding agents. It runs Claude Code, Codex, FreeBuff, opencode, Hermes, Muse Code, muse.ai, Command Code, Gemini CLI, and Antigravity CLI in normal windows, so NVDA, JAWS, and VoiceOver read real buttons, lists and edit boxes instead of a terminal. It runs on Windows, macOS, and Linux. Linux gets the least testing of the three.

[![Join SerrebiProjects on Telegram](https://img.shields.io/badge/Telegram-SerrebiProjects-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/SerrebiProjects)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

**Questions, bugs, or release news?** Join the [SerrebiProjects Telegram group](https://t.me/SerrebiProjects). That's the fastest way to reach me. Bug reports and feature requests can also go in [Issues](https://github.com/serrebidev/BlindPilot/issues).

## Why this exists

Coding agents live in terminals, and terminals are miserable with a screen reader. Output scrolls past, the screen redraws under you, and you can't tell if the thing is working, stuck, or waiting on you. BlindPilot puts the agent in a window your screen reader understands. Every answer is a list you can arrow through, and it tells you when something happens.

BlindPilot is vibe-coded. I use it every day. It started as a fork of [Claude Code Reader](https://github.com/doubletaponair/claude-code-reader) by doubletaponair, and it keeps that app's accessibility design. See [CREDITS.md](CREDITS.md).

## What it does

- Runs ten coding agents. Pick one per tab under Model, Backend. New tabs start on whatever agent the tab you're in uses. If your tabs use different agents, each tab's name, its prompt, and its send sound tell you which one you're talking to.
- Doesn't stop to ask permission for every little thing. Every agent runs in Bypass permissions mode out of the box. If you want to approve things yourself, change it under Model, Permission Mode.
- Splits every answer into rows: one per heading, paragraph, list item, quote, code block, thought, and tool step. Arrow through them like any list.
- Reads answers out as they arrive, or stays quiet until the whole answer is in. Your choice.
- Reads your message back when you send it ("Sent: fix the build"), so you know what dictation or a paste actually sent. Turn it off in Preferences.
- Speaks every step the agent takes, or just your message, the answer, and anything important. See Narration below.
- Reopens old conversations from any agent so you can keep going. Shrinks a long one so the agent has room to carry on.
- Runs several conversations at once, one tab each, each with its own folder, model, and permission mode.
- When the agent asks you a multiple-choice question, you get a normal dialog with radio buttons, checkboxes, and an Other box.
- Lets you redirect a running task with a new message, or stop it and keep what it did. Stop also stops any helper agents it started.
- Shows the helper agents (subagents) a task has running, and what each one is doing.
- Attaches files and pasted pictures.
- Keeps a list of messages you send all the time, by name, so you can drop one into the prompt with one key.
- Searches answers. Copies a code block, an answer, or the whole conversation. Saves a conversation as Markdown, a web page, or plain text.
- Lists the models and effort levels each agent offers.
- Warns you once when a Claude Code plan limit (five-hour or weekly) is getting close or has run out, with how full it is and when it resets.
- Plays sounds for sent, working, answered, and failed, if you want them.
- Pops up a system notification when a task finishes, fails, or needs you while BlindPilot is in the background. Choosing it takes you to that tab. Turn it off in Preferences.
- Installs, updates, and signs in to any agent from a setup wizard. No admin rights needed.
- Has a Chat mode that talks to an AI service directly. No agent, no access to your files.
- Can drive a Hermes running on another computer.
- Updates itself, and checks the download is the real one before installing.
- Keeps a log of what it did. Never what you or the AI said.

## Download and install

Get the latest build from the [Releases page](https://github.com/serrebidev/BlindPilot/releases). What changed in each version is in the [changelog](CHANGELOG.md).

**Windows installer (recommended)**

1. Download `BlindPilot-Setup-x64.exe`.
2. Run it. It installs just for you, so there's no admin prompt. It adds a Start Menu entry, and closes BlindPilot first if it's running.

**Windows portable**

1. Download `BlindPilot-Windows-x64.zip`.
2. Unzip it anywhere and run `BlindPilot.exe`.

**macOS**

Download `BlindPilot-macOS-arm64.zip`. It's Apple Silicon Macs only. The app isn't notarized by Apple, so the first time you open it you may have to allow it in System Settings, Privacy & Security.

**Linux**

There's no ready-made build. Run it from source; see the developer section at the bottom.

Only one copy runs at a time. Start it again on Windows and it brings the open one to the front.

Your settings are kept in `%APPDATA%\BlindPilot` on Windows, `~/Library/Application Support/BlindPilot` on macOS, and `~/.config/blindpilot` on Linux. If you used Claude Code Reader before, its settings are copied over once and left alone.

## Set up an agent

The first time you run BlindPilot, a wizard walks you through it. You can get back to it any time from Model, Manage Backends. It finds, installs, updates, and signs in to any of the ten backends. If an agent needs Node.js and you don't have it, the wizard installs that too. Nothing needs admin rights.

Sign In runs the agent's own sign-in, reads out the web address, and opens your browser. If the site gives you a code, BlindPilot asks for it and passes it on. Hermes asks its setup questions in a terminal, so for Hermes, Sign In opens a terminal window. Answer the questions there, then choose Already Signed In.

Gemini CLI and Antigravity CLI sign in with your Google account. Sign In opens them in a terminal window and starts Google's sign-in in your browser. In Gemini CLI, choose Sign in with Google if it asks. The wizard notices when you're signed in and moves on, then you can close the terminal. Heads up: Google stopped letting personal Google accounts use Gemini CLI on 18 June 2026, so a personal account may be refused. Work accounts on Gemini Code Assist Standard or Enterprise still work.

opencode needs an AI provider hooked up to it. Use Model, Connect a Provider, or type `/connect`. Pick a provider, then paste a key or sign in through your browser.

Rather use a terminal? To do it by hand:

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

# Command Code
npm install -g command-code
command-code login

# Gemini CLI
npm install -g @google/gemini-cli
gemini --screen-reader   # choose Sign in with Google, finish in the browser, then /quit

# Antigravity CLI, see https://antigravity.google/docs/cli/install/
irm https://antigravity.google/cli/install.ps1 | iex
agy                      # signs in with your Google account in the browser

# Muse Code, see https://developer.meta.com/ai/products/muse-code/
# On Windows, run these inside WSL.
curl -fsSL https://dev.meta.ai/install.sh | bash
muse login

# muse.ai, Meta's personal agent, see https://muse.ai/
uv tool install muse-cli
muse-cli auth export     # copies your muse.ai sign-in out of Chrome

# Hermes Agent, see https://hermes-agent.nousresearch.com/docs
hermes status     # shows the provider and model it will use
hermes model      # pick one, if none is set yet
```

Type `/status`, or use Model, Session Status, to hear which agent and model you're on, the permission mode, the folder, whether your next message carries on this conversation, how full the conversation is, and which account you're signed in as. When a conversation gets past 80% full, BlindPilot tells you once and suggests Compact Conversation.

## Using it

The window has three parts: the tabs, the answers, and the prompt. F6 and Shift+F6 move between them, and read the status bar on the way past.

Type in the prompt and press Enter to send. Shift+Enter starts a new line. Ctrl+Up takes you into the newest answer. Arrow through it, and press Tab to get back to the prompt.

Everything is in the menu bar, so if you forget a key, it's there.

- **File:** New Session (Ctrl+T), Recent Conversations (Ctrl+Shift+H), Hermes Conversations (Ctrl+G, only when Hermes is the agent), Side Chat in This Folder, Next and Previous Session, Set Projects Folder, Create Desktop Shortcut, Close Session (Ctrl+W), Quit (Ctrl+Q).
- **Conversation:** Stop Task (Ctrl+.), Attach Files (Ctrl+Shift+A), Slash Command (Ctrl+/), Saved Prompts (Ctrl+Shift+P), Compact Conversation (Ctrl+Shift+K), Start New Conversation (Ctrl+Shift+N), Find in Responses (Ctrl+F), Jump to Latest Response (Ctrl+R), Repeat Last Announcement (Ctrl+Shift+R), Export Conversation (Ctrl+E).
- **Model:** Backend, Model and Effort (Ctrl+Shift+E), Permission Mode, Session Status, Turn Status (Ctrl+Shift+T), Changed Files (Ctrl+Shift+D), What Agents Know (Ctrl+Shift+I), Backend Settings, Manage Backends, Connect a Provider.
- **Options:** what gets spoken, sounds, narration, the working sound, Remote Hermes, and Preferences (Ctrl+Comma). On a Mac, Preferences is in the app menu on Cmd+Comma like every other Mac app.
- **Chat:** accounts, profiles, models and logs for Chat mode. Only works when the Mode box is set to Chat.
- **Help:** Keyboard Shortcuts (F1), Check for Updates, Open Log Folder, Report a Bug, About.

Report a Bug asks what happened, what you expected, and how to make it happen. It shows you exactly what else goes in the report (versions and settings, never anything from your conversations), then opens GitHub with it filled in or copies it for you.

### Recent Conversations

Ctrl+Shift+H lists your old conversations. Sort them newest first, oldest first, by title, or by folder. Type in Filter to narrow the list. Under the list, Last exchange (Alt+T) shows your last message in the selected conversation and the answer to it, so you can tell apart two that started the same way. Enter opens one in a new tab.

F2 renames a conversation. Leave the name blank to go back to the original. Delete hides one, and Show hidden conversations brings it back. Names and hidden conversations are BlindPilot's own; the agents' files aren't touched.

### Saved Prompts

Ctrl+Shift+P opens your saved prompts: messages you send a lot, each with a name. Arrow through them and the full text shows underneath. Enter puts the prompt where your cursor is in the prompt box, ready to change and send. Nothing is sent until you press Enter.

New starts from whatever's already in the prompt box, so to save what you just typed, press Ctrl+Shift+P, then New, then OK. Edit changes one, Delete removes one after asking, and Move Up and Move Down put them in your order. Everything saves straight away.

### Narration

Follow everything, the default, speaks every step the agent takes. Keep up speaks only your message, the answer, and BlindPilot's own notices, like why a task is waiting or how it ended. The steps still show up in the list; they just aren't spoken.

Switch to Keep up when an agent runs a pile of steps at once and your screen reader falls behind. BlindPilot can't shorten your screen reader's speech queue, so this is how you keep up.

### Questions and permissions

When an agent asks you something, you get a dialog. If it writes a question into its answer instead of asking properly, BlindPilot notices that too and opens the same dialog. What you type goes back as your next message. A friendly "Want me to run the tests too?" at the end of a finished task is left alone. Turn the whole thing off under Options if you'd rather a task just end.

If you're not in Bypass permissions and Claude Code wants to do something it needs your OK for, you get the whole request to read line by line, then Allow, Allow for this session, or Deny. Deny is the default, and you can tell Claude why. In Plan mode the plan opens the same way: approve it, or keep planning and say what to change. Escape denies. Closing a dialog never allows anything.

## Keyboard

Help, Keyboard Shortcuts (F1) shows all of this inside the app.

- Ctrl+L: go to the prompt. Ctrl+T: new session. Ctrl+W: close it.
- F6 and Shift+F6: move between the tabs, the answers, and the prompt.
- Ctrl+Tab and Ctrl+Shift+Tab, or Ctrl+Shift+] and Ctrl+Shift+[: next and previous tab. Ctrl+1 to Ctrl+9: jump to a tab.
- Ctrl+Shift+H: reopen an old conversation. Ctrl+G: Hermes conversations, including ones still running.
- Ctrl+Shift+K: shrink this conversation. Ctrl+Shift+N: start a fresh one.
- Ctrl+F: search the answers. F3 and Shift+F3: next and previous match. Ctrl+R: jump to the latest answer.
- Ctrl+Shift+R: say the last announcement again, if a keypress cut it off.
- Ctrl+E: save the conversation as Markdown, a web page, or plain text.
- Ctrl+Shift+T: how long the current task has been running, what it did last, and how many messages are waiting.
- Ctrl+Shift+D: files changed in this folder since the last git commit, with lines added and removed. Enter reads the changes.
- Ctrl+Shift+I: what the agents know about you: their memories, instructions, skills and settings. Enter reads one. Alt+E edits it right there, and Ctrl+S saves. Alt+A adds a Claude Code memory, Command Code taste, or Hermes memory, and Delete removes one after asking.
- Ctrl+Shift+E: model and effort. Ctrl+Shift+M: cycle permission modes.
- Ctrl+/: slash commands. Ctrl+Shift+P: saved prompts. Ctrl+Shift+A: attach files. Ctrl+V: paste a picture as an attachment.
- Ctrl+.: stop the running task.
- Enter: send. Shift+Enter: new line. Ctrl+Up or Alt+Up: into the newest answer.
- In the answers: Enter reads a row in full. Shift+Enter opens the whole answer as a web page, so you can jump by heading, list, table and link; Escape comes back. C copies a row, Shift+C the whole answer, and Ctrl+Shift+C its last code block. Tab goes back to the prompt.
- While helper agents are running, a Subagents running list appears above the answers. Enter on one follows what it's doing, live.

On a Mac, Ctrl is Cmd. Two keys are different so macOS doesn't eat them: Recent Conversations is Cmd+Shift+H (Cmd+H hides the app), and Model and Effort is Cmd+Shift+E (Cmd+M minimizes). Use Cmd+Shift+] and Cmd+Shift+[ to change tabs, since Cmd+Tab belongs to macOS.

## Things to know about each agent

- **Claude Code:** warns you before a plan limit runs out, as well as when the conversation gets full.
- **Codex:** one copy runs for all your tabs. It shuts down after fifteen minutes doing nothing, and starts again with your next message. BlindPilot tells you when.
- **FreeBuff:** has no proper way for other apps to talk to it, so BlindPilot reads its screen behind the scenes and filters out the ads. If you send before it's ready, BlindPilot holds your message and sends it when it can.
- **opencode:** one copy runs for all your tabs. Needs a provider connected first.
- **Hermes:** keeps one connection open for the whole conversation. Its questions, approvals, and password requests come to you as dialogs.
- **Muse Code:** Meta's coding agent. On Windows it runs inside WSL.
- **muse.ai:** Meta's personal agent. It runs on Meta's computers, not yours, so it can't see your files; paste in what it needs. Picking it opens your main chat. New Conversation starts a side chat. Replies can take minutes, so BlindPilot waits up to an hour. Stop ends the wait, not the work; the answer still lands in your muse.ai chat. Model, muse.ai has Approvals, Schedules, Feed, Ideas, and Goals.
- **Command Code:** messages sent while it's working are queued and sent in order when it finishes. `/queue list`, `/queue clear` and `/queue resume` manage them. Every one of its slash commands works.
- **Gemini CLI** and **Antigravity CLI:** sign in with Google. They don't shrink conversations.

## Chat mode

Chat talks to an AI service directly. No agent, and it can't touch your files.

Set the Mode box to Chat, add an account and key under Chat, Accounts, then Chat, Refresh models, and pick one. Supported providers are OpenRouter, OpenAI, Claude, Gemini, Z.AI, Moonshot AI, Kimi, DeepSeek, Command Code, OpenCode Go, and any OpenAI-compatible endpoint. Keys are kept in your system's password store, not a text file.

Profiles hold a system prompt, a default account and model, temperature, token limit, and whether to stream. You can attach pictures, PDFs, and text files. OpenRouter accounts also get web search, image generation and its other server tools, plus thinking controls. Those tools run on OpenRouter's servers, not your computer.

If you used AccessibleAI before, its chat history is copied over once.

## Hermes on another computer

Turn on Options, Remote Hermes, and point it at the other machine. Test connection checks it before anything is sent.

If Hermes is on the same machine and only listening locally, a session token is enough:

```bash
HERMES_DASHBOARD_SESSION_TOKEN=pick-a-long-random-string hermes serve --port 9119
```

If other machines need to reach it, Hermes makes you set a username and password first:

```bash
hermes config set dashboard.basic_auth.username your-name
# Hermes prints the hash to store; run this from its own installation:
python -c "from plugins.dashboard_auth.basic import hash_password; print(hash_password('your-password'))"
hermes config set dashboard.basic_auth.password_hash 'the-hash-it-printed'
hermes serve --port 9119 --host 0.0.0.0
```

Then pick Username and password in Remote Hermes.

Which one to pick:

- **Hermes open to the network** (`--host 0.0.0.0`): Username and password. It refuses a session token.
- **Hermes only listening locally**, reached through a tunnel or a reverse proxy: Session token. It has no login to offer.

Behind a reverse proxy, tell Hermes the address you connect to, or it refuses you before checking anything:

```bash
hermes config set dashboard.public_url https://hermes.example.com
```

When Hermes says no, BlindPilot tells you why, and whether it was Hermes or something in front of it that refused.

## Updates

Help, Check for Updates downloads the new version, checks it's the real file, and restarts into it. If a task is running or you've got an unsent message, it asks first, and No is the default. Check for updates at startup does the same quietly and only speaks up when there's something new.

## Privacy

BlindPilot has no accounts, tracking, or analytics of its own. It talks to the agents and AI services you set up, each under their own privacy policy, and to GitHub to check for updates. Its log records what the app did, never your prompts, the answers, your files, or your passwords.

## Contributing

Pull requests are welcome. If BlindPilot has been useful to you, send a fix or a feature and I'll review it. If you want something that changes how the app works for everyone, ask in Telegram or Issues first.

---

## For developers

Everything below is technical.

### Run from source

1. Install Python 3.10 or newer. Releases are built with 3.12.
2. `pip install -r requirements.txt`
3. `python blind_pilot.py`

`blind_pilot.py` is the entry point; nearly all of the app is in `blindpilot_app.py`, with the backends in `agent_backends.py` and the per-backend `*_backend.py` / `*_worker.py` modules. The UI is wxPython, using native controls so MSAA/UIA, AT-SPI and NSAccessibility expose them.

### Tests and checks

Run these before opening a pull request. CI runs the same on Windows, macOS, and Linux.

```powershell
python -m pytest -q -W error
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

### Build

```powershell
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean BlindPilot.spec
```

`BlindPilot.spec` reads the version from `APP_VERSION` in `blindpilot_app.py` and carries the bundle identifier, minimum macOS version, and icon (`tools/make_icon.py` generates the icon files into `packaging/`). The one-directory layout is what lets the updater replace the app after it exits. The Windows installer is `installer/BlindPilot.iss` (Inno Setup).

Pushing a `v*` tag runs `.github/workflows/release.yml`. It runs the tests and the packaged startup checks (`--startup-smoke`, `--startup-gui-smoke`), then publishes the Windows installer, the Windows zip, the Apple Silicon macOS zip, and a SHA-256 file for each. The updater verifies that SHA-256 before installing. macOS builds are ad-hoc signed, not notarized.

### How each backend is driven

| Backend | How BlindPilot talks to it | Model and effort | Permission modes | Compaction | Asks questions |
|---|---|---|---|---|---|
| Claude Code | Streaming JSON CLI | Yes | Yes | Yes | Yes |
| Codex | app-server protocol, one shared process | Yes, with reasoning effort | Yes | Yes | Yes |
| FreeBuff | Hidden pseudo-terminal | Model yes, effort no. GLM 5.3 Flash by default | Managed by FreeBuff | No | Yes |
| opencode | Its headless HTTP server, one shared process | Yes, with per-model reasoning variants | Yes | Yes | Yes |
| Hermes | Gateway JSON-RPC over a local pipe or the network | Yes | Yes | Yes | Yes |
| Muse Code | MSP JSON-RPC over the stdio of `muse serve`, inside WSL on Windows | Yes, with reasoning effort | Yes | Yes | Yes |
| muse.ai | `muse-cli send` to the main chat or a side chat, one process per message | No, muse.ai picks its own | No, it runs on its own machine | No | In writing only |
| Command Code | Headless JSON CLI, one process per message | Yes, with reasoning effort | Yes | Yes | In writing only |
| Gemini CLI | Headless stream-json CLI, one process per message | Model yes (its auto, pro, flash and flash-lite choices), effort no | Yes | No | In writing only |
| Antigravity CLI | Print mode with stream-json in and out, one process per message | Yes, with reasoning effort up to max | Yes | No | In writing only |

"In writing only" means the backend has no question tool BlindPilot can intercept (Command Code withholds it from headless runs), so questions are detected from the end of the answer text. Claude Code and Codex are told in their system instructions to use their question tool; on Codex this is appended to your own `developer_instructions`.

Notes per backend:

- **Claude Code:** `claude -p` with stream-json in and out, kept alive per tab. Permission prompts arrive as `can_use_tool` control requests. `rate_limit_event` drives the plan-limit warnings; `modelUsage.contextWindow` from the result drives the context-fill line.
- **Codex:** one `codex app-server` shared by every tab, started on first message, closed after fifteen idle minutes.
- **FreeBuff:** no JSON or headless API. Its TUI runs in a hidden pseudo-terminal and the answer is scraped a sentence at a time, with redraws and ads filtered.
- **opencode:** one `opencode serve` on loopback behind a per-run generated password. History is read from its database, read-only. Images and text files go as file parts; other files are named by path.
- **Hermes:** `tui_gateway` JSON-RPC, one connection per conversation. BlindPilot announces the capability for blocking prompts, so questions, dangerous-command approvals and secret requests arrive as requests this window answers. Reopening a conversation parked on a question hands it back. Prompts with no UI here (Hermes' in-app terminal or browser preview, password-manager entries) are declined. The reasoning channel carries a spinner, so it's filtered.
- **Muse Code:** the CLI ships for macOS and Linux only, so on Windows it's reached inside WSL through the same bridge Hermes uses, with the working directory translated. Turns run over MSP. A Muse Spark access refusal is reported instead of hanging the turn.
- **muse.ai:** through `muse-cli`, one process per message, waiting up to an hour. A compatibility hook for muse-cli 0.3.2 lets `muse-cli watch` subscribe the way the web client does, for live activity. API contracts are in [`docs/museai-gateway.md`](docs/museai-gateway.md).
- **Command Code:** `-p` per message, resumed by the session id it reports. BlindPilot adds queueing, Steer (stop and resume with the new instruction), and runs console-only commands (`/context`, `/usage`, `/status`, `/export`, `/todos`) in the console off screen against the same conversation. `/compact` writes a new session and leaves the original listed.
- **Remote Hermes:** Hermes issues a short-lived single-use ticket per WebSocket connection; BlindPilot logs in and fetches one each time. The login session is sent with the connection so cookie-affinity load balancers and authenticating proxies land it on the process that issued the ticket. `websocket-client` is only needed for this path; if it's missing BlindPilot says so and keeps running.

### Settings, data and logs

- Settings: `config.json` in `%APPDATA%\BlindPilot`, `~/Library/Application Support/BlindPilot`, or `~/.config/blindpilot`. Saved prompts, conversation names and hidden conversations live here. On macOS, settings from older versions are moved once, never overwriting.
- Chat data: `chat.sqlite3` beside the config. Keys are in the OS credential store.
- Logs: a rotating `blindpilot.log` (four files of one megabyte) and `blindpilot-crash.log` for native crashes, in `%LOCALAPPDATA%\BlindPilot\Logs`, `~/Library/Logs/BlindPilot`, or `$XDG_STATE_HOME/blindpilot`. Help, Open Log Folder opens it.
- Log level is INFO. Set `BLINDPILOT_LOG_LEVEL=DEBUG` for a bug report. Prompts, answers, file contents and credentials are never logged at any level. On Windows the crash log also records first-chance COM exceptions from screen-reader interop; those are noise, not crashes.
- Only one instance runs: on Windows a second launch brings the first forward, setup wizard included; on Linux, and macOS from source, the second copy says so and exits.

## License and credits

MIT. See [LICENSE](LICENSE). Use it, change it, share it, or package it, no permission needed. Every source file carries an `SPDX-License-Identifier: MIT` header.

Copyright (c) 2026 doubletaponair and BlindPilot contributors. BlindPilot was written with AI coding help. Claude Code Reader is credited here, in the About dialog, in the source headers, in [CREDITS.md](CREDITS.md), and in the original specification kept at [`original-claude-code-reader-spec.html`](original-claude-code-reader-spec.html).
