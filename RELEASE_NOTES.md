# BlindPilot 0.64.0

This release makes usage limits something you can act on without leaving BlindPilot: banked Codex resets can be read and spent in the app, and a turn a usage limit stops is held and sent again when the limit is back.

## Codex

**Banked usage resets.** Some ChatGPT accounts hold banked, one-time usage resets OpenAI granted them. Session Status now names how many your account holds, with when each was granted and when it expires, and `/usage reset` reads those aloud and asks before spending one. Reset history lives on the web at chatgpt.com/settings/usage. Claude Code has no manual reset of any kind, so nothing is offered for it.

## Claude Code and Codex

**Auto-continue at a usage limit.** When a usage limit stops a turn, BlindPilot holds that turn and sends it again once the limit resets, instead of leaving you to re-send it by hand five hours later. This is on by default; Preferences, "Auto-continue a turn stopped by a usage limit when the limit resets", turns it off. Pressing Stop, starting a new conversation, or sending a different message cancels the hold.

**Let the assistant spend a banked reset.** Preferences also has "Let the assistant spend a banked Codex usage reset when a limit stops the turn". It is off, because a banked reset is spent once; turn it on and BlindPilot may spend one itself, only when an allowance sits below ten percent remaining, the same gate the ChatGPT app uses.

# BlindPilot 0.63.0

This release lets Codex use your web browser, and brings BlindPilot up to date with what The Chat Place has added since 0.6.3.

## Codex

**Codex can use your web browser.** Asking Codex to do something in your browser used to get "browser use is disabled", although computer use worked. Codex checks with the ChatGPT app before it touches a browser, and only the ChatGPT app answered that check. BlindPilot now answers it itself, so Codex tabs can use your browser through the ChatGPT browser extension: open pages, read them, click and type, in the browser and profile you are already signed in with. You need the ChatGPT app installed, which provides Codex's computer use plugin, and the ChatGPT browser extension in your browser. Preferences, "Let Codex use your web browser", turns it off, and that takes effect from Codex's next browser step. Windows only for now.

## Claude Code

**Suggested messages.** After a reply, Claude can suggest what you might send next. Turn it on with Preferences, "Suggest what to send next after each Claude reply"; it is off at first, because each suggestion uses a little of your plan. The suggestion is put in your empty message box, selected, and you hear "Suggested message in the message box". Type to replace it, or press Enter to send it as it is. Your own text is never replaced, and not every reply has a suggestion. It is not used on API credits.

**Run as an agent.** Model and Effort (Ctrl+Shift+E) in a Claude Code tab now has "Run as agent": the tab can run as one of the agents Claude Code knows for its folder, such as Explore, Plan or your own, with that agent's instructions and tools. The list names each agent, and an agent that always uses its own model says so. The Agent description box under it (Alt+P) holds the chosen agent's whole description, to read line by line, and changes as you choose another agent. A change takes effect from the tab's next message.

**Approve, don't start.** Claude's plan dialog has a new button, "Approve, don't start" (Alt+D). It tells Claude the plan is right but not to build it yet, and the tab stays in plan mode. Anything typed in the box goes with it. Keep planning is still the default button, so Enter never approves anything.

**The weekly Fable limit, by Claude Code's name.** Usage and limit warnings now name the window only Fable turns report "Weekly Fable limit", and the usage credits a plan can spend past its limits "Usage credit limit", as Claude Code does.

## Mac

**Command+C copies from the formatted view.** Copying from a response opened in the formatted view did nothing on a Mac: the page's security policy refused the script wx uses to copy. The viewer now allows only that; scripts in an answer are still never run, and exported pages are unchanged.

**Number boxes are named for VoiceOver.** On a Mac a number box is a text box and a stepper, and Tab stops on each. The stepper was read as just "stepper"; both parts now carry the box's name, in Preferences, Remote Hermes and agent hand-offs.

# BlindPilot 0.62.0

This release finishes API credits from The Chat Place: Codex tabs can now be charged to an OpenAI API key, and both Claude Code and Codex get a spending limit for each message.

**Codex on API credits.** A Codex tab can now be charged to an OpenAI API key instead of your ChatGPT plan. Open Conversation, Find and Browse, API Credits in a Codex tab, add your key, and choose "Charge this tab to API credits". The key is kept in this computer's credential store and goes only to api.openai.com. Codex runs a separate copy of itself for API credits, so your ChatGPT sign-in and your other Codex tabs are not touched. It takes effect from the tab's next message.

**What a Codex reply cost.** Codex reports the tokens it used, not dollars, so each reply ends with an estimate from OpenAI's prices, for example "Cost: an estimated 3 cents". If BlindPilot does not know a model's price, it says the tokens instead, for example "Cost: unknown, 12,400 tokens", and counts the whole per-message limit against your overall limit, so unpriced messages still run it down.

**A limit for each message.** Claude Code and Codex on API credits now stop a reply partway once it reaches the per-message limit, $2 to start. You hear "Stopped at the $2.00 per-message limit for API credits", then what the message cost, and you can send another message straight away. A message can go a little over, by the cost of one request to the AI. Change the limit in API Credits.

**Claude and Codex are counted separately.** Each has its own key, its own per-message and overall limits, and its own total. What you had already spent on Claude is kept. A Claude reply you stopped with Stop is now counted too; before, its cost was left out of the total.

As before, every figure is BlindPilot's own count on this computer. The Claude Console and the OpenAI dashboard show what you are actually billed.

# BlindPilot 0.61.1

**Focus comes back with you.** Switching away from the BlindPilot window and back (for example with Alt+Tab) could leave keyboard focus on the Attach button instead of where you left it, so every message started with a hunt for the prompt field. The window now remembers which control had focus when you left it and puts focus back there when you return; if there is nothing to restore, focus starts in the prompt. Reported by István Tábor — thank you.

# BlindPilot 0.61.0

This release brings BlindPilot up to date with The Chat Place 0.6.3, ships a Linux download again, and makes Command Code's quick tasks use a stealth model when one is offered.

## Linux

**BlindPilot is released for Linux again.** Every release now has `BlindPilot-Linux-x64.tar.gz` beside the Windows and Mac downloads. All three are built on GitHub's runners and published on Forgejo. Unpack it in a folder you own and run `BlindPilot/BlindPilot`. Help, Check for Updates installs new versions in place: it waits for BlindPilot to close, swaps in the new folder, and puts the old one back if anything goes wrong. The Linux build needs GTK 3 and AT-SPI from your distribution, which most desktops already have. Linux was withdrawn earlier because the test suite failed under the distribution's older wxPython. The build now uses wxPython 4.3, where the suite passes.

## Claude Code

**Claude in Chrome.** Claude can now use Chrome or Edge in Claude Code tabs: open pages, read them, fill in forms and click. You need the Claude in Chrome extension, signed in to the same claude.ai account as Claude Code. Each website asks your permission first. Preferences, "Let Claude use Chrome", turns it off. A change takes effect from each tab's next message.

**API credits.** A Claude Code tab can now be charged to an Anthropic API key instead of your Claude subscription. To set it up, open Conversation, Find and Browse, API Credits, add your key, and choose "Charge this tab to API credits". The key is kept in this computer's credential store. BlindPilot removes any redirect from Claude Code's environment, so the key goes only to Anthropic.

Before the tab's first message on API credits, BlindPilot asks "Send on API credits?". After you send, you hear "Sent on API credits", and each reply ends with what it cost, for example "Cost: 6 cents". The cost is what Claude Code reports for that reply.

An overall limit, $20 to start, stops sending once it is spent. Your message stays in the box. Raise the limit or reset the total in the same dialog. The totals are BlindPilot's own count on this computer, and the Claude Console shows what you are actually billed. Claude in Chrome is off on API credits, because Claude Code turns it off when it runs with a key.

**A warning when your subscription would be billed to a key.** If an API key in your environment, or an apiKeyHelper or key in a Claude Code settings file, would make Claude Code charge a subscription tab to that key, BlindPilot now tells you once per tab before the first message. A `claude setup-token` sign-in belongs to your subscription and does not trigger it.

## Codex

**Long messages go through.** Codex takes at most 32,000 characters in a message, so a longer one, such as a pasted build log, used to fail. BlindPilot now saves it to a temporary file and asks Codex to read the file. You hear that it was given to Codex as a file, and the file is deleted when the turn ends.

## Links

**Open files named in a reply.** The Links list (Ctrl+Shift+L) now finds files on this computer as well as web addresses. That includes links to files, file paths in code, and a code block that holds just a path. Relative paths are taken from the tab's folder. A file gets Open, which opens it in its usual app, and Open Containing Folder. Programs, scripts and shortcuts only offer the folder and Copy, so a reply can never start a program with one key. A file that isn't there is Copy only. Each row says which of these applies, and the file is checked again just before it opens.

## Command Code

**Quick tasks use a stealth model first.** Command Code retired the free Inkling 2 Small model. The quick tier no longer depends on one free model's name. It takes the first stealth model the installed Command Code offers. If there is none, it takes a free one, and only then the paid shortlist. Stealth models are previews whose providers may train on what you send.

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
