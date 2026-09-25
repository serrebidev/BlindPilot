# BlindPilot 0.29.24

The model picker now tells you which model you are choosing. People looking for Opus 5.5, or any of the other current models, could not find them, because Claude Code's list in Model and Effort only offered its short aliases: "opus", "sonnet", "haiku", "fable" and so on, with no version anywhere.

- Every Claude Code model choice names its version. The list now reads "opus: Opus 5.5", "sonnet: Sonnet 5", "haiku: Haiku 4.5", "fable: Fable 5.1", "opus[1m]: Opus 5.5 (1M context)" and "default: Opus 5.5 (default)". The versions are asked of the installed Claude Code each time the list is read, never written into BlindPilot, so when Claude Code ships a new model the picker names it the same day.
- Nothing about choosing changes. Picking "opus: Opus 5.5" hands Claude Code the alias "opus", exactly as before, and a full model ID such as claude-opus-5-5 can still be typed into the box.
- Other backends already list full model IDs that carry their version, such as gpt-5.6-sol on Codex, so they are unchanged.
