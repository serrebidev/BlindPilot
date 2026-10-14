"""Sign in with ChatGPT: chatting on the user's ChatGPT plan, not an API key.

These tests pin the parts of OpenAI's open-source plan-usage guide that are
easy to get quietly wrong: the authorization request, the loopback callback
and its state check, keeping the issued client id, the ID-token checks, the
rotating refresh token, and the request body plan usage accepts.
"""

from __future__ import annotations

import base64
import hashlib
import json
import random
import time
from threading import Event
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from accessible_ai.models import (
    Account,
    MessageAttachment,
    PROVIDER_CHATGPT,
    PROVIDER_LABELS,
)
from accessible_ai.providers import chatgpt
from accessible_ai.providers.base import ProviderError
from accessible_ai.providers.factory import create_provider
from accessible_ai.ui.accounts import BUILTIN_PROVIDER_NOTES, PROVIDER_ORDER


class FakeCredentials:
    def __init__(self) -> None:
        self.values: dict[int, str] = {}

    def get_api_key(self, account_id: int) -> str:
        return self.values.get(account_id, "")

    def set_api_key(self, account_id: int, value: str) -> None:
        self.values[account_id] = value


# ----- A real RSA key, so the signature check is exercised for real -----


def _probable_prime(bits: int, rng: random.Random) -> int:
    while True:
        n = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if all(n % p for p in (3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)) and all(
            pow(a, n - 1, n) == 1 for a in (2, 3, 5, 7, 11, 13, 17)
        ):
            return n


@pytest.fixture(scope="module")
def rsa_key() -> tuple[int, int, int]:
    rng = random.Random(1455)
    e = 65537
    while True:
        p, q = _probable_prime(1024, rng), _probable_prime(1024, rng)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e:
            return p * q, e, pow(e, -1, phi)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sign(key: tuple[int, int, int], claims: dict, header: dict | None = None) -> str:
    n, _e, d = key
    head = _b64(json.dumps(header or {"alg": "RS256", "kid": "k1"}).encode())
    body = _b64(json.dumps(claims).encode())
    size = (n.bit_length() + 7) // 8
    digest = chatgpt._SHA256_DIGEST_INFO + hashlib.sha256(f"{head}.{body}".encode()).digest()
    padded = b"\x00\x01" + b"\xff" * (size - len(digest) - 3) + b"\x00" + digest
    signature = pow(int.from_bytes(padded, "big"), d, n).to_bytes(size, "big")
    return f"{head}.{body}.{_b64(signature)}"


def _claims(**overrides) -> dict:
    claims = {
        "iss": chatgpt.ISSUER,
        "aud": "oaiapp_issued",
        "sub": "user-1",
        "email": "person@example.com",
        "nonce": "the-nonce",
        "exp": time.time() + 600,
    }
    claims.update(overrides)
    return claims


def test_a_correctly_signed_id_token_is_accepted(rsa_key):
    keys = {"k1": (rsa_key[0], rsa_key[1])}
    claims = chatgpt.validate_id_token(
        _sign(rsa_key, _claims()), "oaiapp_issued", "the-nonce", keys
    )
    assert claims["sub"] == "user-1"


@pytest.mark.parametrize(
    "claims, header",
    [
        (_claims(nonce="another"), None),
        (_claims(aud="oaiapp_someone_else"), None),
        (_claims(iss="https://evil.example"), None),
        (_claims(exp=time.time() - 3600), None),
        (_claims(), {"alg": "none", "kid": "k1"}),
        (_claims(), {"alg": "RS256", "kid": "unknown"}),
    ],
)
def test_an_id_token_that_does_not_check_out_is_refused(rsa_key, claims, header):
    keys = {"k1": (rsa_key[0], rsa_key[1])}
    with pytest.raises(chatgpt.ChatGPTError):
        chatgpt.validate_id_token(
            _sign(rsa_key, claims, header), "oaiapp_issued", "the-nonce", keys
        )


def test_a_tampered_id_token_is_refused(rsa_key):
    keys = {"k1": (rsa_key[0], rsa_key[1])}
    head, _body, signature = _sign(rsa_key, _claims()).split(".")
    forged = _b64(json.dumps(_claims(sub="attacker")).encode())
    with pytest.raises(chatgpt.ChatGPTError):
        chatgpt.validate_id_token(
            f"{head}.{forged}.{signature}", "oaiapp_issued", "the-nonce", keys
        )


# ----- The browser sign-in, with the browser played by a test -----


def _fake_auth(monkeypatch, rsa_key, scope=chatgpt.SCOPES, subject="user-1"):
    """Stand in for auth.openai.com. Returns the token requests it received."""
    received: list[dict] = []
    nonce_box: dict[str, str] = {}

    def token_request(form):
        received.append(form)
        return {
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "id_token": _sign(rsa_key, _claims(nonce=nonce_box["nonce"], sub=subject)),
            "scope": scope,
            "expires_in": 3600,
        }

    monkeypatch.setattr(chatgpt, "_token_request", token_request)
    monkeypatch.setattr(chatgpt, "signing_keys", lambda: {"k1": (rsa_key[0], rsa_key[1])})
    monkeypatch.setattr(chatgpt, "host_id", lambda: "urn:uuid:00000000-0000-4000-8000-000000000000")
    return received, nonce_box


def _browser(nonce_box, answer=None, opened=None):
    """A browser that goes straight back to the callback with ``answer``."""

    def open_browser(url: str) -> bool:
        params = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        if opened is not None:
            opened.append((url, params))
        nonce_box["nonce"] = params["nonce"]
        callback = {"code": "the-code", "state": params["state"], "client_id": "oaiapp_issued"}
        callback.update(answer or {})
        httpx.get(params["redirect_uri"], params=callback, timeout=5)
        return True

    return open_browser


def test_a_first_sign_in_registers_and_keeps_the_issued_client_id(monkeypatch, rsa_key):
    received, nonce_box = _fake_auth(monkeypatch, rsa_key)
    opened: list = []
    result = chatgpt.sign_in({}, open_browser=_browser(nonce_box, opened=opened), timeout=10)

    url, params = opened[0]
    assert url.startswith(chatgpt.AUTHORIZE_URL + "?")
    assert params["client_id"] == chatgpt.DYNAMIC_CLIENT
    assert params["agent_name_hint"] == "BlindPilot"
    assert params["ext_agent_host_id"].startswith("urn:uuid:")
    assert params["resource"] == "https://api.openai.com/v1"
    assert params["code_challenge_method"] == "S256"
    assert chatgpt.PLAN_SCOPE in params["scope"].split()
    redirect = urlsplit(params["redirect_uri"])
    assert (redirect.scheme, redirect.hostname, redirect.path) == (
        "http",
        "127.0.0.1",
        "/auth/callback",
    )

    exchange = received[0]
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["client_id"] == "oaiapp_issued"
    assert exchange["redirect_uri"] == params["redirect_uri"]
    assert (
        base64.urlsafe_b64encode(hashlib.sha256(exchange["code_verifier"].encode()).digest())
        .rstrip(b"=")
        .decode()
        == params["code_challenge"]
    )
    assert result["client_id"] == "oaiapp_issued"
    assert result["email"] == "person@example.com"
    assert result["refresh_token"] == "refresh-1"


def test_a_returning_sign_in_reuses_the_client_id_without_the_name_hint(monkeypatch, rsa_key):
    _received, nonce_box = _fake_auth(monkeypatch, rsa_key)
    opened: list = []
    previous = {"client_id": "oaiapp_issued", "email": "person@example.com", "subject": "user-1"}
    chatgpt.sign_in(previous, open_browser=_browser(nonce_box, opened=opened), timeout=10)
    params = opened[0][1]
    assert params["client_id"] == "oaiapp_issued"
    assert "agent_name_hint" not in params
    assert params["login_hint"] == "person@example.com"


def test_a_returning_sign_in_answered_for_another_client_is_refused(monkeypatch, rsa_key):
    received, nonce_box = _fake_auth(monkeypatch, rsa_key)
    browser = _browser(nonce_box, answer={"client_id": "oaiapp_other"})
    with pytest.raises(chatgpt.ChatGPTError, match="different app registration"):
        chatgpt.sign_in({"client_id": "oaiapp_issued"}, open_browser=browser, timeout=10)
    assert received == []


def test_a_sign_in_as_a_different_person_is_refused(monkeypatch, rsa_key):
    _received, nonce_box = _fake_auth(monkeypatch, rsa_key, subject="someone-else")
    previous = {"client_id": "oaiapp_issued", "subject": "user-1"}
    with pytest.raises(chatgpt.ChatGPTError, match="different ChatGPT account"):
        chatgpt.sign_in(previous, open_browser=_browser(nonce_box), timeout=10)


def test_a_sign_in_without_plan_permission_is_refused(monkeypatch, rsa_key):
    _received, nonce_box = _fake_auth(
        monkeypatch, rsa_key, scope="openid profile email offline_access"
    )
    with pytest.raises(chatgpt.ChatGPTError, match="did not allow BlindPilot"):
        chatgpt.sign_in({}, open_browser=_browser(nonce_box), timeout=10)


def test_a_declined_sign_in_exchanges_nothing(monkeypatch, rsa_key):
    received, nonce_box = _fake_auth(monkeypatch, rsa_key)
    browser = _browser(nonce_box, answer={"error": "access_denied", "code": ""})
    with pytest.raises(chatgpt.ChatGPTError, match="declined"):
        chatgpt.sign_in({}, open_browser=browser, timeout=10)
    assert received == []


def test_a_callback_with_another_state_is_ignored(monkeypatch, rsa_key):
    """A page that guesses the port cannot finish someone's sign-in."""
    _fake_auth(monkeypatch, rsa_key)
    statuses: list[int] = []

    def browser(url: str) -> bool:
        params = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        answer = {"code": "stolen", "state": "guessed", "client_id": "oaiapp_x"}
        statuses.append(httpx.get(params["redirect_uri"], params=answer, timeout=5).status_code)
        return True

    with pytest.raises(chatgpt.ChatGPTError, match="timed out"):
        chatgpt.sign_in({}, open_browser=browser, timeout=0.5)
    assert statuses == [400]


def test_cancel_stops_a_sign_in_waiting_on_the_browser(monkeypatch, rsa_key):
    _fake_auth(monkeypatch, rsa_key)
    cancel = Event()

    def browser(url: str) -> bool:
        cancel.set()
        return True

    with pytest.raises(chatgpt.ChatGPTError) as caught:
        chatgpt.sign_in({}, cancel=cancel, open_browser=browser, timeout=10)
    assert caught.value.code == "cancelled"


# ----- Keeping and refreshing the session -----


def test_only_the_small_part_of_the_session_is_stored():
    """Windows Credential Manager holds 1,280 characters; the access token alone is more."""
    credentials = FakeCredentials()
    chatgpt.store_sign_in(
        credentials,  # type: ignore[arg-type]
        7,
        {
            "client_id": "oaiapp_issued",
            "email": "person@example.com",
            "subject": "user-1",
            "refresh_token": "r" * 220,
            "scopes": sorted(chatgpt.SCOPES.split()),
            "access_token": "a" * 1900,
            "expires_at": time.time() + 3600,
        },
    )
    stored = json.loads(credentials.values[7])
    assert "access_token" not in stored
    assert len(credentials.values[7]) < 1280
    assert chatgpt.access_token(credentials, 7) == "a" * 1900  # type: ignore[arg-type]


def test_a_refresh_saves_the_rotated_refresh_token(monkeypatch):
    credentials = FakeCredentials()
    chatgpt.save_session(credentials, 8, {"client_id": "oaiapp_issued", "refresh_token": "old"})  # type: ignore[arg-type]
    sent: list[dict] = []

    def token_request(form):
        sent.append(form)
        return {"access_token": "fresh", "refresh_token": "new", "scope": chatgpt.SCOPES}

    monkeypatch.setattr(chatgpt, "_token_request", token_request)
    assert chatgpt.access_token(credentials, 8, force=True) == "fresh"  # type: ignore[arg-type]
    assert sent[0]["client_id"] == "oaiapp_issued"
    assert sent[0]["resource"] == chatgpt.RESOURCE
    assert "scope" not in sent[0]
    assert json.loads(credentials.values[8])["refresh_token"] == "new"


def test_a_dead_refresh_token_asks_for_a_new_sign_in(monkeypatch):
    credentials = FakeCredentials()
    chatgpt.save_session(credentials, 9, {"client_id": "oaiapp_issued", "refresh_token": "old"})  # type: ignore[arg-type]

    def token_request(form):
        raise chatgpt.ChatGPTError("gone", "refresh_token_reused")

    monkeypatch.setattr(chatgpt, "_token_request", token_request)
    with pytest.raises(chatgpt.ChatGPTError, match="Continue with ChatGPT"):
        chatgpt.access_token(credentials, 9, force=True)  # type: ignore[arg-type]
    kept = json.loads(credentials.values[9])
    assert kept == {"client_id": "oaiapp_issued"}  # the registration stays for the next sign-in


# ----- Requests on the plan -----


def test_system_text_becomes_instructions_and_files_become_input_parts():
    image = MessageAttachment(filename="a.png", mime_type="image/png", data=b"png")
    pdf = MessageAttachment(filename="b.pdf", mime_type="application/pdf", data=b"pdf")
    note = MessageAttachment(filename="c.py", mime_type="", data=b"print(1)")
    instructions, items = chatgpt.responses_input(
        [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Look", "attachments": [image, pdf, note]},
            {"role": "assistant", "content": "Seen."},
            {"role": "status", "content": "Error: something"},
        ]
    )
    assert instructions == "Be brief."
    assert [item["role"] for item in items] == ["user", "assistant"]
    kinds = [part["type"] for part in items[0]["content"]]
    assert kinds == ["input_text", "input_image", "input_file", "input_text"]


def test_audio_is_refused_before_anything_is_sent():
    audio = MessageAttachment(filename="a.mp3", mime_type="audio/mpeg", data=b"mp3")
    with pytest.raises(ProviderError, match="audio or video"):
        chatgpt.responses_input([{"role": "user", "content": "x", "attachments": [audio]}])


def _sse(*events: dict) -> bytes:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()


def _provider(monkeypatch, handler) -> chatgpt.ChatGPTProvider:
    account = Account(id=3, name="Plan", provider=PROVIDER_CHATGPT)
    provider = create_provider(account, FakeCredentials())  # type: ignore[arg-type]
    assert isinstance(provider, chatgpt.ChatGPTProvider)
    monkeypatch.setattr(chatgpt, "access_token", lambda *_a, **_k: "token")
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(provider, "client", lambda: httpx.Client(transport=transport))
    return provider


def test_a_plan_request_is_streamed_unstored_and_without_unsupported_fields(monkeypatch):
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer token"
        return httpx.Response(
            200,
            content=_sse(
                {"type": "response.output_text.delta", "delta": "Hel"},
                {"type": "response.output_text.delta", "delta": "lo"},
                {"type": "response.completed", "response": {}},
            ),
        )

    provider = _provider(monkeypatch, handler)
    settings = SimpleNamespace(
        model="gpt-test",
        messages=[{"role": "system", "content": "Hi"}, {"role": "user", "content": "Say hello"}],
        temperature=0.4,
        max_output_tokens=99,
    )
    events = list(provider.generate(settings, Event()))  # type: ignore[arg-type]
    assert "".join(e.text for e in events if e.kind == "text") == "Hello"
    body = bodies[0]
    assert body["store"] is False and body["stream"] is True
    assert body["instructions"] == "Hi"
    assert "temperature" not in body and "max_output_tokens" not in body


def test_a_usage_limit_mid_stream_points_at_chatgpt_usage(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        failed = {
            "type": "response.failed",
            "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}},
        }
        return httpx.Response(200, content=_sse(failed))

    provider = _provider(monkeypatch, handler)
    settings = SimpleNamespace(model="m", messages=[{"role": "user", "content": "x"}])
    with pytest.raises(chatgpt.ChatGPTError, match="chatgpt.com/settings/usage"):
        list(provider.generate(settings, Event()))  # type: ignore[arg-type]


def test_a_stream_that_never_completes_is_an_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=_sse({"type": "response.output_text.delta", "delta": "Hal"})
        )

    provider = _provider(monkeypatch, handler)
    settings = SimpleNamespace(model="m", messages=[{"role": "user", "content": "x"}])
    with pytest.raises(ProviderError, match="stopped before"):
        list(provider.generate(settings, Event()))  # type: ignore[arg-type]


def test_one_401_retries_with_a_refreshed_token(monkeypatch):
    calls: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(True)
        if len(calls) == 1:
            return httpx.Response(401, json={"detail": "expired"})
        return httpx.Response(
            200,
            content=_sse(
                {"type": "response.output_text.delta", "delta": "ok"},
                {"type": "response.completed", "response": {}},
            ),
        )

    provider = _provider(monkeypatch, handler)
    settings = SimpleNamespace(model="m", messages=[{"role": "user", "content": "x"}])
    events = list(provider.generate(settings, Event()))  # type: ignore[arg-type]
    assert len(calls) == 2 and events[-1].kind == "done"


def test_the_plan_model_list_keeps_listed_models_in_server_order(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [
                    {"slug": "gpt-b", "visibility": "list"},
                    {"slug": "gpt-hidden", "visibility": "hide"},
                    {"slug": "gpt-a", "visibility": "list"},
                ]
            },
        )

    assert _provider(monkeypatch, handler).list_models() == ["gpt-b", "gpt-a"]


def test_the_plan_is_offered_in_the_account_dialog():
    assert PROVIDER_LABELS[PROVIDER_CHATGPT] == "ChatGPT plan"
    assert PROVIDER_CHATGPT in PROVIDER_ORDER
    assert chatgpt.USAGE_URL in BUILTIN_PROVIDER_NOTES[PROVIDER_CHATGPT]


def test_the_account_dialog_signs_in_instead_of_asking_for_a_key(frame, tmp_path):
    """The session never lands in the key field, and OK keeps a fresh sign-in."""
    import wx

    from accessible_ai.storage.database import Database
    from accessible_ai.ui.accounts import AccountEditorDialog

    db = Database(tmp_path / "chat.sqlite3")
    credentials = FakeCredentials()
    account_id = db.save_account(Account(name="Plan", provider=PROVIDER_CHATGPT))
    chatgpt.save_session(
        credentials, account_id, {"client_id": "oaiapp_issued", "email": "p@example.com"}
    )  # type: ignore[arg-type]

    # Built the way the installed app builds it, where a layout assert is fatal.
    app = wx.GetApp()
    old_mode = app.GetAssertMode()
    app.SetAssertMode(wx.APP_ASSERT_EXCEPTION)
    try:
        dialog = AccountEditorDialog(frame, db, credentials, db.get_account(account_id))  # type: ignore[arg-type]
    finally:
        app.SetAssertMode(old_mode)
    try:
        assert dialog.api_key.GetValue() == ""
        assert not dialog.api_key.IsShown()
        assert dialog.chatgpt_button.IsShown()
        assert dialog.chatgpt_status.GetLabel() == "Not signed in."
        assert not dialog.chatgpt_sign_out.IsEnabled()

        dialog.chatgpt_pending = {
            "client_id": "oaiapp_issued",
            "email": "p@example.com",
            "subject": "user-1",
            "refresh_token": "refresh-1",
            "scopes": [chatgpt.PLAN_SCOPE],
            "access_token": "access-1",
            "expires_at": time.time() + 3600,
        }
        dialog._update_chatgpt_status()
        assert dialog.chatgpt_status.GetLabel() == "Signed in as p@example.com."
        assert dialog.chatgpt_sign_out.IsEnabled()
        try:
            dialog.on_ok(None)
        except wx.wxAssertionError:
            pass  # EndModal on a dialog never shown modally; the save came first.
        assert json.loads(credentials.values[account_id])["refresh_token"] == "refresh-1"
    finally:
        dialog.Destroy()
