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
