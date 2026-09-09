# BlindPilot 0.26.1

A fix release for the Muse backend shipped yesterday: its sessions refused to start. The protocol rules this exposed are now pinned by tests, and the fixes were verified against a live `muse serve` before release.

## What broke

Muse's host protocol validates every command id more strictly than the first release assumed. `turn/start` accepted a random UUIDv4 in testing, but `session/start` insists on UUIDv7 — time-ordered — and refused ours with `invalid session/start commandId: expected UUIDv7 (invalidParams)` before any conversation could begin. Every command id the worker sends is v7 now.

## What the deeper probe found

Once the session could start, a second wall appeared: the approval mode BlindPilot asks for on the wire — the one that means "run tools without asking me" — is not the client's to choose. MSP seals an approval-mode ceiling in the host at startup, and `session/start` cannot exceed it; on a stock `muse serve` only `promptUnmatched` and `denyUnmatched` are accepted, and there is no client call or server flag that raises the ceiling. A session asking for bypass was rejected outright.

So bypass is done client-side, which is what the user actually asked for: the worker starts every session in `promptUnmatched`, and when the window is in bypass mode it answers each tool-approval request itself the moment it arrives, without putting a dialog in front of anyone. The semantics the user chose are preserved; the wire's rule is respected.

Two smaller rules were confirmed in the same probing and fixed: an approval decision must pick from that approval's own `availableChoices` ids rather than sending a fixed word the host may not have offered, and `workspaceRoot` must be absolute on the wire — the WSL bridge already absolutised it, so only the fallback path needed it. A cancel pressed while the approval dialog is up now beats a stale answer from it.

## What was verified

The fixed worker was run against a live `muse serve`: the handshake passes, `session/start` creates a real session on the host (session file visible under Muse's own store), `turn/start` is accepted and streams items, and a cancel ends cleanly. The turn's model call itself still meets the account-side HTTP 402 noted in 0.26.0 — authenticated, but Meta has not attached model access to the account yet; that is unchanged and outside the app.

The worker tests were rewritten around the measured protocol: v7 ids asserted by shape, the mode vocabulary the stock host accepts, approvals resolved from `availableChoices` (including the no-human fallback choosing among whatever choices exist), and cancel-during-dialog. The full suite — 1614 tests — passes with `-W error`; ruff, the formatter and mypy are clean; both startup smokes exit 0.
