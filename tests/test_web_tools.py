"""BlindPilot's own web tools, and the tool loops that run them.

Accounts with no tools of their own (OpenAI with a key, Claude, Gemini,
Ollama, a custom endpoint) get web_search, fetch_page and current_time. Each
protocol has to carry a call out and its result back: Chat Completions as
tool messages, Messages as tool_use and tool_result blocks, Responses as
function_call and function_call_output items.
"""

from __future__ import annotations

import json
from threading import Event

import httpx
import pytest

from accessible_ai import web_tools
from accessible_ai.models import (
    Account,
    GenerationSettings,
    MessageAttachment,
    OpenRouterFeatures,
    PROVIDER_CLAUDE,
    PROVIDER_GEMINI,
    PROVIDER_OPENAI,
)
from accessible_ai.providers import protocols
from accessible_ai.providers.anthropic_provider import ClaudeProvider
from accessible_ai.providers.base import ProviderError
from accessible_ai.providers.chat_completions import GeminiProvider
from accessible_ai.providers.openai_provider import OpenAIProvider
from accessible_ai.services.generation_service import GenerationService


def _sse(events: list[dict]) -> str:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events)


def _wire(monkeypatch, provider, answers: list[str], bodies: list[dict]) -> None:
    """Each request gets the next canned stream; every body is kept."""

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, text=answers[len(bodies) - 1])

    monkeypatch.setattr(provider, "headers", dict)
    monkeypatch.setattr(
        provider, "client", lambda: httpx.Client(transport=httpx.MockTransport(handle))
    )


def _settings(executor, tools=None) -> GenerationSettings:
    return GenerationSettings(
        model="m",
        messages=[{"role": "user", "content": "what is new"}],
        tools=list(web_tools.DEFINITIONS if tools is None else tools),
        tool_executor=executor,
    )


RESULTS_PAGE = """
<div class="result"><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Ad</a></div>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwxpython.org%2F&amp;rut=x">wx<b>Python</b></a>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=x">The <b>GUI</b> toolkit</a>
</div>
"""


def test_a_search_reads_duckduckgo_results_without_ads(monkeypatch):
    """Titles, the real address behind the redirect, and the snippet."""
    monkeypatch.setattr(
        web_tools.httpx, "get", lambda *a, **k: httpx.Response(200, text=RESULTS_PAGE)
    )
    text = web_tools.web_search("wxpython")
    assert "1. wxPython\nhttps://wxpython.org/\nThe GUI toolkit" in text
    assert "Ad" not in text


def test_a_rate_limited_search_says_so(monkeypatch):
    monkeypatch.setattr(web_tools.httpx, "get", lambda *a, **k: httpx.Response(202, text=""))
    assert "HTTP 202" in web_tools.web_search("x")


def test_the_executor_hands_other_tools_to_computer_use():
    seen: list[str] = []
    run = web_tools.executor(lambda name, args: seen.append(name) or "pressed")
    assert run("press_control", {"number": 1}) == "pressed"
    assert "20" in run("current_time", {})  # this century's year
    assert seen == ["press_control"]


def test_messages_runs_a_tool_and_sends_its_result_back(monkeypatch):
    """Claude asks for web_search; the result goes back as tool_result."""
    provider = ClaudeProvider(
        Account(id=1, provider=PROVIDER_CLAUDE, base_url="https://example.test"), object()
    )
    first = _sse(
        [
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "tu1", "name": "web_search"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"query": "n'},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": 'ews"}'},
            },
        ]
    )
    second = _sse(
        [{"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}}]
    )
    bodies: list[dict] = []
    _wire(monkeypatch, provider, [first, second], bodies)
    calls: list[tuple] = []
    events = list(
        provider.generate(_settings(lambda n, a: calls.append((n, a)) or "found"), Event())
    )

    assert calls == [("web_search", {"query": "news"})]
    assert bodies[0]["tools"][0]["name"] == "web_search"
    assert "input_schema" in bodies[0]["tools"][0]
    assert bodies[1]["messages"][-2]["content"] == [
        {"type": "tool_use", "id": "tu1", "name": "web_search", "input": {"query": "news"}}
    ]
    assert bodies[1]["messages"][-1]["content"] == [
        {"type": "tool_result", "tool_use_id": "tu1", "content": "found"}
    ]
    assert "Searching the web" in [e.text for e in events if e.kind == "tool"]
    assert "".join(e.text for e in events if e.kind == "text") == "ok"


def test_responses_runs_a_tool_and_sends_its_result_back(monkeypatch):
    provider = OpenAIProvider(
        Account(id=1, provider=PROVIDER_OPENAI, base_url="https://example.test"), object()
    )
    call = {"type": "function_call", "call_id": "c1", "name": "fetch_page", "arguments": "{}"}
    first = _sse(
        [
            {"type": "response.output_item.added", "item": call},
            {"type": "response.reasoning_summary_text.delta", "delta": "thinking"},
            {"type": "response.output_item.done", "item": call},
        ]
    )
    second = _sse([{"type": "response.output_text.delta", "delta": "done"}])
    bodies: list[dict] = []
    _wire(monkeypatch, provider, [first, second], bodies)
    events = list(provider.generate(_settings(lambda n, a: "page text"), Event()))

    assert bodies[0]["tools"][0] == {"type": "function", **web_tools.DEFINITIONS[0]["function"]}
    assert bodies[1]["input"][-2:] == [
        {"type": "function_call", "call_id": "c1", "name": "fetch_page", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "page text"},
    ]
    assert [e.text for e in events if e.kind == "reasoning"] == ["thinking"]
    assert "Reading a web page" in [e.text for e in events if e.kind == "tool"]


def test_responses_takes_attachments_and_moves_the_system_prompt(monkeypatch):
    """Attachments used to be refused on the Responses API outright."""
    provider = OpenAIProvider(
        Account(id=1, provider=PROVIDER_OPENAI, base_url="https://example.test"), object()
    )
    bodies: list[dict] = []
    _wire(
        monkeypatch,
        provider,
        [_sse([{"type": "response.output_text.delta", "delta": "x"}])],
        bodies,
    )
    picture = MessageAttachment(filename="a.png", mime_type="image/png", data=b"\x89PNG")
    settings = GenerationSettings(
        model="m",
        messages=[
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "look", "attachments": [picture]},
        ],
    )
    list(provider.generate(settings, Event()))

    assert bodies[0]["instructions"] == "be brief"
    parts = bodies[0]["input"][0]["content"]
    assert parts[1]["type"] == "input_image"


def test_refused_thinking_is_dropped_and_remembered(monkeypatch):
    """gpt-4o refuses reasoning; the turn retries without it, once per model."""
    protocols._REASONING_REFUSED.clear()
    provider = OpenAIProvider(
        Account(id=1, provider=PROVIDER_OPENAI, base_url="https://example.test"), object()
    )
    bodies: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "reasoning" in body:
            return httpx.Response(
                400, json={"error": {"message": "Unsupported parameter: 'reasoning.summary'"}}
            )
        return httpx.Response(
            200, text=_sse([{"type": "response.output_text.delta", "delta": "x"}])
        )

    monkeypatch.setattr(provider, "headers", dict)
    monkeypatch.setattr(
        provider, "client", lambda: httpx.Client(transport=httpx.MockTransport(handle))
    )
    settings = GenerationSettings(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        reasoning={"summary": "auto"},
    )
    list(provider.generate(settings, Event()))
    list(provider.generate(settings, Event()))

    assert ["reasoning" in body for body in bodies] == [True, False, False]
    protocols._REASONING_REFUSED.clear()


def test_a_model_without_tools_answers_without_web_search(monkeypatch):
    """Many Ollama models refuse a request that carries tools."""
    sent: list[list] = []

    class Provider:
        def generate(self, settings, cancel):
            sent.append(list(settings.tools))
            if settings.tools:
                raise ProviderError("HTTP 400: m does not support tools", status_code=400)
            yield from ()

    monkeypatch.setattr(
        "accessible_ai.services.generation_service.create_provider", lambda *a: Provider()
    )
    events = list(
        GenerationService(object()).generate(
            Account(id=1), _settings(None), Event(), tool_executor=None
        )
    )
    assert sent == [web_tools.DEFINITIONS, []]
    assert "without web search" in events[0].text


def test_gemini_keeps_the_tools_and_executor(monkeypatch):
    """Gemini rebuilt the settings from a few fields and lost the tools."""
    provider = GeminiProvider(
        Account(id=1, provider=PROVIDER_GEMINI, base_url="https://example.test"), object()
    )
    seen: list[GenerationSettings] = []
    monkeypatch.setattr(
        type(provider).__mro__[1],
        "generate",
        lambda self, settings, cancel: iter(seen.append(settings) or ()),
    )
    executor = lambda n, a: ""  # noqa: E731
    list(provider.generate(_settings(executor), Event()))
    assert seen[0].tools == web_tools.DEFINITIONS
    assert seen[0].tool_executor is executor


@pytest.mark.parametrize(("stored", "expected"), [({}, True), ({"web_tools": False}, False)])
def test_profiles_have_web_tools_on_unless_turned_off(stored, expected):
    assert OpenRouterFeatures.from_dict(stored).web_tools is expected
    assert OpenRouterFeatures.from_dict(stored).as_dict()["web_tools"] is expected
