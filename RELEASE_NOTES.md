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
