from __future__ import annotations

from collections.abc import Iterator
from threading import Event

from accessible_ai.models import API_MODE_CHAT, API_MODE_MESSAGES, GenerationSettings, StreamEvent
from accessible_ai.providers.protocols import ProtocolMixin


class OpenAIProvider(ProtocolMixin):
    def list_models(self) -> list[str]:
        return self.list_models_from_endpoint()

    def generate(self, settings: GenerationSettings, cancel: Event) -> Iterator[StreamEvent]:
        mode = self.account.api_mode
        if mode == API_MODE_CHAT:
            yield from self.generate_chat_completions(settings, cancel)
        elif mode == API_MODE_MESSAGES:
            yield from self.generate_messages(settings, cancel)
        else:
            # OpenAI's current primary interface is the Responses API.
            yield from self.generate_responses(settings, cancel)
