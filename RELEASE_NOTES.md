# BlindPilot 0.28.3

Command Code's sign-in no longer shows a console window.

- 0.28.2 ran the sign-in in an off-screen terminal, but making that terminal calls `AllocConsole`, and `AllocConsole` hands back a console that has already appeared on screen. Hiding it is the next thing that happens, so one frame of a window — titled with BlindPilot's own executable — was still being put in front of you. That was the window.
- The sign-in now gets a console that Windows creates hidden from the start, so there is no window to hide and nothing to flash. A watcher still hides any console Windows raises anyway.
- Checked end to end on Windows: the CLI runs in that console, exits 0, and no console window belonging to the process tree is ever visible.

The rest of 0.28.2 stands: the wizard waits for the sign-in to finish and then reports whether it landed, instead of leaving you to choose Already Signed In.
