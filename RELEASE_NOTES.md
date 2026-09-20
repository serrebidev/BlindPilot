# BlindPilot 0.29.10

A cleanup release from the whole-repo over-engineering audit: Muse Code reaches its host through the transport Hermes already uses, and the Codex, FreeBuff and opencode turn workers are built on one scaffold instead of three copies. Nothing you hear or type changed.

- Muse's private copy of the Hermes stdio transport is gone (PR #52): `MuseTransport` and a third private stdio client inside the model catalog are now the shared `StdioTransport`, and the request numbering and frame shapes the two workers duplicated are one small mixin. Hermes' own launch, timeouts, message formats and wording are untouched.
- The Codex, FreeBuff and opencode turn workers share one `_TurnWorker` base (PR #54): the same constructor and callbacks, accepting-input test, one-failure-only guard, and run/teardown order, with each worker keeping only what differs - the turn itself, its own setup, and its own teardown. The four hand-written question builders are one reader keyed by each provider's own flag names, the three opencode auth writes share one helper that never forgets to drop the stale provider cache, FreeBuff's two queue pumps are one, and CodexServer's two expect methods are one with a flag. Behaviour, callback order, and every spoken sentence are unchanged.
- On macOS and Linux, `muse serve` now starts in the conversation's folder instead of wherever BlindPilot was launched. Windows already did this through WSL's `--cd`.
- A Muse turn that fails quotes the last six stderr lines, one per line, as Hermes does, rather than the last three joined with semicolons.
- A `muse serve` that dies without closing its pipe is now noticed, instead of reading as connected until the call's deadline ran out.
- The test suite's one `wx.App` now lives for the whole run, which removes a Linux-only ordering flake in the Preferences tests. No product change from that one.
