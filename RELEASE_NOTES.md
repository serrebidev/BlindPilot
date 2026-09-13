# BlindPilot 0.28.2

Command Code's sign-in no longer opens a console window.

- Choosing Sign In now runs `command-code login` in an off-screen terminal. That command renders a terminal UI for its authentication spinner, which refuses to start without an input terminal — but there is nothing in it to read or answer, because the sign-in itself is the browser page the CLI opens. The wizard used to open a visible console just to satisfy that UI.
- When the command ends, the wizard asks the CLI whether the sign-in landed and reports the answer itself, instead of leaving you to come back and choose Already Signed In. Backing out of the wizard, or asking to sign in again, stops the hidden sign-in rather than leaving it running.
- The test suite no longer fails on a machine whose Python has another `tests` package installed in site-packages; the import no longer goes through that name.
