from __future__ import annotations

from collections.abc import Iterator
from threading import Event

from accessible_ai.models import (
    API_MODE_MESSAGES,
    API_MODE_RESPONSES,
    GenerationSettings,
    StreamEvent,
)
from accessible_ai.providers.protocols import ProtocolMixin


class OpenAICompatibleProvider(ProtocolMixin):
    def headers(self) -> dict[str, str]:
        headers = {str(k): str(v) for k, v in self.account.custom_headers.items()}
        key = (
            self.credentials.get_api_key(int(self.account.id)).strip()
            if self.account.id is not None
            else ""
        )
        if key:
            headers["Authorization"] = f"Bearer {key}"
        headers["Content-Type"] = "application/json"
        return headers

    def list_models(self) -> list[str]:
        return self.list_models_from_endpoint()

    def generate(self, settings: GenerationSettings, cancel: Event) -> Iterator[StreamEvent]:
        if self.account.api_mode == API_MODE_RESPONSES:
            yield from self.generate_responses(settings, cancel)
        elif self.account.api_mode == API_MODE_MESSAGES:
            yield from self.generate_messages(settings, cancel)
        else:
            yield from self.generate_chat_completions(settings, cancel)
