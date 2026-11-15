# BlindPilot reference-app parity

Date: 2026-11-14
Status: Approved by the user, with automatic computer-use actions requested.

## Intent and approved direction

Use The Chat Place (TheIdeaPlace/AIChat) and FastChat (masonasons/FastChat)
as references for improving BlindPilot. Do not change either reference repository.
The user chose **full useful parity, delivered in staged releases**, over a
focused release or branching alone. The original request authorizes implementation,
verification, commits, and releases after the design is settled.
The user approved this design and specified that computer use should act
automatically as long as it can be stopped. That instruction replaces the
per-action approval default originally proposed below.

Parity means useful user outcomes, not copying every setting, shortcut, toolkit,
provider limitation, or visual arrangement. Keep BlindPilot's existing eleven
agent backends, direct Chat mode, native controls, credential store, and updater.

## Evidence and limits

Reference snapshots inspected through the GitHub connector:

- [The Chat Place README](https://github.com/TheIdeaPlace/AIChat/blob/1cf3caf9ab1d738f4374e1d3adc006b055e24c63/README.md)
  and source tree, including `thechatplace/aside.py` and `thechatplace/export.py`.
- [FastChat README](https://github.com/masonasons/FastChat/blob/1f7570a66a48a767d1a914b13ef9933469610d8f/README.md)
  and source tree, including `src/core/conversation_import.h` and
  `src/core/providers/ollama.cpp`.
- Local baseline: commit `9ba7626`, version 0.56.1. Read README, agent callback
  paths, session history readers, Chat panel, database, provider configuration,
  provider base, generation service, and representative tests.

This is a source-based feature comparison, not a runtime accessibility audit
of either reference app. Public documentation describes some behaviors more
fully than the source samples inspected. Verify each protocol against its real
installed backend and official documentation before implementing it.

The Chat Place declares MIT. FastChat's repository metadata reported no license;
use its behavior as inspiration and independently implement features. Do not
copy its source or assets without a license permitting that use.

## Existing coverage

BlindPilot already has parallel sessions, grouped history, cross-session search,
agent conversations and reply handoff, prompt libraries, attachments and pasted
images, question dialogs, permission modes, steering and backend-dependent
queues, task/subagent reporting, narration, sounds, background notifications,
remote sessions, links, setup/sign-in, single-conversation exports, and updates.
Chat mode already has accounts, profiles, credential-store keys, reasoning and
OpenRouter tools, saved history, and last-response regeneration. Reuse these.

## Gaps and intended outcomes

### Conversation control

- Branch and retry from an earlier message, preserving the original conversation.
  An existing git-worktree option is not conversation branching.
- Ask Aside: ask about a completed conversation without adding to its history.
  It receives the selected completed history, cannot execute tools or modify
  files, and never resumes the original session for a turn. Verify that a
  backend can enforce this boundary before offering its native Aside path;
  otherwise offer the same question in a tools-disabled direct Chat account,
  with an explicit account/model choice before sending. Never silently weaken
  the boundary to make a backend work.
- Review and manage queued messages where a backend can support it.
- Inspect background task details/output and stop one task without stopping
  unrelated work, where the backend supports this.
- Show backend retries, fallback-model changes, permission-mode changes, and
  connected-tool requests as concise accessible status events.
- Offer Claude file checkpoint restoration only when supported and verifiable;
  distinguish conversation rollback from file changes explicitly.

### History portability and protection

- Export selected or filtered conversations together using existing renderers.
- Back up BlindPilot settings, profiles, and Chat database consistently. Include
  supported external agent transcripts only under an explicit inclusion choice.
- Keep read-only transcript copies so provider cleanup need not erase history.
  Default coverage is conversations opened in BlindPilot and conversations
  after a successful local turn, not every discovered transcript. Copy in the
  background; report a failed first copy rather than claiming protection.
  Copies do not expire automatically. Show storage use and provide explicit
  selected-copy deletion with confirmation. Deleting a copy never deletes the
  provider original. Provide a preference to disable future keeping.
- Import Basilisk `.bskc` conversations, then a documented BlindPilot interchange
  format for round trips. Preserve messages, attachments and supported settings;
  report every unsupported field or truncated item.
- Support an archive reader and a restore flow with a preview. A restore creates
  new records by default; never silently overwrite live history or credentials.

### Direct Chat capabilities

- Retry transient API failures with bounded, cancellable delays and spoken status.
  Never replay a response after visible text, reasoning, or tool activity begins.
- Show token usage, context limits, response/account costs and balances when
  provided. Unknown values remain unknown; do not present estimates as bills.
- Add native Ollama model discovery/chat, including local no-key use, plus
  convenient presets for compatible providers missing from the account picker.
- Add conversation compaction, preserving full history separately, with explicit
  controls and an optional context threshold once token/window data is reliable.
- Extend edit/resend and branching while preserving attachment content.

### Tools and personal context

- Browse connected MCP servers/tools and their availability. Enable/disable and
  sign in only through supported backend interfaces, scoped to the chosen chat
  or future chats as stated in the control.
- Browse the instructions, memories and skills a backend actually discovers.
  Do not scan arbitrary private folders or claim this is every fact a model knows.
- Provide compatible session-management tools for Claude turns where they are
  absent, using existing history and app metadata. Exclude the calling session
  as appropriate and refuse hiding a running or currently open conversation.

### Speech, sounds and desktop actions

- Per-event custom sounds and imported sound packs; preserve existing defaults.
- Optional independent system speech voice/rate/volume and per-conversation
  overrides, without changing screen-reader navigation speech.
- Opt-in computer-use conversations in direct Chat mode on Windows first, then
  macOS. Use native accessibility actions before coordinates; screenshots only
  when explicitly enabled and supported by the model.
- Once a computer-use conversation is started, actions run automatically by
  default. Do not interrupt every action with an approval dialog. Offer an
  optional ask-before-acting mode without making it the default. Ordinary Chat
  stays unchanged until the user chooses computer use.
- A global emergency stop must work while another app has focus. Register it
  successfully before enabling automatic actions; announce the exact key.
  Cancellation invalidates pending actions and prevents further actions until
  the user explicitly resumes. Keep the stop path independent of a model/network
  request and the action queue. An in-window Stop control provides a second path.
- Keep target/foreground validation, bounded action loops, and protection
  against operating BlindPilot itself. Never fall back to unchecked coordinates
  when a target disappears or changes. Automatic mode is per conversation.

## Approaches considered

1. One broad parity release: fewer releases, but couples storage, provider,
   process and desktop-control changes. Too difficult to validate together.
2. Focused recovery/export release only: fast and useful, but leaves most
   requested parity unimplemented.
3. Staged full useful parity: reuse existing boundaries, validate each behavior,
   and publish independently usable changes. **Chosen by the user.**

## Proposed release order

Each stage is a separate releasable change; version numbers follow the version
available when that stage is ready. Do not reserve tags or move published tags.
Keep remaining stages visible until completed; shipping stage one does not
complete this request.

1. **Recovery and export.** Conversation-only branch/retry for direct Chat,
   Claude and Codex; Ask Aside for Claude/Codex; bulk export; transient API retry.
   Use native fork APIs for agents. If a backend lacks a safe fork, provide an
   explicitly labeled new conversation with copied context rather than modifying
   its transcript. Later backends use that fallback until native support exists.
2. **History protection and import.** Consistent backups, archive preview/restore,
   kept transcript history and retention report, Basilisk import, and BlindPilot
   interchange. Include schema migrations and rollback/preservation checks.
3. **Provider information and long chats.** Usage/cost/balance reports, native
   Ollama, compatible-provider presets, context-aware Chat compaction, and
   attachment-preserving edit/resend coverage.
4. **Agent controls.** Queue management, background-task inspection/individual
   stop, retry/fallback/mode announcements, supported elicitation handling, and
   Claude file checkpoints/restoration.
5. **Connected tools and context.** MCP discovery/control/sign-in, instruction/
   memory/skill browser, and Claude session-management compatibility tools.
6. **Speech and sound customization.** Event settings, sound-pack import, system
   speech options and per-conversation overrides.
7. **Computer use.** Automatic Windows accessibility-driven tools and emergency stop,
   then native macOS implementation and release verification.

The user approved this release order and grouping as part of the design. Refine
technical seams during planning without dropping
outcomes. A platform or external-service blocker must be reported accurately;
never claim unsupported parity to finish a stage.

## Architecture and safety constraints

- Keep `blindpilot_app.py` as orchestration. Put new storage/protocol operations
  beside existing responsibilities; do not introduce a generic plugin framework.
- Reuse `session_history.py`, existing exports, `owned_process.py`, worker
  contracts and callbacks, `GenerationService`, Chat database/models, and
  provider construction. Add backend capability checks rather than guessing.
- GUI callbacks stay below the ADR 0001 50 ms budget. Network, subprocess,
  archive, bulk transcript and database-copy work uses workers. Results arrive
  through the GUI event path only while the owning window/request is live.
- Preserve current keyboard shortcuts. Add commands to suitable submenus; obey
  menu-layout limits. Native labels, predictable focus and short announcements
  are part of acceptance, including cancellation and errors.
- Branches and imports are atomic and preserve system/profile snapshots,
  message order and attachments. In-progress turns cannot be mutated under a
  worker. A retry never deletes the old response before a replacement succeeds.
- Backup defaults exclude API keys, sign-in tokens, cookies and secret-bearing
  backend configuration. Document sensitivity of transcripts and memory files.
  Do not add a credential-export feature as an incidental backup implementation.
- Validate archive names, paths, formats, item counts and expanded sizes; reject
  traversal/symlinks and decompression bombs. Work in temporary files; atomically
  publish completed outputs. Cancellation must not leave a plausible final file.
- Do not edit protected release/spec/evidence paths without a specific need and
  explicit authorization for that file. Existing release packaging should suffice.
- Add no dependency where installed packages, standard library or native APIs
  meet the need. Desktop automation may justify a dependency only after checking
  installed capabilities, distribution and platform support.

## Acceptance and release gates

Every stage must have focused behavioral tests for its new logic, source-data
preservation checks where applicable, and error/cancellation coverage. Test
against fixtures; do not mutate real external-agent history or sign-in state.

Before a commit/release: `python -m ruff check .`,
`python -m ruff format --check .`, `python -m pytest -q -W error`, and the
configured bare `python -m mypy`. Run startup and GUI startup smokes required by
the actual release workflow. Inspect current workflow/configuration rather than
treating stored release memories as authoritative.

Accessibility changes require real screen-reader verification. Record actual
NVDA results for Windows flows: command discovery, dialog labels, tab order,
selection/focus, long content, completion/error/cancel feedback. Use VoiceOver
for macOS-specific accessibility work. UI-tree dumps alone do not satisfy this.
If the available host cannot provide a required real-reader check, leave that
gate visibly incomplete and obtain the check before claiming the stage done.

Each release includes updated version, changelog and release notes, clean
working-tree accounting, release artifacts/checksums, and verification of the
published tag/assets at the current canonical release host. The user's commit
and release authorization covers these stages; do not ask again merely because
a stage is ready to publish.

The current release host is Forgejo. The checked macOS workflow attaches an
arm64 artifact to an existing release. ADR 0004's amendment withdraws universal2
and Linux artifacts; this project does not reopen that platform decision.

## Deliberate exclusions

Do not rewrite BlindPilot in C++, reproduce Windows 7 compatibility, replace its
native toolkit or credential store, or turn every API chat into a coding agent.
Existing coding backends remain the file/command execution path. Computer use
is a distinct opt-in conversation capability, never an automatic Chat upgrade.
Do not weaken current safety, accessibility or quality checks for parity.

## Decisions left to implementation

Choose exact menu placements and nonconflicting accelerators, internal helper
names, archive/interchange schema details, finite retry delays, and supported
provider report adapters from verified protocol evidence. Favor the smallest
complete implementation at each seam. These choices do not need another scope
interview unless they change preservation, permissions or user-visible outcomes.

## Builder check

A fresh builder review identified two missing boundaries: whether Aside may
execute tools, and the default coverage/expiry of kept transcripts. Both are
resolved above as proposed defaults: tools-disabled Aside, and keeping opened/
locally completed conversations without automatic expiry. No product code has
been changed and no implementation or release gates have run yet. The user then
approved the design with automatic computer-use actions replacing per-action
approval as the default; the stop and target-validation requirements remain.
