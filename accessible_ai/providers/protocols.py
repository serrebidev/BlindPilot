from __future__ import annotations

import base64
from collections.abc import Iterator
from pathlib import PurePath
from threading import Event
from typing import Any

from accessible_ai.models import GenerationSettings, MessageAttachment, StreamEvent
from accessible_ai.providers.base import BaseProvider, ProviderError, content_to_text, iter_sse_json


TEXT_EXTENSIONS = {
    ".bat",
    ".c",
    ".cfg",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".csv",
    ".env",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".log",
    ".md",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


def attachment_content_part(attachment: MessageAttachment) -> dict[str, Any]:
    encoded = base64.b64encode(attachment.data).decode("ascii")
    mime_type = attachment.mime_type or "application/octet-stream"
    suffix = PurePath(attachment.filename).suffix.lower()

    if mime_type.startswith("text/") or suffix in TEXT_EXTENSIONS:
        text = attachment.data.decode("utf-8", errors="replace")
        return {"type": "text", "text": f"[Attached file: {attachment.filename}]\n{text}"}
    if mime_type.startswith("image/"):
        return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}}
    if mime_type.startswith("audio/"):
        audio_format = suffix.lstrip(".") or mime_type.split("/", 1)[-1]
        audio_format = {"mpeg": "mp3", "x-wav": "wav"}.get(audio_format, audio_format)
        return {"type": "input_audio", "input_audio": {"data": encoded, "format": audio_format}}
    if mime_type.startswith("video/"):
        return {"type": "video_url", "video_url": {"url": f"data:{mime_type};base64,{encoded}"}}
    return {
        "type": "file",
        "file": {
            "filename": attachment.filename,
            "file_data": f"data:{mime_type};base64,{encoded}",
        },
    }


def chat_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role", "user"))
        content = str(message.get("content", ""))
        attachments = message.get("attachments") or []
        if not attachments:
            result.append({"role": role, "content": content})
            continue
        blocks: list[dict[str, Any]] = [
            {"type": "text", "text": content or "Review the attached files."}
        ]
        blocks.extend(attachment_content_part(attachment) for attachment in attachments)
        result.append({"role": role, "content": blocks})
    return result


class ProtocolMixin(BaseProvider):
    def list_models_from_endpoint(self) -> list[str]:
        url = self.build_url(self.account.models_endpoint)
        with self.client() as client:
            response = client.get(url, headers=self.headers())
            self.raise_for_status(response)
            payload = response.json()
        data = payload.get("data", []) if isinstance(payload, dict) else []
        models: list[str] = []
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                models.append(item["id"])
            elif isinstance(item, str):
                models.append(item)
        return sorted(set(models), key=str.casefold)

    def generate_chat_completions(
        self,
        settings: GenerationSettings,
        cancel: Event,
        endpoint: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Iterator[StreamEvent]:
        url = self.build_url(endpoint or self.account.chat_endpoint)
        headers = self.headers()
        if extra_headers:
            headers.update(extra_headers)
        body: dict[str, Any] = dict(self.account.custom_body)
        body.update(
            {
                "model": settings.model,
                "messages": chat_messages(settings.messages),
                "stream": bool(settings.streaming),
            }
        )
        if settings.temperature is not None:
            body["temperature"] = settings.temperature
        if settings.max_output_tokens is not None:
            body["max_tokens"] = settings.max_output_tokens

        received_text = False
        with self.client() as client:
            if settings.streaming:
                with client.stream("POST", url, headers=headers, json=body) as response:
                    self.raise_for_status(response)
                    yield StreamEvent("headers", metadata=dict(response.headers))
                    for data in iter_sse_json(response, cancel):
                        choices = data.get("choices")
                        if not isinstance(choices, list) or not choices:
                            continue
                        first = choices[0]
                        if not isinstance(first, dict):
                            continue
                        delta = first.get("delta", {})
                        if isinstance(delta, dict):
                            text = content_to_text(delta.get("content"))
                            if text:
                                received_text = True
                                yield StreamEvent("text", text=text)
            else:
                response = client.post(url, headers=headers, json=body)
                self.raise_for_status(response)
                yield StreamEvent("headers", metadata=dict(response.headers))
                data = response.json()
                choices = data.get("choices", []) if isinstance(data, dict) else []
                if choices and isinstance(choices[0], dict):
                    message = choices[0].get("message", {})
                    if isinstance(message, dict):
                        text = content_to_text(message.get("content"))
                        if text:
                            received_text = True
                            yield StreamEvent("text", text=text)
        if not received_text and not cancel.is_set():
            raise ProviderError("The provider completed the request without returning any text.")
        yield StreamEvent("done")

    def generate_responses(
        self,
        settings: GenerationSettings,
        cancel: Event,
        endpoint: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Iterator[StreamEvent]:
        if any(message.get("attachments") for message in settings.messages):
            raise ProviderError("File attachments currently require the Chat Completions API mode.")
        url = self.build_url(endpoint or self.account.responses_endpoint)
        headers = self.headers()
        if extra_headers:
            headers.update(extra_headers)
        body: dict[str, Any] = dict(self.account.custom_body)
        body.update(
            {
                "model": settings.model,
                "input": settings.messages,
                "stream": bool(settings.streaming),
            }
        )
        if settings.temperature is not None:
            body["temperature"] = settings.temperature
        if settings.max_output_tokens is not None:
            body["max_output_tokens"] = settings.max_output_tokens

        received_text = False
        with self.client() as client:
            if settings.streaming:
                with client.stream("POST", url, headers=headers, json=body) as response:
                    self.raise_for_status(response)
                    yield StreamEvent("headers", metadata=dict(response.headers))
                    for data in iter_sse_json(response, cancel):
                        event_type = data.get("type")
                        if event_type == "response.output_text.delta":
                            delta = data.get("delta")
                            if isinstance(delta, str) and delta:
                                received_text = True
                                yield StreamEvent("text", text=delta)
            else:
                response = client.post(url, headers=headers, json=body)
                self.raise_for_status(response)
                yield StreamEvent("headers", metadata=dict(response.headers))
                payload = response.json()
                text = self._responses_text(payload)
                if text:
                    received_text = True
                    yield StreamEvent("text", text=text)
        if not received_text and not cancel.is_set():
            raise ProviderError("The provider completed the request without returning any text.")
        yield StreamEvent("done")

    @staticmethod
    def _responses_text(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        direct = payload.get("output_text")
        if isinstance(direct, str):
            return direct
        parts: list[str] = []
        output = payload.get("output", [])
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content", [])
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") in {"output_text", "text"}:
                        text = block.get("text")
                        if isinstance(text, str):
                            parts.append(text)
        return "".join(parts)

    def generate_messages(
        self,
        settings: GenerationSettings,
        cancel: Event,
        endpoint: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Iterator[StreamEvent]:
        if any(message.get("attachments") for message in settings.messages):
            raise ProviderError("File attachments currently require the Chat Completions API mode.")
        url = self.build_url(endpoint or self.account.messages_endpoint)
        headers = self.headers()
        headers.setdefault("anthropic-version", "2023-06-01")
        if extra_headers:
            headers.update(extra_headers)

        system_parts: list[str] = []
        messages: list[dict[str, str]] = []
        for message in settings.messages:
            role = message.get("role", "user")
            content = str(message.get("content", ""))
            if role in {"system", "developer"}:
                system_parts.append(content)
            elif role in {"user", "assistant"}:
                messages.append({"role": role, "content": content})

        body: dict[str, Any] = dict(self.account.custom_body)
        body.update(
            {
                "model": settings.model,
                "messages": messages,
                "max_tokens": settings.max_output_tokens or 4096,
                "stream": bool(settings.streaming),
            }
        )
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if settings.temperature is not None:
            body["temperature"] = settings.temperature

        received_text = False
        with self.client() as client:
            if settings.streaming:
                with client.stream("POST", url, headers=headers, json=body) as response:
                    self.raise_for_status(response)
                    yield StreamEvent("headers", metadata=dict(response.headers))
                    for data in iter_sse_json(response, cancel):
                        if data.get("type") != "content_block_delta":
                            continue
                        delta = data.get("delta")
                        if isinstance(delta, dict):
                            text = delta.get("text")
                            if isinstance(text, str) and text:
                                received_text = True
                                yield StreamEvent("text", text=text)
            else:
                response = client.post(url, headers=headers, json=body)
                self.raise_for_status(response)
                yield StreamEvent("headers", metadata=dict(response.headers))
                payload = response.json()
                content = payload.get("content", []) if isinstance(payload, dict) else []
                text = content_to_text(content)
                if text:
                    received_text = True
                    yield StreamEvent("text", text=text)
        if not received_text and not cancel.is_set():
            raise ProviderError("The provider completed the request without returning any text.")
        yield StreamEvent("done")

    def unsupported_mode(self, mode: str) -> Iterator[StreamEvent]:
        raise ProviderError(f"Unsupported API mode: {mode}")
        yield StreamEvent("done")
