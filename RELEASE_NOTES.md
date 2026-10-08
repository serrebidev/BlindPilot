# BlindPilot 0.39.0

Your main muse.ai conversation, its approvals, and the things it works on are all available in BlindPilot.

- Recent Conversations, Ctrl+Shift+H, now lists your main muse.ai conversation as Main chat, alongside your side chats. Previously BlindPilot deliberately left it out, so there was no way to reopen it.
- Selecting the muse.ai backend opens the main chat and reads its history into the conversation, ready for your next message. An unnamed tab opened with muse.ai already selected does the same, including when BlindPilot starts with that backend selected. Opening the chat sends nothing to muse.ai.
- New Conversation, Ctrl+Shift+N, still starts a separate side chat. Reopening a side chat continues that same chat. If BlindPilot cannot find the main chat, it tells you and keeps your prompt unsent; it does not quietly send it to a new side chat.
- Chat history containing arrows or other Unicode characters now loads on Windows. muse-cli could fail while printing those characters, leaving the conversation empty even though its messages were there.
- With muse.ai selected, Model replaces the unavailable Permission Mode submenu with muse.ai: Approvals, Schedules, Feed, and Ideas. Each opens a native list; Enter or Read opens the full text, and Refresh asks for the current contents. Schedules show whether a task is enabled, its cadence, timezone, and next run. Feed keeps the full article text and links. Ideas include their descriptions and details; reading one does not start it.
- Approvals lists pending requests and recent decisions. Decide Approval opens the exact request for you to allow once or deny. Requests also appear in a permission dialog while a muse.ai turn is running. BlindPilot's bypass mode does not bypass Muse.ai's Sentinel, and Stop sends no approval decision. Persistent permissions remain in the muse.ai app.
- Larger gateway replies now work with muse-cli 0.3.2. That client tried to decode each piece of a multipart reply separately, causing approval lists and other large responses to fail. BlindPilot supplies a compatibility fix only to the muse-cli processes it launches; it does not change your installed client or other Python programs.
- These account views use the gateway that muse-cli supports. They show the current page of feed and ideas returned by Muse.ai. The schedules, feed, and ideas views are read-only.
