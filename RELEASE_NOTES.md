# BlindPilot 0.57.6

This is the fifth release in the feature comparison with The Chat Place and FastChat: browsing the tools and context a backend actually discovers.

Conversation, Find and Browse gains Connected Tools. It lists the MCP servers found in the configs BlindPilot already knows: a project's .mcp.json for Claude Code and ~/.copilot/mcp-config.json for Copilot CLI, each with its transport and source file. Malformed configs are reported as unavailable rather than crashing the dialog. Enabling, disabling and signing in stay with the backend that owns the file (for Claude Code, its /mcp command), and the dialog says so in words; this browser only reads.

Open Sessions lists the sessions open in BlindPilot now, with the session id and a running flag. A running or currently open conversation cannot be hidden from there; hiding lives in Recent Conversations.

What every backend keeps about you was already browsable under Model: instructions, memories, skills, agents and commands per backend, read from the locations each backend documents. This release adds the connected-tools and open-session views beside it.

Speech and sound customization, and automatic computer use, remain later stages. They are not included in 0.57.6.
Windows ships an installer and portable archive; macOS ships an Apple Silicon archive. Linux remains source-only.
