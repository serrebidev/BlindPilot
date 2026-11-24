# BlindPilot 0.60.0

This release brings over the features FastChat added since BlindPilot last caught up with it. Chat conversations on any account can now search the web and read pages, OpenAI accounts can take attachments and show the model's thinking, and Linux gets computer use and a voice of its own. Half of the work was done by muse.ai, the other half by Claude.

## Chat

**Every account can search the web.** Until now, only OpenRouter and ChatGPT plan accounts could look anything up, because their providers run search themselves. Every other account, such as OpenAI with an API key, Claude, Gemini, DeepSeek, Ollama or a custom endpoint, now gets three tools that BlindPilot runs itself: a web search through DuckDuckGo, which needs no key; reading one public web page as text; and checking the date and time on this computer. They only read, so nothing stops to ask permission. The model decides when to use them, and you hear "Searching the web", "Reading a web page" or "Checking the date and time" when it does. They are on by default. To turn them off for a conversation, clear "Web tools" in its profile. A model that cannot take tools at all, which includes many local Ollama models, still answers without them. BlindPilot tells you it answered without web search instead of failing the message.

**OpenAI accounts take attachments.** An OpenAI account uses OpenAI's Responses API, and until now that refused every attached file and asked you to switch the account to Chat Completions. Pictures, PDFs, text and other files now go through on the Responses API too.

**OpenAI shows the model's thinking.** A reasoning model's thinking summary now arrives as thinking, the same as on a ChatGPT plan, and the profile's thinking effort is sent. Models that take no thinking settings, like gpt-4o, and organizations OpenAI has not verified for summaries are retried once without them. BlindPilot then remembers that for the rest of the session, so later messages do not pay for a refused request.

**Tools work on every protocol.** Before this release, only the Chat Completions protocol could run a tool and carry on with the answer. Claude's Messages protocol and OpenAI's Responses protocol now can too. Gemini and OpenCode Go accounts were quietly losing their tools on the way to the provider; they keep them now.

**Computer use no longer fails on its first action.** Computer use passed its action runner in a form the providers could not call, so the first press or typed text ended the turn with an error. This is fixed.

**Attachments in any encoding.** A text file saved in UTF-16, with a byte order mark, or in the older Windows encoding used to reach the model as garbled characters. It now arrives as readable text. A file that is not text at all, sent to a Claude account, used to arrive as a page of nonsense characters. Now it goes as base64 if it is 100 KB or less. A larger one is named, and the model is told it was too large to include.

**Import VoLlama conversations.** History, Import now reads VoLlama JSON saves, as well as Basilisk conversations and BlindPilot's own exports. A save without a system message keeps your default system prompt. A JSON file that is not a VoLlama save is refused, and nothing is created.

## Linux

**Computer use on Linux.** A Chat conversation can now operate another app on Linux, as it already could on Windows and the Mac. It works through the app's accessibility tree, the same tree Orca reads, and never by screen position. BlindPilot asks before each action, because Linux has no global stop key yet. It was tested by filling in and confirming a real dialog.

**A voice of its own on Linux.** Sound and speech settings can now use Speech Dispatcher voices directly on Linux, with their own voice, rate and volume, separate from Orca's. Voices are listed by name, speech module and language, with your own language first. BlindPilot's sentences wait their turn instead of cutting each other off, and Orca does not interrupt them. Stopping speech stops only BlindPilot's voice. This needs the python3-speechd package. Without it, there are no voices and nothing breaks.

## Fixed in testing

The release checks failed to find the macOS test run after the move to GitHub's newer macOS runner image, because that image is named after its Xcode version. They now recognise it.

# BlindPilot 0.59.6

This release fixes Hermes for people who install it fresh, makes Hermes messages work again, and makes muse.ai quicker and easier to read. Every Hermes and muse.ai dialog was tested against real accounts before release.

## Hermes

**Hermes is found after a fresh install.** On Windows, a new install from the Hermes docs made BlindPilot say Hermes was not installed. Hermes now builds its Python environment in a different place, under `%LOCALAPPDATA%\hermes\installs`, and BlindPilot only looked in the old one. BlindPilot also assumed Hermes lived in `.hermes` in your user folder, but its real home on Windows is `%LOCALAPPDATA%\hermes`. Both are fixed. If you set Hermes up before this change, BlindPilot now uses Hermes' current environment rather than the old copy left behind.

**Hermes messages work again.** Every message failed with "submission_id: Extra inputs are not permitted" when no Hermes gateway was already running. The always-on Hermes gateway needs that identifier, but the private gateway BlindPilot starts on its own refuses it. BlindPilot now sends it, and resends without it when Hermes says that is the problem.

**Recent Conversations keeps your Hermes chats.** Right after BlindPilot had talked to Hermes, Recent Conversations could show no Hermes conversations at all. Hermes' history file is unreadable for a moment as its gateway closes, and BlindPilot gave up instead of trying again. It now waits briefly and reads it again.

**The Model dialog starts on the model you actually use.** It used to preselect the first model on the list (for Anthropic that was Fable 5.2) instead of the one in your Hermes settings.

**The slash command list comes from Hermes.** BlindPilot's own copy had fallen behind. It still offered five commands Hermes no longer has, and it was missing 78, including /model, /new, /resume, /compact and /status. The picker now asks your Hermes for its list, and falls back to the built-in one when Hermes cannot be asked.

## muse.ai

**Faster.** Switching to muse.ai's main chat now takes about 3 seconds instead of 6 to 12, and Recent Conversations lists muse.ai chats in about 1 second instead of 3 to 5. BlindPilot asks for the main chat's id once per session instead of every time. The rest of the speedup comes from a muse-cli change: each muse-cli command used to fetch three sign-in tokens before it could connect, which took about 2.3 seconds. That change has been sent to muse-cli as a pull request. Once it is released, choose Update for muse.ai in Manage Backends to get it.

**Live status works with current muse-cli.** BlindPilot's live "what muse.ai is doing now" lines and activity rows only worked with muse-cli 0.3.2. With anything newer they went quiet. They now work with every version.

**Clearer muse.ai dialogs.**
- The Refresh button in Approvals, Schedules, Feed, Ideas and Goals was disabled after the first load. It now stays usable.
- After you pause, resume or edit a goal, the list stays on that goal. It used to jump back to the top of a list that can have 80 goals.
- Past approvals read "Allow always" and "Timed out" instead of "allow_always" and "timed_out".
- A disabled schedule's title starts with "Disabled", which explains why Run now is unavailable.
- The heartbeat schedule is called "Heartbeat" instead of "Untitled".
- A schedule's next run is a date and time instead of a ten-digit number.
- The Feed count says "feed posts".

# BlindPilot 0.59.5

This release makes the "Send with Ctrl+Enter" setting from 0.59.4 work everywhere you type a message.

The setting only reached the Agent prompt. Chat's Message field had its own fixed keys: Enter always started a new line and only Ctrl+Enter sent, whichever way the checkbox was set. In Chat, turning the setting on or off seemed to do nothing. Chat now follows the setting too. With it off, Enter sends and Shift+Enter starts a new line, the same as the Agent prompt. With it on, Enter and Shift+Enter start a new line, and only Ctrl+Enter or the Send button sends. Ctrl+Enter sends in both modes.

Shift+Enter also inserts a real line break now. The text field was quietly adding an invisible vertical tab character instead of a new line, and that character went out with your message. The same fix covers Enter when it starts a new line.

# BlindPilot 0.59.4

This release adds a setting that stops Enter from sending your message by accident.

In Preferences, turn on "Send with Ctrl+Enter (Enter inserts a new line instead of sending)". With it on, pressing Enter in the prompt just starts a new line, like Shift+Enter does today. Your message goes only when you press Ctrl+Enter, or when you Tab to the Send button and press Enter there. Ctrl+Shift+Enter still saves the message as a prompt and sends it. The setting is off by default, so nothing changes unless you turn it on.

Thank you to István Tábor for asking for this: typing quickly with a screen reader, it is easy to hit Enter before a message is finished, and until now that sent it.

# BlindPilot 0.59.3

This release stops a console window from stealing focus when you send a Hermes prompt.

Before each Hermes prompt, BlindPilot checks whether a Hermes gateway is already running. That check started Hermes' Python without hiding its console, so Windows opened a console window for a moment, moved focus to it and NVDA read out its title and "terminal" before focus came back. The check now runs hidden. Nothing about the reply changes.

An audit of every place BlindPilot starts a process found no other Windows launch that opens a console. The only ones that do are the ones meant to: the interactive sign-in terminal and an app you ask BlindPilot to open. A new test fails if any process is started without hiding its console.
