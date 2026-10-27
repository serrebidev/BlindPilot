"""Remote Control, session links and your other computers' Claude Code sessions.

Remote Control is Claude Code's own: a ``remote_control`` control request on
the stream-json input turns it on for the running session, named and kept on
claude.ai between turns, and ``reattach_session_id`` joins the same Remote
Control session again from a new process. Its answer carries
``bridge_session_id`` and ``session_url``.

Other machines: a Claude Code session with Remote Control on can list your
sessions on other computers (its ``ListAgents`` tool) and message one
(``SendMessage``). There is no API for that list, so BlindPilot asks Claude,
reads the latest ``ListAgents`` result loosely (a line that doesn't look like a
session is skipped), and sends with a turn that asks Claude to pass the words
on exactly.

Session links, ``blindpilot://open/<backend>/<session id>``, reopen a past
conversation; nothing else is ever done for a link.

Modelled on The Chat Place's remote.py and links (TheIdeaPlace/AIChat, MIT).
No wx here: everything is testable on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import quote, unquote

LIST_TOOL = "ListAgents"
REMOTE_KIND = "Remote Control"

# A ListAgents row: "  Name [ref]  ·  Remote Control  ·  offline". The name may
# itself hold brackets or dots; the ref is the last bracketed hex.
_ROW = re.compile(r"^\s*(?P<name>.+?)\s+\[(?P<ref>[0-9A-Fa-f]{4,})\]\s*·\s*(?P<rest>.*)$")

LIST_PROMPT = (
    "Call ListAgents and tell me which of my Claude Code sessions on other computers "
    "(its Remote Control rows) there are: each one's name and whether it's idle, busy "
    "or offline. Don't send any of them a message."
)

_STATES = {"requires_action": "needs you", "running": "working", "busy": "working"}


@dataclass(frozen=True)
class RemoteSession:
    name: str
    ref: str
    state: str = ""

    def describe(self) -> str:
        state = _STATES.get(self.state.strip().lower(), self.state.strip().replace("_", " "))
        return f"{self.name}, {state}" if state else self.name


def parse_list(text: str) -> list[RemoteSession]:
    """The Remote Control sessions in a ListAgents result, in its order."""
    sessions: list[RemoteSession] = []
    for line in (text or "").splitlines():
        if line.lstrip().lower().startswith("this session is"):
            continue
        match = _ROW.match(line)
        if not match:
            continue
        parts = [part.strip() for part in match.group("rest").split("·")]
        if not parts or parts[0] != REMOTE_KIND:
            continue
        name = " ".join(match.group("name").split())
        if name:
            state = parts[1] if len(parts) > 1 else ""
            sessions.append(RemoteSession(name, match.group("ref").lower(), state))
    return sessions


def latest_list(results: Iterable[str]) -> Optional[list[RemoteSession]]:
    """The newest ListAgents result among ``results``, parsed; None if there is none."""
    found = None
    for text in results:
        found = text
    return None if found is None else parse_list(found)


def addresses(sessions: list[RemoteSession]) -> list[str]:
    """What SendMessage takes: the name, with its ref only where a name is shared."""
    counts: dict[str, int] = {}
    for session in sessions:
        counts[session.name] = counts.get(session.name, 0) + 1
    return [s.name if counts[s.name] == 1 else f"{s.name} [{s.ref}]" for s in sessions]


FENCE_START, FENCE_END = "<<<MESSAGE", "MESSAGE>>>"


def send_prompt(address: str, message: str) -> str:
    """The turn that has Claude send ``message`` to ``address``, word for word."""
    address = " ".join((address or "").replace('"', "'").split())
    return (
        f'Use SendMessage with to set to "{address}" to send the message between '
        f"{FENCE_START} and {FENCE_END} below, exactly as written, adding nothing. "
        "Then tell me whether it was sent. Its reply, if any, will arrive here later.\n"
        f"{FENCE_START}\n{message.strip()}\n{FENCE_END}"
    )


def remote_control_fields(enabled: bool, name: str = "", reattach: str = "") -> dict:
    """The ``remote_control`` control request's fields."""
    if not enabled:
        return {"enabled": False}
    fields: dict = {"enabled": True, "name": name or "BlindPilot", "keep_session_on_exit": True}
    if reattach:
        fields["reattach_session_id"] = reattach
    return fields


# ----- Session links -----

LINK_SCHEME = "blindpilot"
_LINK = re.compile(r"^blindpilot://open/([a-z]+)/([^/?#\s]+)/?$")


def session_link(backend: str, session_id: str) -> str:
    return f"{LINK_SCHEME}://open/{backend}/{quote(session_id, safe='')}"


def parse_session_link(text: str) -> Optional[tuple[str, str]]:
    """(backend, session id) from a session link, or None for anything else."""
    match = _LINK.match((text or "").strip())
    if not match:
        return None
    session_id = unquote(match.group(2))
    if not session_id or any(ch.isspace() or ord(ch) < 32 for ch in session_id):
        return None
    return match.group(1), session_id
