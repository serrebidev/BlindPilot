# BlindPilot 0.43.1

muse.ai now tells you what it's actually doing while it works, the moment it does it, instead of just "is working".

- Real activity, live. BlindPilot watches muse.ai's Activity feed, the same one the muse.ai web app's Activity panel uses, while your message is being worked on. Each task muse.ai starts is added to the conversation ("muse.ai started a task: Check disk and uptime"), then what it is doing in it ("muse.ai: Running df -h / and uptime"), then every command it runs and every file it writes or deletes, with the command itself ("muse.ai is running rm -f /home/hatch/workspace/bp-test.txt", "muse.ai: Checked root disk and system uptime. Ran df -h / | tail -1; uptime"). Each row is read out as it arrives. Only the work of the chat you're in is shown, not your other muse.ai chats.
- Connecting and connected. A muse.ai message now starts with "Connecting to muse.ai." and "Connected to muse.ai." before "Sent to muse.ai. Waiting for its reply.", so you know where it's at.
- Steps are back in the conversation. Steps such as "muse.ai: Verifying ports" were only reaching the status line. muse.ai sends its live updates out of order, and when its first update beat the copy of your own message, BlindPilot mistook the whole reply for older work and dropped its steps. That's fixed: a reply belongs to your message when it started after you sent it, whatever order the updates arrive in.
- Status messages arrive straight away. The "## Status" updates muse.ai posts while it works now show up the moment they're finished, instead of on the next check a few seconds later. Nothing is shown twice.
- "is working" and "is responding" still stay on the status line only; they're not added as rows.

muse.ai doesn't publish anything more detailed than this. These are the same steps and commands its own web app shows.
