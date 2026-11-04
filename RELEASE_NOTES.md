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
