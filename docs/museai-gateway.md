# Muse.ai gateway integration

Contracts checked on 2026-10-08 against muse-cli 0.3.2, the current Muse.ai web client, and read-only account calls. This is the consumer personal agent, separate from Muse Code.

The [official connector guidelines](https://muse.ai/platform/docs) describe exposing a developer's API or MCP tools to Muse. They do not document a consumer-account client SDK. [Meta's Sentinel description](https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse) explains that approval requests and decisions travel directly between the client and Sentinel, independently of chat messages.

The [muse-cli command implementation](https://github.com/nikships/muse-cli/blob/ebcb6310432ad2d39927bcb59e686330b0538310/src/muse_cli/cli.py) has named feed and idea commands. Its [route table](https://github.com/nikships/muse-cli/blob/ebcb6310432ad2d39927bcb59e686330b0538310/src/muse_cli/routes.json) exposes the other account controls through `raw`.

| View or action | Command or gateway method | Result |
| --- | --- | --- |
| Approvals | `raw egress.approvals` | `pending_approvals`, `recent_approvals`; legacy `pending`, `recent` |
| Decide approval | `raw egress.approval.decide --param approval_id=ID --body JSON` | Sentinel decision |
| Schedules | `raw tasks.list` | `schedules`, `invalid` |
| Feed | `raw feed.list` | `days`, each containing `editions` and `units` |
| Ideas | `raw api.idea-cards.list` | `sections`, each containing `cards` |
| Idea details | `idea ID` | `card`, including content and items |

Raw commands retain the `{ok, result}` envelope; named commands unwrap it. Approval display fields contain the purpose, scope, permission question, and labelled detail rows. Pending approvals are preferred whenever that field exists, even when empty.

The [official web client](https://muse.ai/_next/static/chunks/290qftk9d6y1e.js) sends `approval_id` in both the URL and JSON body. BlindPilot supports only `{"approval_id":"ID","decision":"allow_once"}` and `{"approval_id":"ID","decision":"deny"}`, with an optional reason. It respects supplied decision options and never creates persistent grants or changes Sentinel settings. No real decision was sent while verifying this integration.

The [muse-cli receiver](https://github.com/nikships/muse-cli/blob/ebcb6310432ad2d39927bcb59e686330b0538310/src/muse_cli/gateway.py) in 0.3.2 parses each decrypted Noise payload as a complete protobuf response, ignoring multipart metadata. Live approval replies exceeded one frame and failed with `DecodeError: Wire format was corrupt`. Joining fragments by chunk ID and index fixed the reads. BlindPilot supplies a version-gated `sitecustomize.py` through the child process's `PYTHONPATH`, and packages that file as data. Other client versions and other backend processes are unchanged. Tests cover interleaved fragments, ordering, conflicting duplicates, invalid metadata, and receive limits.

## Item actions

Actions were checked against the current [Feed UI](https://muse.ai/_next/static/chunks/3ep074oas981g.js), [Ideas helper](https://muse.ai/_next/static/chunks/06hglssbbp-ui.js), [Schedules UI](https://muse.ai/_next/static/chunks/3msqhly_4tqp0.js), [Goals helper](https://muse.ai/_next/static/chunks/2ctf3jfcf-jgs.js), and [route resolver](https://muse.ai/_next/static/chunks/3tq33mfknv4ah.js). IDs consumed by path templates are removed from wire bodies, except the idea execute helper and Sentinel decision contract which explicitly repeat their IDs in the body.

| Action | Method and path parameter | Body |
| --- | --- | --- |
| Run idea | `api.idea-cards.execute`, `ideaCardId` | `ideaCardId`, `mode: "full"` or `mode: "selectedItems"` and `itemIds` |
| Run feed idea | Same idea execute method; `idea_action.idea_id` supplies the ID | Same as above |
| Run schedule now | `tasks.run`, `job_id` | `{}` |
| Read schedule runs | `tasks.runs` | GET query `job_id`, `limit: 100`, supplied using raw `--body` |
| Read goal | `goals.get`, `id` | None |
| Edit or change goal status | `goals.update`, `id` | `title`, `description`, or `status` |
| Delete goal | `goals.delete`, `id` | None |
| Accept/dismiss goal suggestion | `goals.suggestions.decide`, `goal_id`, `suggestion_id` | `decision: "accepted"` or `"dismissed"` |

Idea execution is successful only for `queued` or `accepted`; its optional `chat.session_id`/`sessionId` identifies the chat to open. Goal IDs use `goal_id` or `id`; suggestion IDs use `suggestion_id`, `suggestionId`, or `id`, with title and summary in nested `idea`. Accepting a suggestion executes it. Goal status values are `active`, `paused`, `completed`, and `retired`. Child goals are included recursively in the list.

Schedule runs contain time, status, result summary, and errors, not a chat ID. Only schedules explicitly targeting `main` open the main conversation after Run now. The child-process compatibility hook registers the current `tasks.run` route because upstream 0.3.2 predates it. It never retries an execution automatically; on an uncertain response, check history/status before running again.

Feed article links come from `body_md`; social links use `attachment.social_embed_url`. The official Discuss action quotes a feed item and focuses the composer without sending. Upstream CLI `send` does not expose the quote target, so BlindPilot prepares an editable prompt with the item's full readable details in main chat. Sending remains the user's choice. Native Run selections default omitted `selectable`, `isSelected`, and schedule `enabled` flags as the web client does.

Read-only account calls verified goal details, article links, and schedule run history. Execution and mutation tests use mocked gateway replies; no real idea, schedule, or suggestion was started and no goal was changed during verification.

These internal endpoints are unversioned. Unsupported or unreadable responses are reported as errors rather than empty lists. Views show the current gateway page; paging through older feed and idea results is deferred. Schedule editing through a dedicated form, persistent grants, and outgoing large-message fragmentation are outside this change.

## Live status

The web client's working line ("Fetching release", "Searching") comes from `agent.status` events on `chat.subscribe`: `activity_code` (`online`, `working`, `responding`, `needs_approval`), `activity_text`, optional `activity_emoji`, and `session_id`. The [web client](https://muse.ai/_next/static/chunks/38ncahz8xtg74.js) subscribes with `{after_stream_seq, after_chat_event_seq, capabilities: ["chat_cancel", "delta_stream", ...], session_id}`, adding `session_id` for side chats. muse-cli 0.3.2's `watch` sends `capabilities: {}` without a session and prints only after the stream ends, so it never showed side-chat activity. Verified live on 2026-10-08: with the web client's parameters, both side chats and the main chat stream their activity text while the agent works.

During a turn, BlindPilot runs `muse-cli watch` with `BLINDPILOT_MUSEAI_WATCH=<chat id>`; the compatibility hook then subscribes as the web client does and prints each event line as it arrives. Activity text for the turn's chat goes on the status line and the live step row; `online` is skipped. If the stream ends early, the history-based step count takes over again. The watcher ends with the turn.
