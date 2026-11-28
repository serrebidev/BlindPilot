"""Browser use for Codex: the access host the ChatGPT desktop app normally runs.

Codex's bundled Computer Use plugin (`unified-computer-use`, the `cua_repl`
MCP server) drives both apps and browsers. Before it touches a browser it asks
a "browser access host" whether this chat may, over a named pipe the host
names in ``BROWSER_USE_ACCESS_PIPE_PATH``. The answer must be signed with the
Ed25519 key whose public half is in ``BROWSER_USE_ACCESS_VERIFYING_IDENTITY``.
The ChatGPT app is the only host OpenAI ships, so a Codex started by anything
else -- BlindPilot included -- gets "The browser access host is unavailable"
and the model reports browser use as disabled, while computer use, which has
no such check, works.

BlindPilot is the person's own client, so it hosts the check itself: one pipe
and one throwaway key per BlindPilot process, handed to the Codex app-server
through its environment (the plugin passes these three variables through to
`cua_repl`). Whether the answer is yes is Preferences, "Let Codex use your web
browser", read at the moment of each request, so turning it off takes effect
on the next browser call without restarting Codex.

Wire format, taken from the plugin's own client: each frame is a 4-byte
native-endian length and a UTF-8 JSON-RPC 2.0 message. The request is
``browser/access`` with ``{executorId, sessionId, turnId, nonce}``; the reply
is ``{payload, signature}``, where payload is exactly
``JSON.stringify({domain, request, allowed})`` and signature is base64.
"""

from __future__ import annotations

import base64
import json
import logging
import struct
import sys
import threading
import uuid

log = logging.getLogger(__name__)

# Preferences, "Let Codex use your web browser". Set by the window's settings;
# read on the pipe thread for every request.
allowed = True

_DOMAIN = "codex.browserAccess.v1"
_METHOD = "browser/access"
_MAX_FRAME = 64 * 1024
# FILE_FLAG_FIRST_PIPE_INSTANCE, which pywin32 does not export.
_FIRST_PIPE_INSTANCE = 0x00080000

_lock = threading.Lock()
_env: dict[str, str] | None = None


def _payload(request: object, allow: bool) -> str:
    """What the plugin compares byte for byte against its own JSON.stringify."""
    return json.dumps(
        {"domain": _DOMAIN, "request": request, "allowed": allow},
        separators=(",", ":"),
        ensure_ascii=False,
    )


def answer(message: dict, executor_id: str, sign, allow: bool) -> dict:
    """The JSON-RPC reply to one message from the plugin.

    Anything that is not this executor's access request gets an error, never
    a signed yes: the key signs only what this BlindPilot was asked about.
    """
    reply: dict = {"jsonrpc": "2.0", "id": message.get("id")}
    params = message.get("params")
    if (
        message.get("method") != _METHOD
        or not isinstance(params, dict)
        or params.get("executorId") != executor_id
    ):
        reply["error"] = {"code": -32601, "message": "unsupported browser access request"}
        return reply
    payload = _payload(params, allow)
    reply["result"] = {
        "payload": payload,
        "signature": base64.b64encode(sign(payload.encode("utf-8"))).decode("ascii"),
    }
    return reply


def env() -> dict[str, str]:
    """The variables a Codex app-server needs to reach this host; {} if none.

    Started on first use and kept for the life of the process. Windows only:
    the macOS host would be a Unix socket, and nobody has tried one there.
    """
    # ponytail: Windows named pipe only; add an AF_UNIX twin when macOS asks.
    global _env
    with _lock:
        if _env is not None:
            return dict(_env)
        _env = {}
        if sys.platform != "win32":
            return {}
        try:
            _env = _start()
        except Exception:  # noqa: BLE001 - an optional feature must not stop Codex starting
            log.exception("could not start the Codex browser access host")
            _env = {}
        return dict(_env)


def _start() -> dict[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    pipe = rf"\\.\pipe\blindpilot-browser-access-{uuid.uuid4()}"
    executor_id = str(uuid.uuid4())
    first = _create_pipe(pipe, first=True)
    threading.Thread(
        target=_serve,
        args=(pipe, first, executor_id, key.sign),
        name="codex-browser-access",
        daemon=True,
    ).start()
    return {
        "BROWSER_USE_ACCESS_PIPE_PATH": pipe,
        "BROWSER_USE_ACCESS_EXECUTOR_ID": executor_id,
        "BROWSER_USE_ACCESS_VERIFYING_IDENTITY": base64.b64encode(public).decode("ascii"),
    }


def _create_pipe(pipe: str, first: bool = False):
    import win32pipe

    mode = win32pipe.PIPE_ACCESS_DUPLEX
    if first:
        # Nobody else may already own this name; with a random name this only
        # matters if somebody guessed it, which is the case it guards.
        mode |= _FIRST_PIPE_INSTANCE
    return win32pipe.CreateNamedPipe(
        pipe,
        mode,
        win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
        win32pipe.PIPE_UNLIMITED_INSTANCES,
        _MAX_FRAME,
        _MAX_FRAME,
        0,
        None,
    )


def _serve(pipe: str, handle, executor_id: str, sign) -> None:
    """Accept forever; each connection is answered on a thread of its own."""
    import pywintypes
    import win32file
    import win32pipe

    while True:
        try:
            win32pipe.ConnectNamedPipe(handle, None)
        except pywintypes.error as error:
            # ERROR_PIPE_CONNECTED: the client got in before we waited.
            if error.winerror != 535:
                log.warning("browser access pipe: %s", error)
                win32file.CloseHandle(handle)
                handle = _create_pipe(pipe)
                continue
        threading.Thread(
            target=_handle,
            args=(handle, executor_id, sign),
            name="codex-browser-access-conn",
            daemon=True,
        ).start()
        handle = _create_pipe(pipe)


def _read_exact(handle, count: int) -> bytes:
    import win32file

    data = b""
    while len(data) < count:
        _status, chunk = win32file.ReadFile(handle, count - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


def _handle(handle, executor_id: str, sign) -> None:
    import pywintypes
    import win32file

    try:
        while True:
            (size,) = struct.unpack("=I", _read_exact(handle, 4))
            if size > _MAX_FRAME:
                return
            message = json.loads(_read_exact(handle, size))
            if not isinstance(message, dict):
                return
            body = json.dumps(answer(message, executor_id, sign, allowed)).encode("utf-8")
            win32file.WriteFile(handle, struct.pack("=I", len(body)) + body)
    except (EOFError, ValueError, pywintypes.error):
        # The plugin closes after its one answer; a broken frame ends it too.
        pass
    finally:
        win32file.CloseHandle(handle)
