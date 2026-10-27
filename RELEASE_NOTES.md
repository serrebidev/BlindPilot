# BlindPilot 0.50.0

Seven new features, from a second look at The Chat Place by Kelly Ford: its 0.1.5 to 0.3.0 releases added things BlindPilot didn't have yet. Each is built BlindPilot's own way, on the tabs, lists and dialogs you already use.

## Agents working together

- **Send Response to Another Session (Ctrl+Shift+O):** hand the answer you're on, or the newest one, to another tab. A Codex session can review what Claude Code just wrote, or Hermes can check what Codex planned. Pick the session, change the message if you like, and press Send; it goes labelled with which agent and session wrote it, and you land in that session to hear the reply. A session that is still working is refused rather than interrupted, and anything you were typing there is kept.
- **Agent conversations:** in the same dialog, tick Keep passing replies between the two tabs and choose how many hand-offs to allow (6 to start). Each session's next answer then goes to the other by itself, and every hand-off is announced. It stops at the limit, when a reply is empty or repeats the last one, when a turn fails, when a session is closed, or the moment you press Stop in either one, and it tells you which. Permission prompts and questions still come to you, and because every relayed message starts with who wrote it, one agent can't run a slash command in the other's session.

## Finding your way

- **Task List (Ctrl+Shift+J):** for a longer job, Claude Code and Codex keep a task list. BlindPilot now keeps it too: each step and whether it's to do, in progress or done, in a list you can filter, read and copy. When a step finishes you hear "Task done" and its name, and Turn Status (Ctrl+Shift+T) says how far along the list is.
- **Find in All Conversations (Ctrl+Shift+S):** search the words of every past conversation, from every backend, not just their titles. Each match tells you the conversation, the backend, how long ago, and the words around the match. Open Conversation reopens it in a new tab with that response selected. The search can be cancelled from its progress dialog.

## Starting work

- **New Session remembers your folders:** the folder box starts on the folder you opened last, and Up and Down go through the others you've used, newest first.
- **From GitHub (Alt+H):** pick one of your GitHub repositories, or type owner/name, and BlindPilot clones it into your projects folder if it isn't there yet. It needs the GitHub CLI (gh), signed in.
- **Work in a new git worktree (Alt+W):** type a branch name and the session works on its own copy of the repository, next to the original, so your folder stays exactly as it was. An existing branch is checked out; a new name makes the branch.
- **Send and save (Ctrl+Shift+Enter):** in the prompt, saves what you typed as a saved prompt, asking for a name, then sends it. Escape at the name does neither.

## Also fixed

- Command Code's automatic routing listed MiMo 2.5 for quick tasks, which Command Code 1.88.0 no longer offers; it now uses MiMo 2.6 Flash.
- The startup check no longer prints a wxWidgets "Class still has open windows" warning on the way out; the last closed windows are now freed before BlindPilot exits.

Thanks to Kelly Ford for The Chat Place, whose ideas these are.
