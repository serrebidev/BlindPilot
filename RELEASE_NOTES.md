# BlindPilot 0.20.0

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release adds a fifth backend: Hermes Agent, local, in WSL, or on another computer,
together with a way back into any conversation it knows — including one running right
now. It also carries the changes the new shape of that backend forced on the frame it
sits in: a connection that outlives a turn, narration for a turn that is thinking, and
the working sound finally under the listener's control.

Contributed from outside the project as one branch of nineteen commits, verified on all
three platforms, and taken whole rather than split: the pieces share one spine.

## A fifth backend

Hermes Agent reads its answers, its reasoning, and every tool it runs into the same
navigable rows as the other four. It reopens past conversations, compacts in place, and
can be steered or stopped while it works. It is chosen from the Model menu like the
others, and the setup wizard walks its sign-in the same way — with one honesty the
other backends do not need: Hermes' sign-in is an interactive picker, so the wizard
opens a real terminal window and says to come back when it is answered, rather than
running it hidden with no keyboard attached and reporting the failure as a mystery.

The reason it needs a different shape from the other four: they are command-line
programs on this machine, spoken to over a pipe, each with its own conversation files
on this disk. Hermes is a gateway — newline-delimited JSON over stdio when it is here,
a WebSocket when it is not, and one store for every conversation rather than a file
each. Three consequences follow, and each shaped the code.

**Attachments cannot be paths.** The other backends read this disk, so naming the file
is enough. Hermes may be reading a different disk — or, with two machines that mount a
drive the same way, a different file of the same name. The file itself travels, and the
filename is sent separately from the path, because a Hermes on Linux does not read a
backslash as a folder separator. A file that cannot be sent stops the turn and says
which file and why, instead of returning an answer about something the model never
received.

**The connection outlives a turn.** One connection per conversation rather than one per
message: each message used to pay for a login, a handshake and a resume, and left the
server reaping the session it had just abandoned. It is read continuously, which is
what answers the keepalive pings a Hermes bound to a public address sends — without
that, a held connection is closed from the other end within about half a minute. It is
let go when the conversation is left, when a turn is cancelled, and when it is found
dead, in which case the next message opens a fresh one exactly as before.

**The reasoning channel is not free text.** Hermes' reasoning stream carries the
spinner it draws for a terminal, which read aloud is noise. It is left out, and the
reasoning it genuinely reports is shown — once, not twice when a short answer arrives
as its own reasoning.

## Reaching one that is not here

Options > Remote Hermes takes a name, a port, and either a session token or a username
and password, with a Test connection button that says whether they work before anything
is sent. A server on a home network can be worked with from the desktop without opening
a terminal. Leave it off and the copy installed here is used, with nothing to
configure.

Failure messages say what to do about the failure — a refused key, nothing listening,
an unknown name — rather than only that it failed. The key never appears in any of
them, or anywhere else that is read aloud; it is kept in a file of its own rather than
among the display settings.

A Hermes installed inside WSL is found and run from a Windows desktop directly. Nothing
in Windows' PATH points at one, so the wizard used to report "not found" on a machine
with a working Hermes; the folder you are working in is translated, so Hermes runs
where you expect it to.

## Going back into any conversation it knows

Hermes Conversations (Ctrl+G) asks Hermes for its conversations rather than reading
this disk, which holds nothing for a Hermes on another computer. The list therefore
holds conversations started in a terminal on that machine, or by a messaging channel —
ones Recent Conversations could not show at all. It names where each conversation came
from; the ones its gateway is currently running are marked, and opening one of those
replays the transcript and then follows the turn in progress, which can be steered.

One honest limitation, stated in the dialog rather than left to be discovered: Hermes
keeps one event stream per conversation. Joining a live one moves that stream to this
window, and the previous client stops hearing it until it sends a message of its own,
which rebinds it. Both the list and the confirmation say so before it happens.

Two defects measured against a live gateway fixed rather than glossed. Steering and
stopping used to address the stored conversation id, which the gateway answers with
"session not found" — it takes the id of the live session — so on every remote
conversation, Steer and Stop did nothing while the window reported the instruction as
accepted. And a reopened conversation used to be remembered by the resume handle
belonging to the running gateway process rather than the durable conversation id, so it
worked until that gateway restarted and then could not be found again, with nothing
saying why.

## A connection that outlives silence

A turn that produced no output for about twenty seconds lost its connection. The
socket's connect timeout applies to every later read, so an ordinary quiet stretch — a
build, a test run, a model thinking, a rate limit being waited out — raised a timeout
that was treated as a dead peer and ended the thread reading the connection. With
nobody reading, nobody answered the server's pings either, so the server closed a
connection that was perfectly healthy. Measured against a server pinging as Hermes
does: the reading thread died after twenty-one seconds of quiet before, and survives
three minutes of it now, with the next message answered normally.

A read timing out is no longer confused with a peer that has gone away, while a peer
that really has gone away still ends the turn as before — within seconds rather than at
the end of the idle limit, and with the reason said. And sending into a connection
nobody is reading is refused instead of reported as success: that success was a silent
loss, the answer never arriving and nothing said about it.

## A turn that says what it is doing

Waiting was unnarrated for up to fifteen minutes, which is indistinguishable from a
hang for anyone who cannot glance at the screen. A quiet turn now reports itself about
once a minute, saying how long it has been working and which step it is on, and
Hermes' own account of what it is doing — the process it started, the conversation
being summarised to free up room — is read out as it arrives instead of being
discarded. A turn that is already reporting steps of its own is left alone.

Answers arrive as they are written rather than when the turn ends. They used to be
collected and delivered in one go, so a fifteen-second turn was fifteen seconds of
silence over a stream the server had been sending since its third second. Whole
sentences are released as they finish, never a half-written word, and the last clause
goes out when the turn ends.

The working sound is finally under the listener's control. It was a cue under a second
long repeated end to end for the whole turn — about nineteen times on a fifteen-second
turn, eighty on a minute-long one, over the answer being spoken, with nothing anywhere
to change it. Three choices now in Options: continuous as before, every few seconds
(the default, ten seconds, adjustable from two to a hundred and twenty), or off, which
leaves the send and received sounds in place. Choosing one applies to the turn already
running rather than the next one.

## Two things a screen-reader user found in ordinary use

Up in the prompt moves the caret now, instead of leaving the field. It used to enter
the newest response whenever the caret was on the first line — exactly where somebody
reviewing a multi-line prompt keeps arriving — so reading back what you had just typed
threw focus into the response list mid-sentence, with the text still there and the
caret gone. Up and Down behave as they do in every other multi-line field; the
responses are still reached by Ctrl+R, by Shift+Tab, or by Ctrl+Up for anyone who had
built the habit.

Hermes Conversations is offered only while Hermes is the backend. With another backend
chosen there is no Hermes in the picture, so the item is not unavailable, it is
irrelevant — and it is removed rather than greyed out, deliberately the opposite of
Compact and Connect. Those two are commands for this conversation that a backend cannot
perform, so a disabled item with a reason says something; an irrelevant item only costs
an arrow press in a File menu already at its ten-item ceiling. Removing it takes Ctrl+G
with it, so the chord does nothing rather than opening a dialog that would immediately
report there is nothing to ask. It goes back after Recent Conversations by name when
Hermes is chosen again, and chat mode hides it too, having no backend conversation to
reopen.

## Shapes the other backends inherit

The permission picker, the effort levels, the compaction command, and the wizard's
closing summary used to decide by backend name — which quietly gave any new backend
controls its protocol has no answer for. Each backend is asked what it supports now,
and Hermes is registered the same way the others are, so the fourth and fifth backend
could not drift apart in behaviour. The worker contract test that asserts one worker
per backend joins the registry at the same time, and a new backend's settings file is
offered by the Backend Settings dialog like everybody else's — with the note, where it
applies, that a Hermes on another machine reads that file over there.

## Verification

pytest 998 passed, 3 skipped; ruff check, ruff format --check, mypy, `--startup-smoke`
and `--startup-gui-smoke` all clean at the release commit. The branch ran the full
matrix on Windows, Linux and macOS in this repository's CI, and each of the three
defects found after the original push was verified reverted — on the platform where the
defect lives, since two of them are invisible on Windows.
