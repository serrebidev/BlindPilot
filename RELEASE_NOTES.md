# BlindPilot 0.52.5

This replaces 0.52.4, which was published and pulled within the hour: it shipped while the full test suite was red (type errors in the new code). Same features, gate green this time.

Four features from the latest The Chat Place (the app formerly called AIChat, now at 0.3.3), audited against BlindPilot and added where they fit its tab-based GUI.

- **Chat Permissions:** Conversation, Find and Browse, then Chat Permissions shows what this conversation's agent may do without asking — the approvals you granted for this session, remembered by BlindPilot, plus the allow, ask and deny rules and extra working folders from Claude's own settings files, each line naming the file it came from. It is an honest inventory, not an editor: session grants live inside the agent's own process, and BlindPilot never writes to Claude's settings files.
- **Question previews:** when Claude asks a question and an option carries a preview (a code block, a layout), the question dialog now shows it in a read-only Preview box that follows the option you pick.
- **Ask before each hand-off:** agent conversations gain a checkbox, "Ask me before each hand-off". With it on, a finished reply waits instead of being delivered, and Pass On the Waiting Reply in Find and Browse sends it to the other session — after editing it, if you want.
- **Copy links as Markdown:** links collected from a response can now be copied as Markdown, so pasting gives the link's words over its address instead of a bare URL. Properties also reports whether Remote Control is on for a Claude conversation.

The rest of The Chat Place's recent work is its single window holding every session at once, sorted into needs-you, working and idle. BlindPilot covers the same ground with tabs, Recent Conversations and its background notifications, so that design stays theirs.
