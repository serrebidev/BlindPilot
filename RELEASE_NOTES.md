# BlindPilot 0.43.6

This release repairs the FreeBuff integration after an audit of launching, starting and continuing conversations, reading replies, stopping terminals, model settings, and saved history.

- A new conversation is matched to the FreeBuff process that created it. Another tab's newer chat cannot become this tab's conversation. A missing CLI chat or one from a different folder is refused before launch, because FreeBuff otherwise falls back silently to an unrelated recent chat. Open the original folder to continue a CLI conversation. FreeBuff Desktop transcripts open for reading and copying; continue those conversations in FreeBuff Desktop.
- Startup waits for this terminal's own boot milestone. A terminal started ahead of time remembers where its launch began in the resumed chat's log, and a replacement terminal answers its own model picker. The disconnect written during a normal boot no longer looks like a fresh mid-turn failure after sign-in finishes.
- Windows launch failures release every terminal handle. Shutdown closes the output pipe before the pseudoconsole, preventing a hang on older Windows versions, and stops detached background children as well as the CLI. Paths containing spaces, ampersands, percent signs, and other shell characters are passed literally to batch launchers.
- Damaged or unreadable FreeBuff settings are preserved, and model-save failures are reported. Malformed catalog-cache timestamps and cyclic model aliases no longer crash model selection.
- Replies that repeat prompt words keep their earlier paragraphs. Recent Conversations includes older unbucketed CLI chats and reads small Desktop conversations even when their shared database is large.
