# Audit: the FreeBuff backend

Read-only audit, 2026-09-21, followed by the fixes listed at the end. Line
numbers are as of this date. Ruff, `ruff format` and mypy are clean on the
whole tree before and after, and the FreeBuff test files pass before and after.

Five defects were found and all five are fixed. Four came from reading the code
against its own comments; the fifth came from finally running it, and it is the
proof of what the other four kept saying about this backend: every one of them
is about a screen that was described rather than seen. The live runs are in
"Verified against the release itself" below.

## What "FreeBuff" is, in this repo

FreeBuff has no headless mode, so BlindPilot drives its terminal interface
through a hidden pseudo-terminal and reads the answer off a pyte screen. The
surface is unusually wide for one backend and lives in four places:

| Where | What |
|---|---|
| `agents_backends._freebuff_*` helpers (`:946-1064`, `:1605-1685`, `:2251-2620`, `:4326-5342`) | account, usage, model catalog and picker, chat discovery, log reading, the prewarmed terminal |
| `FreebuffWorker` (`:5345-6145`) | the turn loop, screen reading, the question box |
| `freebuff_screen.py` | the pyte 0.8.2 wide-character crash repair |
| `session_history.py:780-909` | past conversations |

The turn loop is the risky part: it is a polling loop over a `re`-matched
screen, and nearly every condition in it was added in response to a release
that changed its paint. That history is why most of it is right, and why the
few places the reasoning was not carried to the end are worth listing.

## Bugs

### 1. The model swap is announced where Keep-up narration drops it -- MEDIUM / high

`agent_backends.py:5674`.

When the installed FreeBuff has dropped the chosen model, the picker cannot be
walked to it. After five seconds of trying, the turn stops trying and accepts
whatever the picker has highlighted, with a row that says so:

```python
self._on_activity(
    "tool",
    f"FreeBuff no longer offers {self._model}; "
    "using the model it recommends instead",
)
```

The comment above it states the requirement -- "provided the swap is said out
loud rather than made quietly" -- and the kind chosen is the one that fails it.
`_ALWAYS_SPOKEN = ("assistant", "notice")` (`blindpilot_app.py:2629`) is what
`SessionPanel._say` checks against, so under Narration, Keep up every `tool`
row is dropped. A Keep-up user is never told that the model they chose is not
the model their turn ran on, and the only evidence is the answer being unlike
what that model usually says.

Fix: make it a `notice`. Every other sentence in this worker that exists to
tell the person something they must know is already one -- the boot hold, the
mid-turn drop, the hourly cut-off.

Test: `tests/test_freebuff_model_swap.py` -- the swap row is emitted with kind
`notice`. The five-second patience became `_FREEBUFF_PICKER_PATIENCE_SECONDS`
so the test does not have to sleep through it, matching the five other
durations in this module that are already named for that reason.

### 2. A renamed or deleted chat folder lets an unrelated chat take over the tab -- MEDIUM / medium

`agent_backends.py:5744-5757`.

```python
if sent and now >= next_session_check:
    next_session_check = now + 1.0
    if chat_path is None:
        after = _freebuff_chat_dirs(self._cwd)
        new_ids = set(after) - set(before)
        discovered = max(new_ids, key=...) if new_ids else self._session_id
        if discovered:
            self._session_id = discovered
            chat_path = _freebuff_chat_path(self._cwd, discovered)
            log_offset = 0
```

The block exists to learn the id of a conversation this launch created, which
is genuinely unknown until FreeBuff writes the folder. But it is guarded on
`chat_path is None`, which is also the state of a *resumed* conversation whose
folder cannot be found -- a chat deleted between the history list and the send,
a `--continue` naming a conversation another profile owns, or a release that
moves `.config/manicode/projects`.

In that state `new_ids` is not this turn's chat: it is any chat folder created
since the turn started, including one another BlindPilot tab's FreeBuff turn
just made. `max(...)` takes the newest, so the tab adopts it. From there
`self._session_id` names a conversation the user never opened, and the next
message resumes it. The same scan also re-runs once a second for the whole
hour on a turn that can never succeed at it.

Fix: only look for a chat this launch created when there is no session yet --
`if chat_path is None and not self._session_id:`. A resumed conversation
already knows its id, `_on_session` already reported it at construction
(`session_reported = bool(self._session_id)`), and the end of the turn
recomputes the session to prewarm from either way.

Test: `tests/test_freebuff_resumed_session.py` -- a resumed chat whose folder
is missing does not adopt a folder that appeared after the send.

### 3. The About dialog names five of the seven backends -- LOW / high

`blindpilot_app.py:10445` and the module docstring at `:5`.

```
"An accessible desktop frontend for Claude Code, Codex, FreeBuff, "
"opencode, and Hermes.\n\n"
```

Muse Code became the sixth backend in 0.26 and Command Code the seventh in
0.28. The README was updated for both; the About dialog was not, so a person
who asks BlindPilot what it is gets a list that omits two of the agents it
drives. It is spoken text, which is what makes it worth fixing rather than
noting: nothing on screen reveals the omission.

`applied-blindpilot_app.md:119` records the same list being corrected once
before, by hand, when opencode and Hermes were added. Hand-correcting it again
would leave the next backend to do the same, and `BACKEND_LABELS` is already
derived from `BACKENDS` for exactly this reason (`agent_backends.py:724`), so
the fix derives the sentence from it and cannot go stale again.

Fix: `about_description()` at module level, built from `BACKEND_LABELS`, used
by `_show_about`. The sentence shape is unchanged.

Test: `tests/test_about_lists_every_backend.py` -- every label appears in it,
and the list is comma-separated with a final "and".

### 4. A steered message is read out as part of the answer -- MEDIUM / high

`agent_backends.py:5404`, `:6091`.

`steer` submits its text to the same composer the prompt went through, and the
reading's boundary is the echo of the prompt alone. FreeBuff writes what it is
given into the transcript exactly as it writes the reply -- plain text, with
nothing to say whose words they are -- so a steer's echo sat inside the section
that gets spoken, and the person heard their own instruction read back as
though the model had said it. `_freebuff_sections`'s own comment says the cut
exists because the echo is unmarked, so the reasoning was there and the second
message was not followed to the same conclusion.

The two fixes that suggest themselves are both wrong, which is why this was
written down rather than guessed at first. Moving the boundary to the newest
echo loses the answer above it: `_freebuff_sections` returns only what follows
the boundary, and on the degraded path where no chat folder can be found there
is no saved answer to fall back on, so the first half of the turn's work would
be dropped from the transcript. Leaving the boundary alone and removing the
echo's own lines keeps both halves.

Fix: every message typed this turn is remembered as an echo (`_freebuff_echo`),
and `_echo_spans` returns the lines each one covers, which are then skipped
when the reading is built. Only the prompt's echo is still a boundary; a
steer's span is removed from inside the section. That needs nothing new to be
known about FreeBuff, because the content-matching it relies on is the same
assumption the prompt's cut already rests on.

The span, not the single matched line, because an echo is taller than one line
when the terminal has to wrap it: `_keyed`-reduced lines are walked while what
has been collected still spells a beginning of the message, and the reply's
first line stops that run. The same walk now covers the prompt's echo, which
fixes the older half of the bug -- a long prompt's wrapped tail was read as the
answer too. A wrapped steer echo joined into exactly that: against the previous
code the test sees
`assistant: Part one of the answer. please run the full test suite and then commit it`.

Test: `tests/test_freebuff_steer.py`. What that file establishes is that the
reading behaves as designed for the shapes it is designed for, and it was
written in that order: no live capture existed yet, so the assumption behind it,
that an echo contains the text we sent, was taken from the prompt's cut. It has
since been run against the release itself -- which found the fifth defect below,
and left the four above standing.

### 5. A message's divider is read out as the answer -- LOW / high

`agent_backends.py:4398`, `:6139`. Found by running it, not by reading it.

0.0.180 draws each message in the transcript under a divider carrying the time
it was sent, so a message is two lines rather than one:

```
   [05:23 PM]
   Reply with the single word: pong
```

The prompt's divider sits above the boundary and went unread by accident. A
steer's sits below it, so removing the message line left the divider behind and
it was read out on its own, `assistant: [05:30 PM]`, measured on the release --
and joined to the front of the next row as `Kumquat \n kumquat \n [05:30 PM]`.

Fix: the divider immediately above a matched line belongs to that message's
span, since it is how the message is drawn rather than anything the model said.
Recognised by shape (`_FREEBUFF_TIMESTAMP_RE`), so a release that stops drawing
dividers takes nothing with it -- there is no line to match.

Test: the two `V180_STEERED_SCREEN` cases in `tests/test_freebuff_steer.py`,
built from the live capture. Both report `[05:23 PM]` as the answer against the
previous reading.

## Dead code and small things

- `agent_backends.py:5310`: `stale = None` is overwritten by the very next
  statement's tuple unpack and is read nowhere in between. Removed.
- `agent_backends.py:5865`: `timed_out = not self._cancelled and ...`. This
  reads as dead, because `if self._cancelled: return` is ten lines above. It is
  not: `cancel` runs on the window's thread and can set the flag in that gap,
  which is precisely the case the guard is for. Left alone, with this note so
  the next reader does not remove it.
- `_freebuff_log_pending_drop` reads `"Start agent "` with a trailing space
  (`:4794`) and `_freebuff_run_status` reads `"Main prompt finished"` without
  one (`:4762`). Both are exact matches against `msg`, both are covered by
  `test_freebuff_session_drop.py` against real log lines, and I have no second
  sample to say which shape is right. Left as measured.

## Checked and left alone

- **The prewarm lock is held across a spawn.** `prewarm_freebuff.launch()`
  runs under `_FREEBUFF_PREWARM_LOCK` and does file I/O and a `PtyProcess.spawn`
  inside it, so a Send arriving at that moment waits for the spawn instead of
  racing it. That is the documented point of the lock, the spawn is not slow,
  and `test_send_waits_for_starting_terminal` asserts the wait on purpose.
- **`saw_busy` gates the only completion test that does not read the chat
  file** (`:5838`). Dropping it would let the frame right after a send -- the
  composer, cleared, before FreeBuff paints its spinner -- end the turn early.
  The cost of keeping it is a fast turn with no spinner running to the hour,
  which is rarer and less bad.
- **The question box's keystroke replay** (`_press`, `_choose`) sends arrows
  and Enter blind, at `_FREEBUFF_KEY_SETTLE` each, and ignores `_write`'s
  return. A terminal that dies mid-answer therefore costs up to eight settle
  delays before the loop notices. Bounded, and the alternative is a
  read-back after every keystroke.
- **`_freebuff_sections` cuts the reading at the last occurrence of the
  prompt's first line.** That is what keeps a resumed conversation's previous
  answer out of this turn's reading. See bug 4.
- **`_freebuff_screen._RepairedScreen.display`** mutates the screen in place.
  It is the documented repair for pyte 0.8.2 and `test_freebuff_screen_repair.py`
  feeds it the real terminal sequence that crashes the unpatched class.
- **The catalog scan** (`_freebuff_models_from_install`) reads the whole
  installed release and is cached per release stamp, in memory and on disk.
  Already the fix for the slow path; nothing to add.

## Verified against the release itself

The FreeBuff installed here is 0.0.180, four releases newer than the 0.0.168
these comments were written against, and it is signed in. Everything below was
run through `FreebuffWorker` -- the real worker, the real hidden pseudo-terminal,
the real CLI -- from a scratch directory outside this repository.

What was already right, on a release nothing in the suite has ever seen:

- `find_backend_cli`, `_freebuff_signed_in` and the rest of the status path all
  answer correctly.
- `_freebuff_models_from_install` reads all five models out of 0.0.180's 126 MB
  executable: `z-ai/glm-5.3-flash`, `openai/gpt-5.6-luna`, `upstage/solar-pro4`,
  `meta/muse-spark-1.2-contributor`, `mimo/mimo-v2.5`. That scan is the most
  brittle code in this backend and it survived a release it had never seen.
- A plain turn: the boot hold fires and says so, the message sends, the chat id
  is discovered and reported, the reasoning arrives as a thinking row, the answer
  as an assistant row, and completion is detected. Thirty-six seconds from
  launch, twenty-two of them FreeBuff starting.

What the steered turn showed, with everything else held equal:

| Reading | Rows containing the steer | Rows containing a divider |
|---|---|---|
| Before the fixes | 1, `assistant: Also say the single word kumquat` | 2 |
| After the fixes | 0 | 0 |

The previous reading put the person's own instruction in front of them as the
model's words, copy marker and all, and mangled the answer around it: the same
thinking paragraph was read out four times as the section the reading compares
against shifted underneath it. The fixed reading delivered `pong` and `kumquat`,
one row each, and nothing else.

Also worth knowing, and not a BlindPilot defect: FreeBuff picked up this
machine's global agent instructions, because the scratch directory sits under the
home directory, and the answers came back with the `## ` headings those
instructions ask for. A conversation in a project folder gets whatever
instructions that folder's tree holds, which is what FreeBuff intends.

## Fixes applied

| # | Where | Change |
|---|---|---|
| 1 | `agent_backends.py:5669-5681` | picker patience named; the swap is a `notice` |
| 2 | `agent_backends.py:5746` | the chat-discovery scan needs a turn that has no session yet |
| 3 | `blindpilot_app.py:10443-10450`, `:5` | the About sentence is derived from `BACKEND_LABELS` |
| 4 | `agent_backends.py:5404`, `:6091` | every echo this turn made is removed by span, not just the prompt's |
| 5 | `agent_backends.py:6139` | a message's timestamp divider goes with the message |
| 6 | `agent_backends.py:5310` | dead `stale = None` removed |

Tests added: `test_freebuff_model_swap.py`, `test_freebuff_resumed_session.py`,
`test_freebuff_steer.py`, `test_about_lists_every_backend.py`.
