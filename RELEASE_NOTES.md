# BlindPilot 0.58.0

Chat mode now has tabs, the same way Agent mode does. Until now Chat mode showed one conversation at a time, so opening another meant leaving the one you were in. Now File, New Chat (Ctrl+T) opens a fresh chat in its own tab, and File, Close Chat (Ctrl+W) closes the current one. Ctrl+Tab and Ctrl+Shift+Tab (Cmd+Shift+] and Cmd+Shift+[ on a Mac) move between chats, and Ctrl+1 to Ctrl+9 jump straight to one.

The tab strip above the chat is the same strip Agent mode uses. In Chat mode the screen reader calls it Chat tabs, and each tab is named after its conversation, or New chat until the first message gives it a title. Arrowing along the strip switches chats without leaving it; Tab moves into the visible chat. Moving to a chat with Ctrl+Tab says which chat of how many it is and puts focus in its message box.

Each tab keeps its own conversation, account, profile, model, attachments and running answer, so one chat can keep generating while you read or type in another. Closing a tab stops only that tab's answer, and the last chat cannot be closed. Switching to Agent mode puts the session tabs back exactly as they were, and switching to Chat mode brings the chat tabs back.

Windows ships an installer and portable archive; macOS ships an Apple Silicon archive. Linux remains source-only.
