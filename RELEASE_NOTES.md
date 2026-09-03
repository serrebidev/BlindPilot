# BlindPilot 0.20.5

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release is about the questions a session can answer. For a Hermes reached over a
network, "which folder should this run in?" is a question this computer cannot answer —
the folder lives on another machine, and a dialog that insisted on a real local path
turned the silence into a conversation that ran somewhere the person never chose. New
Session now asks the question this machine *can* answer: what is this session called?

## A name, then a folder on that computer

Remote mode asks for a name first, because it is the only field this computer can
answer, and for an optional folder on the *other* machine as free text. There is no
Browse button: a picker here would browse the wrong computer, and an inert control
costs a screen reader user a stop in the tab order on every visit. The path travels
exactly as typed — no `expanduser`, no `abspath`, no `expandvars`, each of which would
resolve against the wrong filesystem.

The silence this replaces was measured against a live gateway, read back from the
server's own state.db rather than from the reply, because the reply looks fine either
way: a Windows path sent to a Linux Hermes comes back with no error of any kind, and
the session is quietly running in the server's home directory. The dialog's old check
only ever proved the folder existed *here*, so a browsed folder passed validation and
the conversation ran somewhere else — in a tab named after a directory it was never in.
Now the resolved directory is reported when it is not what was asked for: "Hermes could
not use `C:\Users\g\Desktop\projekt`, so this conversation is running in
`/home/ubuntu`" — deliberately quiet when the folder was honoured, or when none was
asked for, the ordinary remote case. The comparison is textual, because the two paths
live on different machines with different separators, case rules, and notions of
existence.

Two small labels got their sentences back along the way. A tab with neither a name nor
a folder used to be labelled with nothing at all — the one label a screen reader cannot
tell from its neighbour — and reads "New session" now. `/status` says the folder was
"chosen by the Hermes running this session" rather than printing `Folder:` followed by
nothing.

## A session keeps the name it was given

The name reached the tab at creation, and then the first message took it away: a
conversation is normally named after its first message, and nothing checked whether
this one already had a name of its own. Sending "start" to a session named for its
subject left the tab called "start" — and with Hermes the two names then disagreed
about the same conversation, since a name given at creation is stored with
`title_source='user'` and is not overwritten by the automatic one. The name was the
only thing the person chose, and nothing could get it back.

A name belongs to one conversation, so it is dropped wherever the tab becomes a
different one — clearing it, reopening a Hermes conversation, restoring one from disk,
or changing backend once a conversation exists — but not before the first message,
where there is nothing to leave behind and a name typed seconds ago must survive
picking a backend. Whitespace counts as no name, so a tab cannot end up with a blank
label. The fix lives where the label is recomputed, not in the label function, which
was correct all along; each of the five changes has a test that fails without it, and
an unnamed session is still named by its first message.

## Transport fakes can no longer lie about being connected

The contract harness closes the thread this contributor opened with the Hermes backend:
every transport fake in the suite now runs against the real `Transport` semantics. The
distinction it enforces is the one a single `None` cannot make: a stream that has ended
must turn `connected()` False within a time budget, while a peer that is merely quiet
— a Hermes in the middle of a long turn — stays connected, and fakes declare which
they are. The registry is walked structurally rather than by name, so the next fake
fails at write time instead of on a runner in another country. It found its first
unregistered fake on its own first run, and it caught itself silently skipping on
Linux — a guard disappearing where it is needed most, because a missing wx module
raises `BaseException`, not `Exception`. Four fakes claimed connection after their
stream ran out and six let `send()` succeed after `close()`; all of them now end their
stream and refuse writes the way real pipes and sockets do.

## Verification

39 new tests across the two PRs (13 for the harness, 26 for the naming work), all
written failing-first and each with its negative control. Full suite: **1061 passed,
3 skipped** at the release commit, ruff, format and mypy clean, both startup smokes
clean. Repo CI was green on Windows, Linux and macOS for both PRs before merging.