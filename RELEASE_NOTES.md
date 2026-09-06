# BlindPilot 0.22.1

Claude Code keeps its process between turns now, so the background agents a turn leaves running stop dying with it.

## The agents that died with the turn

A BlindPilot turn used to be a process. Each message started a fresh `claude -p --input-format stream-json`, the conversation carried on through `--resume`, and when the turn's result arrived, BlindPilot closed the process's stdin. The CLI then shut itself down - and took with it anything still running inside it. The worker did wait on `subagent_stats` before closing, which protected agents spawned in the background. It could not protect an agent resumed with SendMessage: in Claude Code 2.1.258, `started_in_background` is counted only in the Agent tool's spawn path, and a resume records nothing, so a running resumed agent was invisible to the count and the turn ended anyway. The log showed it as `turn ended early: exit_code=1 completed=True`, five times in one day on September 5, each followed by the app being restarted and the agent's work lost.

## One process per tab

PR #39 moves the process to the tab in a new `claude_session.py`, registered with the same pool Codex and Hermes already use, with the same fifteen-minute idle reaper. A turn borrows the process, writes its message, reads the stream to its result and detaches; the process stays. The first turn starts it, later turns reuse it, and closing the tab, switching backend, changing the working directory or the reasoning effort, or quitting ends it through paths that were already tested. Model and permission-mode changes travel down the stream as control requests and keep the process; a session that refuses the change is replaced with one started under the new setting.

What an agent reports after its turn has ended arrives as a late turn the panel starts on its own: the working indicator and earcon run, rows appear, the question dialog opens if the agent asks one, and the answer lands as a new response. Nobody typed anything, so there is no "You:" row. The announcement is one new sentence, "A background agent has reported. Receiving response". Two sentences the pool has always had become reachable for Claude Code - "Claude Code had stopped running. Restarting it, which takes a moment." and "Claude Code was idle and has been closed. The next message will restart it." - and two old ones are gone because nothing can say them any more: "Waiting for N background agents to finish" and "No response received". A process that ends without a return code says "Claude Code exited without a code". Stop sends the CLI an interrupt and waits up to five seconds for its confirmation, dropping the process only if none comes.

Because the process no longer starts per turn, the MCP servers no longer reconnect at the top of every message. Codex, opencode and Hermes already held their connections across turns; this closes the one backend that tore its process down.

## What was verified

The design's live check - spawn a foreground agent, resume it with SendMessage, confirm the follow-up arrives as a late turn and the process id holds across the three turns - has not run yet. The machine this was built on does have a signed-in CLI, but its weekly usage limit is spent until September 7, so the check runs after the reset and will be reported on the pull request.

Verified with the full regression suite (1507 tests with warnings as errors), ruff's checks and formatting, mypy over sixteen files, the startup GUI smoke run, and CI on Windows, macOS and Linux.
