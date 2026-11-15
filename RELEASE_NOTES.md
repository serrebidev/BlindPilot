# BlindPilot 0.57.2

This is the second release in the feature comparison with The Chat Place and FastChat: backups, restores, kept transcript copies, and conversation imports.

Under File, Recent Conversations, Back Up BlindPilot saves your Chat database and settings to one archive file with a manifest. You can include kept transcript copies with a checkbox; backups never include API keys, sign-in tokens or cookies, and the archive says so in its manifest.

Restore from Backup reads the archive and previews it first: which accounts, profiles, conversations and messages it holds, and whether it includes kept transcripts. Restoring keeps your current history aside under a timestamped name before installing, so nothing is silently overwritten, and credentials are never touched.

Kept Transcripts automatically saves read-only copies of conversations you open or complete, so provider cleanup cannot erase them. The dialog lists the copies and how much storage they use, lets you delete copies with confirmation (deleting a copy never deletes the original), and offers a switch to turn future keeping off. Copies never expire by themselves.

Import Conversation takes Basilisk .bskc files and BlindPilot interchange JSON. Imports preserve messages, attachments and supported settings; anything the importer cannot carry over, such as per-message model choices or URL attachments, is reported in plain words rather than dropped silently.

Usage and balance reports, Ollama, compaction, agent controls, connected-tool management, sound and voice customization, and automatic computer use remain later stages. They are not included in 0.57.2.
Windows ships an installer and portable archive; macOS ships an Apple Silicon archive. Linux remains source-only.
