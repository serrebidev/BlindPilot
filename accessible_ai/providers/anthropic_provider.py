from __future__ import annotations

from collections.abc import Iterator
from threading import Event
from typing import Any

from accessible_ai.models import GenerationSettings, StreamEvent
from accessible_ai.providers.protocols import ProtocolMixin


# Anthropic pins request and response shapes to a dated version header.
ANTHROPIC_VERSION = "2023-06-01"

# /models is paginated. One page of this size covers the whole catalogue, and
# the loop below still follows has_more if that ever stops being true.
MODEL_PAGE_SIZE = 1000
MODEL_PAGE_LIMIT = 20


class ClaudeProvider(ProtocolMixin):
    """Anthropic's own API, which speaks the Messages protocol only."""

    def headers(self) -> dict[str, str]:
        headers = {str(k): str(v) for k, v in self.account.custom_headers.items()}
        # Anthropic authenticates API keys with x-api-key. A bearer token is
        # rejected as "Invalid bearer token", so do not send one.
        headers["x-api-key"] = self.api_key()
        headers["anthropic-version"] = ANTHROPIC_VERSION
        headers["Content-Type"] = "application/json"
        return headers

    def list_models(self) -> list[str]:
        url = self.build_url(self.account.models_endpoint)
        headers = self.headers()
        models: list[str] = []
        params: dict[str, Any] = {"limit": MODEL_PAGE_SIZE}
        with self.client() as client:
            for _ in range(MODEL_PAGE_LIMIT):
                response = client.get(url, headers=headers, params=params)
                self.raise_for_status(response)
                payload = response.json()
                if not isinstance(payload, dict):
                    break
                data = payload.get("data")
                if not isinstance(data, list) or not data:
                    break
                for item in data:
                    if isinstance(item, dict) and isinstance(item.get("id"), str):
                        models.append(item["id"])
                if not payload.get("has_more"):
                    break
                last_id = payload.get("last_id")
                if not isinstance(last_id, str) or not last_id:
                    break
                params = {"limit": MODEL_PAGE_SIZE, "after_id": last_id}
        return sorted(set(models), key=str.casefold)

    def generate(self, settings: GenerationSettings, cancel: Event) -> Iterator[StreamEvent]:
        yield from self.generate_messages(settings, cancel)
