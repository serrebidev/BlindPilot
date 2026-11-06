"""The loopback relay in front of the plan's Responses endpoint."""

from __future__ import annotations

import gzip
import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from accessible_ai.providers import plan_relay

TIMEOUT = 10

ALLOWED = (
    "message",
    "reasoning",
    "function_call",
    "function_call_output",
    "custom_tool_call",
    "custom_tool_call_output",
    "additional_tools",
    "compaction",
)


def _items(*kinds: str) -> list[dict]:
    return [{"type": kind, "id": f"{kind}-1"} for kind in kinds]


def _post(url: str, body: bytes, headers: dict[str, str] | None = None) -> tuple[int, dict, bytes]:
    """POST through the relay and read the whole reply, chunked or not."""
    parsed = urlsplit(url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=TIMEOUT)
    try:
        connection.request(
            "POST",
            parsed.path,
            body=body,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        response = connection.getresponse()
        sent = {name.lower(): value for name, value in response.getheaders()}
        return response.status, sent, response.read()
    finally:
        connection.close()


class _UpstreamHandler(BaseHTTPRequestHandler):
    """Records the request the relay forwarded and answers with one body."""

    # HTTP/1.0 without a Content-Length closes the connection after the body,
    # which is how a streamed reply ends when the endpoint does not frame it.
    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
        self._handle()

    def do_POST(self) -> None:  # noqa: N802 - the name http.server calls
        self._handle()

    def _handle(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else b""
        with self.server.lock:
            self.server.requests.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "headers": {name.lower(): value for name, value in self.headers.items()},
                    "body": body,
                }
            )
        self.send_response(200)
        self.send_header("Content-Type", self.server.reply_type)
        if self.server.reply_with_length:
            self.send_header("Content-Length", str(len(self.server.reply_body)))
        self.end_headers()
        self.wfile.write(self.server.reply_body)

    def log_message(self, *args: object) -> None:
        pass


class _Upstream:
    """A fake plan endpoint on loopback: records one request, replies once."""

    def __init__(
        self,
        reply_body: bytes = b"",
        *,
        reply_type: str = "text/event-stream",
        with_length: bool = False,
    ) -> None:
        self.requests: list[dict] = []
        self.lock = threading.Lock()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        self.httpd.daemon_threads = True
        self.httpd.requests = self.requests
        self.httpd.lock = self.lock
        self.httpd.reply_body = reply_body
        self.httpd.reply_type = reply_type
        self.httpd.reply_with_length = with_length
        self.port = int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(TIMEOUT)


def test_every_item_the_plan_endpoint_refuses_is_dropped_and_the_rest_kept():
    payload = {
        "model": "gpt-5-codex",
        "input": [
            *_items(*sorted(plan_relay.DROPPED_INPUT_ITEMS)),
            *_items(*ALLOWED),
        ],
    }
    filtered = plan_relay.filter_request(payload)
    assert [item["type"] for item in filtered["input"]] == list(ALLOWED)
    assert filtered["model"] == "gpt-5-codex"


def test_a_request_with_nothing_to_drop_is_returned_untouched():
    payload = {"input": _items("message", "reasoning")}
    assert plan_relay.filter_request(payload) is payload


def test_a_payload_that_is_not_a_responses_request_is_returned_untouched():
    assert plan_relay.filter_request("not a request") == "not a request"
    assert plan_relay.filter_request(None) is None
    no_input = {"model": "gpt-5-codex"}
    assert plan_relay.filter_request(no_input) is no_input
    string_input = {"input": "hello", "type": "configuration_update"}
    assert plan_relay.filter_request(string_input) is string_input


def test_a_responses_turn_reaches_the_plan_endpoint_without_the_refused_items():
    reply = b'data: {"type": "response.completed"}\n\ndata: [DONE]\n\n'
    upstream = _Upstream(reply, with_length=True)
    relay = None
    try:
        relay = plan_relay.Relay(upstream=upstream.url)
        payload = {
            "model": "gpt-5-codex",
            "input": [
                *_items("configuration_update", "compaction_trigger", "shell_call"),
                {"type": "message", "role": "user", "content": "list the files"},
            ],
            "stream": True,
        }
        status, headers, received = _post(
            f"{relay.base_url}/responses",
            json.dumps(payload).encode("utf-8"),
            {"Authorization": "Bearer test-token"},
        )

        assert status == 200
        assert received == reply
        assert headers["content-type"] == "text/event-stream"
    finally:
        if relay is not None:
            relay.close()
        upstream.stop()

    assert len(upstream.requests) == 1
    sent = upstream.requests[0]
    assert sent["path"] == "/v1/responses"
    assert sent["headers"]["authorization"] == "Bearer test-token"
    assert "content-encoding" not in sent["headers"]
    forwarded = json.loads(sent["body"])
    assert forwarded["input"] == [{"type": "message", "role": "user", "content": "list the files"}]
    assert forwarded["model"] == "gpt-5-codex"
    assert forwarded["stream"] is True


def test_a_reply_the_endpoint_does_not_frame_streams_back_to_the_client():
    reply = b'data: {"type": "response.output_text.delta", "delta": "hi"}\n\n'
    upstream = _Upstream(reply)
    relay = None
    try:
        relay = plan_relay.Relay(upstream=upstream.url)
        status, headers, received = _post(
            f"{relay.base_url}/responses", json.dumps({"input": _items("message")}).encode("utf-8")
        )

        assert status == 200
        assert received == reply
        assert headers["transfer-encoding"] == "chunked"
        assert headers["content-type"] == "text/event-stream"
    finally:
        if relay is not None:
            relay.close()
        upstream.stop()


def test_a_gzipped_request_is_read_before_its_items_are_dropped():
    upstream = _Upstream(b"{}", reply_type="application/json", with_length=True)
    relay = None
    try:
        relay = plan_relay.Relay(upstream=upstream.url)
        payload = {"input": [*_items("shell_call"), *_items("message")]}
        status, _headers, received = _post(
            f"{relay.base_url}/responses",
            gzip.compress(json.dumps(payload).encode("utf-8")),
            {"Content-Encoding": "gzip"},
        )

        assert status == 200
        assert received == b"{}"
    finally:
        if relay is not None:
            relay.close()
        upstream.stop()

    sent = upstream.requests[0]
    assert "content-encoding" not in sent["headers"]
    assert json.loads(sent["body"]) == {"input": _items("message")}


def test_the_shared_relay_is_one_loopback_instance_kept_until_exit():
    relay = plan_relay.shared_relay()
    assert plan_relay.shared_relay() is relay
    assert plan_relay.plan_base_url() == relay.base_url
    assert relay.base_url.startswith("http://127.0.0.1:")
    assert relay.base_url.endswith("/v1")


def test_an_https_relay_hands_its_trust_store_to_the_connection(monkeypatch):
    sentinel = object()
    seen = {}

    class _Connection:
        def __init__(self, host, port, timeout=None, context=None):
            seen["context"] = context

    monkeypatch.setattr(plan_relay.http.client, "HTTPSConnection", _Connection)
    relay = plan_relay.Relay(ssl_context=sentinel)
    try:
        relay._connection()
    finally:
        relay.close()
    assert seen["context"] is sentinel
