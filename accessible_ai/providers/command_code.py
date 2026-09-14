from __future__ import annotations

import dataclasses
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


class CommandCodeProvider(ProtocolMixin):
    """Command Code's Provider API, one host serving every top model.

    Each endpoint takes its native shape: `/chat/completions` for OpenAI and
    the open models, `/messages` for Claude. Sending a Claude model to the
    chat endpoint, or any other model to the messages endpoint, is a 400, so
    the protocol is chosen per model below rather than left to the account.
    """

    def list_models(self) -> list[str]:
        return self.list_models_from_endpoint()

    def protocol_for_model(self, model_id: str) -> str:
        # An explicit choice in the account wins; routing is what an account
        # left on "Automatic" falls back to.
        if self.account.api_mode in {API_MODE_CHAT, API_MODE_RESPONSES, API_MODE_MESSAGES}:
            return self.account.api_mode
        if model_id.startswith(("claude-", "anthropic/")):
            return API_MODE_MESSAGES
        return API_MODE_CHAT

    def generate(self, settings: GenerationSettings, cancel: Event) -> Iterator[StreamEvent]:
        normalized = settings.model.removeprefix("command-code/")
        if normalized != settings.model:
            settings = dataclasses.replace(settings, model=normalized)
        protocol = self.protocol_for_model(normalized)
        if protocol == API_MODE_RESPONSES:
            yield from self.generate_responses(settings, cancel)
        elif protocol == API_MODE_MESSAGES:
            yield from self.generate_messages(settings, cancel)
        else:
            yield from self.generate_chat_completions(settings, cancel)
