# BlindPilot 0.48.1

Find code and links without moving through every row of a long conversation.

This patch also gives the new response-menu handlers explicit types, fixing the Windows CI type-inference failure reported after 0.48.0 was packaged.

- **Code Blocks, Ctrl+Shift+B:** Conversation, Find and Browse now lists every code block in the loaded Agent or Chat conversation, with its source, language, and line count. Filter searches the complete code as well as the label. Read Item opens the existing reader; Copy copies the code without its Markdown fence. Symbols inside code survive the speech cleanup used for ordinary response rows.
- **Links, Ctrl+Shift+L:** the same submenu lists Markdown links, autolinks, and bare web addresses in user and assistant messages. Repeated destinations appear once, and links found inside code say so. Read or copy an address without opening it. Open Link is a separate action, available only for valid HTTP/HTTPS destinations; local files and other schemes say copy only.
- **One response or the whole conversation:** the Agent response menu offers Code Blocks in This Response and Links in This Response. The Conversation shortcuts browse the whole loaded conversation in either mode. Ordinary Find in Responses, Ctrl+F, now lives in Find and Browse too.
- **Replies that stop early:** browseable source survives stopped turns, backend failures, and history replays. Streamed chunks that already end with a newline no longer gain an extra blank line between them.

Verified with native Windows controls and live NVDA keyboard interaction. JAWS and VoiceOver interaction were not tested for these additions.
