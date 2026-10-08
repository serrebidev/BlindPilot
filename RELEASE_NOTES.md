# BlindPilot 0.34.0

Read what Claude Code knows about you, and Claude Code sends work again.

- What Claude Knows, Ctrl+Shift+I, in the Model menu. Claude Code keeps your instructions, the memories it saves as you work, your skills, agents, slash commands and settings as plain files on your computer. This lists them by kind with how many there are, and Enter on a kind lists its files. Memories and skills are named by their own name and description, sorted so a letter jumps to the names starting with it. Enter on a file reads it, and Edit in Your Editor opens it in the program your system uses for that file; BlindPilot itself never changes them. The project's own CLAUDE.md files, settings and .mcp.json are included for the tab's folder. The idea comes from Kelly Ford's The Chat Place, where it is on Ctrl+Shift+K; in BlindPilot that key already compacts the conversation.
- Fixed: after 0.33.1's Codex permission change, every Claude Code message failed to start with an internal error, because the permission handler was handed to the turn twice. Claude Code turns start normally again and keep asking before tools in Default mode.
