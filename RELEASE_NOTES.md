# BlindPilot 0.57.4

This is the fourth release in the feature comparison with The Chat Place and FastChat: queued-message management and honest backend status announcements.

Conversation, Find and Browse gains Queued Messages. It lists the messages waiting for the current session, with Remove selected, Clear all, and Pause/Resume controls. Queue state was previously reachable only through slash commands; now it has the same dialog reach as the other session tools, with announcements for every change.

Fallback-model switches are now announced when a backend switches models mid-route, matching how permission-mode changes were already announced. Retries, fallback changes, and tool activity all read as short status lines.

Two things are deliberately not offered. Stopping one background task does not map to any installed backend's worker protocol, and installed Claude exposes no file-checkpoint flag, so checkpoint restore would not be verifiable. Stopping a whole turn with Stop Task remains the supported path, and conversation branching keeps workspace files untouched.

Connected-tool management, sound and voice customization, and automatic computer use remain later stages. They are not included in 0.57.4.
Windows ships an installer and portable archive; macOS ships an Apple Silicon archive. Linux remains source-only.
