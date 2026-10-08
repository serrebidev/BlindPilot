# BlindPilot 0.40.0

Muse.ai's Feed, Ideas, Schedules, and Goals now offer actions alongside their readers. Open them from Model, muse.ai.

- Feed: Enter or Read opens the full post. Open link offers its article and social links in your browser. Discuss in chat prepares the post's details in an editable prompt. Posts linked to an executable idea also offer Run now.
- Ideas: Run now starts the whole idea, or lets you choose its selectable parts. Canceling either picker starts nothing. The returned execution conversation opens in a tab so you can read its progress and continue the chat. Completed or building ideas are not offered as new executions.
- Schedules: Run now starts an enabled task without waiting for its next scheduled time. Run history reads past runs with their times, statuses, result summaries, and errors. Tasks explicitly targeting main chat open that chat after starting. Running a task does not alter its schedule.
- Goals: this new menu entry lists goals, child goals, and their statuses. Read includes details and updates. Manage goal can edit its title and description, make it active, pause it, mark it completed, retire it, or delete it after confirmation. Suggestions reads the goal's proposals; Accept and run starts a suggestion's work, while Dismiss leaves it unexecuted.
- Discuss in chat works for all four views. It opens main chat with the selected item's readable details and a place to write your question. Nothing is sent until you press Send, and another tab's prompt is preserved.
- The installed client remains upstream muse-cli. BlindPilot adds the current web client's scheduled-task Run now route to its existing compatibility hook for muse-cli 0.3.2; your installed client files are unchanged.
- Quick action replies are no longer missed by the chat watcher. The loaded history's sequence number now follows into the next check, including answers that arrived before the follow-up worker started.

Actions use the same internal gateway as Muse.ai's web client. They run only when you select them; opening or reading a view executes nothing. Sentinel approvals remain explicit. Feed and Ideas still show the current gateway page. Schedule editing can be requested through Discuss in chat.
