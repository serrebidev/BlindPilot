"""Gemini CLI and Antigravity CLI backends for BlindPilot.

Google ships two terminal agents, and BlindPilot drives both headlessly:

* Gemini CLI (``npm install -g @google/gemini-cli``, run as ``gemini``). Its
  headless mode starts whenever stdin is not a terminal: the prompt goes in on
  stdin, ``--output-format stream-json`` writes one event per line (``init``,
  ``message``, ``tool_use``, ``tool_result``, ``error``, ``result``), and a
  conversation is carried to the next turn with ``--resume <session id>``.
  Measured at 0.62.0.

* Antigravity CLI (``agy``), Google's successor terminal agent, a single Go
  binary installed by Google's own script. Its print mode reads NDJSON prompts
  on stdin (``--input-format stream-json``), writes ``init``,
  ``step_update`` and ``result`` events (``--output-format stream-json``), and
  resumes with ``--conversation <id>``. Measured at 1.2.17.

Agent mode signs in to both with a Google account, through each CLI's own
sign-in: the window opens the CLI in a terminal, the CLI opens the browser,
and the credentials it caches are what every later headless turn runs on.
BlindPilot hands neither of them an API key; keys are Chat mode's. Whatever a
person configured in a CLI themselves is left for that CLI to use. The turns
themselves are in ``google_worker``.

CLI probes go through ``agent_backends._probe_backend`` and discovery through
``agent_backends.find_backend_cli``, resolved at call time, so the suite's
"every backend" tests fence this adapter off from the machine like the others.

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
from agent_backends import BACKEND_ANTIGRAVITY, BACKEND_GEMINI

# Gemini CLI's own names for its models. "auto" lets the CLI route each request
# between its pro and flash tiers, and is its default. They follow Google's
# current models without BlindPilot naming any; a concrete model id typed into
# the picker is passed through as well.
GEMINI_CLI_ALIASES = ("auto", "pro", "flash", "flash-lite")

# Antigravity's --effort values, read from `agy --help` at 1.2.17.
ANTIGRAVITY_EFFORTS = ("low", "medium", "high", "xhigh", "max")

# Gemini CLI's name for "Sign in with Google".
GEMINI_GOOGLE_SIGN_IN = "oauth-personal"

# A short CLI probe. agy is a native binary and quick; gemini is Node and pays
# a start-up, and the wizard calls these with a dialog on screen.
CLI_PROBE_TIMEOUT = 30

# --------------------------------------------------------------------------
# Settings files
# --------------------------------------------------------------------------


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def gemini_home() -> Path:
    return Path.home() / ".gemini"


def gemini_settings_path() -> Path:
    return gemini_home() / "settings.json"


def antigravity_settings_path() -> Path:
    return gemini_home() / "antigravity-cli" / "settings.json"


def gemini_configured_auth() -> str:
    """The sign-in method Gemini CLI's own settings choose, or "".

    Gemini CLI writes this when its sign-in finishes; "oauth-personal" is
    "Sign in with Google".
    """
    security = _read_json(gemini_settings_path()).get("security")
    auth = security.get("auth") if isinstance(security, dict) else None
    selected = auth.get("selectedType") if isinstance(auth, dict) else None
    if isinstance(selected, str) and selected.strip():
        return selected.strip()
    # Releases before the settings were nested kept it at the top level.
    legacy = _read_json(gemini_settings_path()).get("selectedAuthType")
    return legacy.strip() if isinstance(legacy, str) else ""


def gemini_configured_model() -> str:
    model = _read_json(gemini_settings_path()).get("model")
    if isinstance(model, dict):
        name = model.get("name")
        return name.strip() if isinstance(name, str) else ""
    return model.strip() if isinstance(model, str) else ""


def gemini_credentials_path() -> Path:
    """Where Gemini CLI caches a Google sign-in (its OAUTH_FILE, 0.62.0)."""
    return gemini_home() / "oauth_creds.json"


def gemini_google_account() -> str:
    """The Google account Gemini CLI signed in as, or ""."""
    active = _read_json(gemini_home() / "google_accounts.json").get("active")
    return active.strip() if isinstance(active, str) else ""


# --------------------------------------------------------------------------
# Sign-in
# --------------------------------------------------------------------------


def _probe(binary: str, args: list[str], timeout: int) -> tuple[Optional[int], str]:
    return agent_backends._probe_backend(binary, args, timeout)


def gemini_auth_ok(timeout: int = 12) -> bool:
    """Whether a Gemini CLI turn can sign in without asking anybody.

    A Google sign-in is settled when the CLI chose it and cached its
    credentials. A method somebody configured in the CLI by hand -- Vertex AI,
    a key of their own -- is the CLI's business, and counts as signed in.
    """
    configured = gemini_configured_auth()
    if configured == GEMINI_GOOGLE_SIGN_IN:
        return gemini_credentials_path().is_file()
    return bool(configured)


def antigravity_models(timeout: int = CLI_PROBE_TIMEOUT) -> tuple[list[tuple[str, str]], str]:
    """``agy models`` as (id, name) pairs, and its complaint when it has one.

    Measured layout at 1.2.17: a "Fetching available models..." banner, then
    one ``<id>\\t<name>`` line per model. Signed out it still exits 0 but says
    "Please sign in to view available models", so the text is read, not the
    exit code.
    """
    binary = agent_backends.find_backend_cli(BACKEND_ANTIGRAVITY)
    if not binary:
        return [], "Antigravity CLI was not found."
    code, text = _probe(binary, ["models"], timeout)
    models: list[tuple[str, str]] = []
    complaint = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("Fetching"):
            continue
        if line.casefold().startswith("error") or "sign in" in line.casefold():
            complaint = line.removeprefix("Error:").strip()
            continue
        model_id, _, name = line.partition("\t")
        model_id = model_id.strip()
        if model_id and " " not in model_id:
            models.append((model_id, name.strip()))
    if not models and not complaint and code not in (0, None):
        complaint = f"agy models exited with code {code}."
    return models, complaint


def antigravity_auth_ok(timeout: int = 20) -> bool:
    """Whether an Antigravity turn can sign in without asking anybody.

    agy lists its models only when it is signed in, so that list is the check.
    """
    models, _complaint = antigravity_models(max(timeout, CLI_PROBE_TIMEOUT))
    return bool(models)


def gemini_account_lines() -> list[str]:
    configured = gemini_configured_auth()
    if configured == GEMINI_GOOGLE_SIGN_IN:
        if not gemini_credentials_path().is_file():
            return ["Signed in: no", "Sign-in method: Google account, not finished"]
        lines = ["Signed in: yes", "Sign-in method: Google account"]
        account = gemini_google_account()
        return lines + ([f"Account: {account}"] if account else [])
    if configured:
        return ["Signed in: yes", f"Sign-in method: {configured} (set in Gemini CLI)"]
    return ["Signed in: no"]


def antigravity_account_lines() -> list[str]:
    models, complaint = antigravity_models()
    if models:
        return ["Signed in: yes", "Sign-in method: Google account"]
    return ["Signed in: no"] + ([complaint] if complaint else [])


# --------------------------------------------------------------------------
# Model catalogs
# --------------------------------------------------------------------------


def gemini_model_options() -> tuple[list[str], list[str], str, str, str]:
    """The picker's answer: (models, efforts, current model, current effort, error)."""
    if agent_backends.find_backend_cli(BACKEND_GEMINI) is None:
        return [], [], "", "", "Gemini CLI was not found."
    models = list(GEMINI_CLI_ALIASES)
    current = gemini_configured_model() or "auto"
    if current not in models:
        models.append(current)
    return models, [], current, "", ""


def antigravity_model_options() -> tuple[list[str], list[str], str, str, str]:
    """The picker's answer: (models, efforts, current model, current effort, error)."""
    if agent_backends.find_backend_cli(BACKEND_ANTIGRAVITY) is None:
        return [], [], "", "", "Antigravity CLI was not found."
    pairs, complaint = antigravity_models()
    if not pairs:
        return [], [], "", "", complaint or "Could not read the model list from Antigravity CLI."
    return [model_id for model_id, _name in pairs], list(ANTIGRAVITY_EFFORTS), "", "", ""


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

_MESSAGE_RE = re.compile(r'message\\*"?\s*[:=]\s*\\*"([^"\\]{3,})')
# agy flattens Go error maps: "... map[locale:en-US message:API key not valid. ...]]"
_GO_MAP_MESSAGE_RE = re.compile(r"\bmessage:([^\[\]]{3,})\]")
_PLAIN_MESSAGE_RE = re.compile(r"Message:\s*(.{3,}?)(?:, Status:|, Details:|$)")


def readable_error(text: object) -> str:
    """The one sentence a person needs from an error both CLIs nest deeply.

    Gemini CLI wraps the API's JSON in JSON in a string; agy flattens a Go
    error map into one line. Either way the useful part is the innermost
    "message" -- "Quota exceeded for this account." -- so that
    is what is said, and anything else is passed through trimmed.
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    found = _MESSAGE_RE.findall(raw)
    if found:
        return found[-1].strip()
    go_map = _GO_MAP_MESSAGE_RE.findall(raw)
    if go_map:
        return go_map[-1].strip()
    plain = _PLAIN_MESSAGE_RE.search(raw)
    if plain:
        return plain.group(1).strip()
    first = raw.splitlines()[0]
    return first[:300]
