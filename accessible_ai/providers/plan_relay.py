"""A loopback relay that keeps Codex inside the plan's accepted item set.

A ChatGPT-plan Account signs Codex in with a token minted for
``https://api.openai.com/v1``. That endpoint treats the token as subscription
sharing: it accepts only a fixed set of conversation item types and answers
anything else with ``subscription_sharing_unsupported_capability``. Codex emits
items of its own that are not in that set -- ``configuration_update`` when it
applies thread settings, ``compaction_trigger``, and ``shell_call`` for its
unified shell -- so a turn carrying one fails before the model is asked
anything.

Codex offers no setting that stops it emitting them, so this relay sits on
loopback in front of the plan endpoint: it drops those items from a Responses
request and forwards everything else, streaming the reply back untouched. Only
the plan provider is pointed here; Codex's own login never uses it.
"""

from __future__ import annotations

import atexit
import gzip
import http.client
import json
import ssl
import sys
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

UPSTREAM = "https://api.openai.com/v1"

# Items the plan endpoint refuses. Every one of these was sent to the live
# endpoint and answered with subscription_sharing_unsupported_capability, or
# rejected outright as an unknown item type; the same request without it was
# accepted. ``local_shell_call`` and ``world_state`` are older shapes Codex can
# still replay from a conversation begun on another backend.
DROPPED_INPUT_ITEMS = frozenset(
    {
        "compaction_trigger",
        "configuration_update",
        "local_shell_call",
        "local_shell_call_output",
        "mcp_tool_call",
        "shell_call",
        "shell_call_output",
        "world_state",
    }
)

# Hop-by-hop headers belong to one connection and must not be copied.
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "content-length",
        "keep-alive",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

_READ_CHUNK = 64 * 1024
_UPSTREAM_TIMEOUT_SECONDS = 600


def _is_dropped(item: object) -> bool:
    return isinstance(item, dict) and item.get("type") in DROPPED_INPUT_ITEMS


def filter_request(payload: object) -> object:
    """Drop the input items the plan endpoint refuses, keeping the rest.

    A payload that is not a Responses request, or that has nothing to drop, is
    returned exactly as it arrived.
    """
    if not isinstance(payload, dict):
        return payload
    items = payload.get("input")
    if not isinstance(items, list):
        return payload
    kept = [item for item in items if not _is_dropped(item)]
    if len(kept) == len(items):
        return payload
    filtered = dict(payload)
    filtered["input"] = kept
    return filtered


def _is_responses(path: str) -> bool:
    return path.split("?", 1)[0].rstrip("/").endswith("/responses")


def _decoded(encoding: str | None, body: bytes) -> bytes | None:
    """The body with its content coding removed, or None if it cannot be read."""
    name = (encoding or "").strip().lower()
    if not name or name == "identity":
        return body
    try:
        if name == "gzip":
            return gzip.decompress(body)
        if name == "deflate":
            return zlib.decompress(body)
    except (OSError, zlib.error):
        return None
    return None


class _Server(ThreadingHTTPServer):
    """A server for which a client hanging up is ordinary, not an error.

    Codex abandons a response when a turn is cancelled, and a write to a client
    that has gone already raises where the handler writes. That is the normal
    end of a cancelled turn, so it must not reach the log as a traceback.
    """

    daemon_threads = True

    def handle_error(self, request: object, client_address: object) -> None:
        kind = sys.exc_info()[0]
        if kind is not None and issubclass(kind, (ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class Relay:
    """A loopback Responses proxy in front of the plan endpoint."""

    def __init__(
        self, upstream: str = UPSTREAM, *, ssl_context: ssl.SSLContext | None = None
    ) -> None:
        parsed = urlsplit(upstream)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"not an upstream URL: {upstream}")
        self._scheme = parsed.scheme
        self._host = parsed.hostname
        self._port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self._path = parsed.path.rstrip("/")
        # None means http.client's default context, which is the right answer
        # everywhere except a frozen build; the caller supplies the app's own.
        self._ssl_context = ssl_context
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 - the name http.server calls
                outer._relay(self)

            def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
                outer._relay(self)

            def log_message(self, *args: Any) -> None:
                # The headers carry a bearer token: never log a request.
                pass

        self.httpd = _Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}{self._path}"

    @property
    def _netloc(self) -> str:
        default = 443 if self._scheme == "https" else 80
        return self._host if self._port == default else f"{self._host}:{self._port}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def _relay(self, handler: BaseHTTPRequestHandler) -> None:
        headers = self._headers(handler)
        body = self._body(handler)
        if handler.command == "POST" and _is_responses(handler.path):
            headers, body = self._filtered(handler, headers, body)
        self._forward(handler, headers, body)

    @staticmethod
    def _body(handler: BaseHTTPRequestHandler) -> bytes:
        try:
            length = int(handler.headers.get("Content-Length") or 0)
        except ValueError:
            return b""
        if length <= 0:
            return b""
        return handler.rfile.read(length)

    @staticmethod
    def _headers(handler: BaseHTTPRequestHandler) -> dict[str, str]:
        headers: dict[str, str] = {}
        for name, value in handler.headers.items():
            lowered = name.lower()
            if lowered in _HOP_BY_HOP or lowered in ("host", "accept-encoding"):
                continue
            headers[name] = value
        return headers

    @staticmethod
    def _filtered(
        handler: BaseHTTPRequestHandler, headers: dict[str, str], body: bytes
    ) -> tuple[dict[str, str], bytes]:
        if not body:
            return headers, body
        decoded = _decoded(handler.headers.get("Content-Encoding"), body)
        if decoded is None:
            # Leave an encoding we cannot read to OpenAI rather than corrupt it.
            return headers, body
        headers = dict(headers)
        for name in [name for name in headers if name.lower() == "content-encoding"]:
            del headers[name]
        try:
            payload = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return headers, decoded
        filtered = filter_request(payload)
        if filtered is payload:
            return headers, decoded
        return headers, json.dumps(filtered).encode("utf-8")

    def _connection(self) -> http.client.HTTPConnection:
        if self._scheme == "https":
            return http.client.HTTPSConnection(
                self._host, self._port, timeout=_UPSTREAM_TIMEOUT_SECONDS, context=self._ssl_context
            )
        return http.client.HTTPConnection(self._host, self._port, timeout=_UPSTREAM_TIMEOUT_SECONDS)

    def _forward(
        self, handler: BaseHTTPRequestHandler, headers: dict[str, str], body: bytes
    ) -> None:
        connection = self._connection()
        try:
            connection.putrequest(
                handler.command, handler.path, skip_host=True, skip_accept_encoding=True
            )
            connection.putheader("Host", self._netloc)
            for name, value in headers.items():
                connection.putheader(name, value)
            if body or handler.command in ("POST", "PUT", "PATCH"):
                connection.putheader("Content-Length", str(len(body)))
            connection.endheaders(body or None)
            response = connection.getresponse()
        except (OSError, http.client.HTTPException):
            connection.close()
            _fail(handler, 502, "The plan endpoint could not be reached.")
            return
        try:
            self._respond(handler, response)
        finally:
            response.close()
            connection.close()

    @staticmethod
    def _respond(handler: BaseHTTPRequestHandler, response: http.client.HTTPResponse) -> None:
        handler.send_response(response.status)
        length = response.getheader("Content-Length")
        for name, value in response.getheaders():
            if name.lower() in _HOP_BY_HOP or name.lower() == "content-length":
                continue
            handler.send_header(name, value)
        if length is not None:
            handler.send_header("Content-Length", length)
            handler.end_headers()
            _copy(handler, response, chunked=False)
        else:
            handler.send_header("Transfer-Encoding", "chunked")
            handler.end_headers()
            _copy(handler, response, chunked=True)


def _copy(
    handler: BaseHTTPRequestHandler, response: http.client.HTTPResponse, *, chunked: bool
) -> None:
    try:
        while True:
            chunk = response.read(_READ_CHUNK)
            if not chunk:
                break
            if chunked:
                handler.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                handler.wfile.write(chunk)
                handler.wfile.write(b"\r\n")
            else:
                handler.wfile.write(chunk)
            handler.wfile.flush()
        if chunked:
            handler.wfile.write(b"0\r\n\r\n")
            handler.wfile.flush()
    except OSError:
        # The client went away, or the endpoint stopped sending mid-reply: a
        # read timeout here is also an OSError. Close both ends either way.
        pass


def _fail(handler: BaseHTTPRequestHandler, status: int, message: str) -> None:
    body = json.dumps({"error": {"message": message, "type": "relay_error"}}).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    except OSError:
        pass


_shared: Relay | None = None
_shared_lock = threading.Lock()


def shared_relay(*, ssl_context: ssl.SSLContext | None = None) -> Relay:
    """The process's relay, started on first use and kept until exit."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = Relay(ssl_context=ssl_context)
            atexit.register(_shared.close)
        return _shared


def plan_base_url(*, ssl_context: ssl.SSLContext | None = None) -> str:
    """Where Codex is pointed for a plan turn: this process's relay."""
    return shared_relay(ssl_context=ssl_context).base_url
