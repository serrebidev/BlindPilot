# BlindPilot 0.25.0

A chat belongs to the profile it was started on, and there is a way back into one.

## Two ways a chat ran on the wrong profile

This began as an audit: build a chat panel with two accounts and profiles that name the one which does *not* sort first, then look at what actually reaches the request. Two things came out of it.

**A default profile was shown but never applied.** Picking a profile from the list moves the account and model pickers to the ones it names. A profile restored at startup because it is the default only put its name in the picker — the account and model were left as they were. A conversation is created on whatever account and model are showing, so the first conversation of every session was started on the wrong account and the wrong model while being recorded against a profile that names another one. Both routes go through one place now, so a profile means the same thing however it was selected. A profile whose account has since been deleted applies its model rather than applying nothing.

**A conversation ran on half of one profile and half of another.** The system prompt was snapshotted onto the conversation; the temperature, the token limit and the OpenRouter tools were re-read from the database on every request. Editing a profile part-way through a conversation therefore changed those settings while leaving the prompt it was started with. A conversation now holds the profile it was created on, so it is one thing from the first message to the last. The picker still says what the *next* conversation will start on.

What was already right, and now has tests saying so: the conversation records the profile it was started on, and moving the picker mid-conversation does not change the conversation underneath.

## Recent conversations

The `conversations` table was write-only. Every conversation recorded its profile, its account, its model and the system prompt it was started with — and the database had only `create_conversation` to put them there. No read, no list, and nothing in the window to open one again. Every launch started fresh, and the conversations behind it were reachable only by opening the database file by hand. The machine this was built on had sixty-nine of them.

**Chat menu → Recent conversations** (Alt+T, then E) opens the list. It is laid out like Accounts and Profiles because it is read by the same people: a filter box, a list, and Open, Delete and Close. Each row says what actually separates two conversations that opened with similar words:

```
Give me this prompt in a way that uses less tocans, 5 messages, MBE profile, openRouter, Mon 08 Sep 06:11
```

Times are this machine's, not the UTC the database stores. An empty list says "No conversations yet" and a filter that matches nothing says so, rather than leaving a silent list to be arrowed through.

Opening one restores everything from the conversation rather than from the pickers: the profile it was started on, the prompt it was started with, and the account and model it was talking to — with the pickers moved to match, so the window is not showing one thing while the next message goes somewhere else. A conversation whose profile has since been deleted still carries the prompt it was started with, and takes no temperature from a profile that is gone rather than inventing one. The lookups behind the list are left joins, so deleting a profile or an account loses the label rather than the conversation.

## What was verified

Verified with the full regression suite (1566 tests with warnings as errors), ruff's checks and formatting, and mypy over sixteen files. The two defects above were found by driving the real panel and are covered by tests stating the behaviour they broke.

The new dialog was not driven with a screen reader this time: it is built from the same controls as Accounts and Profiles, which were checked with NVDA for 0.24.0, and it joins those two in the shared dialog tests.
