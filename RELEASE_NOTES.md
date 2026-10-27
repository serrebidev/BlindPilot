# BlindPilot 0.51.0

The rest of what The Chat Place by Kelly Ford does that makes sense for BlindPilot, built BlindPilot's own way. The headline is a new agent: GitHub Copilot CLI.

## GitHub Copilot CLI

- **A new backend:** pick GitHub Copilot CLI under Model, Backend, and talk to it like any other agent. Its answers stream into the list a sentence at a time, every step it takes is announced ("Reading app.py", "Running: git status"), the agents it hands work to show up in the subagent list, and its to-do list feeds Task List. Model offers auto or any model id you type, with reasoning effort up to max. Messages you send while it works wait their turn.
- **Setting it up:** Model, Manage Backends installs it with npm and signs you in. Sign-in runs `copilot login` in a terminal, which shows a code to type at github.com/login/device. If you already signed in to the GitHub CLI (gh), Copilot uses that and there's nothing to do.
- **A Copilot plan is needed:** your GitHub account needs a Copilot plan that includes Copilot CLI; Copilot Free is enough. Without one Copilot refuses every message with "Access denied by policy settings", and BlindPilot tells you to check github.com/settings/copilot instead of just failing.
- **Permission modes:** Bypass lets it do anything; Plan makes it plan without changing files; Auto turns on Copilot's assisted approval, which judges each risky step for you.
- **Your old Copilot conversations** show in Recent Conversations (Ctrl+Shift+H) and Find in All Conversations (Ctrl+Shift+S), so you can carry on one you started in a terminal. What Agents Know (Ctrl+Shift+I) lists Copilot's instructions, skills and custom agents, and can add an agent for it.

## Codex reviews its own requests in Auto

In Auto, a Codex step that needs to leave its sandbox, like reaching the network or writing outside the project, used to just fail. Now Codex's automatic approval review looks at it and allows or refuses it for you, and you hear the decision, what it was about, how risky Codex judged it and why. Don't ask still refuses those steps without a review.

## More lists to read

All three are under Conversation, Find and Browse, and filter, read and copy like Code Blocks and Links.

- **Commands (Ctrl+Shift+X):** every command the agent ran in this conversation, newest first, each marked done, failed, or no result yet. Enter reads the command with its output.
- **Decisions (Ctrl+Shift+Y):** every question an agent asked you and every permission it asked for, with what you answered.
- **Properties (Alt+Enter):** this session one line at a time: title, backend, folder, model, effort, permission mode and session ID, then how many messages, responses, tool steps, commands, code blocks, links and decisions it has, and how far along its task list is.

## Agent conversations, with roles

- **Kind of conversation (Alt+D)** in Send Response to Another Session (Ctrl+Shift+O): Builder and reviewer, Second opinion, Debate, or Plan and do. Each fills in a role for both sessions, which you can change or write yourself.
- **A third agent (Alt+G):** bring one more session in, with a role of its own. Replies then go round in order, from this session to the receiving one, to the third, and back.
- **Each agent is told the rules once:** before its first message, who else is in the conversation, that their messages aren't yours, its role, and to end its reply with DONE when they all agree the work is finished. A reply ending in DONE ends the conversation, and you hear who said it.

## Task lists come back

Reopening a Claude Code or Copilot conversation from Recent Conversations brings back the task list it left, so Task List (Ctrl+Shift+J) and Turn Status (Ctrl+Shift+T) pick up where it stopped.

Thanks to Kelly Ford for The Chat Place, whose ideas these are.
