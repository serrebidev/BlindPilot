# ADR 0003: Plan tokens delivered to Codex by a command, not an environment key

- **Status:** Accepted
- **Date:** 2026-11-05
- **Context:** When the Codex backend runs on a ChatGPT-plan Account, Codex must
  present that Account's access token, which lives an hour and must be replaced
  without a trip through the app. The design document proposed supplying the
  token in the child's environment and restarting the app-server on expiry. The
  shipped code instead has Codex fetch its own token.

## Decisions

Codex is given a provider entry through configuration overrides on its own
command line, and in that entry an `auth.command` that is this app run in its
token-printing mode for one Account id. Codex runs that command, takes the token
it prints, and runs it again on an interval to refresh; the token reaches neither
the app-server's command line nor its environment. Overrides travel on the
command line because the user's own Codex configuration also holds their MCP
servers and preferences, and is never written.

So the app exposes that mode: `--print-plan-token <Account id>` prints a current
access token and exits without opening a window. It reads the same stored
sign-in the chat half reads and refreshes from the stored refresh token, so the
access token stays in memory and no new token record is written to disk.

Because the command is its own process and refresh tokens rotate, refresh is
serialized across processes by a lock file. Only the stored record is shared: a
process that waited on the lock re-reads it, finds the refresh token the holder
rotated, and performs its own exchange with it, so two askers perform two
exchanges. The stored record is replaced atomically.

The base URL in that provider entry is not OpenAI's endpoint but a loopback
relay run by this app (`accessible_ai.providers.plan_relay`). A plan token is a
subscription-sharing token, and the endpoint refuses several item types Codex
emits -- `configuration_update`, `compaction_trigger`, `shell_call` -- with
`subscription_sharing_unsupported_capability`, so every turn carrying one failed
before the model was asked anything. The relay drops those items from a
Responses request, forwards everything else unchanged, and streams the reply
back; only the plan provider is pointed at it, never Codex's own login.

## Considered Options

- **The token as an environment key, with the app-server restarted when it
  expires.** Rejected: the variable is inherited by every process Codex starts,
  so the token is readable by all of them; and an hourly restart means tearing
  down and resuming threads on a clock, so whether a turn survives would depend
  on when it happened to have started.

## Consequences

The token command is a new way to obtain a live plan token: anyone who can run
the app as this user can run it and read the token it prints. That is the same
trust boundary as the stored refresh token, which such a person could already
read — but it is written down here rather than assumed, so it is not closed as a
leak by someone who finds the flag.

Refresh became a problem across processes, not just across threads, which the
chat half alone never had. A refresh now waits, boundedly, for another process to
finish: past that bound it fails rather than blocking the command or a turn. A
waiting process does not take the token the holder was issued — it refreshes
again with the rotated refresh token from the stored record — so a refresh window
can cost an extra exchange, and every run of the token command costs one, since a
fresh process holds no token in memory.

Three things are written down here because they are otherwise invisible in the
code. The provider entry deliberately omits `requires_openai_auth`: Codex's
reference says not to combine it with `auth`, and its default is already false.
And two test seams sit in production code: `BLINDPILOT_HOME` moves the whole
application data folder, credentials included, so a subprocess test writes
nothing to the user's own; `BLINDPILOT_CHATGPT_TOKEN_URL` redirects the token
exchange so subprocess tests can point at a local server, and because it carries
the refresh token when set, it is named here rather than left to be found.

No glossary term was added. "Token command" describes an implementation, not the
project's language, and the Accounts vocabulary already covers what a reader
needs: Account, ChatGPT plan, Registration, Codex login.
