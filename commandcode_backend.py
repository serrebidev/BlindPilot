"""Command Code backend for BlindPilot.

Command Code (https://commandcode.ai) is a terminal coding agent installed
with ``npm install -g command-code``. Its package ships four bin names --
``cmd``, ``cmdc``, ``command-code`` and ``commandcode`` -- and this adapter
drives the ``command-code`` one on purpose: a Windows process that looks up
``cmd`` finds ``C:\\Windows\\System32\\cmd.exe`` first, which is the command
interpreter, not the agent.

It does not speak Claude Code's stream-json protocol. Its non-interactive
surface is its own: ``command-code -p --output-format json`` writes one event
object per line and a single final ``result`` line, and a conversation is
carried between runs with ``--resume <session id>`` (measured at 1.53.1, the
shapes this file reads). Discovery, authentication, the model catalog and the
stored session locations live here so ``agent_backends`` stays
provider-neutral; the turn itself is in ``commandcode_worker``.

CLI probes go through ``agent_backends._probe_backend`` and binary discovery
through ``agent_backends.find_backend_cli`` -- resolved at call time, not
imported into this module's namespace -- so the suite's "every backend"
tests, which patch those two in ``agent_backends``, fence this adapter off
from the machine it runs on exactly like the others.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
Based on the original Claude Code Reader application by doubletaponair:
https://github.com/doubletaponair/claude-code-reader
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import agent_backends
from agent_backends import BACKEND_COMMANDCODE

# The reasoning levels the catalog advertises. Which of them a given model
# accepts depends on the model (/effort says so), so this is the vocabulary
# rather than a per-model list; Command Code ignores a level it cannot use.
COMMANDCODE_EFFORTS = ("low", "medium", "high", "xhigh", "max")

# How long a short CLI probe may take. Command Code is a Node program, so
# every check pays a start-up; it is quick once running, and the wizard's
# sign-in step calls this while a dialog is on screen.
CLI_PROBE_TIMEOUT = 30


def commandcode_home() -> Path:
    """Where Command Code keeps its configuration and sessions."""
    return Path.home() / ".commandcode"


def commandcode_config() -> dict:
    """The CLI's own config.json, read without starting it.

    It holds the model, provider and reasoning effort last used, which is how
    the picker knows what is selected now. The shape is measured at 1.53.1:
    ``{"model": "...", "provider": "...", "reasoningEffort": {"<model>": "..."}}``.
    """
    try:
        payload = json.loads((commandcode_home() / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def commandcode_credentials() -> Optional[dict]:
    """The account Command Code stored at sign-in, or None when there is none."""
    try:
        payload = json.loads((commandcode_home() / "auth.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) and payload else None


def _probe(binary: str, args: list[str], timeout: int) -> tuple[Optional[int], str]:
    """One short CLI command, through the shared probe the suite can fence."""
    return agent_backends._probe_backend(binary, args, timeout)


def _says_signed_out(text: str) -> bool:
    lowered = text.casefold()
    return any(
        phrase in lowered
        for phrase in ("not authenticated", "not logged in", "no account", "signed out")
    )


def _after(text: str, marker: str) -> str:
    """The value on the line carrying *marker*, for a report that is prose."""
    for line in text.splitlines():
        if marker in line:
            return line.split(marker, 1)[1].strip(" :\t")
    return ""


def commandcode_auth_ok(timeout: int = 12) -> bool:
    """Best-effort, non-interactive sign-in check.

    Command Code answers this with ``command-code status``, whose exit code is
    the signal (its documented exit 3 is "not authenticated") and whose output
    names the account otherwise. A release that stops answering in a shape
    this reads falls back to the credential file, so a check never reports a
    signed-in user as signed out.
    """
    binary = agent_backends.find_backend_cli(BACKEND_COMMANDCODE)
    if binary:
        code, text = _probe(binary, ["status"], timeout)
        if code == 0 and not _says_signed_out(text):
            return True
    return commandcode_credentials() is not None


def commandcode_account_lines() -> list[str]:
    """The sign-in report, as the lines ``/status`` prints them."""
    binary = agent_backends.find_backend_cli(BACKEND_COMMANDCODE)
    if binary:
        code, text = _probe(binary, ["status"], 20)
        if code == 0 and not _says_signed_out(text):
            lines = ["Signed in: yes"]
            account = _after(text, "Authenticated as")
            provider = _after(text, "Provider")
            if account:
                lines.append(f"Account: {account}")
            if provider:
                lines.append(f"Provider: {provider}")
            return lines
    credentials = commandcode_credentials()
    if credentials:
        name = str(credentials.get("userName") or "").strip()
        return ["Signed in: yes"] + ([f"Account: {name}"] if name else [])
    return ["Signed in: no"]


# --------------------------------------------------------------------------
# Model catalog
# --------------------------------------------------------------------------


def _looks_like_section(line: str) -> bool:
    """Whether a line is one of the catalog's provider headings.

    The headings are plain words ("Open Source", "Anthropic", "xAI"); every
    other non-model line in the output carries punctuation the headings do
    not ("Available models · 70 models", the "Pass the full id..." note, the
    "Docs:" line), so those are told apart by shape rather than by a list that
    would go stale.
    """
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z &-]{1,30}", line.strip()))


def _models_from_catalog(text: str) -> list[str]:
    """Read the model ids out of ``command-code --list-models``.

    Measured layout: provider headings, a blank line, then ``<id>  <description>``
    rows. A row is only taken once a heading has been seen, so the banner
    ("Available models · 70 models") and the trailing "Docs:" link - the only
    other lines with the two-space gap - cannot be mistaken for a model. The
    heading is not undone by the blank line that follows it, which is why a
    heading is only ever replaced by another heading.
    """
    models: list[str] = []
    in_section = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        match = re.match(r"^(\S+)\s{2,}\S", line)
        if match:
            name = match.group(1)
            if in_section and not name.endswith(":") and name not in models:
                models.append(name)
            continue
        if _looks_like_section(line):
            in_section = True
    return models


def commandcode_model_options(
    cwd: Optional[str] = None,
) -> tuple[list[str], list[str], str, str, str]:
    """The picker's answer: (models, efforts, current model, current effort, error)."""
    binary = agent_backends.find_backend_cli(BACKEND_COMMANDCODE)
    if not binary:
        return [], [], "", "", "Command Code was not found."
    code, text = _probe(binary, ["--list-models"], CLI_PROBE_TIMEOUT)
    models = _models_from_catalog(text) if code == 0 else []
    config = commandcode_config()
    current = str(config.get("model") or "").strip()
    efforts = config.get("reasoningEffort")
    current_effort = str(efforts.get(current) or "").strip() if isinstance(efforts, dict) else ""
    if not models:
        return [], [], current, current_effort, "Could not read the model list from Command Code."
    return models, list(COMMANDCODE_EFFORTS), current, current_effort, ""
