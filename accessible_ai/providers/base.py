from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Iterator
from threading import Event
from typing import Any
from urllib.parse import urljoin

import httpx

from accessible_ai.models import Account, GenerationSettings, StreamEvent
from accessible_ai.storage.credentials import CredentialStore


class ProviderError(RuntimeError):
    pass


class BaseProvider(ABC):
    def __init__(self, account: Account, credentials: CredentialStore):
        if account.id is None:
            raise ValueError("Account must be saved before it can be used")
        self.account = account
        self.credentials = credentials

    def api_key(self) -> str:
        key = self.credentials.get_api_key(int(self.account.id)).strip()
        if not key:
            raise ProviderError(f"No API key is stored for account '{self.account.name}'.")
        return key

    def build_url(self, endpoint: str) -> str:
        endpoint = endpoint.strip()
        if endpoint.lower().startswith(("http://", "https://")):
            return endpoint
        base = self.account.base_url.rstrip("/") + "/"
        return urljoin(base, endpoint.lstrip("/"))

    def headers(self) -> dict[str, str]:
        headers = {str(k): str(v) for k, v in self.account.custom_headers.items()}
        headers["Authorization"] = f"Bearer {self.api_key()}"
        headers["Content-Type"] = "application/json"
        return headers

    def client(self) -> httpx.Client:
        return httpx.Client(
            timeout=httpx.Timeout(self.account.timeout_seconds),
            follow_redirects=True,
        )

    @staticmethod
    def raise_for_status(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        # Responses opened through client.stream() do not buffer their body.
        # Load failed responses before extracting the provider's error message.
        try:
            response.read()
        except Exception:
            # The HTTP status and reason phrase still provide a useful error if
            # the server closes the response before its body can be read.
            pass
        try:
            data = response.json()
            # Gemini wraps its error object in a single-element array. Unwrap it
            # so the user reads the provider's sentence, not a dumped list.
            if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
                data = data[0]
            if isinstance(data, dict):
                error = data.get("error")
                if isinstance(error, dict):
                    detail = error.get("message") or json.dumps(error, ensure_ascii=False)
                elif isinstance(error, str) and error:
                    detail = error
                else:
                    detail = str(data.get("message") or error or data)
            else:
                detail = str(data)
        except Exception:
            try:
                detail = response.text.strip()
            except Exception:
                detail = ""
            detail = detail or response.reason_phrase
        raise ProviderError(f"HTTP {response.status_code}: {detail}")

    @abstractmethod
    def list_models(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def generate(self, settings: GenerationSettings, cancel: Event) -> Iterator[StreamEvent]:
        raise NotImplementedError


def iter_sse_json(response: httpx.Response, cancel: Event) -> Iterator[dict[str, Any]]:
    for raw_line in response.iter_lines():
        if cancel.is_set():
            return
        line = raw_line.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload == "[DONE]":
                return
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                error = parsed.get("error")
                if error:
                    if isinstance(error, dict):
                        detail = error.get("message") or json.dumps(error, ensure_ascii=False)
                    else:
                        detail = str(error)
                    raise ProviderError(f"Provider stream error: {detail}")
                yield parsed


def content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)
