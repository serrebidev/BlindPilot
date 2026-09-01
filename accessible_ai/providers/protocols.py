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


def reasoning_text(payload: dict[str, Any]) -> str:
    """The thinking in one delta or one finished message, as plain text.

    Providers write this two ways: a bare ``reasoning`` string, and the newer
    ``reasoning_details`` list, whose entries are the thinking proper, a
    summary of it, or an encrypted block that has no readable text at all.
    Both are read, because a single response can carry either.
    """
    parts: list[str] = []
    direct = payload.get("reasoning")
    if isinstance(direct, str) and direct:
        parts.append(direct)
    details = payload.get("reasoning_details")
    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, dict):
            continue
        # An encrypted block is the model's thinking sealed for the provider to
        # read back on the next turn. There is nothing in it to show.
        if str(detail.get("type") or "") == "reasoning.encrypted":
            continue
        text = detail.get("text") or detail.get("summary")
        if isinstance(text, str) and text:
            parts.append(text)
    return "".join(parts)


class ToolCallCollector:
    """Follows the tool calls a response makes, so they can be spoken.

    A tool call is streamed a piece at a time: the first delta names it, and
    later ones add to its arguments. Only the name is wanted here, and only
    once, so a run of deltas for one call produces a single announcement.

    OpenRouter's own tools -- the ones it runs itself -- are told apart from
    ordinary function calls by their ``openrouter:`` prefix. That distinction
    is what decides whether a turn ending in a tool call is normal or is a
    request this application cannot serve.
    """

    SERVER_PREFIX = "openrouter:"

    def __init__(self) -> None:
        self._named: dict[str, str] = {}

    def absorb(self, payload: dict[str, Any]) -> list[str]:
        """Take in one delta or message. Returns anything newly worth saying."""
        calls = payload.get("tool_calls")
        announcements: list[str] = []
        for index, call in enumerate(calls if isinstance(calls, list) else []):
            if not isinstance(call, dict):
                continue
            key = str(call.get("id") or call.get("index") or index)
            name = self._call_name(call)
            if not name or key in self._named:
                continue
            self._named[key] = name
            announcements.append(self._announcement(name))
        return announcements

    @staticmethod
    def _call_name(call: dict[str, Any]) -> str:
        function = call.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str) and name:
                return name
        name = call.get("name") or call.get("type")
        return str(name) if isinstance(name, str) and name else ""

    @classmethod
    def _announcement(cls, name: str) -> str:
        if name.startswith(cls.SERVER_PREFIX):
            spoken = name[len(cls.SERVER_PREFIX) :].replace("experimental__", "")
            return f"Running {spoken.replace('_', ' ')}"
        return f"The model asked to run {name}"

    def client_side_names(self) -> set[str]:
        """Names of calls this application would have had to run itself."""
        return {name for name in self._named.values() if not name.startswith(self.SERVER_PREFIX)}


class SourceCollector:
    """Gathers the pages a searching answer cites, in the order first seen.

    Citations come back as ``annotations``, which a streamed response repeats
    across deltas, so the same page arrives many times. They are collected
    rather than reported as they arrive: a list of sources is worth reading at
    the end of an answer, and worth reading once.
    """

    def __init__(self) -> None:
        self._by_url: dict[str, str] = {}

    def absorb(self, payload: dict[str, Any]) -> None:
        annotations = payload.get("annotations")
        for annotation in annotations if isinstance(annotations, list) else []:
            if not isinstance(annotation, dict):
                continue
            citation = annotation.get("url_citation")
            if not isinstance(citation, dict):
                # Some providers flatten the citation into the annotation.
                citation = annotation if annotation.get("url") else None
            if not isinstance(citation, dict):
                continue
            url = str(citation.get("url") or "").strip()
            if not url or url in self._by_url:
                continue
            self._by_url[url] = str(citation.get("title") or "").strip()

    def as_list(self) -> list[dict[str, str]]:
        return [{"url": url, "title": title} for url, title in self._by_url.items()]

    def listing(self) -> str:
        """The sources as numbered lines, or "" when nothing was cited.

        Written as a heading and one line per source so it can be read line by
        line, with the title first because that is the part worth hearing --
        an address read out character by character is not.
        """
        if not self._by_url:
            return ""
        lines = [f"Sources ({len(self._by_url)}):"]
        for number, (url, title) in enumerate(self._by_url.items(), start=1):
            lines.append(f"{number}. {title or url}" + (f" - {url}" if title else ""))
        return "\n".join(lines)


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
        # Only sent when something asked for them, so a provider that rejects
        # a key it does not know never sees one.
        if settings.reasoning:
            body["reasoning"] = settings.reasoning
        if settings.tools:
            body["tools"] = settings.tools
        if settings.plugins:
            body["plugins"] = settings.plugins

        received_text = False
        sources = SourceCollector()
        tools = ToolCallCollector()
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
                        if not isinstance(delta, dict):
                            continue
                        # The thinking arrives on its own key, ahead of and
                        # interleaved with the answer, so it is reported as its
                        # own kind rather than mixed into the response text.
                        thinking = reasoning_text(delta)
                        if thinking:
                            yield StreamEvent("reasoning", text=thinking)
                        for announcement in tools.absorb(delta):
                            yield StreamEvent("tool", text=announcement)
                        sources.absorb(delta)
                        sources.absorb(first)
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
                        thinking = reasoning_text(message)
                        if thinking:
                            yield StreamEvent("reasoning", text=thinking)
                        for announcement in tools.absorb(message):
                            yield StreamEvent("tool", text=announcement)
                        sources.absorb(message)
                        sources.absorb(choices[0])
                        text = content_to_text(message.get("content"))
                        if text:
                            received_text = True
                            yield StreamEvent("text", text=text)
        listed = sources.listing()
        if listed:
            yield StreamEvent("sources", text=listed, metadata={"sources": sources.as_list()})
        if not received_text and not cancel.is_set():
            raise ProviderError(self._empty_response_reason(tools))
        yield StreamEvent("done")

    @staticmethod
    def _empty_response_reason(tools: "ToolCallCollector") -> str:
        """Why a request that returned no answer returned no answer.

        A model that ends its turn asking for a tool this application does not
        run leaves no text behind, and "returned no text" is a poor account of
        that. Naming the tool says what to do about it — turn on the matching
        OpenRouter tool, which OpenRouter runs itself, or ask something the
        model can answer on its own.
        """
        pending = tools.client_side_names()
        if pending:
            return (
                "The model ended its turn asking to run "
                + ", ".join(sorted(pending))
                + ", which this chat window does not run. Turn on the matching OpenRouter tool "
                "in the conversation profile, and OpenRouter will run it instead."
            )
        return "The provider completed the request without returning any text."

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
