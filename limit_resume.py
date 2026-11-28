"""Wait-and-resume when a plan usage limit stops a turn, and banked Codex resets.

Two backends meter a subscription of their own, and both stop a turn when the
account reaches a limit rather than letting it run into the wall:

Claude Code reports ``rate_limit_event`` with ``status: rejected`` and the
window's ``resetsAt``. Since Claude Code 2.1.234 an interactive session can
wait for the reset itself; BlindPilot drives Claude Code headless
(``claude -p`` with stream-json), so that wait is BlindPilot's to do. There
is no manual reset for Claude: no command, endpoint or web page offers one,
only waiting, usage credits on API rates, or switching account.

Codex reports the windows through ``account/rateLimits/read`` and, alongside
them, the banked rate-limit reset credits the account has been granted --
one-time resets OpenAI hands out for referral promotions and similar, kept
until they expire. A banked reset is spent with
``account/rateLimitResetCredit/consume``. ChatGPT's own app only lets an
assistant spend one when a main allowance sits at ten percent or less
remaining, and refuses outright while every covered allowance has eighty
percent or more. BlindPilot follows the stricter of those two gates for
automatic spending, and asks first for manual spending.

This module holds only the decisions. How the windows and credits are read
and spent lives in ``agent_backends``; how a held turn is resumed lives in
the window. Nothing here touches a process, a thread or the GUI.
"""

from __future__ import annotations

import time
from typing import Optional

# ChatGPT's own rule for letting an assistant spend a banked reset: a main
# allowance must be at ten percent remaining or less. OpenAI refuses the
# spend outright while every covered allowance has eighty percent or
# more, so a turn that merely grazed a limit never spends one.
AUTO_SPEND_REMAINING_MAX = 10.0

# How long after the window resets the held turn goes again. A window can
# reset a few seconds late on the server side; resuming into a still-full
# window would fail the same way and spend the resume.
RESUME_GRACE_SECONDS = 5.0


def is_usage_limit_failure(message: str) -> bool:
    """Whether a failed turn's message names a plan usage limit.

    Codex and Claude word these differently per version and per plan, so
    this matches the ideas rather than any one sentence. A generic backend
    error that merely contains the word "limit" is not a usage limit.
    """
    text = message.lower()
    if "usage limit" in text:
        return True
    if "rate limit" in text and ("hit" in text or "reached" in text):
        return True
    if "you've hit your limit" in text or "you have hit your limit" in text:
        return True
    return False


def wait_seconds(resets_at: Optional[float], now: Optional[float] = None) -> Optional[float]:
    """Seconds from ``now`` until ``resets_at``, plus the grace stretch.

    None when there is no reset time to wait for: a limit the backend named
    without saying when it ends is a limit BlindPilot cannot resume from,
    and waiting blindly would hold the tab with no end.
    """
    if resets_at is None:
        return None
    current = time.time() if now is None else now
    remaining = resets_at - current + RESUME_GRACE_SECONDS
    return remaining if remaining > 0 else RESUME_GRACE_SECONDS


def earliest_reset_at(windows: list, now: Optional[float] = None) -> Optional[float]:
    """The soonest future reset among the windows, or None if none resets.

    The shortest wait wins: resuming when the five-hour window opens is
    the point; a weekly window that ends later is the same tab's next
    problem, not this one's.
    """
    current = time.time() if now is None else now
    resets = [
        window.resets_at
        for window in windows
        if getattr(window, "resets_at", None) is not None and window.resets_at > current
    ]
    return min(resets) if resets else None


def remaining_percent(window) -> Optional[float]:
    """How much of this window is left, or None where it is not a percentage."""
    percent = getattr(window, "percent", None)
    if percent is None:
        return None
    return max(0.0, min(100.0, 100.0 - float(percent)))


def auto_spend_allowed(windows: list, credit_count: int) -> bool:
    """Whether an assistant may spend a banked Codex reset without asking.

    Needs at least one credit and at least one covered allowance at ten
    percent remaining or less -- ChatGPT's own gate, and stricter than
    OpenAI's eighty percent refusal. Windows that are not percentages
    (credit balances, spent credentials) cannot pass this gate: spending
    an irreversible reset on a figure the backend did not state as a
    fraction would be guessing.
    """
    if credit_count < 1:
        return False
    return any(
        (remaining := remaining_percent(window)) is not None
        and remaining <= AUTO_SPEND_REMAINING_MAX
        for window in windows
    )


def consume_outcome_line(outcome: object) -> str:
    """What a banked-reset consume answered, said plainly.

    The five outcomes are the whole contract with the app-server; an
    unknown word is reported as itself rather than mapped to a guess.
    """
    known = {
        "reset": "Banked usage reset spent. The limit window is open again.",
        "alreadyRedeemed": "That banked reset was already spent. Refreshing the usage windows.",
        "nothingToReset": "No banked reset was spent: no window needed one.",
        "noCredit": "No banked reset was spent: the account has none available.",
    }
    text = str(outcome or "").strip()
    return known.get(text, f"Banked reset outcome: {text or 'unknown'}")


def consume_succeeded(outcome: object) -> bool:
    """Whether the windows are usable again after this answer."""
    return str(outcome or "").strip() in ("reset", "alreadyRedeemed")
