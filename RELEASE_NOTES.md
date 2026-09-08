# BlindPilot 0.24.0

Chat mode opens on the account and profile you chose, and the keyboard reaches all of it.

## The account you actually use

Chat mode opened on whichever account sorted first alphabetically and on no profile at all. With more than one account that meant re-picking yours on every launch, and a conversation profile was something you set again each time or did without.

There is now a **Use as default** checkbox under the list in Accounts and under the list in Conversation profiles. Arrow to a row, tick the box, and the tick comes off whichever row had it. Chat mode opens on that account and that profile from then on.

The box sits under the list rather than inside the editor because it says which of them is the one, not what any of them is set to — so marking a default is arrow, Tab, space, rather than opening an editor and saving it. Each row also says "default" in its own text, so finding the current one does not mean arrowing the whole list with an ear on a checkbox behind you. Unticking leaves none marked, which is a state the window understands: it opens on the first account and on "No profile", exactly as it did before. The database keeps at most one marked in the same statement that marks it, so "the default" cannot quietly become two, and a database written by an earlier release has the column added to it when it opens.

## Four things NVDA found

Driving the window with a screen reader turned up four defects that had nothing to do with defaults. All four are fixed.

**The Chat menu could not be opened from the keyboard.** "&Chat" and "&Conversation" both claimed Alt+C. Windows opens the first match and pressing the key again does not move on to the second, so Accounts, Conversation profiles, Refresh models, History view and Diagnostics — every Chat-only command there is — sat behind Alt and four right arrows, and the menu you landed in was greyed out end to end. The Chat menu is **Alt+T** now, and Alt+C still opens Conversation. Every letter of "Chat" was already spoken for, and a menu takes an access key from a button rather than sharing it, so three buttons that were shadowed anyway give theirs up: Stop generation is **Alt+G**, Clear all is **Alt+L** and Remove selected is **Alt+E**. A test asserts no two menus share a letter and no chat button claims one a menu has.

**An empty History said "unknown" and then nothing.** A native list box with no items has nothing for focus to land on, so landing there announced the list, then "unknown", and the arrow keys answered in silence — which reads as a control that has broken rather than a conversation that has not started. It holds one row saying "No messages yet" now. It is not an entry: nothing offers to copy or edit it, and it goes the moment a real message arrives.

**Chat mode started with focus inside the Agent page nobody could see.** Making a session queues its prompt's focus so the page is shown first; in Chat mode that queued call outlived the mode switch and arrived after the window was up, landing on a control inside the hidden notebook. Tab and Shift+Tab then walked a page that was not on screen until something later moved focus back. One method decides where a mode starts now, it is asked once after the window is on screen rather than before it exists, and a page that is not shown refuses focus it was queued for.

**A Hermes tab's status named opencode's providers** — carried over from 0.23.0's own audit and already released there.

## What was verified

Live-checked on Windows with NVDA: the window opens in the Message box, the pickers open on the marked account and profile, History reads "No messages yet" as a list item with a name and a role, Alt+T opens the Chat menu and Alt+C still opens Conversation.

Verified with the full regression suite (1544 tests with warnings as errors), ruff's checks and formatting, and mypy over sixteen files.
