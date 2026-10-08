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

These internal endpoints are unversioned. Unsupported or unreadable responses are reported as errors rather than empty lists. Views show the current gateway page; paging through older feed and idea results is deferred. Schedule editing, idea execution, persistent grants, and outgoing large-message fragmentation are outside this change.
