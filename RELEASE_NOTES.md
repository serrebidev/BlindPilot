# BlindPilot 0.31.0

Two new agent backends from Google, and a tidier Gemini model list in Chat mode.

- Gemini CLI is a backend. BlindPilot runs it headless, one process per message, reads its streamed answer, tool steps and results, and carries the conversation to the next message. Permission modes map onto its approval modes (bypass is yolo, accept edits is auto edit, plan is plan). The model picker offers its auto, pro, flash and flash-lite choices plus the models the Gemini API lists for your key. Install it from the setup wizard or Model, Manage Backends; it comes from npm.
- Antigravity CLI (agy), Google's newer terminal agent, is a backend. It runs in print mode with streamed input and output, one process per message, and resumes the conversation by id. The model picker reads agy's own model list, and reasoning effort goes from low to max. Bypass permissions skips its permission prompts; accept edits and plan use its own modes. The wizard installs it with Google's official installer.
- Both run on a Gemini API key from Google AI Studio. Sign In in the wizard asks for the key once, keeps it in your system's credential store, and uses it for both; Get an API Key opens the page that creates one. A key in the GEMINI_API_KEY environment variable, or the key of a Gemini account you already use in Chat mode, is picked up without asking. For Antigravity, Sign In also sets "modelProvider": "gemini" in agy's settings file, its only switch for running on a key; to keep your Google account sign-in instead, run agy in a terminal once and choose Already Signed In.
- Gemini CLI no longer serves personal Google accounts (Google ended that on 18 June 2026), so the API key is the way in for most people. A Gemini Code Assist Standard or Enterprise sign-in made in Gemini CLI is still used as it is.
- Messages typed while a Gemini CLI or Antigravity turn runs are queued and sent when it finishes, as with Command Code; Steer stops the turn and resumes with your instruction.
- Errors from either CLI are read as the one sentence that matters, such as "API key not valid. Please pass a valid API key.", instead of several lines of nested JSON.
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
