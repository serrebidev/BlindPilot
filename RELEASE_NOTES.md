# BlindPilot 0.48.2

Codex conversations with very large rollout files open again.

- **Large Codex histories:** a conversation whose rollout file exceeds 64 MiB no longer fails to reopen when its messages are available in Codex's history database. BlindPilot reads the database first and applies the file-size guard only to the rollout-file fallback, so huge tool output in the rollout no longer blocks loading the conversation.
- Regression tests cover database loading with an oversized rollout and the file fallback at and above the size limit.

Thanks to pitermach for the fix in #101.
