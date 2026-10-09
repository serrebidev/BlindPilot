# BlindPilot 0.41.0

While muse.ai works on a message, the status line now says what it is doing, in the same words the muse.ai web app shows: "muse.ai: Searching", "muse.ai: Fetching release", "muse.ai is responding". Before, it could only count the agent's working steps.

- Where to find it: the status line, and the live status row at the bottom of the conversation, which changes in place as muse.ai moves on. Nothing is spoken for these updates, since they change every few seconds; arrow to the bottom of the conversation or read the status line to check progress. The row is removed when the turn ends.
- It works for the main chat, side chats, and a reopened chat that muse.ai is still working on. Only the conversation's own chat is followed, so activity in your other muse.ai chats does not appear here.
- If the live connection drops, the status line falls back to the step count it used before. Your messages and answers travel exactly as they did; this only adds a listener alongside.
- How it works: the muse.ai web app reads these updates from a live chat subscription. Upstream muse-cli 0.3.2 can subscribe, but in a way that never sees side-chat activity, and it prints nothing until the subscription ends. BlindPilot's existing compatibility hook for muse-cli 0.3.2 now lets its watcher subscribe the way the web app does and pass each update along as it arrives. The hook applies only to muse-cli processes BlindPilot starts; your installed muse-cli is unchanged. Statuses replayed from before the turn began are ignored.
