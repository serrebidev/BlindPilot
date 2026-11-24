"""Claude Code tabs charged to an Anthropic API key instead of the subscription.

From The Chat Place 0.6.2 (#658). A tab turned to API credits starts Claude
Code with ANTHROPIC_API_KEY set and every redirect removed, so the key goes
only to Anthropic. Each reply's cost is what Claude Code reports, and an
overall limit ($20 to start) stops sending once it is spent.

Claude Code's ``total_cost_usd`` is the session's running total, carried
over on resume, so a reply's cost is the difference from the last total seen
for that session.

Every figure is BlindPilot's own count for this computer; the Claude Console
is the bill.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from accessible_ai.storage.credentials import CredentialStore
from accessible_ai.storage.paths import app_data_dir

# The credential store is keyed by Chat account id; Chat's ids start at 1, so
# this reserved one cannot collide with an account.
KEY_ID = -1
DEFAULT_OVERALL_LIMIT = 20.0
# Environment that would send the key elsewhere, or bill something else.
REDIRECTS = (
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_CUSTOM_HEADERS",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_OAUTH_TOKEN",
)

_lock = threading.Lock()


def _state_path() -> Path:
    return app_data_dir() / "api_credits.json"


def _read() -> dict[str, Any]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def get_key() -> str:
    try:
        return CredentialStore().get_api_key(KEY_ID).strip()
    except Exception:
        return ""


def set_key(key: str) -> None:
    store = CredentialStore()
    if key.strip():
        store.set_api_key(KEY_ID, key.strip())
    else:
        store.delete_api_key(KEY_ID)


def spent() -> float:
    with _lock:
        return float(_read().get("spent") or 0.0)


def overall_limit() -> float:
    with _lock:
        value = _read().get("overall_limit")
    try:
        return max(0.0, float(value)) if value is not None else DEFAULT_OVERALL_LIMIT
    except (TypeError, ValueError):
        return DEFAULT_OVERALL_LIMIT


def set_overall_limit(limit: float) -> None:
    with _lock:
        data = _read()
        data["overall_limit"] = max(0.0, float(limit))
        _write(data)


def reset_total() -> None:
    with _lock:
        data = _read()
        data["spent"] = 0.0
        _write(data)


def limit_reached() -> bool:
    return spent() >= overall_limit()


def record(session_id: str, total_cost: Any) -> float | None:
    """Count one reply. Returns its cost, or None when none was reported."""
    try:
        total = float(total_cost)
    except (TypeError, ValueError):
        return None
    if total < 0:
        return None
    with _lock:
        data = _read()
        totals = data.get("session_totals")
        totals = totals if isinstance(totals, dict) else {}
        before = float(totals.get(session_id) or 0.0) if session_id else 0.0
        # A smaller total is a new run of the CLI that did not carry the old one.
        cost = total - before if total >= before else total
        if session_id:
            totals[session_id] = total
            # Kept bounded: only recent sessions are resumed.
            data["session_totals"] = dict(list(totals.items())[-200:])
        data["spent"] = float(data.get("spent") or 0.0) + cost
        _write(data)
    return cost


def money(amount: float) -> str:
    """Said the way a person says it: "6 cents", "$1.42"."""
    if amount < 1:
        cents = amount * 100
        if cents < 1:
            return "less than a cent"
        return f"{round(cents)} cent" + ("" if round(cents) == 1 else "s")
    return f"${amount:,.2f}"


def summary() -> str:
    return f"API credits: {money(spent())} spent of {money(overall_limit())}"


def claude_env(env: dict[str, str], key: str) -> dict[str, str]:
    """A Claude process's environment charged to ``key`` and to nothing else."""
    charged = {name: value for name, value in env.items() if name not in REDIRECTS}
    charged["ANTHROPIC_API_KEY"] = key
    return charged


def subscription_charged_elsewhere(env: Mapping[str, str] | None = None, cwd: str = "") -> str:
    """Where an API key would bill a subscription tab, or "" when nothing does.

    An ANTHROPIC_API_KEY or auth token in the environment, or an apiKeyHelper
    or key in a Claude Code settings file, makes ``claude -p`` use it instead
    of the subscription. A ``claude setup-token`` sign-in
    (CLAUDE_CODE_OAUTH_TOKEN) belongs to the subscription and does not count.
    """
    source: Mapping[str, str] = os.environ if env is None else env
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        if source.get(name):
            return f"the {name} environment variable"
    files = [Path.home() / ".claude" / "settings.json"]
    if cwd:
        files += [
            Path(cwd) / ".claude" / "settings.json",
            Path(cwd) / ".claude" / "settings.local.json",
        ]
    for path in files:
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(settings, dict):
            continue
        if settings.get("apiKeyHelper"):
            return f"the apiKeyHelper in {path}"
        configured = settings.get("env")
        if isinstance(configured, dict) and (
            configured.get("ANTHROPIC_API_KEY") or configured.get("ANTHROPIC_AUTH_TOKEN")
        ):
            return f"the key in {path}"
    return ""
