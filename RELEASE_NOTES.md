# BlindPilot 0.27.2

Chat's Newest first and Oldest first model orders now use the generation, revision, and dated build numbers in model IDs. For example, 5.6 appears ahead of 5.4 and 5.1.

This release also fixes a Codex cancellation race found by the Intel macOS release test. If a turn's id arrived just after Stop had finished waiting for it, BlindPilot recorded the turn as abandoned but did not interrupt it. A late-named turn is now interrupted by its worker before it exits.
