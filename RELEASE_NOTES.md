# BlindPilot 0.59.0

This release finishes the last two agent controls from the comparison with The Chat Place, both for Claude Code sessions.

Stop Background Task, in Conversation, Find and Browse, lists what Claude is running in the background: agents it started and shell commands it left running, each by what it is doing. Choose one and only that one stops. The turn and every other task keep going. Before, Stop ended the turn and all of its agents together, and a background command could only be stopped by stopping everything.

Put Files Back Before Here, in Conversation, Find and Browse, Conversation Copies, undoes Claude's file edits. Select one of your messages or its answer, confirm, and every file Claude changed with its edit tools since that message goes back to how it was. Changes made by shell commands are not undone, and the conversation itself stays as it is, so you can carry on or branch from there. BlindPilot now asks Claude Code to keep file checkpoints for every session it runs, so this works for turns from this version on; for older turns it says no checkpoints were kept.

Smaller fixes: Chat mode says that AccessibleAI data was imported once, instead of on every new chat tab. Importing a sound pack works when the temporary folder is on a different drive, which is common on Linux. Computer Use no longer leaves an app it launched running when that app never opens a window.

Windows ships an installer and portable archive; macOS ships an Apple Silicon archive. Linux remains source-only.
