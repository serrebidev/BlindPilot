# BlindPilot 0.19.0

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release is four audits landed together: a settings file nobody could write, two
ways of holding less power, six lifecycle and threading fixes, and two things the menu
bar and the narration were missing. It also carries a piece of 0.18.0's story: the
append fix the last release cut on top of the accessibility audit.

## A settings file nobody can write

`_save_config` caught `OSError` and did nothing with it — no return value, no log
line, nothing said. Ten call sites had no way to know, and several of them speak a
sentence the failed write has just made untrue: "BlindPilot will check for updates at
startup" is a claim about a file that was not written.

The worst is the first-run wizard. Finishing it is recorded by writing the settings,
and startup shows the wizard whenever that record is missing. A profile the settings
cannot be written to — a roaming profile on an unreachable share, a full disk, a
locked directory — therefore put somebody through the whole wizard, CLI install and
browser sign-in included, on every launch, with nothing connecting that to a file
nobody can write. The same write carries `backend`, so a Codex or FreeBuff user was
also dropped back to the Claude default and sent through the Claude checks: the one
visible symptom pointed at the wrong cause.

It now returns whether it wrote, logs the reason, and the wizard says plainly that its
settings were not saved and it will ask again.

### And it can no longer lose everything at once

The write truncated the file before filling it, so an interruption partway left a
half-written one that `_load_config` cannot parse — it returns `{}`, and every setting
resets together, `setup_complete` among them. The failure that loses one setting should
not be able to lose all of them. It is written to one side and moved into place now, so
an interrupted write leaves the previous settings standing.

## Two ways to hold less power

### The project folder is no longer on the executable search path

Every provider CLI is started with `cwd` set to the user's project folder, and CLIs
shell out constantly — `git`, `node`, `npm`, `sh`. Windows has historically included
the current directory in that search, so a `git.exe` in the folder was what ran when
the agent asked for git.

That folder is usually not the user's own work: the ordinary use of this application
is to clone a repository and ask an agent to look at it, which means running commands
inside a directory whose contents somebody else chose. A screen-reader user gets no
glance-at-the-folder moment in which to notice a file that does not belong.

`subprocess_env` is the single place every CLI is launched through, so
`NoDefaultCurrentDirectoryInExePath` set there covers all of them, and because the
environment is inherited it covers everything they go on to start. The PATH
construction that lets an npm shim find its sibling `node` is untouched, and a test
holds that part so the next edit does not "simplify" it away.

### The release token goes to the job that publishes

`permissions:` at the top of a workflow file applies to every job in it, so
`contents: write` was held by the build as well as the release. The build installs
`requirements-build.txt` and runs PyInstaller on three runners — a large amount of
third-party code executing with a token that can write to the repository, and none of
it needs one. The workflow now defaults to read, and the one job that needs write —
the one that installs nothing, checks checksums, and creates the release — asks for it
on its own. Same release, smaller blast radius.

## Six lifecycle and threading fixes

### Closing a tab mid-turn no longer freezes the window

Cancelling a turn ran on the UI thread. For opencode that is an abort POST to its
server, where the thirty-second timeout is per socket operation rather than a budget —
and every backend then joined for three seconds more. Run from the Close Tab handler,
that stopped the window pumping messages, which for a screen-reader user means
silence: nothing can be announced by a thread that is parked.

A tab being closed has nothing to wait for — its worker's callbacks are already
discarded once the panel is gone — so cancelling is handed to a daemon thread and the
close proceeds. Quitting still waits, because a CLI that is not killed outlives the
application that started it, but all the cancels are started first and one budget is
shared across them instead of three seconds apiece.

### The working sound stops when its tab does

The sound stopped only if the worker's completion event reached a panel that still
existed. The event drain discards everything once the panel is gone — by design —
which also threw away the one event that stops the sound, and the sound is not the
panel's to lose: every tab shares the frame's earcons, and on Windows the loop is
process-wide. It played on with nothing left alive to stop it. Closing the tab stops
it directly now.

### A FreeBuff turn cut off at its hour says so

Every turn gets an hour, and the loop watching the terminal ends either because
FreeBuff finished or because the hour ran out underneath it. From the code after the
loop those were indistinguishable, so a turn cut off mid-sentence was delivered through
the same `_on_complete` a finished one uses: announced as the answer, kept in the
transcript as the answer, with nothing to suggest it was not the whole of it. The only
clue was that the answer stops in an odd place — exactly the clue somebody who cannot
see the screen does not get.

A turn stopped at the deadline now says so before what it had got to, and keeps what
it had got to — an hour of work is worth having. One with nothing to show says what
stopped waiting rather than failing as if the backend had answered with nothing.

### A failing Codex turn waits for the line that says why

stdout reaching EOF and stderr's last line are different threads on different pipes.
The line worth having — the panic, the unauthorized, the out of memory — is the last
one written, which is exactly the one still in flight when the failure was composed.
Everything earlier was already in the list, so the race did not lose noise; it lost
the reason, and it lost it differently each time. The last words are waited for now,
bounded, because a pipe that never closes must not hold the turn open.

### Deferred callbacks no longer touch dialogs that are gone

The wizard's CLI and sign-in checks can land an event-loop iteration after the wizard
closes; its install callbacks arrive a minute into an install whose Cancel and Escape
stay live and which nothing tells to stop; and a prompt read-back scheduled a second
and a half earlier can outlive the tab it belonged to. Each now says nothing when it
arrives homeless, in the same terms the event drain already used for the same reason.

## The menu bar is the complete inventory

### `/status` is in it

Session Status was BlindPilot's own command, offered for every backend, and reachable
only by already knowing the word and typing it. It is Model > Session Status… now,
beside Backend and Model and Effort — the menu about what this conversation is set to.

The test is the more useful half: every command in the command table must now either
name the menu entry it lives under or record why it deliberately has none. Adding a
command without deciding fails there instead of shipping something findable only by
being told.

### Claude Code edits say how much they changed

"Editing server.py, 3 lines added, 1 removed." For somebody listening, the count is
the only sense of scale available — "changed a line" and "rewrote the file" are the
same sentence without it.

It is a real diff of the block, not a count of the lines in it: replacing a
twenty-line function to change two of its lines is a two-line edit, and "twenty added,
twenty removed" would be a worse answer than none. The count goes in the line the tool
call already has, never a second utterance — the running narration is long enough to
fall behind on a fan-out. A half that is zero is left out, Write says how many lines
it wrote, and MultiEdit adds its edits up. Claude Code only, deliberately: its Edit
carries both strings, and the claim cannot honestly be made for the others yet.

### Backend Settings: a way in to the agents' own files

Model > Backend Settings… lists every settings file BlindPilot knows of for the
current folder, says which are really there and which the CLI has not written yet, and
opens the chosen one in whatever the person already edits files with.

It deliberately does not edit them. Claude Code's settings.json is over three hundred
lines of nested JSON and Codex's config.toml over two hundred of TOML; a text box
holding either, navigated by ear and counted brace by brace, is worse than the editor
somebody already has — and a stray comma written back breaks the CLI silently until it
next refuses to start. The problem was never that editing text is hard. It is that
these are dotfiles in directories nothing announces, which is the same problem
`open_log_folder` already exists to solve.

It creates nothing either. These belong to the CLIs, every one of which writes its own
on first run; a file invented at a path BlindPilot chose would do nothing while
looking like it did. A missing one says so and offers the folder it belongs in.

The scope is in the label because the scopes are not interchangeable:
`.claude/settings.json` is committed to a repository and shared with whoever has it,
while `.claude/settings.local.json` is personal and normally gitignored. Opening the
wrong one silently is how somebody's own settings end up in a repository that is not
theirs, so both the scope and the consequence are in the text the screen reader reads
on arrow. And Enter on a focused button reaches that button — the mistake the
past-conversations dialog made last release, not repeated here.

## Verification

pytest 767 passed, 2 skipped; ruff check, ruff format --check, mypy, `--startup-smoke`
and `--startup-gui-smoke` all clean at the release commit. Each PR was additionally
verified merged onto the main that carried the ones before it, so the numbers here are
for the whole stack, not each part in isolation.
