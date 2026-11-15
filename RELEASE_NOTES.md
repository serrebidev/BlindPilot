# BlindPilot 0.57.3

This is the third release in the feature comparison with The Chat Place and FastChat: provider usage reports, local Ollama chat, and Chat compaction.

Under Chat, Usage Report checks the selected account and reads its usage, limit and balance when the provider publishes them. OpenRouter reports usage and remaining limit from its key endpoint and balance from its credits endpoint. Values a provider does not publish stay unknown in the dialog; nothing is estimated.

Accounts gain Ollama (local) for models served on this machine. It needs no API key; the picker lists the models Ollama itself serves.

A Chat history item's context menu gains Compact Conversation. It summarizes older messages on a worker, keeps the newest ones, and leaves the original conversation untouched so the full history stays reachable as its own conversation.

Agent controls, connected-tool management, sound and voice customization, and automatic computer use remain later stages. They are not included in 0.57.3.
Windows ships an installer and portable archive; macOS ships an Apple Silicon archive. Linux remains source-only.
