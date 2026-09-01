# BlindPilot 0.7.2

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

## Claude's background agents finish their work

Claude Code can finish its own turn while helper agents it started are still working.
BlindPilot previously took that first result as the end of the entire run, closed Claude's
input, waited five seconds, and then stopped the CLI. Every background agent stopped with
it, so a large task could lose all of its delegated work and report only an exit code.

BlindPilot now keeps the stream open until all of those agents have completed, failed, or
been stopped. While it waits, the live view announces how many agents remain and reminds
you that Stop Task can end the run immediately. Helper-agent narration is still shown
live, but it is no longer mixed into Claude's final reply.

## A bad ending keeps its explanation

Claude's error stream is now read continuously, preventing a full error pipe from freezing
the process. One malformed character can no longer end the reader, an unexpected reading
error is reported in plain language, and a good answer is retained even if Claude exits
non-zero afterwards.

When a Claude turn ends without producing its result event, BlindPilot records the exit
code and Claude's error output in `claude-worker.log` beside the settings file. This gives
an interrupted run something useful to diagnose after the window is gone.

## Sound cues can be muted

The Options menu now includes **Play sound cues**. Turning it off mutes the sent, working,
and received earcons immediately, including a progress cue already playing. The choice is
remembered between launches, and sound remains on by default.
