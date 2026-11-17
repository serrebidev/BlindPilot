# BlindPilot 0.59.2

This release makes the About dialog's links reachable from the keyboard.

In 0.59.1, Help, About listed the GitHub and Forgejo pages, the projects site, the Telegram channel and the full credits as text inside the description, so a screen reader read them out but no key could reach or open any of them. About is now its own dialog: the description comes first, then each of those five links as a separate control. Tab moves to a link, Enter or Space opens it in your browser, and Tab and the arrow keys never open anything by accident. OK or Escape closes the dialog.
