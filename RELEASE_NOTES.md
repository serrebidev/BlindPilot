# BlindPilot 0.37.0

muse.ai joins as a backend, the backend lists are alphabetical, and Recent Conversations finds far more of your conversations.

- muse.ai, Meta's personal agent, is the tenth backend. It is a different product from Muse Code: it lives on its own cloud machine with its own connectors, memory and browser, and BlindPilot talks to it through muse-cli. Install it from the wizard or with `uv tool install muse-cli`, and sign in with `muse-cli auth export`, which copies your muse.ai sign-in out of Chrome.
- Each BlindPilot conversation with muse.ai is its own muse.ai side chat, so nothing lands in your main muse.ai chat, and reopening a conversation goes back to the same side chat.
- You hear what muse.ai says while it works. Every status update it posts during a turn is added to the conversation as it arrives, followed by its answer. Connection notices are left out. Real work there can take minutes, so a turn waits up to an hour. Stop ends the wait, not the work: muse.ai finishes on its own machine and its reply appears in the muse.ai chat. A message typed while one is running waits its turn.
- muse.ai cannot see this computer, so the working folder and attachments do not reach it. Paste in what it needs.
- The Backend menu, the setup wizard and the history filter now list backends alphabetically, from Antigravity CLI to opencode. Each backend's send sound keeps the pitch you know it by.
- Recent Conversations lists much more. Codex moved its conversations out of the files BlindPilot was reading into a database of its own, so no Codex conversations were showing; they are back, along with the Codex desktop app's. FreeBuff Desktop's threads, Claude Desktop's Cowork sessions and your muse.ai side chats are listed too. Command Code Desktop's threads were already there, because it shares the command line's storage. Claude Desktop's ordinary chats are kept on claude.ai rather than on this computer, so they cannot be listed.

# BlindPilot 0.36.0

Edit anything in What Agents Know.

- Everything Ctrl+Shift+I lists can now be changed without leaving BlindPilot: memories, tastes, Hermes memory entries, instructions, skills, agents and settings. Press Edit, Alt+E, on an entry in the list, or while you are reading it, and the text becomes editable where you are. Save, or Ctrl+S, writes it back, and the button says Save while you are editing.
- Only what you edited changes. A Command Code taste is saved as its own line in its category's file, and a Hermes memory as its own entry; everything else in the file stays as it was. Memories, instructions and settings are saved as the whole file.
- Nothing is lost by accident. Closing with changes you have not saved asks whether to save them. A taste or Hermes entry emptied out is not saved; Delete is how to remove one. Edit in Your Editor still opens the file in another program if you prefer.

# BlindPilot 0.35.0

What Claude Knows becomes What Agents Know, and you can now add and remove what your agents remember.

- Every backend, not just Claude Code. Ctrl+Shift+I, in the Model menu, now lists what each of your coding agents keeps about you, grouped by backend: Claude Code's memories, instructions, skills, agents, commands and settings; Command Code's tastes, instructions, skills and agents; Hermes's memories and skills; Codex's instructions, memories and skills; Gemini CLI's GEMINI.md and skills; opencode's instructions and agents; and the settings files of all of them. Each line says how many there are, and only what exists is listed.
- Add and delete memories. In Claude Code memories, Add Memory asks for a name, a one-line description, a type, the folder, and the memory itself, and writes it the way Claude Code does, with a line in MEMORY.md. Delete removes a memory and its MEMORY.md line, after asking. Press Delete in the list, or Alt+A to add.
- Command Code tastes, one at a time. Command Code learns your preferences as tastes, one line each in a file per category. The list shows each taste with its category, Delete removes just that one, and Add Taste puts a new one in a category you pick or type.
- Hermes memories, one entry at a time. Hermes keeps notes about your work in MEMORY.md and about you in USER.md. Each entry is listed on its own, and can be deleted or added the same way.
- Everything else is read only here. Enter reads it, and Edit in Your Editor opens its file.

# BlindPilot 0.34.1

What Claude Knows sees more of your project.

- When a tab is open in a folder inside a repository, What Claude Knows (Ctrl+Shift+I) now also lists the skills, agents, slash commands and rules kept in the repository root's .claude folder, and in any folder between, just as Claude Code finds them. It stops at the repository root and never treats your home folder as part of the project.
- AGENTS.md files are listed with the instructions, next to CLAUDE.md, because Claude Code reads them too.

# BlindPilot 0.34.0

Read what Claude Code knows about you, and Claude Code sends work again.

- What Claude Knows, Ctrl+Shift+I, in the Model menu. Claude Code keeps your instructions, the memories it saves as you work, your skills, agents, slash commands and settings as plain files on your computer. This lists them by kind with how many there are, and Enter on a kind lists its files. Memories and skills are named by their own name and description, sorted so a letter jumps to the names starting with it. Enter on a file reads it, and Edit in Your Editor opens it in the program your system uses for that file; BlindPilot itself never changes them. The project's own CLAUDE.md files, settings and .mcp.json are included for the tab's folder. The idea comes from Kelly Ford's The Chat Place, where it is on Ctrl+Shift+K; in BlindPilot that key already compacts the conversation.
- Fixed: after 0.33.1's Codex permission change, every Claude Code message failed to start with an internal error, because the permission handler was handed to the turn twice. Claude Code turns start normally again and keep asking before tools in Default mode.
