# BlindPilot 0.59.3

This release stops a console window from stealing focus when you send a Hermes prompt.

Before each Hermes prompt, BlindPilot checks whether a Hermes gateway is already running. That check started Hermes' Python without hiding its console, so Windows opened a console window for a moment, moved focus to it and NVDA read out its title and "terminal" before focus came back. The check now runs hidden. Nothing about the reply changes.

An audit of every place BlindPilot starts a process found no other Windows launch that opens a console. The only ones that do are the ones meant to: the interactive sign-in terminal and an app you ask BlindPilot to open. A new test fails if any process is started without hiding its console.
