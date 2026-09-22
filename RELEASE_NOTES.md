# BlindPilot 0.29.17

The three JSONL transcript formats are now read by one reader instead of three copies.

- Claude Code, Codex and Command Code each keep one conversation per file, one JSON record per line. Only three things differ between them - where the files live, which record shapes they use, and how a turn's text is found - so the three near-identical readers in `session_history.py` are now a single reader with the per-format differences passed in.
- No behaviour change is intended: the same transcripts read exactly the same way, with less code to keep in step. Contributed by blindndangerous in #53.
