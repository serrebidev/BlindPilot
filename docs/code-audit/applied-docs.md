# Applied documentation changes

Applied 2026-09-04 on branch audit/2026-09-fixes, from the verdicts in docs-unslop.md and the maintainer's decisions. Nothing was committed.

## Line counts

| File | Before | After | Change |
|---|---|---|---|
| README.md | 247 lines, 3,104 words | 214 lines | Rewritten from the audit draft, with corrections listed below |
| CHANGELOG.md | 423 lines, 19,341 words | 278 lines, 4,422 words | Every entry compressed to one to three sentences; v0.21.3 section added |
| RELEASE_NOTES.md | 26 | 10 | One line of what, four bullets, one line of evidence, for 0.21.4 |
| CREDITS.md | 32 | 11 | Rewritten per the audit; all five backends named |
| NOTICE | 8 | 8 | Unchanged |
| AGENTS.md | 15 | 0 | Removed with `git rm` (staged deletion, not committed) |
| installer/BlindPilot.iss | 72 | 67 | The nine-line CloseApplications comment cut to four lines; no functional change |

The CHANGELOG target was 150 to 200 lines. It has 58 version headings, and a heading plus its blank lines plus one bullet is four lines, so 232 is the floor with one bullet per version. 278 lines is the result with one to six bullets per version. The word count fell by 77 percent.

## Facts verified against the code

Each item names the grep or file read used.

- Five backends, labels "Claude Code", "Codex", "FreeBuff", "opencode", "Hermes". `grep -n "BACKEND_LABELS" agent_backends.py` and `sed -n 286,295p agent_backends.py`.
- Default permission mode is Bypass permissions. `grep -n "DEFAULT_PERMISSION_MODE" blindpilot_app.py` gives line 2085, `DEFAULT_PERMISSION_MODE = "bypassPermissions"`. The README says so plainly and points at Model, Permission Mode to change it.
- Permission mode labels Default, Accept edits, Plan, Auto, Don't ask, Bypass permissions. `sed -n 1858,1895p blindpilot_app.py` (PERMISSION_MODES).
- Backend capability table. `sed -n 423,500p agent_backends.py`. Hermes has `supports_effort=True` (the comment there says the earlier False was wrong), so the README table says "Yes" for Hermes model and effort, not "Model yes, effort no" as the audit draft had. FreeBuff has `supports_effort=False`, `supports_permissions=False`, `supports_compaction=False`.
- Hermes asks questions. `grep -n "_clarify_questions" hermes_worker.py` gives line 240. Table says Yes.
- File menu items. `sed -n 9767,9845p blindpilot_app.py`. Hermes Conversations (Ctrl+G) is in the File menu and is removed when Hermes is not the backend (comment at `self._file_menu`).
- Conversation menu items. `sed -n 9847,9905p blindpilot_app.py`.
- Model menu items including Session Status and Backend Settings. `sed -n 9707,9765p blindpilot_app.py`.
- Options menu items including the three Working sound radio items, Working sound interval, Remote Hermes, Preferences (Ctrl+,). `sed -n 8764,8856p blindpilot_app.py`.
- Chat menu items and History view submenu (List, Read-only text). `sed -n 8858,8902p blindpilot_app.py`.
- Help menu items. `sed -n 8904,8928p blindpilot_app.py`.
- Accelerator table: Ctrl+L, Ctrl+Tab, Ctrl+Shift+Tab, Ctrl+Shift+], Ctrl+Shift+[, Ctrl+Shift+M, Ctrl+Shift+A, Ctrl+R, Ctrl+/, Ctrl+1 to Ctrl+9. `sed -n 9078,9092p blindpilot_app.py`. Ctrl+Shift+M appears in no menu label (`grep -n "Ctrl+Shift+M" blindpilot_app.py` hits only the wizard text), so the README lists it as the third chord-only key.
- Ctrl+Up or Alt+Up from the prompt enters the newest response; bare Up does not. `sed -n 5881,5890p blindpilot_app.py`.
- Enter sends, Shift+Enter inserts a newline. `sed -n 5875,5880p blindpilot_app.py`.
- Shift+Tab from the prompt and Down on the last response row. `sed -n 5868,5873p` and `sed -n 6928,6952p blindpilot_app.py`.
- macOS tab chords Cmd+Shift+] and Cmd+Shift+[. `sed -n 8550,8563p blindpilot_app.py` (`_tab_chord_notes`).
- Build command. `.github/workflows/release.yml` lines 79 and 111 run `python -m PyInstaller --noconfirm --clean BlindPilot.spec` on both platforms. The old README's `pyinstaller --onedir --windowed ...` line is gone. Release assets are the Windows zip and two macOS zips (lines 25 to 33) plus the Inno installer (line 100), so the README says there is no packaged Linux build.
- Pre-PR checks. `ci.yml` lines 87 to 118 run `pytest -q -W error`, `ruff check`, `ruff format --check`, and `mypy`. Added `-W error` and `mypy` to the README block.
- Python versions. `release.yml:48` and `ci.yml:56` pin 3.12. The 3.10 floor is carried over from the old README and was not re-verified against a `requires-python` field, since there is none.
- Settings paths. `sed -n 1106,1116p agent_backends.py` (`blindpilot_config_dir`).
- Log paths and rotation. `grep -n -E "Logs|XDG_STATE_HOME|MAX_BYTES|KEEP" diagnostics.py`: `MAX_BYTES = 1024 * 1024`, `KEEP = 3`, so four files of one megabyte.
- Updater checksum. `grep -n sha256 app_updater.py` lines 199 to 202.
- Node.js download SHA-256 check. `blindpilot_app.py:1226,1235`.
- Working sound interval range. `CUE_SECONDS_MIN = 2`, `CUE_SECONDS_MAX = 120`, `CUE_SECONDS_DEFAULT = 10` at `blindpilot_app.py:2508-2510`.
- Codex idle reap of fifteen minutes. `backend_pool.py:27`, `_HELD_IDLE_SECONDS = 900.0`.
- FreeBuff default model. `agent_backends.py:301`, `FREEBUFF_PREFERRED_MODEL = "z-ai/glm-5.3-flash"`.
- Chat providers. `accessible_ai/models.py:19-28`.
- OpenRouter server tools. `accessible_ai/models.py:155-165`.
- Chat profile labels "Thinking effort", "Send the thinking back", "Read attached PDFs with". `accessible_ai/ui/profiles.py:107-159`.
- `:batch` model ids and cache handling. `accessible_ai/providers/openrouter.py:21,24` and lines 101 to 111.
- AccessibleAI import and `chat.sqlite3`. `chat_integration.py:44-65`.
- websocket-client is needed only for remote Hermes and is named when missing. `hermes_backend.py:908-911`.
- Hermes in WSL. `agent_backends.py:416,499`.
- Remote Hermes dialog: session token or username and password, Test connection, TLS checkbox. `sed -n 8396,8432p blindpilot_app.py`.
- Sign In, Already Signed In, Open Sign-in Page buttons. `blindpilot_app.py:7582-7590`.
- Hermes sign-in needs a terminal. `login_needs_terminal=True` in the Hermes BACKENDS entry.
- Startup smoke flags. `blindpilot_app.py:10688-10705`.
- macOS builds ad-hoc signed. `release.yml:112`, `codesign --force --deep --sign -`.
- Installer behaviour (per user, closes running copy, Start Menu entry). `installer/BlindPilot.iss`: `PrivilegesRequired=lowest`, `CloseApplications=force`, `[Icons]`.
- v0.21.3 content. `git show 8320abf:RELEASE_NOTES.md` and `git show 211f601 -- agent_backends.py` (the `_freebuff_log_messages` helper, the POSIX pump thread, and the prewarm generation counter).

## Left out or softened because it could not be verified

- The audit draft said Hermes tickets live thirty seconds. `grep -n ticket hermes_worker.py` finds only comments about minting a ticket, no lifetime, so the README says "short-lived single-use ticket".
- "The fastest place to get help" (old README line 8) was dropped as unverifiable.
- The old README said Hermes never asks questions and that there are four backends. Both are wrong against the code and were corrected.
- The old README said bare Up enters the newest response. The code requires Ctrl+Up or Alt+Up; corrected.
- "vibe-coded" was dropped from the first line. One sentence in License and credits says the app was written with AI coding assistance.
- The CHANGELOG header sentence about combined version ranges was dropped, because `grep "^## " CHANGELOG.md` shows no combined range exists in the file.
- CHANGELOG v0.3.9 and v0.20.0 keep "Ctrl+H" for Recent Conversations, since that was the chord at the time; v0.21.0 records the change.
- Test counts, investigation narratives, and quoted announcements were removed from CHANGELOG entries unless the wording itself was the change.

## Style checks run

`grep -n -E "—|–"` on every edited file returns nothing. A grep for the audit's puffery patterns (not just, not only, seamless, robust, powerful, gratefully, simply, delve, leverage, surface, elegant, cleverness, fastest, vibe, comprehensive, effortless, unlock, empower, harness, streamline) returns nothing in any edited file. Mid-sentence colons in prose return nothing; the remaining colons are in the badge alt text "License: MIT", the `SPDX-License-Identifier: MIT` header name, Inno Setup directives, and a pre-existing `[Tasks]` comment in the installer that was outside the audit's scope.
