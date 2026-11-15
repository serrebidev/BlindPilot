"""Transient failures may retry only before a response has started."""

from threading import Event

import httpx
import pytest

from accessible_ai.models import Account, GenerationSettings, StreamEvent
from accessible_ai.providers.base import BaseProvider, ProviderError
from accessible_ai.services import generation_service as service


def _generate(monkeypatch, failures, events=()):
    calls = []
    cancel = Event()
    waits = []
    monkeypatch.setattr(cancel, "wait", lambda delay: waits.append(delay) or False)

    class Provider:
        def generate(self, settings, cancel):
            calls.append(settings)
            if len(calls) <= len(failures):
                yield from events
                raise failures[len(calls) - 1]
            yield StreamEvent("text", "answer")

    monkeypatch.setattr(service, "create_provider", lambda *_: Provider())
    output = service.GenerationService(None).generate(
        Account(id=1), GenerationSettings("m", []), cancel
    )
    return output, calls, waits, cancel


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_status_retries_then_returns_answer(monkeypatch, status):
    output, calls, waits, _ = _generate(monkeypatch, [ProviderError("busy", status_code=status)])
    result = list(output)
    assert len(calls) == 2 and waits == [1]
    assert result[0].kind == "status" and "retry" in result[0].text.lower()
    assert result[-1].text == "answer"


def test_retry_exhaustion_preserves_the_last_error(monkeypatch):
    error = ProviderError("busy", status_code=503)
    output, calls, waits, _ = _generate(monkeypatch, [error] * 3)
    with pytest.raises(ProviderError) as caught:
        list(output)
    assert caught.value is error
    assert len(calls) == 3 and waits == [1, 2]


@pytest.mark.parametrize("status", [400, 401, 403, 404, None])
def test_permanent_or_unclassified_errors_never_retry(monkeypatch, status):
    output, calls, waits, _ = _generate(monkeypatch, [ProviderError("failed", status_code=status)])
    with pytest.raises(ProviderError):
        list(output)
    assert len(calls) == 1 and not waits


@pytest.mark.parametrize("kind", ["text", "reasoning", "tool", "sources"])
def test_partial_output_or_tool_activity_prevents_replay(monkeypatch, kind):
    output, calls, waits, _ = _generate(
        monkeypatch,
        [ProviderError("busy", status_code=503)],
        [StreamEvent(kind, "" if kind == "tool" else "partial")],
    )
    with pytest.raises(ProviderError):
        list(output)
    assert len(calls) == 1 and not waits


def test_headers_and_status_do_not_count_as_response_content(monkeypatch):
    output, calls, _, _ = _generate(
        monkeypatch,
        [ProviderError("busy", status_code=429)],
        [StreamEvent("headers"), StreamEvent("status", "Connecting")],
    )
    assert list(output)[-1].text == "answer"
    assert len(calls) == 2


def test_an_accepted_batch_is_never_submitted_twice(monkeypatch):
    output, calls, waits, _ = _generate(
        monkeypatch,
        [ProviderError("poll failed", status_code=503)],
        [StreamEvent("status", "Batch accepted", {"batch_id": "paid-batch"})],
    )
    with pytest.raises(ProviderError):
        list(output)
    assert len(calls) == 1 and not waits


def test_cancel_during_backoff_prevents_another_request(monkeypatch):
    output, calls, _, cancel = _generate(monkeypatch, [ProviderError("busy", status_code=429)])
    monkeypatch.setattr(cancel, "wait", lambda delay: cancel.set() or True)
    assert [event.kind for event in output] == ["status"]
    assert len(calls) == 1


def test_already_cancelled_never_starts_a_request(monkeypatch):
    output, calls, _, cancel = _generate(monkeypatch, [])
    cancel.set()
    assert list(output) == [] and calls == []


@pytest.mark.parametrize("error", [httpx.ConnectError("offline"), httpx.ConnectTimeout("offline")])
def test_connection_establishment_failure_can_retry(monkeypatch, error):
    output, calls, _, _ = _generate(monkeypatch, [error])
    assert list(output)[-1].text == "answer" and len(calls) == 2


@pytest.mark.parametrize("error", [httpx.ReadTimeout("unknown"), httpx.WriteError("unknown")])
def test_ambiguous_transport_failure_never_replays(monkeypatch, error):
    output, calls, waits, _ = _generate(monkeypatch, [error])
    with pytest.raises(type(error)):
        list(output)
    assert len(calls) == 1 and waits == []


@pytest.mark.parametrize("delay, expected", [(12, 12), (60, 30), (-1, 1)])
def test_retry_after_is_bounded(monkeypatch, delay, expected):
    output, _, waits, _ = _generate(
        monkeypatch, [ProviderError("busy", status_code=429, retry_after=delay)]
    )
    list(output)
    assert waits == [expected]


@pytest.mark.parametrize("value, expected", [("5", 5), ("junk", None), ("nan", None), ("-3", None)])
def test_http_errors_keep_status_and_valid_retry_after(value, expected):
    response = httpx.Response(
        429, json={"error": {"message": "busy"}}, headers={"Retry-After": value}
    )
    with pytest.raises(ProviderError, match="HTTP 429: busy") as caught:
        BaseProvider.raise_for_status(response)
    assert caught.value.status_code == 429
    assert caught.value.retry_after == expected
