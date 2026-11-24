from __future__ import annotations

import base64
import codecs
import json
from collections.abc import Callable, Iterator
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


LENGTH_LIMIT_NOTICE = "Response cut off at the model's length limit"

# (base URL, model) pairs that refused Responses reasoning options this run,
# so a model without thinking is not sent them, refused and retried each turn.
_REASONING_REFUSED: set[tuple[str, str]] = set()

# Other files reach a Messages request as base64 only up to this size; past
# it the file is named and left out, rather than sent as garbled text.
MAX_INLINE_BINARY_BYTES = 100 * 1024

_ALLOWED_CONTROLS = frozenset(b"\t\n\f\r")


def attachment_text(attachment: Any) -> str | None:
    """An attachment's bytes as text, or None when they look binary.

    Text arrives in more encodings than UTF-8: a byte order mark names
    UTF-8, UTF-16 or UTF-32 outright, strict UTF-8 covers the modern
    default, and what is left is tried as cp1252, the older Windows
    encoding. Bytes with NULs or a high ratio of control characters are
    not text in any of those, however they decode, so they return None
    instead of mojibake. ``attachment`` is a MessageAttachment; raw bytes
    are accepted too.
    """
    data = bytes(getattr(attachment, "data", attachment) or b"")
    if not data:
        return ""
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        try:
            return data.decode("utf-32")
        except (UnicodeDecodeError, ValueError):
            return None
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            return data.decode("utf-16")
        except (UnicodeDecodeError, ValueError):
            return None
    if data.startswith(codecs.BOM_UTF8):
        try:
            return data.decode("utf-8-sig")
        except (UnicodeDecodeError, ValueError):
            return None
    if b"\x00" in data:
        return None
    # A sample is enough to tell text from binary, and a 100 MB file is
    # not walked byte by byte in Python.
    sample = data[:65536]
    controls = sum(
        1 for byte in sample if (byte < 0x20 and byte not in _ALLOWED_CONTROLS) or byte == 0x7F
    )
    if controls * 10 > len(sample):
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        return None


def _raise_choice_error(choice: dict[str, Any]) -> None:
    """OpenRouter reports a failure part-way through inside the choice itself.

    It arrives as `choices[0].error` with `finish_reason: "error"`, not as the
    top-level error object `iter_sse_json` already raises on. Left alone it
    ended as "completed without returning any text".
    """
    error = choice.get("error")
    if not error:
        return
    if isinstance(error, dict):
        detail = error.get("message") or str(error)
    else:
        detail = str(error)
    raise ProviderError(f"Provider stream error: {detail}")


def attachment_content_part(attachment: MessageAttachment) -> dict[str, Any]:
    encoded = base64.b64encode(attachment.data).decode("ascii")
    mime_type = attachment.mime_type or "application/octet-stream"
    suffix = PurePath(attachment.filename).suffix.lower()

    if mime_type.startswith("text/") or suffix in TEXT_EXTENSIONS:
        text = attachment_text(attachment)
        if text is not None:
            return {"type": "text", "text": f"[Attached file: {attachment.filename}]\n{text}"}
        # Claimed as text but binary underneath: fall through to the file
        # part below, which carries the bytes as base64, not mojibake.
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
        if role == "tool":
            result.append(
                {
                    "role": "tool",
                    "tool_call_id": str(message.get("tool_call_id") or ""),
                    "content": content,
                }
            )
            continue
        if not attachments:
            result.append({"role": role, "content": content})
            continue
        blocks: list[dict[str, Any]] = [
            {"type": "text", "text": content or "Review the attached files."}
        ]
        blocks.extend(attachment_content_part(attachment) for attachment in attachments)
        result.append({"role": role, "content": blocks})
    return result


def messages_content(content: str, attachments: list[Any]) -> list[dict[str, Any]]:
    """A message carrying files, in the Anthropic Messages shape of blocks.

    Anthropic serves images and PDFs as content blocks of their own. Files
    that are neither have no block type on this protocol, so their text is
    sent inline as one more text block, exactly as the Chat Completions path
    sends them. Only called when there is something attached; a plain string
    body stays a plain string, as it always was.
    """
    parts: list[dict[str, Any]] = [
        {"type": "text", "text": content or "Review the attached files."}
    ]
    parts.extend(attachment_source_block(attachment) for attachment in attachments)
    return parts


def attachment_source_block(attachment: MessageAttachment) -> dict[str, Any]:
    """One attachment as a Messages content block."""
    encoded = base64.b64encode(attachment.data).decode("ascii")
    mime_type = attachment.mime_type or "application/octet-stream"
    suffix = PurePath(attachment.filename).suffix.lower()
    if mime_type.startswith("image/"):
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": mime_type, "data": encoded},
        }
    if mime_type == "application/pdf" or suffix == ".pdf":
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": encoded},
        }
    text = attachment_text(attachment)
    if text is not None:
        return {"type": "text", "text": f"[Attached file: {attachment.filename}]\n{text}"}
    # Not an image, not a PDF and not text: never send the bytes decoded
    # as if they were text. Small files travel as base64 in a text block;
    # large ones are named and left out.
    size = len(attachment.data)
    if size <= MAX_INLINE_BINARY_BYTES:
        return {
            "type": "text",
            "text": (
                f"[Attached file: {attachment.filename}, {mime_type}, "
                f"{size} bytes, base64]\n{encoded}"
            ),
        }
    return {
        "type": "text",
        "text": (
            f"[Attached file: {attachment.filename}, {mime_type}, {size} bytes] "
            "This file is binary and too large to include."
        ),
    }


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
        # Assembled calls, in order: {"id", "name", "arguments"}.
        self.calls: list[dict[str, str]] = []

    def absorb(self, payload: dict[str, Any]) -> list[str]:
        """Take in one delta or message. Returns anything newly worth saying."""
        calls = payload.get("tool_calls")
        announcements: list[str] = []
        for index, call in enumerate(calls if isinstance(calls, list) else []):
            if not isinstance(call, dict):
                continue
            key = str(call.get("id") or call.get("index") or index)
            name = self._call_name(call)
            self._collect(key, call, name)
            if not name or key in self._named:
                continue
            self._named[key] = name
            announcements.append(self._announcement(name))
        return announcements

    def _collect(self, key: str, call: dict[str, Any], name: str) -> None:
        """Append one call's id/name/arguments, assembling streamed fragments."""
        function = call.get("function")
        fragment = ""
        if isinstance(function, dict):
            fragment = str(function.get("arguments") or "")
        slot = next((c for c in self.calls if c["id"] == key), None)
        if slot is None:
            self.calls.append({"id": key, "name": name, "arguments": fragment})
        else:
            if name and not slot["name"]:
                slot["name"] = name
            if fragment:
                slot["arguments"] += fragment

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
        spoken = {
            "web_search": "Searching the web",
            "fetch_page": "Reading a web page",
            "current_time": "Checking the date and time",
        }.get(name)
        return spoken or f"The model asked to run {name}"

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
    def tool_executor(
        self, settings: GenerationSettings
    ) -> Callable[[str, dict[str, Any]], str] | None:
        """The local executor for client calls, when the caller set one."""
        return getattr(settings, "tool_executor", None)

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
    ) -> Iterator[StreamEvent]:
        tool_executor = self.tool_executor(settings)
        url = self.build_url(self.account.chat_endpoint)
        headers = self.headers()
        messages = chat_messages(settings.messages)

        def build_body() -> dict[str, Any]:
            body: dict[str, Any] = dict(self.account.custom_body)
            body.update(
                {
                    "model": settings.model,
                    "messages": messages,
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
            return body

        received_text = False
        cut_off = False
        sources = SourceCollector()
        tools = ToolCallCollector()
        for _round in range(30):
            tools = ToolCallCollector()
            body = build_body()
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
                            _raise_choice_error(first)
                            cut_off = cut_off or first.get("finish_reason") == "length"
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
                        _raise_choice_error(choices[0])
                        cut_off = choices[0].get("finish_reason") == "length"
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
            if not (tool_executor is not None and tools.calls and not cancel.is_set()):
                break
            self._run_tools_and_extend(messages, tools, tool_executor)
        listed = sources.listing()
        if listed:
            yield StreamEvent("sources", text=listed, metadata={"sources": sources.as_list()})
        if cut_off:
            # Otherwise a sentence that stops mid-word is announced as complete.
            # Recorded into History so the reason is still there afterwards.
            yield StreamEvent("status", text=LENGTH_LIMIT_NOTICE, metadata={"record": True})
        if not received_text and not cancel.is_set():
            raise ProviderError(self._empty_response_reason(tools, executor=tool_executor))
        yield StreamEvent("done")

    @staticmethod
    def _run_tools_and_extend(
        messages: list[dict[str, Any]],
        tools: ToolCallCollector,
        tool_executor: Callable[[str, dict[str, Any]], str],
    ) -> None:
        """Run one round of client calls and append them to the message list.

        The assistant's tool-calls message goes back with the very calls the
        provider returned, and each result follows as a ``tool`` message, so
        the next request resumes the turn where it left off.
        """
        assembled = [
            {
                "id": call["id"],
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": call["arguments"] or "{}",
                },
            }
            for call in tools.calls
            if call["name"]
        ]
        if not assembled:
            return
        messages.append({"role": "assistant", "content": "", "tool_calls": assembled})
        for call in assembled:
            try:
                arguments = json.loads(call["function"]["arguments"] or "{}")
            except (TypeError, ValueError):
                arguments = {}
            result = tool_executor(call["function"]["name"], arguments)
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": str(result)})

    @staticmethod
    def _empty_response_reason(
        tools: "ToolCallCollector",
        executor: Callable[[str, dict[str, Any]], str] | None = None,
    ) -> str:
        """Why a request that returned no answer returned no answer.

        A model that ends its turn asking for a tool this application does not
        run leaves no text behind, and "returned no text" is a poor account of
        that. Naming the tool says what to do about it — turn on the matching
        OpenRouter tool, which OpenRouter runs itself, or ask something the
        model can answer on its own.
        """
        pending = tools.client_side_names()
        if executor is not None and pending:
            pending = set()
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
    ) -> Iterator[StreamEvent]:
        # The ChatGPT plan's input builder: system text becomes instructions and
        # attachments become input_image / input_file / input_text parts.
        from accessible_ai.providers.chatgpt import responses_input

        tool_executor = self.tool_executor(settings)
        url = self.build_url(self.account.responses_endpoint)
        headers = self.headers()
        instructions, items = responses_input(settings.messages)
        tools = responses_tools(settings.tools)
        refusal_key = (self.account.base_url, settings.model)
        reasoning = None if refusal_key in _REASONING_REFUSED else settings.reasoning

        def build_body() -> dict[str, Any]:
            body: dict[str, Any] = dict(self.account.custom_body)
            body.update(
                {
                    "model": settings.model,
                    "input": items,
                    "stream": bool(settings.streaming),
                }
            )
            if instructions:
                body["instructions"] = instructions
            if settings.temperature is not None:
                body["temperature"] = settings.temperature
            if settings.max_output_tokens is not None:
                body["max_output_tokens"] = settings.max_output_tokens
            if reasoning:
                body["reasoning"] = reasoning
            if tools:
                body["tools"] = tools
            return body

        received_text = False
        named = ToolCallCollector()
        for _round in range(30):
            calls: list[dict[str, Any]] = []
            round_text: list[str] = []
            try:
                for event in self._responses_round(url, headers, build_body(), settings, cancel):
                    if event.kind == "_call":
                        calls.append(event.metadata)
                        continue
                    if event.kind == "text":
                        received_text = True
                        round_text.append(event.text)
                    elif event.kind == "tool":
                        for announcement in named.absorb({"tool_calls": [event.metadata]}):
                            yield StreamEvent("tool", text=announcement)
                        continue
                    yield event
            except ProviderError as exc:
                # Asked-for thinking that the model or organization refuses
                # (gpt-4o takes no reasoning; an unverified organization gets
                # no summaries) is dropped rather than failing the turn.
                refused = str(exc).lower()
                if (
                    reasoning
                    and exc.status_code == 400
                    and not received_text
                    and ("reasoning" in refused or "summary" in refused)
                ):
                    reasoning = None
                    _REASONING_REFUSED.add(refusal_key)
                    yield StreamEvent(
                        "status",
                        text="This model does not take thinking settings, so it answered without them.",
                    )
                    continue
                raise
            if not (tool_executor is not None and calls and not cancel.is_set()):
                break
            if round_text:
                items.append({"role": "assistant", "content": "".join(round_text)})
            for call in calls:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call["call_id"],
                        "name": call["name"],
                        "arguments": call["arguments"] or "{}",
                    }
                )
            for call in calls:
                try:
                    arguments = json.loads(call["arguments"] or "{}")
                except (TypeError, ValueError):
                    arguments = {}
                result = tool_executor(
                    call["name"], arguments if isinstance(arguments, dict) else {}
                )
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": str(result),
                    }
                )
        if not received_text and not cancel.is_set():
            raise ProviderError(self._empty_response_reason(named, executor=tool_executor))
        yield StreamEvent("done")

    def _responses_round(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        settings: GenerationSettings,
        cancel: Event,
    ) -> Iterator[StreamEvent]:
        """One Responses request. Function calls come out as ``_call`` events."""
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
                                yield StreamEvent("text", text=delta)
                        elif event_type == "response.reasoning_summary_text.delta":
                            delta = data.get("delta")
                            if isinstance(delta, str) and delta:
                                yield StreamEvent("reasoning", text=delta)
                        elif event_type == "response.output_item.added":
                            item = data.get("item")
                            if isinstance(item, dict) and item.get("type") == "function_call":
                                yield StreamEvent(
                                    "tool",
                                    metadata={
                                        "id": str(item.get("call_id") or item.get("id") or ""),
                                        "name": str(item.get("name") or ""),
                                    },
                                )
                        elif event_type == "response.output_item.done":
                            item = data.get("item")
                            if isinstance(item, dict) and item.get("type") == "function_call":
                                yield StreamEvent("_call", metadata=_responses_call(item))
                        elif event_type in {"response.failed", "error"}:
                            response_data = data.get("response")
                            error = (
                                response_data.get("error")
                                if isinstance(response_data, dict)
                                else data.get("error") or data
                            )
                            detail = (
                                error.get("message")
                                if isinstance(error, dict)
                                else str(error or "")
                            )
                            raise ProviderError(
                                detail or "The provider failed to complete the response."
                            )
            else:
                response = client.post(url, headers=headers, json=body)
                self.raise_for_status(response)
                yield StreamEvent("headers", metadata=dict(response.headers))
                payload = response.json()
                output = payload.get("output") if isinstance(payload, dict) else None
                for item in output if isinstance(output, list) else []:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "reasoning":
                        summary = "".join(
                            str(part.get("text") or "")
                            for part in item.get("summary") or []
                            if isinstance(part, dict)
                        )
                        if summary:
                            yield StreamEvent("reasoning", text=summary)
                    elif item.get("type") == "function_call":
                        call = _responses_call(item)
                        yield StreamEvent(
                            "tool", metadata={"id": call["call_id"], "name": call["name"]}
                        )
                        yield StreamEvent("_call", metadata=call)
                text = self._responses_text(payload)
                if text:
                    yield StreamEvent("text", text=text)

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
    ) -> Iterator[StreamEvent]:
        tool_executor = self.tool_executor(settings)
        url = self.build_url(self.account.messages_endpoint)
        headers = self.headers()
        headers.setdefault("anthropic-version", "2023-06-01")

        system_parts: list[str] = []
        messages: list[dict[str, Any]] = []
        for message in settings.messages:
            role = message.get("role", "user")
            content = str(message.get("content", ""))
            attachments = message.get("attachments") or []
            if role in {"system", "developer"}:
                system_parts.append(content)
            elif role in {"user", "assistant"}:
                body_content: Any = content
                if attachments:
                    body_content = messages_content(content, attachments)
                messages.append({"role": role, "content": body_content})
        tools = messages_tools(settings.tools)

        def build_body() -> dict[str, Any]:
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
            if tools:
                body["tools"] = tools
            return body

        received_text = False
        cut_off = False
        named = ToolCallCollector()
        for _round in range(30):
            # Blocks of this round's answer by index: text and tool_use, the
            # latter with its input JSON assembled from partial deltas.
            blocks: dict[int, dict[str, Any]] = {}
            with self.client() as client:
                if settings.streaming:
                    with client.stream("POST", url, headers=headers, json=build_body()) as response:
                        self.raise_for_status(response)
                        yield StreamEvent("headers", metadata=dict(response.headers))
                        for data in iter_sse_json(response, cancel):
                            kind = data.get("type")
                            if kind == "error":
                                error = data.get("error")
                                detail = error.get("message") if isinstance(error, dict) else error
                                raise ProviderError(f"Provider stream error: {detail or data}")
                            if kind == "message_delta":
                                delta = data.get("delta")
                                if (
                                    isinstance(delta, dict)
                                    and delta.get("stop_reason") == "max_tokens"
                                ):
                                    cut_off = True
                                continue
                            index = data.get("index", 0)
                            if not isinstance(index, int):
                                continue
                            if kind == "content_block_start":
                                block = data.get("content_block")
                                if isinstance(block, dict) and block.get("type") == "tool_use":
                                    blocks[index] = {
                                        "type": "tool_use",
                                        "id": str(block.get("id") or ""),
                                        "name": str(block.get("name") or ""),
                                        "json": "",
                                    }
                                    for announcement in named.absorb(
                                        {"tool_calls": [blocks[index]]}
                                    ):
                                        yield StreamEvent("tool", text=announcement)
                                continue
                            if kind != "content_block_delta":
                                continue
                            delta = data.get("delta")
                            if not isinstance(delta, dict):
                                continue
                            if delta.get("type") == "input_json_delta" and index in blocks:
                                blocks[index]["json"] += str(delta.get("partial_json") or "")
                                continue
                            thinking = delta.get("thinking")
                            if isinstance(thinking, str) and thinking:
                                yield StreamEvent("reasoning", text=thinking)
                            text = delta.get("text")
                            if isinstance(text, str) and text:
                                received_text = True
                                slot = blocks.setdefault(index, {"type": "text", "text": ""})
                                slot["text"] = slot.get("text", "") + text
                                yield StreamEvent("text", text=text)
                else:
                    response = client.post(url, headers=headers, json=build_body())
                    self.raise_for_status(response)
                    yield StreamEvent("headers", metadata=dict(response.headers))
                    payload = response.json()
                    content = payload.get("content", []) if isinstance(payload, dict) else []
                    cut_off = (
                        isinstance(payload, dict) and payload.get("stop_reason") == "max_tokens"
                    )
                    for index, block in enumerate(content if isinstance(content, list) else []):
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            blocks[index] = {
                                "type": "tool_use",
                                "id": str(block.get("id") or ""),
                                "name": str(block.get("name") or ""),
                                "json": json.dumps(block.get("input") or {}),
                            }
                            for announcement in named.absorb({"tool_calls": [blocks[index]]}):
                                yield StreamEvent("tool", text=announcement)
                    text = content_to_text(content)
                    if text:
                        received_text = True
                        blocks[-1] = {"type": "text", "text": text}
                        yield StreamEvent("text", text=text)
            calls = [block for block in blocks.values() if block["type"] == "tool_use"]
            if not (tool_executor is not None and calls and not cancel.is_set()):
                break
            assistant: list[dict[str, Any]] = []
            results: list[dict[str, Any]] = []
            for index in sorted(blocks):
                block = blocks[index]
                if block["type"] == "text":
                    if block.get("text"):
                        assistant.append({"type": "text", "text": block["text"]})
                    continue
                try:
                    arguments = json.loads(block["json"] or "{}")
                except (TypeError, ValueError):
                    arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}
                assistant.append(
                    {
                        "type": "tool_use",
                        "id": block["id"],
                        "name": block["name"],
                        "input": arguments,
                    }
                )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": str(tool_executor(block["name"], arguments)),
                    }
                )
            messages.append({"role": "assistant", "content": assistant})
            messages.append({"role": "user", "content": results})
        if cut_off:
            yield StreamEvent("status", text=LENGTH_LIMIT_NOTICE, metadata={"record": True})
        if not received_text and not cancel.is_set():
            raise ProviderError(self._empty_response_reason(named, executor=tool_executor))
        yield StreamEvent("done")


def responses_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chat Completions function tools in the Responses shape; others as given."""
    converted: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function") if tool.get("type") == "function" else None
        if isinstance(function, dict):
            converted.append({"type": "function", **function})
        else:
            converted.append(tool)
    return converted


def messages_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chat Completions function tools in the Anthropic Messages shape.

    Anything else (OpenRouter's server tools) has no Messages form and is
    left out rather than failing the request.
    """
    converted: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function") if tool.get("type") == "function" else None
        if isinstance(function, dict) and function.get("name"):
            converted.append(
                {
                    "name": function["name"],
                    "description": str(function.get("description") or ""),
                    "input_schema": function.get("parameters")
                    or {"type": "object", "properties": {}},
                }
            )
    return converted


def _responses_call(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "call_id": str(item.get("call_id") or item.get("id") or ""),
        "name": str(item.get("name") or ""),
        "arguments": str(item.get("arguments") or ""),
    }
