# BlindPilot 0.29.25

Command Code no longer pops up command windows on Windows. People reported that when Command Code built something, or ran a longer command, cmd or PowerShell windows kept appearing on screen, which is distracting and can pull focus away from BlindPilot and your screen reader.

- The cause was in how Command Code starts background commands. Its ordinary commands already run hidden, but the ones it runs in the background, such as builds, test runs and dev servers, are started "detached". On Windows a detached command has no console of its own to borrow, so every console program it launched, whether cmd, PowerShell, a compiler or npm, was given a brand new visible window.
- BlindPilot now keeps those commands out of sight. When it starts Command Code it loads a small Node preload that keeps any command Command Code asked to hide attached to the hidden console BlindPilot started it with. Nothing else about the command changes: it still runs in the background, its output still reaches Command Code, and Command Code still stops it the same way.
- Any NODE_OPTIONS you have set yourself are kept; the preload is added after them.
- This is Windows only. macOS and Linux never had the problem and are unchanged.
