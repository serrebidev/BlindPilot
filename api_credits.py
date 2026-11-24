"""Claude Code and Codex tabs charged to an API key instead of the subscription.

From The Chat Place 0.6.2 (#658, #692). A Claude tab on API credits starts
Claude Code with ANTHROPIC_API_KEY set and every redirect removed, so the key
goes only to Anthropic. A Codex tab on API credits runs its own app-server on a
provider that sends an OpenAI key to api.openai.com, so the ChatGPT sign-in is
left alone. Claude and Codex each have their own key, their own limits and
their own total.

Claude Code's ``total_cost_usd`` is the session's running total, carried over
on resume, so a reply's cost is the difference from the last total seen for
that session. Codex reports tokens, not dollars, so its cost is an estimate
from OpenAI's prices; a model not in the table is "cost unknown", and each
such message counts the whole per-message limit against the overall one.

The per-message limit ($2 to start) stops a reply partway once the usage it
has reported reaches it; it can go over by one request to the model. The
overall limit ($20 to start) stops sending once it is spent.

Every figure is BlindPilot's own count for this computer; the Claude Console
and the OpenAI dashboard are the bill.
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

PROVIDERS = ("claude", "codex")
# The credential store is keyed by Chat account id; Chat's ids start at 1, so
# these reserved ones cannot collide with an account.
KEY_IDS = {"claude": -1, "codex": -2}
KEY_ID = KEY_IDS["claude"]
DEFAULT_OVERALL_LIMIT = 20.0
DEFAULT_MESSAGE_LIMIT = 2.0
# Environment that would send the Anthropic key elsewhere, or bill something else.
REDIRECTS = (
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_CUSTOM_HEADERS",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_OAUTH_TOKEN",
)
# The variable a Codex API-credits process reads its key from. Its own name,
# so an OPENAI_API_KEY the user set for something else is never what is charged.
CODEX_KEY_ENV = "BLINDPILOT_OPENAI_API_KEY"
CODEX_PROVIDER_ID = "blindpilot-api-credits"
OPENAI_BASE_URL = "https://api.openai.com/v1"

# Dollars per million tokens: input, output, cache read, cache write (5 min).
# From platform.claude.com and developers.openai.com pricing, 2026-11-24.
# A model is matched by its id, or an id it starts with followed by "-", the
# longest first: "claude-opus-5-5[1m]" is Opus 5.5, and "gpt-5.7" is unknown
# rather than priced as "gpt-5".
# ponytail: a fixed table; a model released later reads as cost unknown until added.
CLAUDE_PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-fable-5-2": (8, 40, 0.2, 10),
    "claude-mythos-5-2": (8, 40, 0.2, 10),
    "claude-fable-5-1": (10, 50, 0.25, 12.5),
    "claude-mythos-5-1": (10, 50, 0.25, 12.5),
    "claude-fable-5": (10, 50, 1, 12.5),
    "claude-mythos-5": (10, 50, 1, 12.5),
    "claude-opus-5-5": (4, 20, 0.2, 5),
    "claude-opus-5": (5, 25, 0.5, 6.25),
    "claude-opus-4": (5, 25, 0.5, 6.25),
    "claude-opus-4-1": (15, 75, 1.5, 18.75),
    "claude-sonnet-5-5": (2, 10, 0.1, 2.5),
    "claude-sonnet-5": (2, 10, 0.2, 2.5),
    "claude-sonnet-4": (3, 15, 0.3, 3.75),
    "claude-haiku-5-5": (0.1, 0.5, 0.01, 0.125),
    "claude-haiku-4-5": (1, 5, 0.1, 1.25),
}
# Input, cached input, output. Short-context Standard rates.
OPENAI_PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-6.2-astra": (8, 0.4, 40),
    "gpt-6.2-sol": (2, 0.1, 10),
    "gpt-6.1-sol": (2, 0.1, 10),
    "gpt-6.1-terra": (1, 0.05, 5),
    "gpt-6-astra": (10, 1, 50),
    "gpt-6-sol": (2, 0.2, 10),
    "gpt-6-luna": (0.1, 0.01, 0.5),
    "gpt-5.6-sol": (4, 0.4, 20),
    "gpt-5.6-terra": (2, 0.2, 12),
    "gpt-5.6-luna": (0.2, 0.02, 1.2),
    "gpt-5.5": (5, 0.5, 30),
    "gpt-5.4": (2.5, 0.25, 15),
    "gpt-5.4-mini": (0.75, 0.075, 4.5),
    "gpt-5.4-nano": (0.2, 0.02, 1.25),
    "gpt-5.2": (1.75, 0.175, 14),
    "gpt-5.1": (1.25, 0.125, 10),
    "gpt-5": (1.25, 0.125, 10),
    "gpt-5-mini": (0.25, 0.025, 2),
    "gpt-5-nano": (0.05, 0.005, 0.4),
}

_lock = threading.Lock()


def _price(table: Mapping[str, Any], model: str) -> Any:
    model = (model or "").strip().lower().split("[", 1)[0]
    for name in sorted(table, key=len, reverse=True):
        if model == name or model.startswith(name + "-"):
            return table[name]
    return None


def _count(usage: Mapping[str, Any], name: str) -> float:
    try:
        return max(0.0, float(usage.get(name) or 0))
    except (TypeError, ValueError):
        return 0.0


def claude_cost(model: str, usage: Mapping[str, Any]) -> float | None:
    """What one Claude API response's usage costs, or None for an unknown model."""
    price = _price(CLAUDE_PRICES, model)
    if price is None:
        return None
    fresh, out, read, write = price
    return (
        _count(usage, "input_tokens") * fresh
        + _count(usage, "output_tokens") * out
        + _count(usage, "cache_read_input_tokens") * read
        + _count(usage, "cache_creation_input_tokens") * write
    ) / 1e6


def codex_cost(model: str, usage: Mapping[str, Any]) -> float | None:
    """What a Codex usage report costs, or None for an unknown model.

    Codex reports input with the cached part included, and output with the
    reasoning included, the way the Responses API bills them.
    """
    price = _price(OPENAI_PRICES, model)
    if price is None:
        return None
    fresh, cached_rate, out = price
    total_in = _count(usage, "inputTokens")
    cached = min(_count(usage, "cachedInputTokens"), total_in)
    output = _count(usage, "outputTokens")
    return ((total_in - cached) * fresh + cached * cached_rate + output * out) / 1e6


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


def _section(data: dict[str, Any], provider: str) -> dict[str, Any]:
    """One provider's figures. BlindPilot 0.61 kept Claude's at the top level."""
    section = data.get(provider)
    if not isinstance(section, dict):
        section = {}
        if provider == "claude":
            for name in ("spent", "overall_limit", "session_totals"):
                if name in data:
                    section[name] = data.pop(name)
        data[provider] = section
    return section


def get_key(provider: str = "claude") -> str:
    try:
        return CredentialStore().get_api_key(KEY_IDS[provider]).strip()
    except Exception:
        return ""


def set_key(key: str, provider: str = "claude") -> None:
    store = CredentialStore()
    if key.strip():
        store.set_api_key(KEY_IDS[provider], key.strip())
    else:
        store.delete_api_key(KEY_IDS[provider])


def spent(provider: str = "claude") -> float:
    with _lock:
        return float(_section(_read(), provider).get("spent") or 0.0)


def _limit(provider: str, name: str, default: float) -> float:
    with _lock:
        value = _section(_read(), provider).get(name)
    try:
        return max(0.0, float(value)) if value is not None else default
    except (TypeError, ValueError):
        return default


def _store(provider: str, name: str, value: float) -> None:
    with _lock:
        data = _read()
        _section(data, provider)[name] = value
        _write(data)


def overall_limit(provider: str = "claude") -> float:
    return _limit(provider, "overall_limit", DEFAULT_OVERALL_LIMIT)


def set_overall_limit(limit: float, provider: str = "claude") -> None:
    _store(provider, "overall_limit", max(0.0, float(limit)))


def message_limit(provider: str = "claude") -> float:
    return _limit(provider, "message_limit", DEFAULT_MESSAGE_LIMIT)


def set_message_limit(limit: float, provider: str = "claude") -> None:
    _store(provider, "message_limit", max(0.0, float(limit)))


def reset_total(provider: str = "claude") -> None:
    _store(provider, "spent", 0.0)


def limit_reached(provider: str = "claude") -> bool:
    return spent(provider) >= overall_limit(provider)


def add(amount: float, provider: str = "codex") -> None:
    """Count a cost that is not a running total, such as a Codex estimate."""
    with _lock:
        data = _read()
        section = _section(data, provider)
        section["spent"] = float(section.get("spent") or 0.0) + max(0.0, float(amount))
        _write(data)


def record(session_id: str, total_cost: Any, provider: str = "claude") -> float | None:
    """Count one reply. Returns its cost, or None when none was reported."""
    try:
        total = float(total_cost)
    except (TypeError, ValueError):
        return None
    if total < 0:
        return None
    with _lock:
        data = _read()
        section = _section(data, provider)
        totals = section.get("session_totals")
        totals = totals if isinstance(totals, dict) else {}
        before = float(totals.get(session_id) or 0.0) if session_id else 0.0
        # A smaller total is a new run of the CLI that did not carry the old one.
        cost = total - before if total >= before else total
        if session_id:
            totals[session_id] = total
            # Kept bounded: only recent sessions are resumed.
            section["session_totals"] = dict(list(totals.items())[-200:])
        section["spent"] = float(section.get("spent") or 0.0) + cost
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


def summary(provider: str = "claude") -> str:
    return (
        f"API credits: {money(spent(provider))} spent of {money(overall_limit(provider))}, "
        f"at most {money(message_limit(provider))} a message"
    )


def claude_env(env: dict[str, str], key: str) -> dict[str, str]:
    """A Claude process's environment charged to ``key`` and to nothing else."""
    charged = {name: value for name, value in env.items() if name not in REDIRECTS}
    charged["ANTHROPIC_API_KEY"] = key
    return charged


def codex_args() -> tuple[str, ...]:
    """Codex config that sends requests, and the key, only to api.openai.com.

    A provider of BlindPilot's own rather than Codex's built-in "openai", which
    would use the ChatGPT sign-in and honour OPENAI_BASE_URL. ``-c`` parses
    each value as TOML, so strings are quoted.
    """
    provider = f"model_providers.{CODEX_PROVIDER_ID}"
    overrides = (
        ("model_provider", json.dumps(CODEX_PROVIDER_ID)),
        (f"{provider}.name", json.dumps("OpenAI API credits")),
        (f"{provider}.base_url", json.dumps(OPENAI_BASE_URL)),
        (f"{provider}.env_key", json.dumps(CODEX_KEY_ENV)),
        (f"{provider}.wire_api", json.dumps("responses")),
    )
    args: list[str] = []
    for name, value in overrides:
        args += ["-c", f"{name}={value}"]
    return tuple(args)


def codex_env(env: dict[str, str], key: str) -> dict[str, str]:
    """A Codex process's environment with ``key`` where ``codex_args`` reads it."""
    charged = dict(env)
    charged[CODEX_KEY_ENV] = key
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
