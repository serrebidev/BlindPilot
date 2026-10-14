# BlindPilot 0.43.12

This release hardens Command Code routing and backend failure handling, contributed by Farhan Khan.

- When Command Code reports a rate or credit stop, BlindPilot records a cooldown and withholds paid models while it is in effect: a quick task runs on a free model, a heavier task keeps the CLI's configured model, and the spoken route says Command Code is cooling down. A lookup phrased as a question now counts as a quick task.
- Hermes "Don't ask" mode now declines approval prompts instead of approving them, and a Hermes turn that crashes says so instead of silently ending. Cancelling a turn no longer risks closing the connection the next turn has adopted.
- Failed writes no longer lose agent data: agent files are written atomically, backend processes that survive a kill are reaped, unknown result frames fail the turn instead of being announced as finished answers, and update downloads write through the temporary file's own descriptor.
- wxPython 4.3.2 callback and drawing types are aligned, with focused tests covering the changed paths.
