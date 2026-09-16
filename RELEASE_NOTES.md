# BlindPilot 0.29.1

Command Code's bypass permission mode now bypasses what it can, and says plainly what it cannot.

- Command Code hides nine tools from any headless run, `todo_write` among them. A call to one of those was refused with "No tool named ... exists", which is not a permission question and which no mode — bypass included — could grant. `todo_write` and Command Code's `taste` note are now asked back explicitly. The tools that would answer a question or approve a plan with nobody watching are still withheld, because a headless run answers its own prompts by taking the first option.
- Every refusal now says which tool was refused, what it was about to do, and the reason given. Before this, a refusal was read out as the single word "tool_denied".
- In bypass, a refusal is followed by one sentence explaining what bypass does not cover: Command Code checks a `permissions.deny` rule, a `permissions.ask` rule and a destructive shell command before it checks the mode, so all three refuse in bypass exactly as they do in default.
- A bypass turn now says what your settings will still refuse before it starts, including `permissions.disableBypass`, which switches the whole flag off with one line on standard error that a windowed run never showed anybody.
- A turn Command Code stopped because a tool needed approval says so, instead of finishing with "Finished with nothing to say."
