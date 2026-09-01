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


BUILTIN_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    PROVIDER_OPENROUTER: {
        "base_url": "https://openrouter.ai/api/v1",
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
        "responses_endpoint": "/responses",
        "messages_endpoint": "/messages",
        "api_mode": API_MODE_AUTO,
    },
    PROVIDER_OPENAI: {
        "base_url": "https://api.openai.com/v1",
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
        "responses_endpoint": "/responses",
        "messages_endpoint": "/messages",
        "api_mode": API_MODE_AUTO,
    },
    PROVIDER_OPENCODE_GO: {
        "base_url": "https://opencode.ai/zen/go/v1",
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
        "responses_endpoint": "/responses",
        "messages_endpoint": "/messages",
        "api_mode": API_MODE_AUTO,
    },
    # Anthropic speaks only its own Messages API and authenticates with an
    # x-api-key header rather than a bearer token.  The chat and responses paths
    # below are placeholders that this provider never calls.
    PROVIDER_CLAUDE: {
        "base_url": "https://api.anthropic.com/v1",
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
        "responses_endpoint": "/responses",
        "messages_endpoint": "/messages",
        "api_mode": API_MODE_MESSAGES,
    },
    # Google exposes Gemini through an OpenAI-compatible surface.  Only /models
    # and /chat/completions exist there.
    PROVIDER_GEMINI: {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
        "responses_endpoint": "/responses",
        "messages_endpoint": "/messages",
        "api_mode": API_MODE_CHAT,
    },
    PROVIDER_Z_AI: {
        "base_url": "https://api.z.ai/api/paas/v4",
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
        "responses_endpoint": "/responses",
        "messages_endpoint": "/messages",
        "api_mode": API_MODE_CHAT,
    },
    # Moonshot AI's international endpoint.
    PROVIDER_MOONSHOT: {
        "base_url": "https://api.moonshot.ai/v1",
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
        "responses_endpoint": "/responses",
        "messages_endpoint": "/messages",
        "api_mode": API_MODE_CHAT,
    },
    # Kimi is Moonshot's mainland China service.  It is a separate account with
    # a separate API key, which is why it is a separate provider here.
    PROVIDER_KIMI: {
        "base_url": "https://api.moonshot.cn/v1",
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
        "responses_endpoint": "/responses",
        "messages_endpoint": "/messages",
        "api_mode": API_MODE_CHAT,
    },
    PROVIDER_DEEPSEEK: {
        "base_url": "https://api.deepseek.com/v1",
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
        "responses_endpoint": "/responses",
        "messages_endpoint": "/messages",
        "api_mode": API_MODE_CHAT,
    },
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
