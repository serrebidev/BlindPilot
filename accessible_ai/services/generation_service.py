from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from threading import Event

import httpx

from accessible_ai import web_tools
from accessible_ai.models import Account, GenerationSettings, StreamEvent
from accessible_ai.providers.base import ProviderError
from accessible_ai.providers.factory import create_provider
from accessible_ai.storage.credentials import CredentialStore


class GenerationService:
    def __init__(self, credentials: CredentialStore):
        self.credentials = credentials

    def generate(
        self,
        account: Account,
        settings: GenerationSettings,
        cancel: Event,
        tool_executor=None,
    ) -> Iterator[StreamEvent]:
        if settings.tools_disabled:
            account = replace(account, custom_body={})
            settings = replace(settings, tools=[], plugins=[], tool_executor=None)
        if tool_executor is not None:
            settings = replace(settings, tool_executor=tool_executor)
        provider = create_provider(account, self.credentials)
        started = False
        for attempt in range(3):
            if cancel.is_set():
                return
            try:
                for event in provider.generate(settings, cancel):
                    if cancel.is_set():
                        return
                    if event.metadata.get("batch_id") or event.metadata.get("record"):
                        started = True
                    if event.kind not in {"status", "headers"} and (
                        event.text or event.kind == "tool"
                    ):
                        started = True
                    yield event
                return
            except (ProviderError, httpx.ConnectError, httpx.ConnectTimeout) as exc:
                web = [tool for tool in settings.tools if _is_web_tool(tool)]
                if (
                    isinstance(exc, ProviderError)
                    and not started
                    and web
                    and exc.status_code in {400, 404, 422}
                    and "tool" in str(exc).lower()
                ):
                    # A model that takes no tools (many local Ollama models)
                    # still answers, just without looking anything up.
                    settings = replace(
                        settings, tools=[t for t in settings.tools if not _is_web_tool(t)]
                    )
                    yield StreamEvent(
                        "status",
                        "This model does not take tools, so it answered without web search.",
                    )
                    continue
                if started or attempt == 2:
                    raise
                delay = float(attempt + 1)
                if isinstance(exc, ProviderError):
                    if exc.status_code not in {408, 429, 500, 502, 503, 504}:
                        raise
                    if exc.retry_after is not None and exc.retry_after >= 0:
                        delay = min(30.0, max(delay, exc.retry_after))
                yield StreamEvent(
                    "status",
                    f"Service unavailable. Retrying in {delay:g} seconds, retry {attempt + 1} of 2.",
                )
                if cancel.wait(delay):
                    return


def _is_web_tool(tool: dict) -> bool:
    return (tool.get("function") or {}).get("name") in web_tools.NAMES
