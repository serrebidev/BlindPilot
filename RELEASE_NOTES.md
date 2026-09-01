# BlindPilot 0.10.0

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release is about the menu bar: everything the application can do is now listed in it,
including the model picker, which until now could only be reached by typing `/model` into
the prompt. It follows 0.9.2, which made the three sound cues independently switchable,
0.9.1, which stopped a turn from starting on top of the last one, and 0.9.0, which gave
`/status` an answer on every backend and gave chat mode OpenRouter's server tools.

If you are coming from 0.8.1, read the 0.9.x notes as well — everything in them is in this
release. Nothing here removes a button or changes a chord, so existing muscle memory is
untouched.

## The model picker has a way in from the menu bar

The picker already existed and already worked. Typing `/model` into the prompt was the only
way to reach it, and nothing in the menu bar said the word "model" at all — which is where
a command is discovered, and where the shortcut for it is printed.

A **Model** menu now carries the backend, **Model and Effort…** on Ctrl+M, the permission
mode, **Manage Backends** and **Connect a Provider**. Backend and Manage Backends move
there out of File, where they had less to do with the rest of that menu than with each
other.

The permission mode belongs to the conversation, not to the window, so the mark in the menu
follows whichever tab is visible — arrowing along the tab strip moves it, because that is
exactly when the answer changes. The modes are radio items rather than check items, because
they are exclusive and that is what a screen reader says about them when they are built
that way. **Connect a Provider** is greyed out with a reason on a backend that has no
providers to connect, rather than being offered and then refused, which is how Compact
already treats a backend that cannot compact.

## File is split into File and Conversation

File had grown into everything: sessions, tabs, compaction, stop, find, the projects folder
and the desktop shortcut. It is now **File** for sessions, tabs and the application itself,
and **Conversation** for what happens inside one.

Every item is appended and bound through a single helper, so neither half can be added
without the other. A menu item that does nothing is worse than no menu item, and that is
the failure this shape makes impossible rather than merely unlikely.

One detail worth recording, because it looks like an inconsistency and is not: a chord
written in brackets rather than after a tab is one the frame's own accelerator table
already carries. A tab there would register a second menu accelerator for the same key, and
Windows will not fire a menu accelerator whose key is Tab at all.
