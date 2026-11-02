# Muser backend for BlindPilot — plan

Date: 2026-11-01. Asked by Brandon: add https://github.com/nyarurant/muser as a
backend. The existing muse.ai backend is web/chat only (the agent runs on
Meta's machine); Muser runs Muse as a coding agent on this PC, so this backend
must fully support agent mode. This plan was written from the audit below,
before the design decisions in it were settled.

## Audit: the muser CLI surface (verified live, muser-cli 0.3.6)

- Windows only: npm package `muser-cli` declares `os: win32` and
  `engines: node >= 22`; the binary is `muser`. Sign-in state is DPAPI-protected
  under `%LOCALAPPDATA%\muser`; config, skills and agent types live in
  `~/.muser`.
- One headless turn: `muser exec --json [--session ID] [--mode MODE] [--cd DIR] -`
  with the prompt on stdin (an argument prompt also works; stdin avoids
  `.cmd` quoting). stdout is JSON Lines:
  - `{"type":"start","session":uuid,"cwd":...,"mode":...}`
  - `{"type":"tool","agent":"main","command":"code.read","summary":"README.md","ok":true}`
    per tool call (failed calls add `error`, declined ones `declined: true`);
    parallel agents report with their own `agent` value.
  - `{"type":"muse_step","title":...,"detail":...}` for Muse-side steps.
  - Last line `{"type":"result","ok":true,"session":...,"reply":...,"sources":[{label,uri}],"duration_ms":...,"usage":{"week_percent_before":29,"week_percent_after":29}}`.
    Failures: `ok: false` with `error`, exit code 1. There is no `tool_calls`
    field in the result despite the `--help` wording, and no streamed reply
    deltas: the reply arrives whole in the result line.
- Modes: `default`, `auto-edit`, `plan` (read-only), `bypass`. In exec mode the
  approver declines anything the mode does not pre-authorize, and Muse is told
  why, so a headless turn never stalls waiting for an answer.
- Setup: `muser login` runs onboarding that does both missing steps — the
  muse.ai web sign-in (opens an Edge window) and registering this PC as a Muse
  device (`pc-login` is the same second step on its own). One login command
  therefore covers full setup.
- Auth/status: `muser doctor` prints `web login : ready`,
  `gateway : connected (N chats)`, `pc device : authenticated (vm ok)`,
  `usage today : N turns, M tool calls`. `muser usage` prints the plan line
  (`Power · weekly usage 29% (resets at 11/5, 05:57 PM)`) plus muser's own
  per-PC tallies. Exec refuses to run when either the web login or the device
  registration is missing, so the auth check must require both doctor lines.
- Conversations: `%LOCALAPPDATA%\muser\sessions.json` is a plain JSON index
  (`{id, cwd, title, created, updated, turns, parent?}`; `parent` marks
  parallel-agent side chats). Transcripts are
  `%LOCALAPPDATA%\muser\transcripts\<id>.jsonl`, one JSON object per turn:
  `{v, at, user, reply, tools, steps, sources, todos, from}` where `todos` is
  `[{content, status}]` — the same shape as Claude Code's TodoWrite list.
  A conversation resumes with `exec --session <id>`.
- No model or effort picker: Muse picks its own model, like the muse.ai
  backend. No compaction command. Attachments are not an exec input; the agent
  runs on this PC, so file paths named in the prompt work as they do for the
  other local CLI backends.
- Muser reads instructions from `AGENTS.md` / `CLAUDE.md` / `MUSE.md` /
  `.muser/instructions.md` walking up from the folder, `~/.muser/instructions.md`
  globally; skills from `~/.muser/skills` (and the Claude/Codex/agents skill
  folders); custom agent types from `~/.muser/agents/*.md` and
  `<project>/.muser/agents/*.md` in Claude Code format (`name` / `description`
  frontmatter); MCP servers from `~/.muser/mcp.json`; per-folder approval modes
  in `~/.muser/permissions.json`; language and agent limits in
  `~/.muser/config.json`.

## Audit: what a BlindPilot backend must touch (from the Copilot backend)

Setup and lifecycle:

- `agent_backends.BACKENDS` entry (appended last: the send earcon's pitch is
  keyed on dict order), id constant, and `normalize_backend` aliases.
- Install: `blindpilot_app._NPM_BACKEND_PACKAGES` (managed npm prefix install,
  which also gives the setup wizard its Install button and Node guidance) and
  `backend_updates._NPM_PACKAGES` (update checks against npm).
- Sign-in: `BackendInfo.login_args`, `login_needs_terminal` /
  `login_opens_browser` flags, and `agent_backends.backend_auth_ok`.
- Status: `agent_backends.backend_status` account lines; usage lines where
  the CLI reports them.

Using it:

- A worker on `agent_backends._TurnWorker`, returned by
  `agent_backends.worker_class`; one process per turn like Copilot's, so the
  backend joins `blindpilot_app._QUEUEING_BACKENDS` (messages typed mid-turn
  queue, Steer stops and resumes).
- Permission modes translated from BlindPilot's neutral set.
- Tool steps narrated as activity; the task list fed through the window's
  `on_tasks`; failures reported once, with the CLI's own words made readable.

Every other BlindPilot feature that names backends:

- Recent Conversations: a lister and a reader in `session_history`, plus
  `restore_tasks` so a reopened conversation brings its task list back.
- Backend Settings: `agent_backends.settings_files` entries.
- What Agents Know: `blindpilot_app.agent_files` groups (instructions, skills,
  agents) and `add_agent` so a custom agent can be created for the backend.
- README (backends sentence, install snippet, per-agent notes, driven-how
  table — `test_readme_matches_code` pins these), the About sentence (built
  from `BACKEND_LABELS`, automatic), CHANGELOG, `APP_VERSION`.
- Tests: a backend test module modelled on `tests/test_copilot_backend.py`,
  and `tests/test_worker_contract.py`'s worker list (it counts one worker per
  backend).

## Design decisions (brainstorm, settled after the audit)

- New module `muser_backend.py` holding the settings/sign-in helpers and
  `MuserWorker`, modelled on `copilot_backend.py`. muse.ai's modules stay
  untouched.
- Permission mapping: `plan` → `plan`; `acceptEdits` → `auto-edit`;
  `default` → `default`; `dontAsk` → `default` (in exec mode an unanswered
  approval is declined, which is exactly Don't Ask); `auto` → `bypass`
  (Muser has no reviewer mode like Copilot's assisted approval; Auto's promise
  is that routine work does not stop, and bypass is the only mode that keeps
  it — the README says this plainly); `bypassPermissions` → `bypass`.
- The reply is delivered whole from the result line. Tool lines become
  `tool` activity (a failed or declined call is announced as such);
  `muse_step` titles become `notice` activity. Sources in the result are
  appended to the answer as a Sources list when present, as links a screen
  reader can follow.
- Task list: at the end of a turn the worker reads the session transcript's
  newest `todos` and reports them through `on_tasks` as a TodoWrite-shaped
  list (new source branch in `blindpilot_app._on_tasks`). The same list is
  what `restore_tasks` replays for a reopened conversation.
- Auth: `muser doctor` must show both `web login : ready` and
  `pc device : authenticated`. Account lines come from doctor, plus the
  weekly usage line from `muser usage`.
- History is Windows-only in practice (the store lives under LOCALAPPDATA);
  on other platforms the lister returns nothing, and the backend itself is
  offered everywhere but can only be installed where npm accepts the package
  (Windows) — the install text says Windows only.
- No compaction, no model/effort controls: `supports_compaction=False`,
  `supports_model=False`, `supports_effort=False`,
  `supports_permissions=True`.

## Steps

1. `muser_backend.py`: doctor/usage parsing, auth, account lines, readable
   errors, `MuserWorker`.
2. `agent_backends.py`: id, BACKENDS entry, aliases, auth branch, status
   branch, settings files, worker_class.
3. `session_history.py`: lister, reader, restore_tasks, docstring.
4. `blindpilot_app.py`: npm package, queueing set, `_on_tasks` branch and its
   empty-list sentence, agent_files groups, add_agent.
5. `backend_updates.py`: npm package.
6. README, CHANGELOG, APP_VERSION 0.53.0.
7. Tests: `tests/test_muser_backend.py`; worker contract list.
8. Gate: `python -m ruff check .`, `python -m ruff format --check .`,
   `python -m pytest -q -W error`, `python -m mypy`, both startup smokes.
   Then commit, push, wait for the Tests workflow green on Windows, macOS and
   Linux, tag `v0.53.0`, and verify the published release and its assets.
