# BlindPilot 0.29.18

Tabs on different backends no longer send to the wrong one.

- Each tab now keeps its own backend. Before, the backend was one setting for the whole app: picking Codex for a new tab moved your other tabs to Codex too, so a message typed into a Claude tab went to Codex without warning. Model, Backend now changes only the tab you are in. A new tab starts on the backend of the tab you are in, and the menu follows whichever tab you switch to.
- Once your tabs use more than one backend, every tab tells you which one it sends to. The tab name starts with the backend, for example "Codex: fix login bug". The prompt is named after it, for example "Codex prompt", so your screen reader says it when you land in the box. Switching tabs announces it, for example "Session 2 of 3, Codex". And the send sound has a different pitch for each backend, with Claude Code keeping the original.
- With only one backend open, nothing reads or sounds any different.
- The version shown inside the app is correct again: 0.29.17 still called itself 0.29.16.
