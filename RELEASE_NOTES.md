# BlindPilot 0.29.4

A turn that ends by asking you something now opens the question dialog, whichever backend it was.

- Every backend can stop a turn to ask you a question through a question tool of its own, and BlindPilot announces that and opens a dialog. A model does not always use the tool: asked to interview you, or told to ask one question at a time, it writes the question into its answer instead. Skills that grill you do this as a matter of course. A question written into an answer sends no event, so nothing was announced and no dialog opened — the turn simply ended, and nothing told you an answer was wanted.
- The end of each answer is now read, and a turn that ends on a question opens the same dialog it would have opened for a question tool. What you type is sent as your next message. It is under Options, on by default, and can be switched off.
- This matters most on Command Code, which withholds its `ask_user_question` tool from headless runs entirely, so a written question was the only kind it could ever ask.
- The reading is narrow on purpose. The question mark has to be near the end of the answer, question marks inside code blocks are ignored, and a question the answer then answers itself is left alone, so a dialog does not open when nothing was asked of you.
- Claude Code and Codex are also told, in their own instructions, to ask through their question tool rather than writing the question out. On Codex that is added behind your own `developer_instructions` rather than replacing them.
