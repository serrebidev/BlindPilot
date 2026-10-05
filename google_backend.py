"""Gemini CLI and Antigravity CLI backends for BlindPilot.

Google ships two terminal agents, and BlindPilot drives both headlessly:

* Gemini CLI (``npm install -g @google/gemini-cli``, run as ``gemini``). Its
  headless mode starts whenever stdin is not a terminal: the prompt goes in on
  stdin, ``--output-format stream-json`` writes one event per line (``init``,
  ``message``, ``tool_use``, ``tool_result``, ``error``, ``result``), and a
  conversation is carried to the next turn with ``--resume <session id>``.
  Measured at 0.62.0. Since 18 June 2026 Google no longer serves Gemini CLI to
  personal Google accounts; it keeps working with a Gemini API key, Vertex AI,
  and Gemini Code Assist Standard/Enterprise sign-ins.

* Antigravity CLI (``agy``), Google's successor terminal agent, a single Go
  binary installed by Google's own script. Its print mode reads NDJSON prompts
  on stdin (``--input-format stream-json``), writes ``init``,
  ``step_update`` and ``result`` events (``--output-format stream-json``), and
  resumes with ``--conversation <id>``. Measured at 1.2.17. It signs in with a
  Google account by default, or runs on a Gemini API key once its settings
  say ``"modelProvider": "gemini"``.

Both accept the same key, a Google AI Studio (Gemini API) key, so BlindPilot
keeps one for the two of them in the system credential store and, failing
that, borrows the key of a Gemini account set up in Chat mode. A key already
in the environment always wins. The turns themselves are in ``google_worker``.

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
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import agent_backends
from agent_backends import BACKEND_ANTIGRAVITY, BACKEND_GEMINI

# The credential-store name of the key the two agent backends share.
GEMINI_KEY_SECRET = "gemini_agent_api_key"

# Where Google says to get a key.
GEMINI_KEY_PAGE = "https://aistudio.google.com/apikey"

# Gemini CLI's own names for its models. "auto" lets the CLI route each request
# between its pro and flash tiers, and is its default.
GEMINI_CLI_ALIASES = ("auto", "pro", "flash", "flash-lite")

# Antigravity's --effort values, read from `agy --help` at 1.2.17.
ANTIGRAVITY_EFFORTS = ("low", "medium", "high", "xhigh", "max")

# A short CLI probe. agy is a native binary and quick; gemini is Node and pays
# a start-up, and the wizard calls these with a dialog on screen.
CLI_PROBE_TIMEOUT = 30

_GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def is_chat_model(model_id: str) -> bool:
    """Whether a Gemini API model id can take a conversation turn."""
    from accessible_ai.providers.chat_completions import is_gemini_chat_model

    return is_gemini_chat_model(model_id)


# --------------------------------------------------------------------------
# The key
# --------------------------------------------------------------------------


def _credentials():
    from accessible_ai.storage.credentials import CredentialStore

    return CredentialStore()


def saved_gemini_key() -> str:
    """The key BlindPilot stored for the agent backends, or ""."""
    try:
        return _credentials().get_secret(GEMINI_KEY_SECRET).strip()
    except Exception:  # noqa: BLE001 - no credential backend means no key
        return ""


def save_gemini_key(key: str) -> None:
    """Store the key both Google agent backends use. Raises when it cannot."""
    _credentials().set_secret(GEMINI_KEY_SECRET, key.strip())


def forget_gemini_key() -> None:
    try:
        _credentials().delete_secret(GEMINI_KEY_SECRET)
    except Exception:  # noqa: BLE001 - nothing stored is nothing to forget
        pass


def chat_account_gemini_key() -> str:
    """The key of a Gemini account set up in Chat mode, or "".

    Somebody who already chats with Gemini in BlindPilot has given it a key
    once; asking for the same key again to use the agent would be busywork.
    """
    try:
        from accessible_ai.models import PROVIDER_GEMINI
        from accessible_ai.storage.database import Database
        from accessible_ai.storage.paths import database_path

        path = database_path()
        if not path.is_file():
            return ""
        accounts = Database(path).list_accounts()
        credentials = _credentials()
        for account in accounts:
            if account.provider != PROVIDER_GEMINI or account.id is None:
                continue
            key = credentials.get_api_key(account.id).strip()
            if key:
                return key
    except Exception:  # noqa: BLE001 - a broken chat database is not an agent error
        return ""
    return ""


def gemini_api_key() -> str:
    """The key a turn runs on: the environment's, then ours, then Chat mode's."""
    return (
        os.environ.get("GEMINI_API_KEY", "").strip()
        or saved_gemini_key()
        or chat_account_gemini_key()
    )


def key_source() -> str:
    """Where the key a turn would use comes from, for the status report."""
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return "the GEMINI_API_KEY environment variable"
    if saved_gemini_key():
        return "the key saved in BlindPilot"
    if chat_account_gemini_key():
        return "your Gemini account in Chat mode"
    return ""


def turn_env(binary: str, use_key: bool) -> dict[str, str]:
    """The environment for one turn: the usual one, plus the key when needed."""
    env = agent_backends.subprocess_env(binary)
    if use_key and not env.get("GEMINI_API_KEY", "").strip():
        key = gemini_api_key()
        if key:
            env["GEMINI_API_KEY"] = key
    return env


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

    Gemini CLI prefers this over any environment variable, so when it is set
    the CLI signs itself in and BlindPilot's key stays out of the way.
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


def antigravity_uses_api_key() -> bool:
    """Whether agy runs on a Gemini API key rather than a Google sign-in."""
    provider = _read_json(antigravity_settings_path()).get("modelProvider")
    return isinstance(provider, str) and provider.strip().casefold() == "gemini"


def use_api_key_for_antigravity() -> None:
    """Switch agy to the Gemini API key, keeping every other setting.

    agy has no flag or environment variable for this; its settings file is
    the only switch (Antigravity CLI docs, "Gemini API Key Authentication").
    Raises OSError when the file cannot be written.
    """
    path = antigravity_settings_path()
    settings = _read_json(path)
    settings["modelProvider"] = "gemini"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(settings, indent=4) + "\n", encoding="utf-8")
    os.replace(temp, path)


# --------------------------------------------------------------------------
# Sign-in
# --------------------------------------------------------------------------


def _probe(binary: str, args: list[str], timeout: int) -> tuple[Optional[int], str]:
    return agent_backends._probe_backend(binary, args, timeout)


def gemini_auth_ok(timeout: int = 12) -> bool:
    """Whether a Gemini CLI turn can sign in without asking anybody."""
    return bool(gemini_configured_auth() or gemini_api_key())


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
    """Whether an Antigravity turn can sign in without asking anybody."""
    if antigravity_uses_api_key():
        return bool(gemini_api_key())
    models, _complaint = antigravity_models(max(timeout, CLI_PROBE_TIMEOUT))
    return bool(models)


def gemini_account_lines() -> list[str]:
    configured = gemini_configured_auth()
    if configured:
        return ["Signed in: yes", f"Sign-in method: {configured} (from Gemini CLI's settings)"]
    source = key_source()
    if source:
        return ["Signed in: yes", f"Sign-in method: Gemini API key, from {source}"]
    return ["Signed in: no"]


def antigravity_account_lines() -> list[str]:
    if antigravity_uses_api_key():
        source = key_source()
        if source:
            return ["Signed in: yes", f"Sign-in method: Gemini API key, from {source}"]
        return ["Signed in: no", "Sign-in method: Gemini API key, but no key was found"]
    models, complaint = antigravity_models()
    if models:
        return ["Signed in: yes", "Sign-in method: Google account"]
    return ["Signed in: no"] + ([complaint] if complaint else [])


# --------------------------------------------------------------------------
# Model catalogs
# --------------------------------------------------------------------------


def gemini_api_models(key: str, timeout: float = 15.0) -> list[str]:
    """The chat-capable models the Gemini API offers this key, newest first."""
    if not key:
        return []
    request = urllib.request.Request(
        f"{_GEMINI_MODELS_URL}?pageSize=1000",
        headers={"x-goog-api-key": key},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https URL
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return []
    models: list[str] = []
    for entry in payload.get("models", []) if isinstance(payload, dict) else []:
        if not isinstance(entry, dict):
            continue
        methods = entry.get("supportedGenerationMethods") or []
        name = str(entry.get("name") or "").removeprefix("models/")
        if "generateContent" in methods and is_chat_model(name) and name not in models:
            models.append(name)
    return sorted(models, key=_newest_first)


def _newest_first(model_id: str) -> tuple:
    """Sort key: higher version numbers first, then by name."""
    numbers = tuple(-int(part) for part in re.findall(r"\d+", model_id)[:3])
    return (numbers, model_id)


def gemini_model_options() -> tuple[list[str], list[str], str, str, str]:
    """The picker's answer: (models, efforts, current model, current effort, error)."""
    if agent_backends.find_backend_cli(BACKEND_GEMINI) is None:
        return [], [], "", "", "Gemini CLI was not found."
    models = list(GEMINI_CLI_ALIASES)
    models += [m for m in gemini_api_models(gemini_api_key()) if m not in models]
    return models, [], gemini_configured_model() or "auto", "", ""


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
    "message" -- "API key not valid. Please pass a valid API key." -- so that
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
