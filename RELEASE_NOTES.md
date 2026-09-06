# BlindPilot 0.22.0

Chat mode can start new conversations again, the Responses list wraps the way it used to, and the checks CI runs now run on the machine before a commit is made.

## Chat mode: new conversations

Start New Conversation carries Ctrl+Shift+N in the Conversation menu, and its handler was written to serve both of BlindPilot's modes. The menu item, though, was built as an agent-only command - one of the set that acts on the visible session tab - so the moment Chat mode was shown, `_set_app_mode` greyed the item out along with the rest of them, and the chord went dead with it. Nothing said so. The small New conversation button on the chat panel still worked, but a person following the menu - and a screen reader user following the menu is the ordinary path - pressed Ctrl+Shift+N or opened the Conversation menu, found the item greyed, and kept typing into the conversation they had been trying to leave. The chat log shows it plainly: four sends of the same first message in an hour, three of them landing in a conversation that was supposed to have been abandoned.

The item now stands outside the agent-only set. It stays enabled in both modes, and the one handler routes to whichever mode is showing: the session tab's clear-conversation in Agent mode, the chat panel's new-conversation in Chat mode. Two regression tests pin the menu item's enabled state in each mode and the routing itself, so the next mode that arrives cannot quietly swallow the chord again.

## The Responses list wraps again

A contributor's visual pass (PR #37) replaces the flat list of responses with one that wraps long rows to the width of the window instead of cutting paragraphs off at the right edge, and draws each row by its kind - your lines bold, thinking muted, code monospaced. Wrapping lists cannot be drawn by a native control, and a native screen reader sees nothing inside a custom-drawn one, so on Windows the list carries its own accessible object and announces rows as a list should. On Linux and macOS the toolkit has no accessible object to give - constructing one there raises, and on macOS it aborts the process outright - so those builds keep the platform's own list, which their screen readers already read by themselves. Every construction site in the window went through a single factory, so the platform split happens once.

## Checks before the commit

PR #38 adds a pre-commit configuration running what CI runs: ruff's checks, ruff's formatting and mypy on every commit, and the full test suite with warnings as errors on push. The suites take about three minutes, and a CI run spent discovering a formatting nit is a run wasted; what these hooks cannot catch - a failure that only shows on Linux or macOS - the runners remain the only check for, and the configuration says so.

## Also in this release

- A scratch CI-fix report that rode along with PR #37 and failed formatting on every runner has been removed, along with the formatting failure itself.

Verified with the full regression suite (1468 tests), lint, formatting, type checks, and the startup, GUI, and Chat GUI smoke runs.
