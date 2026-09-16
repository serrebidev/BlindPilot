# BlindPilot 0.29.2

Claude Code's refused tool calls are now said out loud as refusals.

- A `permissions.deny` rule, a tool disabled for the session, and a `PreToolUse` hook all refuse a call in `bypassPermissions` mode exactly as they do in every other mode. Each came back as an ordinary result row — "Result: Permission to use Bash with command ... has been denied" — which sounds like the output of a command that in fact never ran. A refusal now names the tool and the reason it was given, and a call that simply failed is distinguished from one that was refused.
- In bypass, the first refusal of a turn is followed by one sentence explaining what bypass does not cover, so a refusal in the mode you chose so that nothing would be refused is not left looking like a fault.
- The full text of the refusal still gets a row of its own to read in the list.
- Checked against the installed Claude Code: in bypass the CLI asks no permission at all, and Read, Write, Edit, Bash, Glob and Grep all run. The refusals that reach you come from your own deny rules, disabled tools and hooks.
