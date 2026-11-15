"""Client-side tool calls run locally and resume the turn."""

from __future__ import annotations

import pytest

from accessible_ai.models import Account, GenerationSettings
from accessible_ai.providers.chat_completions import ChatCompletionsProvider
from accessible_ai.storage.credentials import CredentialStore


class FakeCredentials(CredentialStore):
    def get_api_key(self, account_id: int) -> str:
        return "k"


class FakeResponse:
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    """One non-streaming request: a tool call, then text after the result."""

    def __init__(self, messages_seen):
        self._messages_seen = messages_seen

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, headers=None, json=None):
        messages = (json or {}).get("messages", [])
        self._messages_seen.append(messages)
        if any(message.get("role") == "tool" for message in messages):
            return FakeResponse(
                {"choices": [{"message": {"content": "Done."}, "finish_reason": "stop"}]}
            )
        return FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "list_controls",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )


def _account():
    return Account(id=1, name="A", provider="openai", base_url="https://api.example.test/v1")


@pytest.fixture()
def provider(monkeypatch):
    import accessible_ai.providers.base as base

    messages_seen: list = []
    monkeypatch.setattr(base.httpx, "Client", lambda **kwargs: FakeClient(messages_seen))
    provider = ChatCompletionsProvider(_account(), FakeCredentials())
    return provider, messages_seen


def test_tool_call_runs_locally_and_resumes(provider):
    from threading import Event

    provider, messages_seen = provider
    ran = []

    def executor(name, args):
        ran.append((name, args))
        return "1. Document"

    settings = GenerationSettings(
        model="m",
        messages=[{"role": "user", "content": "look"}],
        tool_executor=executor,
        streaming=False,
    )
    events = list(provider.generate(settings, Event()))
    assert ran == [("list_controls", {})]
    assert [e.text for e in events if e.kind == "text"] == ["Done."]
    # The second request carried the assistant's call and the tool result.
    second = messages_seen[1]
    assert any(m.get("role") == "tool" for m in second)
    assert any(m.get("role") == "assistant" and m.get("tool_calls") for m in second)


def test_executor_bound_stops_the_loop(provider):
    from threading import Event

    provider, messages_seen = provider
    count = []

    def executor(name, args):
        count.append(name)
        return "ok"

    # The fake always calls the tool, so only the round cap ends the loop.
    settings = GenerationSettings(
        model="m",
        messages=[{"role": "user", "content": "loop"}],
        tool_executor=executor,
        streaming=False,
    )
    events = list(provider.generate(settings, Event()))
    assert len(messages_seen) <= 30
    assert events[-1].kind == "done"


def test_cancelled_turn_does_not_execute(provider):
    from threading import Event

    provider, messages_seen = provider
    ran = []
    cancel = Event()
    cancel.set()

    def executor(name, args):
        ran.append(name)
        return "ok"

    settings = GenerationSettings(
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        tool_executor=executor,
        streaming=False,
    )
    list(provider.generate(settings, cancel))
    assert ran == []
