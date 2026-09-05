from __future__ import annotations

from accessible_ai.models import (
    API_MODE_AUTO,
    API_MODE_CHAT,
    API_MODE_MESSAGES,
    Account,
    PROVIDER_CLAUDE,
    PROVIDER_DEEPSEEK,
    PROVIDER_GEMINI,
    PROVIDER_KIMI,
    PROVIDER_MOONSHOT,
    PROVIDER_OPENAI,
    PROVIDER_OPENCODE_GO,
    PROVIDER_OPENROUTER,
    PROVIDER_Z_AI,
)


# Every built-in provider speaks the same four paths; only the host and the
# API mode differ, so that is all the table below says.
_ENDPOINTS = {
    "models_endpoint": "/models",
    "chat_endpoint": "/chat/completions",
    "responses_endpoint": "/responses",
    "messages_endpoint": "/messages",
}

_BUILTIN_PROVIDERS: dict[str, tuple[str, str]] = {
    PROVIDER_OPENROUTER: ("https://openrouter.ai/api/v1", API_MODE_AUTO),
    PROVIDER_OPENAI: ("https://api.openai.com/v1", API_MODE_AUTO),
    PROVIDER_OPENCODE_GO: ("https://opencode.ai/zen/go/v1", API_MODE_AUTO),
    # Anthropic speaks only its own Messages API and authenticates with an
    # x-api-key header rather than a bearer token.  The chat and responses paths
    # are placeholders that this provider never calls.
    PROVIDER_CLAUDE: ("https://api.anthropic.com/v1", API_MODE_MESSAGES),
    # Google exposes Gemini through an OpenAI-compatible surface.  Only /models
    # and /chat/completions exist there.
    PROVIDER_GEMINI: ("https://generativelanguage.googleapis.com/v1beta/openai", API_MODE_CHAT),
    PROVIDER_Z_AI: ("https://api.z.ai/api/paas/v4", API_MODE_CHAT),
    # Moonshot AI's international endpoint.
    PROVIDER_MOONSHOT: ("https://api.moonshot.ai/v1", API_MODE_CHAT),
    # Kimi is Moonshot's mainland China service.  It is a separate account with
    # a separate API key, which is why it is a separate provider here.
    PROVIDER_KIMI: ("https://api.moonshot.cn/v1", API_MODE_CHAT),
    PROVIDER_DEEPSEEK: ("https://api.deepseek.com/v1", API_MODE_CHAT),
}

BUILTIN_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    provider: {"base_url": base_url, "api_mode": api_mode, **_ENDPOINTS}
    for provider, (base_url, api_mode) in _BUILTIN_PROVIDERS.items()
}


def is_builtin_provider(provider: str) -> bool:
    return provider in BUILTIN_PROVIDER_DEFAULTS


def apply_builtin_provider_defaults(account: Account) -> Account:
    """Force canonical connection settings for built-in providers.

    These values are application configuration, not user configuration.  Calling
    this before provider construction also repairs old account rows that contain
    blank or stale URLs.
    """
    defaults = BUILTIN_PROVIDER_DEFAULTS.get(account.provider)
    if defaults is None:
        return account

    account.base_url = defaults["base_url"]
    account.models_endpoint = defaults["models_endpoint"]
    account.chat_endpoint = defaults["chat_endpoint"]
    account.responses_endpoint = defaults["responses_endpoint"]
    account.messages_endpoint = defaults["messages_endpoint"]
    account.api_mode = defaults["api_mode"]
    return account
