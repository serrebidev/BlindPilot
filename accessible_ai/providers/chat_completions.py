from __future__ import annotations

import re
from collections.abc import Iterator
from threading import Event

from accessible_ai.models import (
    API_MODE_MESSAGES,
    API_MODE_RESPONSES,
    GenerationSettings,
    StreamEvent,
)
from accessible_ai.providers.protocols import ProtocolMixin


class ChatCompletionsProvider(ProtocolMixin):
    """A built-in service that speaks the OpenAI Chat Completions protocol.

    Unlike the generic OpenAI-compatible account, the connection settings here
    are application-owned, so an API key is always required and the account
    dialog never asks for a URL.
    """

    def list_models(self) -> list[str]:
        return self.list_models_from_endpoint()

    def generate(self, settings: GenerationSettings, cancel: Event) -> Iterator[StreamEvent]:
        if self.account.api_mode == API_MODE_RESPONSES:
            yield from self.generate_responses(settings, cancel)
        elif self.account.api_mode == API_MODE_MESSAGES:
            yield from self.generate_messages(settings, cancel)
        else:
            yield from self.generate_chat_completions(settings, cancel)


# Gemini's OpenAI-compatible surface names its models "models/gemini-...", while
# its chat endpoint accepts the bare id. Present and send the bare id, and keep
# accepting a prefixed one that an old account row or a profile may still hold.
GEMINI_MODEL_PREFIX = "models/"


def normalize_gemini_model(model_id: str) -> str:
    return model_id.strip().removeprefix(GEMINI_MODEL_PREFIX)


# Model families the Gemini API lists that cannot hold a conversation: speech,
# images, video, embeddings, live audio and the like. Offered in the picker, a
# chat sent to one fails, so they are left out.
_NON_CHAT_GEMINI_MODEL = re.compile(
    r"(embedding|embed|tts|image|imagen|veo|omni|live|transcribe|aqa|learnlm|robotics|computer-use)",
    re.IGNORECASE,
)


def is_gemini_chat_model(model_id: str) -> bool:
    """Whether a Gemini API model id can take a conversation turn."""
    name = normalize_gemini_model(model_id)
    return name.startswith(("gemini", "gemma")) and not _NON_CHAT_GEMINI_MODEL.search(name)


class GeminiProvider(ChatCompletionsProvider):
    def list_models(self) -> list[str]:
        models = {normalize_gemini_model(model) for model in super().list_models()}
        chat = {model for model in models if is_gemini_chat_model(model)}
        # Never leave the picker empty because Google renamed a family: an
        # account whose list holds nothing recognisable still gets it all.
        return sorted(chat or models, key=str.casefold)

    def generate(self, settings: GenerationSettings, cancel: Event) -> Iterator[StreamEvent]:
        effective = GenerationSettings(
            model=normalize_gemini_model(settings.model),
            messages=settings.messages,
            temperature=settings.temperature,
            max_output_tokens=settings.max_output_tokens,
            streaming=settings.streaming,
        )
        yield from super().generate(effective, cancel)
