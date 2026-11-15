"""Ollama: local models through Ollama's own catalog and chat surface."""

from __future__ import annotations

from collections.abc import Iterator
from threading import Event

from accessible_ai.models import GenerationSettings, StreamEvent
from accessible_ai.providers.base import BaseProvider
from accessible_ai.providers.protocols import ProtocolMixin


class OllamaProvider(ProtocolMixin, BaseProvider):
    """Local Ollama chat. No API key: the server runs on this machine.

    Model discovery comes from ``GET /api/tags`` (Ollama's own catalog
    shape); chat uses Ollama's OpenAI-compatible surface at ``/v1``.
    """

    def headers(self) -> dict[str, str]:
        headers = {str(k): str(v) for k, v in self.account.custom_headers.items()}
        headers["Content-Type"] = "application/json"
        return headers

    def list_models(self) -> list[str]:
        """Model names from ``/api/tags``; empty when Ollama is not running."""
        import json

        try:
            with self.client().stream("GET", self.build_url("/api/tags")) as response:
                self.raise_for_status(response)
                data = json.loads(response.read().decode("utf-8"))
        except Exception:
            return []
        models = data.get("models") if isinstance(data, dict) else None
        names = {
            str(item.get("name") or "").strip()
            for item in (models or [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }
        return sorted(names)

    def generate(self, settings: GenerationSettings, cancel: Event) -> Iterator[StreamEvent]:
        yield from self.generate_chat_completions(settings, cancel)
