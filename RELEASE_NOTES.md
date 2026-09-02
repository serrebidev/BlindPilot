# BlindPilot 0.11.0

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release is about the things that fail quietly. The largest of them: when the
connection to the screen reader dropped, BlindPilot stopped speaking for the rest of the
session while its menus went on reporting narration as enabled. It follows 0.10.0, which
put everything the application does into the menu bar, and 0.9.x, which made the sound cues
independently switchable and stopped a turn from starting on top of the last one.

Nothing here removes a button or changes a chord.

## It keeps talking when the reader connection drops

The Windows speech output was built once, at import, and every failure to speak was
swallowed. When the connection to the reader went away — which is what NVDA restarting or
a JAWS COM object disconnecting looks like from here — the output was dead for the rest of
the session. Every later announcement raised, was swallowed, and said nothing. The Options
menu still said narration was on. Restarting BlindPilot was the only way back.

On an application driven entirely by ear that is not a degraded state. It is the whole
thing failing, silently, while continuing to claim it works.

A failed announcement now rebuilds the output and says that same line again, so the line
that discovers the drop is not the one that is lost. If the rebuilt output cannot speak
either, it is let go of rather than tried first on every line for the rest of the session.

The same rebuild covers the case that was equally permanent the other way round: a reader
started *after* BlindPilot, which used to mean silence for good. Looking again is throttled
to once every five seconds, because building the output scans for a reader, and doing that
per narration line during a fan-out would cost more than the speech does.

Startup now also says so, in the log, when there is no speech output at all — with
accessible-output2 missing, BlindPilot on Windows runs in total silence while its menus
report narration as on.

## A startup check does not take your screen any more

`--startup-gui-smoke` built the real window and closed it a second and a half later, and
ran the whole of a real launch to get there: it allocated a console, which Windows hands
back already visible; it showed the frame, raised it and asked for the foreground; and it
put focus into the prompt. That last one dropped whoever was working in another window into
BlindPilot's prompt field — and since Windows has to show a window to give it focus, it
also dragged the hidden window onto their screen.

Building the window is the point of the check. `Layout()` gives all of it — every menu,
control and binding made, and the sizers able to lay them out — without the window ever
being on screen.

## The console is claimed only for the backend that needs one

FreeBuff is driven through a pseudo-terminal, and creating one gives a windowed application
a console whether it wants one or not. Claiming one up front and hiding it is right, and
stays: it means the console never arrives in the middle of your first message.

What was wrong is that it happened on every launch. Claude Code, Codex and opencode are
ordinary subprocesses that never need a console at all, so three quarters of launches paid
one frame of a visible console for something they would never use. It is now claimed when
FreeBuff is the selected backend, and when the backend is switched to FreeBuff.

## There is a record when a turn dies

Only Claude Code left any account of a turn that ended badly. Codex, FreeBuff and opencode
left nothing. The packaged build is windowed, which on Windows means it has no stderr at
all, so an uncaught exception — or a native crash in wxPython, pywinpty or ConPTY — went
nowhere: no console, no message, no file.

All four backends now record an unfinished turn through one diagnostics module, alongside
uncaught exceptions on the main thread and in the workers, and native crashes.

**What is never written, at any level, for any reason: the text of a prompt, the text of an
answer, the contents of a file, or a credential.** This application's content is your source
code and the questions you asked about it, so that line is enforced rather than trusted.

The log goes where each platform keeps logs — `%LOCALAPPDATA%\BlindPilot\Logs`,
`~/Library/Logs/BlindPilot`, `$XDG_STATE_HOME/blindpilot` — rather than into the roaming
settings folder, and it is capped at four files of a megabyte. Set `BLINDPILOT_LOG_LEVEL`
to raise the detail for a bug report. **Help > Open Log Folder** opens it, because reading a
path out loud and leaving you to navigate to it is not a way in.
