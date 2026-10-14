"""Sign in with ChatGPT: chat on the user's ChatGPT plan instead of an API key.

Follows OpenAI's guide for open-source and locally run apps
(developers.openai.com/siwc/token-sharing-open-source): an authorization code
with PKCE in the system browser, answered on a loopback listener on 127.0.0.1;
the ID token checked against OpenAI's published keys; Responses requests sent
with ``store`` false and ``stream`` true; the access token refreshed when it
nears expiry and the refresh token revoked on sign-out.

What is kept: the issued client id, the e-mail, the account's subject and the
refresh token, as one JSON value in the account's credential slot. The access
token lives in memory only. It is 1,800 characters, and Windows Credential
Manager holds at most 2,560 bytes, which is 1,280 characters in the UTF-16 the
store writes; the refresh token is about 200. A new process refreshes once.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
import uuid
import webbrowser
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from html import escape
from pathlib import PurePath
from threading import Event
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import httpx

from accessible_ai.models import (
    REASONING_EFFORT_LABELS,
    SERVER_TOOL_WEB_SEARCH,
    GenerationSettings,
    OpenRouterFeatures,
    StreamEvent,
)
from accessible_ai.providers.base import BaseProvider, ProviderError, iter_sse_json
from accessible_ai.providers.protocols import (
    LENGTH_LIMIT_NOTICE,
    TEXT_EXTENSIONS,
    SourceCollector,
)
from accessible_ai.storage.credentials import CredentialStore
from accessible_ai.storage.paths import app_data_dir


logger = logging.getLogger(__name__)

ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = ISSUER + "/api/accounts/authorize"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
DISCOVERY_URL = ISSUER + "/.well-known/openid-configuration"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
# The first-time registration entry point. Never the client id to keep: the
# callback names the one OpenAI issued, and later sign-ins reuse that.
DYNAMIC_CLIENT = "dynamic_agent_client"
AGENT_NAME = "BlindPilot"
CALLBACK_PATH = "/auth/callback"
USAGE_URL = "https://chatgpt.com/settings/usage"
SIGN_IN_TIMEOUT = 300.0
REFRESH_MARGIN = 120.0
CLOCK_SKEW = 300.0
AUTH_TIMEOUT = 30.0

# Refresh answers that mean the session is over: only a new sign-in helps.
SESSION_OVER = {
    "invalid_grant",
    "invalid_refresh_token",
    "token_expired",
    "refresh_token_expired",
    "refresh_token_invalidated",
    "refresh_token_reused",
    "invalid_client",
}

SIGN_IN_AGAIN = (
    "The ChatGPT sign-in for this account has ended. Open Chat, Accounts, edit the account "
    "and choose Continue with ChatGPT."
)

# The plan-usage error codes OpenAI documents, as something to tell the user.
PLAN_ERRORS = {
    "subscription_sharing_usage_limit_exceeded": (
        "You have reached the usage limit for using your ChatGPT plan here. The limit can be "
        f"your plan's or one set for BlindPilot. Review it at {USAGE_URL}"
    ),
    "subscription_sharing_usage_unavailable": (
        "ChatGPT could not check your plan usage right now. Try again in a few minutes."
    ),
    "subscription_sharing_user_unavailable": (
        "ChatGPT could not read your account right now. Try again in a few minutes."
    ),
    "subscription_sharing_user_not_eligible": (
        "Your ChatGPT plan, workspace or region does not allow using the plan in other apps."
    ),
    "subscription_sharing_route_not_supported": (
        "ChatGPT plan usage does not support this kind of request."
    ),
    "chatpass_v2_scope_not_authorized": (
        "This ChatGPT sign-in does not allow using your plan. " + SIGN_IN_AGAIN
    ),
    "chatpass_v2_invalid_authorization_context": (
        "This ChatGPT sign-in does not allow using your plan. " + SIGN_IN_AGAIN
    ),
    "subscription_sharing_invalid_user": "ChatGPT did not accept this sign-in. " + SIGN_IN_AGAIN,
}


class ChatGPTError(ProviderError):
    """A failure with OpenAI's machine-readable code kept beside the sentence."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


# ----- Small helpers -----


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _same(a: str, b: str) -> bool:
    """Constant-time compare that also takes text an attacker could send."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def host_id() -> str:
    """This computer's stable, opaque ``ext_agent_host_id``.

    Chosen once before the first sign-in and kept across sign-outs, as the
    guide asks. It identifies the installation, not the person, and is not a
    credential, so a plain file next to the chat database is the right place.
    """
    path = app_data_dir() / "chatgpt-host-id.txt"
    try:
        value = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        value = ""
    if not value.startswith("urn:uuid:"):
        value = f"urn:uuid:{uuid.uuid4()}"
        path.write_text(value, encoding="ascii")
    return value


def scope_list(scope: Any) -> list[str]:
    if isinstance(scope, list):
        return sorted(str(s) for s in scope)
    return sorted(str(scope or "").split())


# ----- Stored session -----


def load_session(credentials: CredentialStore, account_id: int) -> dict[str, Any]:
    raw = credentials.get_api_key(account_id)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def save_session(credentials: CredentialStore, account_id: int, session: dict[str, Any]) -> None:
    kept = ("client_id", "email", "subject", "refresh_token", "scopes")
    credentials.set_api_key(account_id, json.dumps({k: session[k] for k in kept if k in session}))


def is_signed_in(session: dict[str, Any]) -> bool:
    return bool(session.get("client_id") and session.get("refresh_token"))


# Access tokens by account id, with the time each expires.
_access_tokens: dict[int, tuple[str, float]] = {}
# ponytail: one lock for every account, so refreshes never race a rotating
# refresh token. Per-account locks if several plan accounts ever chat at once.
_refresh_lock = threading.Lock()


def store_sign_in(credentials: CredentialStore, account_id: int, result: dict[str, Any]) -> None:
    """Keep what ``sign_in`` returned for a saved account."""
    with _refresh_lock:
        save_session(credentials, account_id, result)
        _access_tokens[account_id] = (result["access_token"], result["expires_at"])


def access_token(credentials: CredentialStore, account_id: int, force: bool = False) -> str:
    """A usable access token, refreshed when it is about to expire or ``force``."""
    with _refresh_lock:
        cached = _access_tokens.get(account_id)
        if cached and not force and time.time() < cached[1] - REFRESH_MARGIN:
            return cached[0]
        session = load_session(credentials, account_id)
        if not is_signed_in(session):
            raise ChatGPTError(
                "This account is not signed in to ChatGPT. Open Chat, Accounts, edit the account "
                "and choose Continue with ChatGPT."
            )
        try:
            tokens = _token_request(
                {
                    "grant_type": "refresh_token",
                    "client_id": session["client_id"],
                    "refresh_token": session["refresh_token"],
                    "resource": RESOURCE,
                }
            )
        except ChatGPTError as exc:
            if exc.code in SESSION_OVER:
                session.pop("refresh_token", None)
                if exc.code == "invalid_client":
                    # The registration itself is gone; the next sign-in registers anew.
                    session = {}
                save_session(credentials, account_id, session)
                _access_tokens.pop(account_id, None)
                raise ChatGPTError(SIGN_IN_AGAIN, exc.code) from exc
            raise
        token = tokens.get("access_token")
        if not isinstance(token, str) or not token:
            raise ChatGPTError("ChatGPT answered the token refresh without an access token.")
        # The refresh token rotates: the replacement must be saved before use.
        session["refresh_token"] = tokens.get("refresh_token") or session["refresh_token"]
        if tokens.get("scope"):
            session["scopes"] = scope_list(tokens["scope"])
        save_session(credentials, account_id, session)
        _access_tokens[account_id] = (token, time.time() + _lifetime(tokens))
        if PLAN_SCOPE not in session.get("scopes", [PLAN_SCOPE]):
            raise ChatGPTError(PLAN_ERRORS["chatpass_v2_scope_not_authorized"])
        return token


def _lifetime(tokens: dict[str, Any]) -> float:
    try:
        seconds = float(tokens.get("expires_in") or 3600)
    except (TypeError, ValueError):
        seconds = 3600.0
    return seconds if seconds > 0 else 3600.0


# ----- Talking to auth.openai.com -----


def _auth_error(response: httpx.Response) -> ChatGPTError:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    code = ""
    detail = ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "")
            detail = str(error.get("message") or "")
        elif isinstance(error, str):
            code = error
        detail = detail or str(payload.get("error_description") or "")
    detail = detail or code or f"HTTP {response.status_code}"
    return ChatGPTError(f"ChatGPT sign-in service: {detail}", code)


def _token_request(form: dict[str, str]) -> dict[str, Any]:
    try:
        response = httpx.post(TOKEN_URL, data=form, timeout=AUTH_TIMEOUT)
    except httpx.HTTPError as exc:
        raise ChatGPTError(f"Could not reach the ChatGPT sign-in service: {exc}") from exc
    if response.status_code >= 400:
        raise _auth_error(response)
    data = response.json()
    if not isinstance(data, dict):
        raise ChatGPTError("The ChatGPT sign-in service sent an answer that could not be read.")
    return data


def _discovery() -> dict[str, Any]:
    response = httpx.get(DISCOVERY_URL, timeout=AUTH_TIMEOUT)
    if response.status_code >= 400:
        raise _auth_error(response)
    data = response.json()
    return data if isinstance(data, dict) else {}


def _openai_https(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host == "openai.com" or host.endswith(".openai.com"))


def signing_keys() -> dict[str, tuple[int, int]]:
    """OpenAI's published RSA signing keys, by key id."""
    jwks_uri = str(_discovery().get("jwks_uri") or "")
    if not _openai_https(jwks_uri):
        raise ChatGPTError("The ChatGPT sign-in service named an unexpected key address.")
    response = httpx.get(jwks_uri, timeout=AUTH_TIMEOUT)
    if response.status_code >= 400:
        raise _auth_error(response)
    keys: dict[str, tuple[int, int]] = {}
    for key in response.json().get("keys", []):
        if isinstance(key, dict) and key.get("kty") == "RSA" and key.get("n") and key.get("e"):
            n = int.from_bytes(_unb64url(key["n"]), "big")
            e = int.from_bytes(_unb64url(key["e"]), "big")
            keys[str(key.get("kid") or "")] = (n, e)
    return keys


# DER prefix of a SHA-256 DigestInfo, as RSASSA-PKCS1-v1_5 (RFC 8017) pads it.
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def rs256_valid(signing_input: bytes, signature: bytes, n: int, e: int) -> bool:
    """RS256 verification with the standard library alone."""
    size = (n.bit_length() + 7) // 8
    if size < 256 or len(signature) != size:  # 2048-bit keys or larger
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    digest = _SHA256_DIGEST_INFO + hashlib.sha256(signing_input).digest()
    expected = b"\x00\x01" + b"\xff" * (size - len(digest) - 3) + b"\x00" + digest
    return hmac.compare_digest(pow(s, e, n).to_bytes(size, "big"), expected)


def validate_id_token(
    token: str,
    client_id: str,
    nonce: str,
    keys: dict[str, tuple[int, int]],
    now: float | None = None,
) -> dict[str, Any]:
    """The ID token's claims, once signature, issuer, audience, expiry and nonce check out."""
    invalid = ChatGPTError("ChatGPT sent a sign-in token that did not check out.")
    try:
        header_part, claims_part, signature_part = token.split(".")
        header = json.loads(_unb64url(header_part))
        claims = json.loads(_unb64url(claims_part))
        signature = _unb64url(signature_part)
    except (ValueError, TypeError) as exc:
        raise invalid from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise invalid
    key = keys.get(str(header.get("kid") or ""))
    if header.get("alg") != "RS256" or key is None:
        raise invalid
    signing_input = f"{header_part}.{claims_part}".encode("ascii")
    if not rs256_valid(signing_input, signature, *key):
        raise invalid
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    now = time.time() if now is None else now
    try:
        expires = float(claims.get("exp") or 0)
    except (TypeError, ValueError) as exc:
        raise invalid from exc
    if (
        claims.get("iss") != ISSUER
        or client_id not in audiences
        or expires + CLOCK_SKEW < now
        or not _same(str(claims.get("nonce") or ""), nonce)
        or not claims.get("sub")
    ):
        raise invalid
    return claims


# ----- The browser sign-in -----

_PAGE = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>BlindPilot</title></head>'
    "<body><main><h1>BlindPilot</h1><p>{text}</p></main></body></html>"
)


class _CallbackServer:
    """Waits on http://127.0.0.1:PORT/auth/callback for the browser to come back."""

    def __init__(self, state: str):
        self.state = state
        self.result: dict[str, str] | None = None
        self.arrived = Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
                url = urlsplit(self.path)
                if url.path != CALLBACK_PATH:
                    self._page(404, "Not found.")
                    return
                params = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
                # Any other state is not this sign-in's answer: ignore it.
                if not _same(params.get("state", ""), outer.state):
                    self._page(400, "This address does not belong to the current sign-in.")
                    return
                if params.get("error"):
                    self._page(200, "Sign-in was not completed. You can close this tab.")
                else:
                    self._page(200, "Signed in. You can close this tab and go back to BlindPilot.")
                if outer.result is None:
                    outer.result = params
                    outer.arrived.set()

            def _page(self, status: int, text: str) -> None:
                body = _PAGE.format(text=escape(text)).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                # The request line carries the authorization code: never log it.
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self.httpd.server_address[1])
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def wait(self, timeout: float, cancel: Event | None) -> dict[str, str]:
        deadline = time.monotonic() + timeout
        while not self.arrived.wait(0.2):
            if cancel is not None and cancel.is_set():
                raise ChatGPTError("ChatGPT sign-in was cancelled.", "cancelled")
            if time.monotonic() >= deadline:
                raise ChatGPTError("ChatGPT sign-in timed out. Try again.", "timeout")
        assert self.result is not None
        return self.result

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def sign_in(
    previous: dict[str, Any],
    cancel: Event | None = None,
    open_browser: Callable[[str], bool] = webbrowser.open,
    timeout: float = SIGN_IN_TIMEOUT,
) -> dict[str, Any]:
    """Run the browser sign-in and return the new session with its access token.

    ``previous`` is the account's stored session, or empty for a new one. Its
    issued client id is reused, and the new ID token must name the same person.
    Nothing is stored here: the caller keeps the result with ``store_sign_in``.
    """
    client_id = str(previous.get("client_id") or DYNAMIC_CLIENT)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    server = _CallbackServer(state)
    redirect_uri = f"http://127.0.0.1:{server.port}{CALLBACK_PATH}"
    try:
        params = [("client_id", client_id)]
        if client_id == DYNAMIC_CLIENT:
            params.append(("agent_name_hint", AGENT_NAME))
        params += [
            ("ext_agent_host_id", host_id()),
            ("response_type", "code"),
            ("redirect_uri", redirect_uri),
            ("scope", SCOPES),
            ("resource", RESOURCE),
            ("state", state),
            ("nonce", nonce),
            ("code_challenge_method", "S256"),
            ("code_challenge", challenge),
        ]
        if previous.get("email"):
            params.append(("login_hint", str(previous["email"])))
        if not open_browser(AUTHORIZE_URL + "?" + urlencode(params, quote_via=quote)):
            raise ChatGPTError("Could not open your web browser for the ChatGPT sign-in.")
        callback = server.wait(timeout, cancel)
    finally:
        server.close()

    error = callback.get("error")
    if error == "access_denied":
        raise ChatGPTError(
            "ChatGPT sign-in was declined, so BlindPilot cannot use your ChatGPT plan. "
            "Choose Continue with ChatGPT again and allow it, or use an OpenAI account with an API key.",
            error,
        )
    if error:
        detail = callback.get("error_description") or error
        raise ChatGPTError(f"ChatGPT sign-in failed: {detail}", error)
    returned = callback.get("client_id", "")
    if client_id == DYNAMIC_CLIENT:
        if not returned or returned == DYNAMIC_CLIENT:
            raise ChatGPTError("ChatGPT did not finish registering BlindPilot. Try again.")
        client_id = returned
    elif returned and returned != client_id:
        raise ChatGPTError("ChatGPT answered for a different app registration. Try again.")
    code = callback.get("code")
    if not code:
        raise ChatGPTError("ChatGPT did not send a sign-in code. Try again.")
    if cancel is not None and cancel.is_set():
        raise ChatGPTError("ChatGPT sign-in was cancelled.", "cancelled")

    tokens = _token_request(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "resource": RESOURCE,
        }
    )
    if not all(
        isinstance(tokens.get(k), str) for k in ("access_token", "refresh_token", "id_token")
    ):
        raise ChatGPTError("ChatGPT sent an incomplete sign-in answer. Try again.")
    claims = validate_id_token(tokens["id_token"], client_id, nonce, signing_keys())
    if previous.get("subject") and previous["subject"] != claims["sub"]:
        raise ChatGPTError(
            "That is a different ChatGPT account from the one this account signed in with. "
            "Add a new account for it instead."
        )
    scopes = scope_list(tokens.get("scope"))
    if PLAN_SCOPE not in scopes:
        raise ChatGPTError(
            "You signed in, but ChatGPT did not allow BlindPilot to use your ChatGPT plan. "
            "Choose Continue with ChatGPT again and allow plan usage."
        )
    return {
        "client_id": client_id,
        "email": str(claims.get("email") or ""),
        "subject": str(claims["sub"]),
        "refresh_token": tokens["refresh_token"],
        "scopes": scopes,
        "access_token": tokens["access_token"],
        "expires_at": time.time() + _lifetime(tokens),
    }


def revoke(session: dict[str, Any]) -> bool:
    """End the renewable session at OpenAI. True when OpenAI confirmed it."""
    if not is_signed_in(session):
        return True
    try:
        url = str(_discovery().get("revocation_endpoint") or "")
        if not _openai_https(url):
            return False
        response = httpx.post(
            url,
            data={
                "token": session["refresh_token"],
                "token_type_hint": "refresh_token",
                "client_id": session["client_id"],
            },
            timeout=AUTH_TIMEOUT,
        )
    except (httpx.HTTPError, ChatGPTError, ValueError):
        return False
    return response.status_code == 200


def sign_out(credentials: CredentialStore, account_id: int) -> bool:
    """Revoke and forget the tokens; keep the registration for the next sign-in.

    Returns whether OpenAI confirmed the revocation.
    """
    session = load_session(credentials, account_id)
    confirmed = revoke(session)
    with _refresh_lock:
        session.pop("refresh_token", None)
        save_session(credentials, account_id, session)
        _access_tokens.pop(account_id, None)
    return confirmed


# ----- The provider -----


def responses_input(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Instructions and input items for a plan request.

    Plan usage rejects system items in ``input``, so system and developer text
    becomes ``instructions``. Audio and video are not accepted on this route.
    """
    instructions: list[str] = []
    items: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role", "user"))
        content = str(message.get("content", ""))
        if role in {"system", "developer"}:
            instructions.append(content)
            continue
        if role not in {"user", "assistant"}:
            continue
        attachments = message.get("attachments") or []
        if role == "assistant" or not attachments:
            items.append({"role": role, "content": content})
            continue
        parts: list[dict[str, Any]] = [
            {"type": "input_text", "text": content or "Review the attached files."}
        ]
        for attachment in attachments:
            mime_type = attachment.mime_type or "application/octet-stream"
            suffix = PurePath(attachment.filename).suffix.lower()
            data_url = (
                f"data:{mime_type};base64,{base64.b64encode(attachment.data).decode('ascii')}"
            )
            if mime_type.startswith(("audio/", "video/")):
                raise ProviderError(
                    f"{attachment.filename}: ChatGPT plan usage does not accept audio or video files."
                )
            if mime_type.startswith("image/"):
                parts.append({"type": "input_image", "image_url": data_url})
            elif mime_type.startswith("text/") or suffix in TEXT_EXTENSIONS:
                text = attachment.data.decode("utf-8", errors="replace")
                parts.append(
                    {
                        "type": "input_text",
                        "text": f"[Attached file: {attachment.filename}]\n{text}",
                    }
                )
            else:
                parts.append(
                    {"type": "input_file", "filename": attachment.filename, "file_data": data_url}
                )
        items.append({"role": "user", "content": parts})
    return "\n\n".join(instructions), items


def plan_reasoning(features: OpenRouterFeatures) -> dict[str, Any] | None:
    """The profile's thinking choices as a Responses ``reasoning`` object.

    Plan models think whatever is asked; "Read the thinking" asks for the
    summary OpenAI streams back, which is all of the thinking it shares.
    """
    reasoning: dict[str, Any] = {}
    if features.reasoning_effort:
        reasoning["effort"] = features.reasoning_effort
    if features.show_reasoning and features.reasoning_effort != "none":
        reasoning["summary"] = "auto"
    return reasoning or None


def plan_tools(features: OpenRouterFeatures) -> list[dict[str, Any]]:
    """Web search is the one hosted tool plan usage runs; the others are refused."""
    if SERVER_TOOL_WEB_SEARCH not in features.server_tools:
        return []
    tool: dict[str, Any] = {"type": "web_search"}
    if features.search_context:
        tool["search_context_size"] = features.search_context
    return [tool]


# The plan's model catalogue by slug: thinking levels, length support and
# display names, read whenever the model list is fetched.
_catalog: dict[str, dict[str, Any]] = {}
# Set once a chat has tried to read the catalogue, so a failure costs one try.
_catalog_tried = Event()


def fit_to_model(
    model: str, reasoning: dict[str, Any] | None, verbosity: str
) -> tuple[dict[str, Any] | None, str, str]:
    """Drop what the chosen model does not take, and say so.

    A plan model rejects a thinking level it does not offer with a 400, and
    the profile is shared by every model, so an unknown level falls back to the
    model's own default rather than failing the request.
    """
    entry = _catalog.get(model)
    if not entry:
        return reasoning, verbosity, ""
    notice = ""
    reasoning = dict(reasoning) if reasoning else None
    levels = [
        str(level.get("effort"))
        for level in entry.get("supported_reasoning_levels") or []
        if isinstance(level, dict)
    ]
    effort = (reasoning or {}).get("effort")
    if reasoning and effort and levels and effort not in levels:
        reasoning.pop("effort")
        name = entry.get("display_name") or model
        default = entry.get("default_reasoning_level") or "its default"
        notice = (
            f"{name} does not offer the thinking effort {REASONING_EFFORT_LABELS.get(effort, effort)}, "
            f"so it used its default, {default}."
        )
    if reasoning and not entry.get("supports_reasoning_summary_parameter", True):
        reasoning.pop("summary", None)
    if verbosity and not entry.get("support_verbosity", True):
        verbosity = ""
    return reasoning or None, verbosity, notice


def plan_error(error: Any, status: int | None = None) -> ChatGPTError:
    """The sentence for a plan-usage failure, from its error object or status."""
    error = error if isinstance(error, dict) else {}
    code = str(error.get("code") or "")
    detail = str(error.get("message") or error.get("detail") or "")
    if code == "subscription_sharing_unsupported_capability":
        param = error.get("param") or detail or "part of the request"
        return ChatGPTError(f"ChatGPT plan usage does not support {param}.", code)
    if code in PLAN_ERRORS:
        return ChatGPTError(PLAN_ERRORS[code], code)
    if status == 401:
        return ChatGPTError("ChatGPT did not accept this sign-in. " + SIGN_IN_AGAIN, code)
    if status == 429:
        return ChatGPTError(PLAN_ERRORS["subscription_sharing_usage_limit_exceeded"], code)
    prefix = f"HTTP {status}: " if status else "ChatGPT: "
    return ChatGPTError(prefix + (detail or code or "the request failed."), code)


class ChatGPTProvider(BaseProvider):
    """OpenAI's Responses API, paid for by the signed-in ChatGPT plan."""

    def _headers(self, force: bool = False) -> dict[str, str]:
        headers = {str(k): str(v) for k, v in self.account.custom_headers.items()}
        token = access_token(self.credentials, int(self.account.id), force=force)  # type: ignore[arg-type]
        headers["Authorization"] = f"Bearer {token}"
        headers["Content-Type"] = "application/json"
        return headers

    def headers(self) -> dict[str, str]:
        return self._headers()

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        try:
            response.read()
            payload = response.json()
        except Exception:
            payload = {}
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            # Direct admission can answer {"detail": "..."} before Responses starts.
            error = payload if isinstance(payload, dict) else {}
        raise plan_error(error, response.status_code)

    def list_models(self) -> list[str]:
        url = self.build_url(self.account.models_endpoint)
        with self.client() as client:
            response = client.get(url, headers=self._headers())
            if response.status_code == 401:
                response = client.get(url, headers=self._headers(force=True))
            self._raise_for_status(response)
            payload = response.json()
        models: list[str] = []
        if isinstance(payload, dict) and isinstance(payload.get("models"), list):
            for item in payload["models"]:
                if (
                    isinstance(item, dict)
                    and item.get("slug")
                    and item.get("visibility", "list") == "list"
                ):
                    models.append(str(item["slug"]))
                    _catalog[str(item["slug"])] = item
        elif isinstance(payload, dict):
            for item in payload.get("data", []):
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    models.append(item["id"])
        return list(dict.fromkeys(models))

    def generate(self, settings: GenerationSettings, cancel: Event) -> Iterator[StreamEvent]:
        instructions, items = responses_input(settings.messages)
        body: dict[str, Any] = dict(self.account.custom_body)
        # Temperature and output limits are not accepted on plan usage, and
        # every request must be streamed and left unstored.
        body.update({"model": settings.model, "input": items, "store": False, "stream": True})
        if instructions:
            body["instructions"] = instructions
        if not _catalog and not _catalog_tried.is_set():
            # Read once per run, so the thinking level can be fitted to the model.
            # Best effort: the request below reports any real failure itself.
            _catalog_tried.set()
            try:
                self.list_models()
            except Exception:
                logger.info("Could not read the ChatGPT plan model catalogue", exc_info=True)
        reasoning, verbosity, notice = fit_to_model(
            settings.model, settings.reasoning, settings.verbosity
        )
        if notice:
            yield StreamEvent("status", text=notice)
        if reasoning:
            body["reasoning"] = reasoning
        if verbosity:
            body["text"] = {"verbosity": verbosity}
        if settings.tools:
            body["tools"] = settings.tools
        url = self.build_url(self.account.responses_endpoint)
        with self.client() as client:
            for attempt in (0, 1):
                headers = self._headers(force=attempt == 1)
                with client.stream("POST", url, headers=headers, json=body) as response:
                    # One 401 may only mean the access token was just rotated.
                    if response.status_code == 401 and attempt == 0:
                        continue
                    self._raise_for_status(response)
                    yield StreamEvent("headers", metadata=dict(response.headers))
                    yield from self._read_stream(response, cancel)
                    return

    @staticmethod
    def _read_stream(response: httpx.Response, cancel: Event) -> Iterator[StreamEvent]:
        received_text = False
        finished = False
        sources = SourceCollector()
        for data in iter_sse_json(response, cancel):
            kind = data.get("type")
            if kind == "response.output_text.delta":
                delta = data.get("delta")
                if isinstance(delta, str) and delta:
                    received_text = True
                    yield StreamEvent("text", text=delta)
            elif kind == "response.reasoning_summary_text.delta":
                delta = data.get("delta")
                if isinstance(delta, str) and delta:
                    yield StreamEvent("reasoning", text=delta)
            elif kind == "response.reasoning_summary_part.added" and data.get("summary_index"):
                # Each summary part opens with its own bold heading.
                yield StreamEvent("reasoning", text="\n\n")
            elif kind == "response.output_text.annotation.added":
                sources.absorb({"annotations": [data.get("annotation")]})
            elif kind == "response.output_item.done":
                item = data.get("item") or {}
                if item.get("type") == "web_search_call":
                    action = item.get("action") or {}
                    query = action.get("query") or ", ".join(action.get("queries") or [])
                    said = f"Searched the web for {query}" if query else "Searched the web"
                    yield StreamEvent("tool", text=said)
            elif kind == "response.completed":
                finished = True
            elif kind == "response.incomplete":
                finished = True
                reason = ((data.get("response") or {}).get("incomplete_details") or {}).get(
                    "reason"
                )
                notice = (
                    LENGTH_LIMIT_NOTICE
                    if reason == "max_output_tokens"
                    else f"Response incomplete: {reason}"
                )
                yield StreamEvent("status", text=notice, metadata={"record": True})
            elif kind == "response.failed":
                raise plan_error((data.get("response") or {}).get("error"))
            elif kind == "error":
                raise plan_error(data)
        if cancel.is_set():
            yield StreamEvent("done")
            return
        if not finished:
            raise ProviderError("The ChatGPT answer stopped before it was complete. Try again.")
        if not received_text:
            raise ProviderError("The provider completed the request without returning any text.")
        listed = sources.listing()
        if listed:
            yield StreamEvent("sources", text=listed, metadata={"sources": sources.as_list()})
        yield StreamEvent("done")
