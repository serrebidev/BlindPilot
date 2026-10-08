# BlindPilot 0.33.0

Seventeen new features, picked from an audit of Kelly Ford's The Chat Place, an accessible companion for Claude Code. The biggest: Claude Code can finally ask before it does something, instead of being refused without anyone being told.

- Claude Code permission prompts. Outside Bypass permissions, a tool call Claude Code's permission mode leaves to you used to be refused without a word. Now a dialog shows the whole request to read line by line, with Allow, Allow for this session, or Deny, which is the default and can carry a reason Claude reads. "For this session" stays in memory and is never written to Claude's settings files. In Plan mode the plan opens the same way: approve it and carry on accepting edits, approve it and keep being asked, or keep planning with what to change.
- Changed Files, Ctrl+Shift+D, in the Model menu. It lists the files that differ from the last commit in this tab's git repository, with lines added and removed, and Enter reads a file's changes as Added, Removed and Unchanged lines. It asks git, so it works the same for all nine backends.
- How full the context is. Session Status now says it, for Claude Code and Codex ("Context 62% full: 124,000 of 200,000 tokens"), and BlindPilot tells you once when a conversation passes 80%, pointing you to Compact Conversation.
- Turn Status, Ctrl+Shift+T, in the Model menu, says how long the running turn has been working, the last thing it did, and how many messages are queued.
- Notifications. When a turn finishes, fails, asks you a question or wants permission while you are in another window, a system notification says so, and choosing it brings that tab forward. Turn it off in Preferences.
- Export Conversation, Ctrl+E, saves the conversation as Markdown, a web page, or plain text, offered in Documents. Each response is a heading, so browse mode moves between them.
- Shift+Enter on a row opens the whole response as a formatted page, so your screen reader's browse mode moves by heading, list, table and link. Nothing is fetched from the internet, and links open in your browser.
- Repeat Last Announcement, Ctrl+Shift+R, says the last thing BlindPilot announced, for when a keystroke cut it off.
- Your message is read back as it is sent ("Sent: fix the build"), so dictated or pasted text is heard. Long messages are cut short and code blocks left out. Turn it off in Preferences.
- F3 and Shift+F3 in the responses move to the next and previous match of your last search. F6 and Shift+F6 move between the tab strip, the responses and the prompt, and read the status bar on the way round. Ctrl+Shift+C copies the last code block of the response you are on.
- Recent Conversations can be sorted newest first, oldest first, by title or by folder. F2 renames a conversation and Delete hides it; Show hidden conversations brings it back. No backend's files are changed.
- Only one BlindPilot runs at a time. On Windows, starting it again brings the running one to the front.
- Help, Report a Bug asks what happened and opens GitHub's new-issue page with it filled in, showing exactly what else goes with it: versions and settings, never anything from a conversation.
- Help, Keyboard Shortcuts, F1, lists every shortcut grouped by where it works.
- Check for Updates asks before installing would stop a running turn or lose a message you have not sent.

# BlindPilot 0.32.1

Clearer Gemini messages, a tidier Gemini model list in Chat mode, and no more blank row after answers.

- When Google refuses a Gemini CLI turn because you signed in with a personal Google account, BlindPilot now says so in plain words and tells you to switch the Backend to Antigravity CLI, which signs in with the same Google account. Before, it read out a line of Node.js error output. Google stopped serving personal accounts in Gemini CLI in June 2026; Gemini CLI still works with a Gemini API key or Vertex AI set up in Gemini CLI itself. The setup wizard's Gemini sign-in page now warns about this before you start.
- Chat mode's Gemini model list no longer offers native-audio models, which Google lists but which cannot chat.
- Streamed answers no longer end with an empty row. This affected Antigravity CLI, Gemini CLI, Command Code and Muse.
- Antigravity CLI 1.3.0 was checked live: tool use and resuming a conversation both work.

# BlindPilot 0.32.0

A research and integration design document for ChatGPT sign-in is added to the repository.

- A design document for "Continue with ChatGPT" is added to docs. It researches OpenAI's Sign-in with ChatGPT (SIWC) protocol and proposes how a ChatGPT-plan account would let users reach the public Responses API and the existing Codex CLI backend without an API key, keeping the account lifecycle shared between the chat subsystem and the pooled agent process. The document covers registration, persistent host identity, loopback callback, fresh state/nonce/PKCE per attempt, ID-token validation against OpenAI JWKS, scope checks, protected credential storage outside SQLite, and the responses that surface failures. Status: researched; design awaiting review; product code unchanged.

# BlindPilot 0.31.0

Two new agent backends from Google, and a tidier Gemini model list in Chat mode.

- Gemini CLI is a backend. BlindPilot runs it headless, one process per message, reads its streamed answer, tool steps and results, and carries the conversation to the next message. Permission modes map onto its approval modes (bypass is yolo, accept edits is auto edit, plan is plan). The model picker offers its auto, pro, flash and flash-lite choices, which follow Google's current models. Install it from the setup wizard or Model, Manage Backends; it comes from npm.
- Antigravity CLI (agy), Google's newer terminal agent, is a backend. It runs in print mode with streamed input and output, one process per message, and resumes the conversation by id. The model picker reads agy's own model list, and reasoning effort goes from low to max. Bypass permissions skips its permission prompts; accept edits and plan use its own modes. The wizard installs it with Google's official installer.
- Both sign in with your Google account. Sign In in the setup wizard opens the CLI in a terminal window, Gemini CLI in its screen-reader mode, and the CLI opens Google's sign-in page in your browser. The wizard checks every few seconds and moves on by itself once you are signed in, and then you can close the terminal. Agent mode never uses an API key; keys are for Chat mode only.
- Google stopped offering Gemini CLI to personal Google accounts on 18 June 2026. If Google refuses your personal account there, Antigravity CLI is the one to use with it. Gemini Code Assist Standard and Enterprise accounts still work in Gemini CLI.
- Messages typed while a Gemini CLI or Antigravity turn runs are queued and sent when it finishes, as with Command Code; Steer stops the turn and resumes with your instruction.
- Errors from either CLI are read as the one sentence that matters instead of several lines of nested JSON, and a turn that fails because you are signed out says how to sign in.
- Chat mode's Gemini model list leaves out models that cannot chat, such as speech, image, video, embedding and live-audio models, which failed when picked.

# BlindPilot 0.30.1

Small fix release.

- Muse history that could not be inspected is read again: the transcript size guard treated any
   log path it could not stat (for example a PermissionError on Muse's WSL log under /root) as
   empty, so the conversation came back with no turns. The guard now steps aside for paths it
   cannot inspect and lets the backend reader, which caps itself and reports its own failures,
   decide.

# BlindPilot 0.30.0

This release is about Stop, and about the agents a task starts behind your back. Stop used to sound broken on three backends even when it worked, and on every backend it left the task's subagents running. Now Stop is quiet when it lands and reaches every agent the task started, and a new list shows you what those agents are doing while they run.

- Stop that sounds like Stop: on Muse Code, Command Code and FreeBuff, a stopped turn was reported the way a finished one is, so BlindPilot switched narration back on, played the received sound and read the partial answer aloud. The turn had actually stopped, as a live test against Muse confirmed. BlindPilot now recognises the reply to Stop for what it is, keeps what had already streamed, and says Stopped.
- Stop that reaches the subagents: ending a turn never ended the agents it had spawned. BlindPilot now stops each one by name, in each backend's own way: Claude Code's stop_task, Muse's subagent/stop and workflow/cancel, Hermes' subagent.interrupt, an interrupt on each Codex agent's thread, and an abort of each opencode child session. Command Code and FreeBuff already ended the whole process. On Muse this was tested live: a subagent in the middle of a 45-second command was stopped, and the command never finished.
- The Subagents running list: while a task has agents running, a list appears between the tab strip, where Tab from the tab strip lands on it, and the responses. Each row says the agent's name, whether it is running, completed, failed or stopped, and the last thing it did. Press Enter on a row to read that agent's activity in a read-only edit field that keeps updating while it is open. Leave the caret on the last line to follow along, or move it anywhere else to read undisturbed. When no agent is running, the list leaves the tab order. It works on every backend: Claude Code, Codex, Hermes, Muse Code, opencode, Command Code and FreeBuff.
- Codex and opencode agents that can ask: a Codex agent's own permission requests used to be refused without anyone seeing them, and opencode's child sessions asked on a stream BlindPilot was ignoring. Both are now answered under the tab's permission mode, and an agent's words no longer leak into the main answer.
