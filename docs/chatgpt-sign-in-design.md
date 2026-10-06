# ChatGPT sign-in: research and proposed integration

Date: 2026-09-30. Status: researched; design awaiting review; product code unchanged.

## Requested outcome

One **Continue with ChatGPT** browser authorization lets a BlindPilot user use
their eligible ChatGPT plan for both normal chat and the existing Codex coding
agent. No API key or endpoint entry is needed. Conversation history stays in
BlindPilot. This is a new ChatGPT-plan connection, separate from existing API-key
accounts and Codex CLI authentication.

“ChatGPT Chat” here means a BlindPilot conversation through the public Responses
API. Authorization does not import ChatGPT.com conversations, memory, custom GPTs,
or account context. Do not imply that it launches the ChatGPT website service.

## Ten web results inspected

The first search returned these ten results. Nine had scraped page bodies; the
Reddit result supplied only a search excerpt. Nonofficial results help distinguish
older Codex authentication from the newly documented flow; OpenAI documentation
is the authority for the implementation.

1. [OpenAI: open-source ChatGPT plan usage](https://developers.openai.com/siwc/token-sharing-open-source).
   Relevant official entry point: dynamic registration, persistent host identity,
   and public Responses API access.
2. [EvanZhouDev/openai-oauth](https://github.com/EvanZhouDev/openai-oauth).
   Community SDK and browser-extension approach. Its existence does not establish
   compliance with the new dynamic-client contract; do not add this dependency.
3. [Reddit: OpenClaw with a ChatGPT account](https://www.reddit.com/r/AI_Agents/comments/1qul4oj/is_there_a_way_to_make_openclaw_use_my_chatgpt/).
   Only an excerpt was available. No implementation decision relies on it.
4. [OpenAI: authentication](https://learn.chatgpt.com/docs/auth).
   Existing Codex authentication and custom-provider behavior. Distinct from the
   new third-party Responses grant.
5. [OpenClaw Launch: Codex OAuth](https://openclawlaunch.com/guides/openclaw-codex-oauth).
   Describes the older Codex/device-code setup; not the new OSS registration flow.
6. [OpenClaw: OAuth documentation](https://docs.openclaw.ai/concepts/oauth).
   Useful provider/account context. Requires checking actual implementation
   against the current OpenAI docs.
7. [Hacker News: community ChatGPT OAuth SDK](https://news.ycombinator.com/item?id=48922687).
   Older community discussion; no authoritative new API contract.
8. [OpenAI: plugin authentication](https://developers.openai.com/plugins/build/auth).
   Authentication for tools exposed to ChatGPT, rather than BlindPilot calling
   Responses with the user's ChatGPT-plan grant.
9. [openai/codex discussion 8338](https://github.com/openai/codex/discussions/8338).
   Historical integration questions, marked unanswered. Use current SIWC docs
   for the supported new route.
10. [nexu: Codex OAuth announcement](https://nexu.io/blog/nexu-openai-codex-oauth).
    March 2026 announcement about older subscription authentication, not evidence
    for the new dynamic client flow.

## Official contract verified

- [Quickstart](https://developers.openai.com/siwc/quickstart): eligible Plus and
  Pro users; the OSS flow needs neither a client secret nor a partner API key.
  Eligibility should come from the actual grant and account catalog, not an
  editable plan name in BlindPilot.
- [Registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in):
  persist a per-installation host UUID URI; start a loopback listener before
  opening the system browser; generate fresh state, nonce, and S256 PKCE for
  every attempt. First registration uses `dynamic_agent_client`. Exchange the
  code with the callback's issued client ID. Later sign-ins reuse that ID.
- Validate the ID-token signature with OpenAI JWKS and its issuer, audience,
  expiry, subject, and nonce. Require the returned `chatgpt.tokens.use.direct`
  scope for inference. Do not activate a pending connection before validation.
- Callback URI: `http://127.0.0.1:<available-port>/auth/callback`. Keep the
  scheme, host, and path fixed; use the exact same selected URI for exchange.
  Validate state before handling errors; reject unexpected client IDs and
  returning-account identity changes.
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference):
  GET `https://api.openai.com/v1/models` using the selected access token. Parse
  `models`, retain entries with `visibility == "list"`, preserve server ordering,
  display `display_name`, and send `slug` as the model identifier.
- POST only to `https://api.openai.com/v1/responses`, with `store: false` and
  `stream: true`. Send required history as an input array. Treat only
  `response.completed` as successful completion; surface failures, incomplete
  responses, and premature stream termination even after text arrives.
- [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations):
  omit temperature, output-token limits, previous-response IDs, and all other
  unsupported fields. Use instructions or developer messages instead of explicit
  system-message items. Hosted computer use, image generation, Code Interpreter,
  hosted MCP, file search, and Responses tool search are unsupported. This route
  does not offer a documented `mode=work` entry point.
- [Codex app-server](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server):
  supply the same access token in the child process environment; configure a
  Responses provider at `https://api.openai.com/v1`, with environment-key auth,
  `requires_openai_auth=false`, and `supports_websockets=false`. No second Codex
  login is necessary. After token replacement, restart app-server and resume
  saved threads. Its model/list catalog is not an entitlement check.
- [Accounts and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions):
  separate registrations by issued client and verified identity, including when
  emails match. Serialize refresh read/exchange/write across app instances.
  Atomically replace rotating tokens and scope/expiry metadata. Sign-out attempts
  refresh-token revocation, clears local tokens, and retains registration and host
  identity. Report unconfirmed remote revocation.
- [Token reference](https://developers.openai.com/siwc/token-sharing-open-source/token-reference):
  access-token lifetime is one hour; refresh tokens rotate and have a 30-day
  lifetime. Retain expiry and `earliest_refresh_at` information.
- [UI/UX guidelines](https://developers.openai.com/siwc/ui-ux-guidelines):
  label the action Continue with ChatGPT, show Using ChatGPT plan, explain plan
  usage once after first authorization, and link Manage usage to
  `https://chatgpt.com/settings/usage`. Usage-limit errors must remain visible.

## GitHub developer research

After the web search, searched GitHub for `dynamic_agent_client` and the direct
usage scope. GitHub code search found implementations in OpenClaw, Pi, T3 Code,
big-AGI, OpenMausBot, Atomic, Rootshell, and Codegraff. Examined these files and PR:

- [OpenClaw token-sharing constants](https://github.com/openclaw/openclaw/blob/8f469e9ba304e7c625459e37cd5655ee5c95d5bb/extensions/openai/token-sharing.ts).
  Confirms separate identity and plan-usage flows. Its localhost callback constant
  differs from current OpenAI guidance; do not copy that constant.
- [Pi OAuth implementation](https://github.com/earendil-works/pi/blob/d4d74eb19be92c559f629a7f9707c5503a840edc/packages/ai/src/auth/oauth/openai-chatgpt.ts).
  Useful PKCE, callback, scope, expiry, and listener-cleanup examples. The inspected
  version registers a new client each login and only checks ID-token presence;
  BlindPilot must reuse registrations and verify identity as OpenAI documents.
- [Hermes implementation PR 128926](https://github.com/NousResearch/hermes-agent/pull/128926).
  Open when inspected. Read its `hermes_cli/auth_chatgpt.py`: demonstrates Python
  JWT validation, ephemeral loopback port, returning-account checks, refresh,
  and revocation. Its author reports live and automated validation; those are
  upstream claims, not BlindPilot test results. Reuse ideas, not Hermes's separate
  provider and credential-pool architecture.

## Proposed BlindPilot changes

This is an architectural change because account lifecycle must be shared between
the chat subsystem and the pooled coding-agent process.

1. Add a **ChatGPT plan** account type to the existing account settings. Reuse the
   existing database account IDs, account picker, model picker, history, and wx
   dialog patterns. Add Continue with ChatGPT, Reconnect, Sign out, and Manage
   usage actions. Browser/network work runs off the GUI thread, with cancellation,
   announced status, and focus restored on completion.
2. Add one shared OAuth module for registration, identity validation, protected
   credential reads/writes, refresh coordination, and revocation. Store only
   nonsecret registration metadata in SQLite. Keep tokens out of SQLite, logs,
   diagnostics, and ordinary preferences. Use existing OS credential storage;
   check Windows Credential Manager's blob-size limit before deciding token layout.
   If complete records exceed that limit, use a single atomically replaced,
   DPAPI-protected local record on Windows and the system keychain elsewhere.
3. Use stdlib HTTPServer, secrets, hashlib, uuid, and the existing httpx dependency
   for browser authorization. Add a maintained JWT verifier with cryptographic
   support if no installed production dependency provides verification. Do not
   implement signature verification or OAuth cryptography from scratch.
4. Extend existing Responses handling with the ChatGPT-plan request contract and
   terminal-event checks. Force streaming, forbid unsupported request fields, use
   account-specific catalog parsing, and retain complete local chat history.
   API-key accounts keep their current request options.
5. Add a saved ChatGPT-account selection for the existing Codex backend. Default
   new ChatGPT-plan setup to the connected account; retain the existing Codex-login
   option. Supply the selected token only to the child environment, never command
   arguments or global environment. Match registration attribution and initialize
   clientInfo consistently as BlindPilot.
6. Keep Codex process ownership tied to its selected account/token generation.
   The current app-server is shared across tabs: account switching and refresh
   must not silently move other tabs onto another account. If another account is
   already in use, require finishing its turns before switching the shared server.
   Expiry replacement waits for an active turn to finish, then restarts before the
   next turn and resumes the correct stored thread. Sign-out stops new requests
   and releases affected process ownership.

## Existing code affected

- `accessible_ai/models.py`, provider config/factory, and account settings:
  register the new account type and sign-in actions.
- `accessible_ai/storage/credentials.py` and app-data paths: protected OAuth
  credentials without replacing current API-key storage.
- `accessible_ai/providers/openai_provider.py` and `protocols.py`: current code
  expects API-key model catalogs and optional streaming, and includes temperature
  and output-token limits. Add the stricter plan route and completion validation.
- `agent_backends.py`: `_start_codex_server` currently uses existing Codex auth
  and launches `app-server --stdio`; extend launch arguments/environment and
  ensure initialization completes before subsequent RPCs.
- `backend_pool.py`, `blindpilot_app.py`, and chat integration: process/account
  selection, restart coordination, UI status, and shutdown behavior.

## Validation required before claiming integration complete

- Callback success, denial, cancellation, timeout, wrong/reused state, missing or
  changed client ID; first registration and returning registration.
- Invalid ID-token signature, issuer, audience, expiry, nonce, and returning
  subject; absent plan-usage permission must prevent inference.
- Protected storage failure, process-safe refresh rotation, restart persistence,
  separate same-email registrations, sign-out and unconfirmed revocation.
- Correct model catalog, mandatory streaming and storage flags, forbidden fields,
  developer instructions, supplied history, and terminal SSE failures after text.
- Codex launch arguments/environment, initialization, account binding, refresh
  restart/thread resume, and switching while other tabs have active turns.
- Existing focused chat/Codex/account tests, lint/type checks required by the repo,
  and keyboard/speech behavior for added native controls.
- A real user-authorized browser login and one completed chat and Codex turn.
  Mock protocol tests cannot establish live eligibility or server compatibility.

No product implementation, live login, or inference was performed during research.
