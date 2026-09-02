from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from threading import Event
from typing import Any

from accessible_ai.models import (
    API_MODE_MESSAGES,
    API_MODE_RESPONSES,
    GenerationSettings,
    StreamEvent,
)
from accessible_ai.providers.base import ProviderError, content_to_text
from accessible_ai.providers.protocols import ProtocolMixin, chat_messages


logger = logging.getLogger(__name__)

# OpenRouter lists a ":batch" copy of most of its models. Those ids are served
# only by its asynchronous batch API: /chat/completions answers a request naming
# one with "This model is only available through the Batch API."
BATCH_VARIANT_SUFFIX = ":batch"

# The batch API is versioned separately from the rest of the account's base URL,
# and, like the other built-in provider URLs, it is application configuration
# rather than something an account row may override.
BATCH_URL = "https://openrouter.ai/api/beta/batches"
BATCH_REQUEST_ENDPOINT = "/v1/chat/completions"
BATCH_CUSTOM_ID = "accessible-ai-1"

BATCH_TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled", "canceled"}

# A submitted batch is not immediately readable back under its own id, so an
# early "not found" means "not registered yet" rather than "gone".
BATCH_LOOKUP_GRACE_SECONDS = 300.0

# Batches are answered in anything from seconds to hours. Poll quickly at first,
# for the short ones, then settle into a slow rhythm.
BATCH_FIRST_POLL_SECONDS = 2.0
BATCH_MAX_POLL_SECONDS = 20.0
BATCH_POLL_GROWTH = 1.5

# OpenRouter's own completion window. Waiting past it means the batch expired.
BATCH_MAX_WAIT_SECONDS = 24 * 60 * 60

# A batch can sit in one state for a long time. Say how long the wait has been
# every so often so a silent status bar is not mistaken for a stuck one.
BATCH_HEARTBEAT_SECONDS = 300.0

BATCH_STATUS_TEXT = {
    "validating": "Batch accepted; OpenRouter is validating it.",
    "queued": "Batch queued at OpenRouter.",
    "in_progress": "Batch running at OpenRouter.",
    "finalizing": "Batch finishing at OpenRouter.",
    "completed": "Batch completed.",
}


def is_batch_variant(model: str) -> bool:
    """Return True for a model id that is served only by the batch API."""
    return model.strip().casefold().endswith(BATCH_VARIANT_SUFFIX)


def batch_status_text(status: str, batch_id: str) -> str:
    described = BATCH_STATUS_TEXT.get(status.casefold())
    if described:
        return described
    return f"Batch {batch_id} status: {status or 'unknown'}."


class OpenRouterProvider(ProtocolMixin):
    def headers(self) -> dict[str, str]:
        headers = super().headers()
        # Force response caching off even if an OpenRouter preset enables it.
        headers["X-OpenRouter-Cache"] = "false"
        # Also prevent ordinary HTTP intermediaries from retaining responses.
        headers["Cache-Control"] = "no-store, no-cache, max-age=0"
        headers["Pragma"] = "no-cache"
        return headers

    def list_models(self) -> list[str]:
        return self.list_models_from_endpoint()

    def generate(self, settings: GenerationSettings, cancel: Event) -> Iterator[StreamEvent]:
        mode = self.account.api_mode
        if is_batch_variant(settings.model):
            # Batch models ignore the account's API mode: one endpoint serves
            # them, and it never streams.
            events = self.generate_batch(settings, cancel)
        elif mode == API_MODE_RESPONSES:
            events = self.generate_responses(settings, cancel)
        elif mode == API_MODE_MESSAGES:
            events = self.generate_messages(settings, cancel)
        else:
            events = self.generate_chat_completions(settings, cancel)

        for event in events:
            if event.kind == "headers":
                cache_status = ""
                cache_age = ""
                for key, value in event.metadata.items():
                    normalized = str(key).lower()
                    if normalized == "x-openrouter-cache-status":
                        cache_status = str(value).strip().upper()
                    elif normalized == "x-openrouter-cache-age":
                        cache_age = str(value).strip()
                if cache_age or cache_status not in {"", "MISS", "BYPASS"}:
                    raise ProviderError(
                        "Cached OpenRouter response rejected. No cached content was accepted or saved."
                    )
            yield event

    def generate_batch(self, settings: GenerationSettings, cancel: Event) -> Iterator[StreamEvent]:
        """Answer one message through OpenRouter's asynchronous batch API.

        The batch is submitted and then polled until it is answered. Nothing
        arrives before the whole batch is done, so this reports progress as
        status events while it waits and yields the text once at the end.
        """
        # The batch API takes text only: image, audio, video, and file content
        # are rejected, so say which model to pick instead of sending a request
        # that cannot be served.
        if any(message.get("attachments") for message in settings.messages):
            raise ProviderError(
                "OpenRouter batch models accept text only. Choose "
                f"'{settings.model.strip()[: -len(BATCH_VARIANT_SUFFIX)]}' to send attachments."
            )

        request_body: dict[str, Any] = dict(self.account.custom_body)
        request_body["messages"] = chat_messages(settings.messages)
        if settings.temperature is not None:
            request_body["temperature"] = settings.temperature
        if settings.max_output_tokens is not None:
            request_body["max_tokens"] = settings.max_output_tokens
        # A batch request body is a chat completion body, so a conversation's
        # thinking and tools carry into a batch model unchanged.
        if settings.reasoning:
            request_body["reasoning"] = settings.reasoning
        if settings.tools:
            request_body["tools"] = settings.tools
        if settings.plugins:
            request_body["plugins"] = settings.plugins

        # OpenRouter parses this body as it arrives and rejects it unless
        # "endpoint" and "model" are read before "requests", so the key order
        # below is part of the request rather than a matter of taste.
        body = {
            "endpoint": BATCH_REQUEST_ENDPOINT,
            "model": settings.model.strip(),
            "requests": [{"custom_id": BATCH_CUSTOM_ID, "body": request_body}],
        }

        headers = self.headers()
        with self.client() as client:
            response = client.post(BATCH_URL, headers=headers, json=body)
            self.raise_for_status(response)
            yield StreamEvent("headers", metadata=dict(response.headers))

            batch = self._batch_payload(response.json())
            batch_id = str(batch.get("id") or "").strip()
            if not batch_id:
                raise ProviderError("OpenRouter accepted the batch but did not name it.")
            logger.info("OpenRouter batch submitted id=%s model=%s", batch_id, settings.model)

            status = str(batch.get("status") or "").strip()
            yield StreamEvent(
                "status",
                text=f"Batch {batch_id} submitted. This can take minutes or longer; Stop stops waiting.",
                # Worth keeping in History: it names a request that now exists,
                # and is being paid for, at the provider.
                metadata={"batch_id": batch_id, "batch_status": status, "record": True},
            )

            waited = 0.0
            heartbeat = BATCH_HEARTBEAT_SECONDS
            delay = BATCH_FIRST_POLL_SECONDS
            while status.casefold() not in BATCH_TERMINAL_STATUSES:
                if cancel.wait(delay):
                    # Nothing here can call the batch back: OpenRouter has no
                    # cancel route for one, so say plainly that it is still
                    # running and still being paid for.
                    logger.info("Stopped waiting for OpenRouter batch id=%s", batch_id)
                    yield StreamEvent(
                        "status",
                        text=(
                            f"Stopped waiting for batch {batch_id}. It keeps running at OpenRouter "
                            "and is still billed."
                        ),
                        metadata={"batch_id": batch_id, "batch_status": status, "record": True},
                    )
                    return
                waited += delay
                delay = min(delay * BATCH_POLL_GROWTH, BATCH_MAX_POLL_SECONDS)
                if waited > BATCH_MAX_WAIT_SECONDS:
                    raise ProviderError(
                        f"Batch {batch_id} did not finish within its 24-hour completion window."
                    )

                polled = client.get(f"{BATCH_URL}/{batch_id}", headers=headers)
                if polled.status_code == 404 and waited <= BATCH_LOOKUP_GRACE_SECONDS:
                    # A batch is not readable back under its own id for the
                    # first few seconds after it is accepted.
                    continue
                self.raise_for_status(polled)
                batch = self._batch_payload(polled.json())

                if waited >= heartbeat:
                    heartbeat += BATCH_HEARTBEAT_SECONDS
                    minutes = int(waited // 60)
                    yield StreamEvent(
                        "status",
                        text=f"Still waiting for batch {batch_id}, {minutes} minutes so far.",
                        # Repeated on a timer, so it is shown rather than spoken.
                        metadata={"batch_id": batch_id, "batch_status": status, "quiet": True},
                    )

                new_status = str(batch.get("status") or "").strip()
                if new_status and new_status != status:
                    status = new_status
                    logger.info("OpenRouter batch id=%s status=%s", batch_id, status)
                    yield StreamEvent(
                        "status",
                        text=batch_status_text(status, batch_id),
                        metadata={"batch_id": batch_id, "batch_status": status},
                    )

        if status.casefold() != "completed":
            raise ProviderError(self._batch_failure_message(batch, batch_id, status))

        text = self._batch_text(batch)
        if not text:
            logger.info(
                "OpenRouter batch id=%s completed without readable text: %s",
                batch_id,
                json.dumps(batch)[:2000],
            )
            raise ProviderError(f"Batch {batch_id} completed without returning any text.")
        yield StreamEvent("text", text=text)
        yield StreamEvent("done")

    @staticmethod
    def _batch_payload(payload: Any) -> dict[str, Any]:
        """Return the batch object itself, wrapped in an envelope or not."""
        if not isinstance(payload, dict):
            raise ProviderError("OpenRouter returned a batch that could not be read.")
        for key in ("batch", "data"):
            inner = payload.get(key)
            if isinstance(inner, dict) and inner.get("id"):
                return inner
        return payload

    @staticmethod
    def _batch_failure_message(batch: dict[str, Any], batch_id: str, status: str) -> str:
        detail = ""
        error = batch.get("error") or batch.get("errors")
        if isinstance(error, dict):
            detail = str(error.get("message") or json.dumps(error, ensure_ascii=False))
        elif isinstance(error, list) and error:
            first = error[0]
            detail = str(first.get("message") if isinstance(first, dict) else first)
        elif error:
            detail = str(error)
        return f"Batch {batch_id} ended with status {status or 'unknown'}. {detail}".strip()

    @classmethod
    def _batch_text(cls, batch: dict[str, Any]) -> str:
        """Pull the assistant's text out of a completed batch."""
        results = batch.get("results")
        if isinstance(results, dict):
            results = results.get("data") or results.get("results") or [results]
        if not isinstance(results, list):
            return ""

        errors: list[str] = []
        texts: list[str] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            error = item.get("error")
            if isinstance(error, dict) and error.get("message"):
                errors.append(str(error["message"]))
            elif isinstance(error, str) and error:
                errors.append(error)
            text = cls._completion_text(item)
            if text:
                texts.append(text)

        if not texts and errors:
            raise ProviderError(f"The batch request failed: {errors[0]}")
        return "\n\n".join(texts)

    @classmethod
    def _completion_text(cls, item: dict[str, Any]) -> str:
        """Read one chat completion out of whichever envelope holds it."""
        for key in ("response", "body", "result"):
            inner = item.get(key)
            if isinstance(inner, dict):
                text = cls._completion_text(inner)
                if text:
                    return text

        choices = item.get("choices")
        if not isinstance(choices, list):
            return ""
        parts: list[str] = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict):
                text = content_to_text(message.get("content"))
                if text:
                    parts.append(text)
        return "".join(parts)
