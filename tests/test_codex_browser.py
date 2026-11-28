"""Codex's browser access check, answered the way its plugin verifies it."""

import base64
import json
import struct
import sys

import pytest

import codex_browser


def _request(executor: str = "exec-1") -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "browser/access",
        "params": {"executorId": executor, "sessionId": "s", "turnId": "t", "nonce": "n"},
    }


def test_payload_matches_the_plugins_json_stringify() -> None:
    reply = codex_browser.answer(_request(), "exec-1", lambda data: b"sig", True)

    # The plugin compares the payload string against its own
    # JSON.stringify({domain, request, allowed: true}), keys in that order.
    assert reply["id"] == 7
    assert reply["result"]["payload"] == (
        '{"domain":"codex.browserAccess.v1","request":'
        '{"executorId":"exec-1","sessionId":"s","turnId":"t","nonce":"n"},"allowed":true}'
    )
    assert base64.b64decode(reply["result"]["signature"]) == b"sig"


def test_turned_off_is_a_signed_no() -> None:
    reply = codex_browser.answer(_request(), "exec-1", lambda data: b"sig", False)

    assert json.loads(reply["result"]["payload"])["allowed"] is False


@pytest.mark.parametrize(
    "message",
    [
        _request(executor="someone-else"),
        {**_request(), "method": "browser/other"},
        {**_request(), "params": ["not", "a", "dict"]},
    ],
)
def test_nothing_else_gets_signed(message: dict) -> None:
    signed: list[bytes] = []
    reply = codex_browser.answer(message, "exec-1", signed.append, True)

    assert "error" in reply and "result" not in reply
    assert signed == []


@pytest.mark.skipif(sys.platform != "win32", reason="the host is a Windows named pipe")
def test_pipe_round_trip_verifies_with_the_advertised_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import win32file
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    monkeypatch.setattr(codex_browser, "allowed", True)
    env = codex_browser.env()
    request = _request(env["BROWSER_USE_ACCESS_EXECUTOR_ID"])
    body = json.dumps(request).encode()
    handle = win32file.CreateFile(
        env["BROWSER_USE_ACCESS_PIPE_PATH"],
        win32file.GENERIC_READ | win32file.GENERIC_WRITE,
        0,
        None,
        win32file.OPEN_EXISTING,
        0,
        None,
    )
    try:
        win32file.WriteFile(handle, struct.pack("=I", len(body)) + body)
        _, header = win32file.ReadFile(handle, 4)
        _, data = win32file.ReadFile(handle, struct.unpack("=I", header)[0])
    finally:
        win32file.CloseHandle(handle)

    result = json.loads(data)["result"]
    key = load_der_public_key(base64.b64decode(env["BROWSER_USE_ACCESS_VERIFYING_IDENTITY"]))
    key.verify(base64.b64decode(result["signature"]), result["payload"].encode())  # type: ignore[call-arg]
    assert json.loads(result["payload"])["allowed"] is True
