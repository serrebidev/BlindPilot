from __future__ import annotations

import json
from threading import Event

import httpx
import pytest

from accessible_ai.models import Account, GenerationSettings, PROVIDER_OPENAI
from accessible_ai.providers.openai_provider import OpenAIProvider
from accessible_ai.providers.protocols import ProviderError


def test_a_failed_responses_stream_is_not_reported_as_complete(monkeypatch):
    def handle(_request: httpx.Request) -> httpx.Response:
        events = [
            {"type": "response.output_text.delta", "delta": "partial answer"},
            {
                "type": "response.failed",
                "response": {"error": {"message": "provider failed"}},
            },
        ]
        content = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(200, content=content)

    provider = OpenAIProvider(
        Account(id=1, provider=PROVIDER_OPENAI, base_url="https://example.test"), object()
    )
    monkeypatch.setattr(provider, "headers", dict)
    monkeypatch.setattr(
        provider,
        "client",
        lambda: httpx.Client(transport=httpx.MockTransport(handle)),
    )
    settings = GenerationSettings(model="model", messages=[{"role": "user", "content": "hi"}])

    with pytest.raises(ProviderError, match="provider failed"):
        list(provider.generate(settings, Event()))
