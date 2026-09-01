from __future__ import annotations

from collections.abc import Iterator
from threading import Event

from accessible_ai.models import (
    API_MODE_CHAT,
    API_MODE_MESSAGES,
    API_MODE_RESPONSES,
    GenerationSettings,
    StreamEvent,
)
from accessible_ai.providers.protocols import ProtocolMixin


RESPONSES_MODELS = {
    "grok-4.6",
    "grok-4.5",
    "gpt-5.6-luna",
    "muse-spark-1.2-contributor",
}

MESSAGES_MODELS = {
    "minimax-m3",
    "minimax-m2.7",
    "minimax-m2.5",
    "qwen3.8-max",
    "qwen3.8-flash",
    "qwen3.7-max",
    "qwen3.7-plus",
    "qwen3.6-plus",
    "qwen3.5-plus",
}


class OpenCodeGoProvider(ProtocolMixin):
    def list_models(self) -> list[str]:
        return self.list_models_from_endpoint()

    def protocol_for_model(self, model_id: str) -> str:
        if self.account.api_mode in {API_MODE_CHAT, API_MODE_RESPONSES, API_MODE_MESSAGES}:
            return self.account.api_mode
        normalized = model_id.removeprefix("opencode-go/")
        if normalized in RESPONSES_MODELS or normalized.startswith(("gpt-", "grok-", "muse-")):
            return API_MODE_RESPONSES
        if normalized in MESSAGES_MODELS or normalized.startswith(("minimax-", "qwen")):
            return API_MODE_MESSAGES
        return API_MODE_CHAT

    def generate(self, settings: GenerationSettings, cancel: Event) -> Iterator[StreamEvent]:
        normalized = settings.model.removeprefix("opencode-go/")
        effective = GenerationSettings(
            model=normalized,
            messages=settings.messages,
            temperature=settings.temperature,
            max_output_tokens=settings.max_output_tokens,
            streaming=settings.streaming,
        )
        protocol = self.protocol_for_model(normalized)
        if protocol == API_MODE_RESPONSES:
            yield from self.generate_responses(effective, cancel)
        elif protocol == API_MODE_MESSAGES:
            yield from self.generate_messages(effective, cancel)
        else:
            yield from self.generate_chat_completions(effective, cancel)
