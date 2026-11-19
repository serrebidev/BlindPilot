# BlindPilot 0.59.5

This release makes the "Send with Ctrl+Enter" setting from 0.59.4 work everywhere you type a message.

The setting only reached the Agent prompt. Chat's Message field had its own fixed keys: Enter always started a new line and only Ctrl+Enter sent, whichever way the checkbox was set. In Chat, turning the setting on or off seemed to do nothing. Chat now follows the setting too. With it off, Enter sends and Shift+Enter starts a new line, the same as the Agent prompt. With it on, Enter and Shift+Enter start a new line, and only Ctrl+Enter or the Send button sends. Ctrl+Enter sends in both modes.

Shift+Enter also inserts a real line break now. The text field was quietly adding an invisible vertical tab character instead of a new line, and that character went out with your message. The same fix covers Enter when it starts a new line.

# BlindPilot 0.59.4

This release adds a setting that stops Enter from sending your message by accident.

In Preferences, turn on "Send with Ctrl+Enter (Enter inserts a new line instead of sending)". With it on, pressing Enter in the prompt just starts a new line, like Shift+Enter does today. Your message goes only when you press Ctrl+Enter, or when you Tab to the Send button and press Enter there. Ctrl+Shift+Enter still saves the message as a prompt and sends it. The setting is off by default, so nothing changes unless you turn it on.

Thank you to István Tábor for asking for this: typing quickly with a screen reader, it is easy to hit Enter before a message is finished, and until now that sent it.

# BlindPilot 0.59.3

This release stops a console window from stealing focus when you send a Hermes prompt.

Before each Hermes prompt, BlindPilot checks whether a Hermes gateway is already running. That check started Hermes' Python without hiding its console, so Windows opened a console window for a moment, moved focus to it and NVDA read out its title and "terminal" before focus came back. The check now runs hidden. Nothing about the reply changes.

An audit of every place BlindPilot starts a process found no other Windows launch that opens a console. The only ones that do are the ones meant to: the interactive sign-in terminal and an app you ask BlindPilot to open. A new test fails if any process is started without hiding its console.
