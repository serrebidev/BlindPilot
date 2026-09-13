# BlindPilot 0.28.1

Command Code's sign-in is fixed.

- Choosing Sign In now runs `command-code login` in a real terminal window. That command mounts a terminal UI for its authentication spinner, and the toolkit it uses refuses to start when its input is not a terminal. Run hidden behind the wizard, it stopped there — speaking "Raw mode is not supported on the current process.stdin" and the rest of a stack trace — before it could open the browser, and the crash's own documentation link was read out as the address to sign in at. A console window gives it the terminal it needs, which is how Hermes' setup already runs.
- The Command Code sign-in page now describes its own sign-in instead of borrowing Hermes' wording about needing a provider and model configured.

Everything else about the Command Code backend is unchanged.
