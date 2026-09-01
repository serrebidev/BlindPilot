from __future__ import annotations

from accessible_ai.models import (
    Account,
    PROVIDER_CLAUDE,
    PROVIDER_DEEPSEEK,
    PROVIDER_GEMINI,
    PROVIDER_KIMI,
    PROVIDER_MOONSHOT,
    PROVIDER_OPENAI,
    PROVIDER_OPENAI_COMPATIBLE,
    PROVIDER_OPENCODE_GO,
    PROVIDER_OPENROUTER,
    PROVIDER_Z_AI,
)
from accessible_ai.providers.anthropic_provider import ClaudeProvider
from accessible_ai.providers.base import BaseProvider, ProviderError
from accessible_ai.providers.chat_completions import ChatCompletionsProvider, GeminiProvider
from accessible_ai.providers.config import apply_builtin_provider_defaults
from accessible_ai.providers.openai_compatible import OpenAICompatibleProvider
from accessible_ai.providers.openai_provider import OpenAIProvider
from accessible_ai.providers.opencode_go import OpenCodeGoProvider
from accessible_ai.providers.openrouter import OpenRouterProvider
from accessible_ai.storage.credentials import CredentialStore


# Services that need nothing beyond the built-in address and a Chat Completions
# request. Anything with its own protocol or authentication gets its own class.
CHAT_COMPLETIONS_PROVIDERS = {
    PROVIDER_Z_AI,
    PROVIDER_MOONSHOT,
    PROVIDER_KIMI,
    PROVIDER_DEEPSEEK,
}


def create_provider(account: Account, credentials: CredentialStore) -> BaseProvider:
    # Built-in provider URLs and endpoint paths are application-owned.  Never
    # trust stale values from an old database row for those providers.
    apply_builtin_provider_defaults(account)

    if account.provider == PROVIDER_OPENROUTER:
        return OpenRouterProvider(account, credentials)
    if account.provider == PROVIDER_OPENAI:
        return OpenAIProvider(account, credentials)
    if account.provider == PROVIDER_CLAUDE:
        return ClaudeProvider(account, credentials)
    if account.provider == PROVIDER_GEMINI:
        return GeminiProvider(account, credentials)
    if account.provider in CHAT_COMPLETIONS_PROVIDERS:
        return ChatCompletionsProvider(account, credentials)
    if account.provider == PROVIDER_OPENCODE_GO:
        return OpenCodeGoProvider(account, credentials)
    if account.provider == PROVIDER_OPENAI_COMPATIBLE:
        return OpenAICompatibleProvider(account, credentials)
    raise ProviderError(f"Unknown provider type: {account.provider}")
