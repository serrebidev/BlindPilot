# BlindPilot 0.57.0

This is the first release in the feature comparison with The Chat Place and FastChat: conversation copies, separate questions, bulk exports, and more resilient Chat requests.

Branch from Here keeps the conversation up to a selected message. Retry from Here copies earlier messages and restores the selected question and retained attachments so you can edit and send it again. Original conversations and workspace files stay unchanged. Claude and Codex use native fork protocols when saved boundary IDs are available; other cases explicitly use a text-context copy. Agent copies are created by the backend when you first Send; Chat copies are saved immediately, including stored attachment bytes.

Ask Aside asks about completed messages in a separate window. You choose the Chat account and model that receives the text copy. All tools are disabled, including account-configured tools and ChatGPT's usual built-in tools. Source history and drafts stay unchanged, and Stop or Close affects only Aside. Find these commands under Conversation, Find and Browse, Conversation Copies. Branch and Retry are also on a message's context menu.

File, Recent Conversations, Export Conversations writes selected saved Agent and Chat conversations as Markdown, HTML or plain text. Filter and select what to keep, choose a format and folder, and export. Existing files are never replaced; duplicate names get numbers. Cancellation keeps completed files and removes unfinished output. Text exports include attachment names, not binary contents. Reopen the dialog if source history changes while it is open.

Chat requests retry transient failures up to twice, with cancellable delays. Once text, thinking, tool activity or an accepted batch has begun, requests are not replayed. The Windows updater also fixes an archive-module conflict between PowerShell 7 and Windows PowerShell 5.1. Command Code's free-model shortlist now uses Inkling 2 Small instead of the retired Beam entry.

Automatic computer use, backups/imports, provider reports, connected-tool management, and sound/voice customization remain later stages. They are not included in 0.57.0. Windows ships an installer and portable archive; macOS ships an Apple Silicon archive. Linux remains source-only.
