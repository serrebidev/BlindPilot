# BlindPilot 0.55.12

- Fixed: the Codex backend failed to launch on Windows with "[WinError 5] Access denied" for anyone who installed Codex the standard way, `npm install -g @openai/codex` (issue #150). BlindPilot runs Codex's native executable rather than the npm wrapper, but only looked for it in the nested package location; npm hoists the platform package beside @openai/codex, so the search missed and the launch fell back to the codex.cmd wrapper, which Windows cannot start as a process. The hoisted location is now found, and the model list probe uses the native executable too.

# BlindPilot 0.55.11

- Fixed: Codex turns funded by a ChatGPT-plan Account no longer fail before the model is asked with `subscription_sharing_unsupported_capability`. Plan turns now go through a loopback relay inside the app that drops the conversation items the plan endpoint refuses (configuration updates, compaction triggers, shell and other unsupported call/output items) and forwards everything else unchanged, streaming the reply back; Codex's own login never goes through the relay. The same change removes the shared plan-token cache (a process that waited for a token rotation now re-reads the record and does its own exchange) and stops translating Codex's own refusal text. From Farhan Khan, PR #148, fixing #147.

# BlindPilot 0.55.10

- Fixed: a Codex conversation bound to a ChatGPT-plan Account that was deleted or signed out no longer retries that dead Account on every turn. The failed turn still reports the failure, but the conversation now lets go of the binding when nothing has started on that Account, so the next turn takes the saved choice again — already back on Codex's own login after the deletion. A conversation that already has a thread keeps its Account, since a thread cannot move to another login. From Farhan Khan, PR #146, fixing #145.

# BlindPilot 0.55.9

- New: one Continue with ChatGPT sign-in now serves both Chat and the Codex coding backend, so nobody logs in twice for one plan. Codex runs on the plan through its own command-line configuration, fetching its token from the app's --print-plan-token mode — the token reaches neither the app-server's command line nor its environment, and the user's own Codex configuration is never written. Refresh is serialized across processes with the stored record replaced atomically; the process pool is keyed by Account, so two Accounts hold two processes and a conversation keeps the Account it began on; the account dialog's Codex choice is a labelled, keyboard-operable control that announces its value; sign-out and switching drop only that Account's process, and never while a turn is live. Codex's own login remains available and unchanged. From Farhan Khan, PR #143, landed via replacement PR #144. Design record: ADR 0003 and docs/chatgpt-sign-in-design.md.
- Fixed (test suite): the account-choice fixture no longer overrides the directory-migration tests, the Codex launch capture stubs the CLI lookup so the account test no longer needs a real codex binary installed, and tests/doubles.py is formatted for the current ruff — the three clean-runner CI failures #143 arrived with.

# BlindPilot 0.55.8

- Fixed: a Muse.ai follow test left its working cue running after the test ended, and the leaked cue thread kept spawning a player that a later test counted as its own launch — the macOS-only failure on PRs #139 and #140. The test now stops the cue in teardown, the same way closing a tab does. From Farhan Khan, PR #141.
- Docs: the responsiveness ticket plan's final gate count is corrected. From Farhan Khan, PR #140.

# BlindPilot 0.55.7

- Fixed: a resumed Muse.ai tab queued its pending send with nothing re-checked when the send actually fired, so it could land after the tab's backend had changed. The deferred send now carries a serial and re-checks it plus both backend fields before sending. From Farhan Khan, PR #139.
- Docs: ADR 0001 records the GUI-thread budget (callbacks stay within 50 ms, no waiting, process starts, or network work on the GUI thread) and the shutdown and process-ownership decision; AGENTS.md links it from a new GUI callbacks section, and the responsiveness ticket records are updated.

# BlindPilot 0.55.6

- Changed: question handling and shared process utilities now live in focused modules of their own — question types and parsing in questions.py, the question dialog in question_dialog.py, and shared process-group, pipe-cleanup and console-window helpers in process_utils.py — so unrelated changes are easier to isolate and review. Windows Job Object ownership stays in owned_process.py under ADR 0001. From Farhan Khan's spec #134, issues #135, #136 and #137, PR #138.
- Fixed: in the question dialog, selecting "Other" now announces which question's custom-answer field appeared and how to Tab to it, instead of a generic message; multi-select questions now show the previews of all selected options together, each labelled; and long question sets scroll, so every question stays reachable.

# BlindPilot 0.55.5

- Fixed: a structured question block a turn wrote into its answer could be lost when the turn went on to write about the block itself, because only the last block was read and the later samples are not the JSON they claim to be. The last usable block now wins. Blocks fenced with ~~~ are read as well as ones fenced with backticks, and a single question written as one object is taken. From Farhan Khan, PR #133.
- Docs: the README now says which backends support permission modes (FreeBuff has none, and muse.ai manages its own approvals) and that running from source uses Python 3.12, the version CI and releases use.

# BlindPilot 0.55.4

- Changed: dialogs now share one background-task lifecycle, so the slow work they start is tracked and shut down the same way instead of each dialog managing its own. From Farhan Khan, PR #131.

# BlindPilot 0.55.3

- Fixed: the window could freeze - keyboard focus and screen-reader speech stalling - while BlindPilot did slow work such as cloning a repository, checking a setup tool, resuming history, creating a desktop shortcut, or quitting with backends still running. Those actions now run in the background under the 50 ms GUI responsiveness budget (spec #125), and quitting ends the backend processes BlindPilot started instead of leaving them behind. From Farhan Khan's PRs #128 and #129, landed as #130.

# BlindPilot 0.55.2

- Fixed: connecting to Hermes failed with a raw "[gateway-exit] refused: ... is owned by a running gateway" error whenever a Hermes gateway was already running for the profile (started by the CLI, a login item, or another client). Blind Pilot now attaches to the running gateway instead of trying to start a second one, using Hermes' own discovery and session ticket. If attaching is not possible, the message says what happened and what to do, in plain words. Reported by Devin Prater, issue #127.

# BlindPilot 0.55.1

- Fixed: Find in All Conversations (Ctrl+Shift+S) read every saved conversation on the GUI thread, so a large history froze keyboard focus and screen-reader speech until the search finished. The search now runs on a worker thread: progress is announced as it goes, the progress dialog can cancel it, and a new search replaces a stale one instead of its results landing on top. First slice of the 50 ms GUI budget spec (#125), from Farhan Khan's issue #126.

# BlindPilot 0.55.0

- Fixed: the Codex model picker offered the catalog bundled inside the installed CLI, so a model the service retired stayed on offer — and a new one stayed hidden — until the CLI itself was updated. The list is now read from the service's refreshed catalog; models Codex marks hidden (including its internal codex-auto-review) are no longer offered, and if the service cannot be reached the bundled copy is used with a spoken note that it may be out of date.
- New: a window left open re-reads each tab's model list on a timer and announces which models were added or dropped. The interval is a Preferences setting, 15 minutes by default; set it to 0 to turn the background read off. From Farhan Khan, PR #124.

# BlindPilot 0.54.4

- Fixed: Hermes conversations could tear a token in half while streaming. Hermes delivers a reply in small fragments, and the worker released text as soon as a sentence end reached the end of its buffer — so a version number like 3.14.9, a date, a file name or a list marker whose dot landed on a fragment boundary was announced mid-token and split across two rows. The worker now holds a stop at the live edge until the next fragment shows whether the token continues, the same rule the other backends use. The final text is unchanged. Contributed by Devin Prater (PR #122, landed as #123).

# BlindPilot 0.54.3

- New: a reply can now carry its questions as a structured questions block with labelled options, so a backend that cannot ask through a question tool (such as Command Code) can offer choices in the question dialog instead of only a text field. Multiple questions and multi-select are supported, and a question with no options stays free text.

# BlindPilot 0.54.2

- Updated dependencies: pyobjc-framework-cocoa to 13.0 on macOS, and mypy, pytest and hypothesis to their latest versions for development and testing. No feature changes.

# BlindPilot 0.54.1

- Fixed: a GitHub Copilot turn could never start — the worker thread crashed on start with an AttributeError before the turn ran. Copilot turns now start normally.

# BlindPilot 0.54.0

The Muser backend added in 0.53.0 is removed again, at the owner's request, for now.

- Muser no longer appears as a backend to pick, install, update or sign in to.
- The muse.ai backend is unchanged, and no other backend is affected.
- Conversations that used Muser no longer appear in Recent Conversations.

# BlindPilot 0.53.0

A twelfth agent joins the list: Muser (https://github.com/nyarurant/muser), the muse.ai coding agent that runs on your own PC. This is muse.ai with full agent mode — the muse.ai backend BlindPilot already had runs its agent on Meta's machine and can only chat.

- **Windows only.** The wizard installs Muser with npm, installing Node.js 22 or later first if it is missing, updates it, and signs it in with `muser login`, which covers both the muse.ai web sign-in and registering this PC as a Muse device.
- **Turns and tool steps.** A turn runs as `muser exec --json`, one process per message, resumed by session id. Tool steps are announced as they happen, and the answer arrives whole at the end of the turn with its cited sources listed.
- **Permission modes** map onto Muser's own: Plan is read-only, Accept edits is auto-edit, and Auto and Bypass run with everything automatic. Muser has no reviewer mode, so nothing is lost in the mapping.
- **No model picker.** Muse chooses the model itself.
- **Where its work shows up:** its conversations in Recent Conversations with their task lists, so reopening one brings the task list back; its weekly muse.ai usage in Session Status; and its settings, instructions, skills and agent types in Backend Settings and What Agents Know.

# BlindPilot 0.52.6

A performance fix for agent mode on Windows, from a CPU audit. Two background loops ran far more often than they needed to:

- When an agent runs in a hidden terminal, a watcher keeps any console window off your screen. For the first 8 seconds of each run it checked 200 times a second, and every check enumerated every window on the desktop. It now checks 40 times a second — still quick enough to banish a console in the frame it appears — which ends the burst of CPU at the start of every run.
- The terminal's output reader polled 100 times a second for the whole life of a session, including the long stretches where an agent is thinking or waiting for you. After a second of quiet it now backs off to 20 polls a second; output is still picked up within 50 ms when it arrives.

Chat mode was audited too and measured clean: no measurable CPU while idle, and streamed replies are parsed incrementally instead of being re-parsed as they grow.

# BlindPilot 0.52.5

This replaces 0.52.4, which was published and pulled within the hour: it shipped while the full test suite was red (type errors in the new code). Same features, gate green this time.

Four features from the latest The Chat Place (the app formerly called AIChat, now at 0.3.3), audited against BlindPilot and added where they fit its tab-based GUI.

- **Chat Permissions:** Conversation, Find and Browse, then Chat Permissions shows what this conversation's agent may do without asking — the approvals you granted for this session, remembered by BlindPilot, plus the allow, ask and deny rules and extra working folders from Claude's own settings files, each line naming the file it came from. It is an honest inventory, not an editor: session grants live inside the agent's own process, and BlindPilot never writes to Claude's settings files.
- **Question previews:** when Claude asks a question and an option carries a preview (a code block, a layout), the question dialog now shows it in a read-only Preview box that follows the option you pick.
- **Ask before each hand-off:** agent conversations gain a checkbox, "Ask me before each hand-off". With it on, a finished reply waits instead of being delivered, and Pass On the Waiting Reply in Find and Browse sends it to the other session — after editing it, if you want.
- **Copy links as Markdown:** links collected from a response can now be copied as Markdown, so pasting gives the link's words over its address instead of a bare URL. Properties also reports whether Remote Control is on for a Claude conversation.

The rest of The Chat Place's recent work is its single window holding every session at once, sorted into needs-you, working and idle. BlindPilot covers the same ground with tabs, Recent Conversations and its background notifications, so that design stays theirs.
