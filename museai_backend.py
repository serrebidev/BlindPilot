"""muse.ai backend for BlindPilot.

muse.ai is Meta's personal agent: it runs on its own cloud machine, with its
own connectors, memory and browser, and is not the same product as Muse Code
(the ``muse`` coding CLI this app also drives). BlindPilot talks to it through
``muse-cli`` (PyPI ``muse-cli``, installed with ``uv tool install muse-cli``),
which reaches the agent's gateway with the muse.ai sign-in cookies it keeps
in ``~/.config/muse-cli``.

Each BlindPilot conversation is one muse.ai side chat, so turns from here
never land in the user's main muse.ai chat. A turn is ``muse-cli send
--thread <id> --wait <seconds> <text>``, which prints one JSON object with
the agent's reply (measured at 0.3.2). The turn itself is in
``museai_worker``.

The agent runs somewhere else, so it cannot read files on this machine: the
working folder means nothing to it, and attachments are not passed.

CLI probes go through ``agent_backends._probe_backend`` and binary discovery
through ``agent_backends.find_backend_cli``, resolved at call time, so the
suite's "every backend" tests fence this adapter off the way they fence the
others.

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Optional

import agent_backends
from agent_backends import BACKEND_MUSEAI


def museai_config_path() -> Path:
    return Path.home() / ".config" / "muse-cli" / "config.json"


def _json_from(text: str) -> object:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except ValueError:
        return None


def museai_status(timeout: int = 20) -> Optional[dict]:
    """``muse-cli status`` as a dict, or None when it is missing or signed out."""
    binary = agent_backends.find_backend_cli(BACKEND_MUSEAI)
    if not binary:
        return None
    code, text = agent_backends._probe_backend(binary, ["status"], timeout)
    payload = _json_from(text) if code == 0 else None
    return payload if isinstance(payload, dict) else None


def museai_auth_ok(timeout: int = 20) -> bool:
    """Signed in means the gateway answered ``status`` with the agent's VM."""
    status = museai_status(timeout)
    return bool(status and status.get("vm_id"))


def museai_account_lines() -> list[str]:
    status = museai_status()
    if not status:
        return ["Signed in: no"]
    lines = ["Signed in: yes"]
    identity = status.get("identity")
    name = identity.get("name") if isinstance(identity, dict) else ""
    if name:
        lines.append(f"Agent: {name}")
    if status.get("sessions") is not None:
        lines.append(f"Chats: {status['sessions']}")
    return lines


def _cli_json(args: list[str], timeout: int = 60) -> object:
    binary = agent_backends.find_backend_cli(BACKEND_MUSEAI)
    if not binary:
        return None
    code, text = agent_backends._probe_backend(binary, args, timeout)
    if code != 0:
        return None
    start = min((i for i in (text.find("["), text.find("{")) if i != -1), default=-1)
    if start == -1:
        return None
    try:
        return json.loads(text[start:])
    except ValueError:
        return None


def museai_side_chats() -> list[dict]:
    """The agent's side chats (not its main chat), newest first, as muse-cli lists them."""
    found = _cli_json(["threads"])
    chats = [c for c in found if isinstance(c, dict)] if isinstance(found, list) else []
    return [c for c in chats if c.get("thread") and c.get("session_id")]


def museai_chat_turns(session_id: str, limit: int = 300) -> list[tuple[str, str]]:
    """One chat as (what was asked, everything the agent said back) pairs.

    Everything means every message the agent posted after the question --
    status updates included -- joined in order, as the turn showed them.
    """
    found = _cli_json(["history", "--thread", session_id, "--limit", str(limit)])
    turns: list[tuple[str, list[str]]] = []
    for message in found if isinstance(found, list) else []:
        if not isinstance(message, dict):
            continue
        text = str(message.get("text") or "").strip()
        if not text:
            continue
        if message.get("role") == "user":
            turns.append((text, []))
        elif message.get("role") == "assistant":
            if not turns:
                turns.append(("", []))
            turns[-1][1].append(text)
    return [(prompt, "\n\n".join(said)) for prompt, said in turns]


def museai_latest_seq(session_id: str) -> int:
    """The newest agent event in a chat, text or not; 0 if it cannot be read.

    What an open muse.ai tab compares against to notice muse.ai working again
    (or still) after a turn has ended.
    """
    found = _cli_json(["history", "--thread", session_id, "--limit", "5", "--raw"], 30)
    events = found.get("chat_events") if isinstance(found, dict) else None
    seqs = [
        int((e.get("payload") or {}).get("seq") or e.get("seq") or 0)
        for e in (events if isinstance(events, list) else [])
        if isinstance(e, dict) and e.get("event_name") == "message.assistant"
    ]
    return max(seqs, default=0)


def museai_install_argv(upgrade: bool = False) -> Optional[list[str]]:
    """How to install (or upgrade) muse-cli: uv first, then pip for this user."""
    uv = shutil.which("uv")
    if uv:
        return [uv, "tool", "upgrade" if upgrade else "install", "muse-cli"]
    # A frozen build's sys.executable is BlindPilot itself, not a Python.
    python = None if getattr(sys, "frozen", False) else sys.executable
    python = python or shutil.which("python3") or shutil.which("python") or shutil.which("py")
    if not python:
        return None
    return [python, "-m", "pip", "install", "--user", "--upgrade", "muse-cli"]


MUSEAI_MISSING_PREREQ = (
    "muse.ai needs muse-cli, a Python package. Install uv "
    "(https://docs.astral.sh/uv/) or Python, then choose Install again, or run: "
    "uv tool install muse-cli"
)
