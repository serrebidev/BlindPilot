# BlindPilot 0.43.7

This release fixes issues found by live testing FreeBuff 0.2.27 after the previous integration audit.

- Sign-in and credit usage recognize FreeBuff accounts whose tokens are in the system's secure credential store. Tokens stay there; BlindPilot does not copy them into the account file. Older file-based accounts remain supported.
- Multiline prompts, accented characters, and emoji reach FreeBuff intact. Custom question answers use the same terminal paste handling.
- Messages sent while FreeBuff works wait in BlindPilot's queue. Steer stops the current turn and resumes the conversation with your new instruction, avoiding instructions lost by a busy terminal. Stop pauses the queue; use /queue list, /queue clear, or /queue resume to manage pending messages.
- /history opens Recent Conversations and /usage shows FreeBuff credit usage in BlindPilot.
