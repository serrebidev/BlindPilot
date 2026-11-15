"""Request Accessibility permission for the BlindPilot process.

Called from the computer-use dialog when the macOS backend reports the
permission missing. Shows the system prompt in the GUI session.
"""

from __future__ import annotations


def request_accessibility_permission() -> bool:
    """Prompt once; return whether the process is now trusted."""
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions

        options = {"AXTrustedCheckOptionPrompt": True}
        return bool(AXIsProcessTrustedWithOptions(options))
    except Exception:
        return False
