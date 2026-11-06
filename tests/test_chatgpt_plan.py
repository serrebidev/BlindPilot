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
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from accessible_ai.models import (
    Account,
    GenerationSettings,
    MessageAttachment,
    PROVIDER_CHATGPT,
    PROVIDER_LABELS,
    PROVIDER_OPENROUTER,
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


# ----- The signature check, against a signature this code did not produce -----

# RFC 7515 Appendix A.2: the public key, the signing input and the signature
# for an RSASSA-PKCS1-v1_5 SHA-256 JWS, as published in the RFC.
RFC_N = (
    "ofgWCuLjybRlzo0tZWJjNiuSfb4p4fAkd_wWJcyQoTbji9k0l8W26mPddx"
    "HmfHQp-Vaw-4qPCJrcS2mJPMEzP1Pt0Bm4d4QlL-yRT-SFd2lZS-pCgNMs"
    "D1W_YpRPEwOWvG6b32690r2jZ47soMZo9wGzjb_7OMg0LOL-bSf63kpaSH"
    "SXndS5z5rexMdbBYUsLA9e-KXBdQOS-UTo7WTBEMa2R2CapHg665xsmtdV"
    "MTBQY4uDZlxvb3qCo5ZwKh9kG4LT6_I5IhlJH7aGhyxXFvUK-DWNmoudF8"
    "NAco9_h9iaGNj8q2ethFkMLs91kzk2PAcDTW9gb54h4FRWyuXpoQ"
)
RFC_E = "AQAB"
RFC_SIGNING_INPUT = (
    "eyJhbGciOiJSUzI1NiJ9."
    "eyJpc3MiOiJqb2UiLA0KICJleHAiOjEzMDA4MTkzODAsDQogImh0dHA6Ly9leGFt"
    "cGxlLmNvbS9pc19yb290Ijp0cnVlfQ"
)
RFC_SIGNATURE = (
    "cC4hiUPoj9Eetdgtv3hF80EGrhuB__dzERat0XF9g2VtQgr9PJbu3XOiZj5RZmh7"
    "AAuHIm4Bh-0Qc_lF5YKt_O8W2Fp5jujGbds9uJdbF9CUAr7t1dnZcAcQjbKBYNX4"
    "BAynRFdiuB--f_nZLgrnbyTyWzO75vRK5h6xBArLIARNPvkSjtQBMHlb1L07Qe7K"
    "0GarZRmB_eSN9383LcOLn6_dO--xi12jzDwusC-eOkHWEsqtFZESc6BfI7noOPqv"
    "hJ1phCnvWh6IeYI2w9QOYEUipUTI8np6LbgGY9Fs98rqVt5AXLIhWkWywlVmtVrB"
    "p0igcN_IoypGlUPQGe77Rw"
)


def _rfc_key() -> tuple[int, int]:
    return (
        int.from_bytes(chatgpt._unb64url(RFC_N), "big"),
        int.from_bytes(chatgpt._unb64url(RFC_E), "big"),
    )


def test_the_rfc_7515_signature_is_accepted():
    signature = chatgpt._unb64url(RFC_SIGNATURE)
    assert chatgpt.rs256_valid(RFC_SIGNING_INPUT.encode("ascii"), signature, *_rfc_key())


def test_the_rfc_7515_signature_is_refused_once_a_byte_changes():
    signature = bytearray(chatgpt._unb64url(RFC_SIGNATURE))
    signature[0] ^= 0x01
    assert not chatgpt.rs256_valid(RFC_SIGNING_INPUT.encode("ascii"), bytes(signature), *_rfc_key())


def test_a_key_smaller_than_2048_bits_is_refused():
    small = (1 << 1024) - 1
    assert not chatgpt.rs256_valid(b"anything", b"\x01" * 128, small, 65537)


def test_a_signature_not_below_the_modulus_is_refused(rsa_key):
    n, e, _d = rsa_key
    size = (n.bit_length() + 7) // 8
    assert not chatgpt.rs256_valid(b"anything", n.to_bytes(size, "big"), n, e)
    assert not chatgpt.rs256_valid(b"anything", b"\xff" * size, n, e)


def test_a_signature_of_the_wrong_length_is_refused(rsa_key):
    n, e, _d = rsa_key
    assert not chatgpt.rs256_valid(b"anything", b"\x01" * 255, n, e)


# ----- Whose keys are trusted -----


def test_only_openai_addresses_serve_the_signing_keys():
    assert chatgpt._openai_https("https://auth.openai.com/keys")
    assert chatgpt._openai_https("https://openai.com/.well-known/jwks.json")
    assert not chatgpt._openai_https("http://auth.openai.com/keys")
    assert not chatgpt._openai_https("https://openai.com.evil.example/keys")
    assert not chatgpt._openai_https("https://evilopenai.com/keys")
    assert not chatgpt._openai_https("https://auth.openai.com@evil.example/keys")
    assert not chatgpt._openai_https("")


def test_signing_keys_refuses_a_key_address_that_is_not_openai(monkeypatch):
    def boom(url, **kwargs):
        raise AssertionError(f"fetched keys from {url}")

    monkeypatch.setattr(chatgpt, "_discovery", lambda: {"jwks_uri": "https://evil.example/keys"})
    monkeypatch.setattr(chatgpt.httpx, "get", boom)
    with pytest.raises(chatgpt.ChatGPTError):
        chatgpt.signing_keys()


def test_signing_keys_reads_the_rsa_keys_and_skips_the_rest(monkeypatch, rsa_key):
    n, e, _d = rsa_key

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "keys": [
                    {
                        "kty": "RSA",
                        "kid": "k1",
                        "n": _b64(n.to_bytes((n.bit_length() + 7) // 8, "big")),
                        "e": _b64(e.to_bytes(3, "big")),
                    },
                    {"kty": "EC", "kid": "another-algorithm"},
                ]
            }

    monkeypatch.setattr(chatgpt, "_discovery", lambda: {"jwks_uri": "https://auth.openai.com/jwks"})
    monkeypatch.setattr(chatgpt.httpx, "get", lambda url, **kwargs: Response())
    assert chatgpt.signing_keys() == {"k1": (n, e)}


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
    # generate() reads the model catalogue once per process. Whichever test
    # ran first used to spend a request of its handler on it, so a count of
    # requests passed or failed on test order.
    tried = Event()
    tried.set()
    monkeypatch.setattr(chatgpt, "_catalog_tried", tried)
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
    settings = GenerationSettings(
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
    settings = GenerationSettings(model="m", messages=[{"role": "user", "content": "x"}])
    with pytest.raises(chatgpt.ChatGPTError, match="chatgpt.com/settings/usage"):
        list(provider.generate(settings, Event()))  # type: ignore[arg-type]


def test_a_stream_that_never_completes_is_an_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=_sse({"type": "response.output_text.delta", "delta": "Hal"})
        )

    provider = _provider(monkeypatch, handler)
    settings = GenerationSettings(model="m", messages=[{"role": "user", "content": "x"}])
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
    settings = GenerationSettings(model="m", messages=[{"role": "user", "content": "x"}])
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


# ----- The Account the Codex backend uses, chosen in the account dialog -----


def _codex_account_dialog(frame, db, spoken: list[str], mirrored: list[str]):
    """The account dialog as Chat mode opens it: one sentence, spoken and shown."""
    from accessible_ai.services.model_service import ModelService
    from accessible_ai.storage.credentials import CredentialStore
    from accessible_ai.ui.accounts import AccountsDialog

    credentials = CredentialStore()
    return AccountsDialog(
        frame,
        db,
        credentials,
        ModelService(db, credentials),
        mirrored.append,
        spoken.append,
    )


def _codex_items(dialog) -> list[str]:
    return [dialog.codex_account.GetString(i) for i in range(dialog.codex_account.GetCount())]


def test_the_account_dialog_offers_a_labelled_choice_of_the_codex_account(frame, tmp_path):
    """One choice, said in words, that a screen reader can read and Tab can reach."""
    from accessible_ai.storage.database import Database

    db = Database(tmp_path / "chat.sqlite3")
    db.save_account(Account(name="Work", provider=PROVIDER_CHATGPT))
    db.save_account(Account(name="Personal", provider=PROVIDER_CHATGPT))
    db.save_account(Account(name="Keyed", provider=PROVIDER_OPENROUTER))
    dialog = _codex_account_dialog(frame, db, [], [])
    try:
        # The name is what the screen reader reads out on the control itself.
        assert dialog.codex_account.GetName() == "Codex backend account"
        # A choice takes focus, so Tab reaches it and the arrow keys change it.
        assert dialog.codex_account.IsEnabled() and dialog.codex_account.AcceptsFocus()
        # Codex's own login is still offered, still selectable, and is what an
        # install that has chosen nothing runs on.
        assert dialog.codex_account.GetString(0) == "Codex's own login"
        assert dialog.codex_account.GetSelection() == 0
        # Only a ChatGPT-plan Account can pay for a Codex turn on a plan.
        assert _codex_items(dialog) == ["Codex's own login", "Personal", "Work"]
        # It keeps the dialog's own Tab order: after the checkbox, before the buttons.
        siblings = dialog.codex_account.GetParent().GetChildren()
        assert siblings.index(dialog.default_check) < siblings.index(dialog.codex_account)
        assert siblings.index(dialog.codex_account) < siblings.index(dialog.add_button)
    finally:
        dialog.Destroy()


def test_the_account_dialog_opens_on_the_account_codex_will_use(frame, tmp_path):
    import agent_backends
    from accessible_ai.storage.database import Database

    db = Database(tmp_path / "chat.sqlite3")
    account_id = db.save_account(Account(name="Work", provider=PROVIDER_CHATGPT))
    assert agent_backends.save_codex_plan_account(agent_backends.PlanAccount(account_id, "Work"))

    spoken: list[str] = []
    dialog = _codex_account_dialog(frame, db, spoken, [])
    try:
        assert dialog.codex_account.GetStringSelection() == "Work"
        assert spoken == [], "opening the dialog is not a change to announce"
    finally:
        dialog.Destroy()


def test_choosing_an_account_saves_it_and_says_which_one_codex_will_use(frame, tmp_path):
    import wx

    import agent_backends
    from accessible_ai.storage.database import Database

    db = Database(tmp_path / "chat.sqlite3")
    account_id = db.save_account(Account(name="Work", provider=PROVIDER_CHATGPT))
    spoken: list[str] = []
    mirrored: list[str] = []
    dialog = _codex_account_dialog(frame, db, spoken, mirrored)
    try:
        dialog.codex_account.SetSelection(_codex_items(dialog).index("Work"))
        dialog.on_codex_account_changed(wx.CommandEvent())

        assert agent_backends.codex_plan_account() == agent_backends.PlanAccount(account_id, "Work")
        # Announced at once, in the words the status bar mirror carries.
        assert spoken == mirrored == ["Codex will use the ChatGPT plan Account Work."]

        dialog.codex_account.SetSelection(0)
        dialog.on_codex_account_changed(wx.CommandEvent())

        assert agent_backends.codex_plan_account() is None
        assert spoken[-1] == mirrored[-1] == "Codex will use its own login."
    finally:
        dialog.Destroy()


# ----- Signing out, deleting, and switching: the choice and the process -----
#
# A person who signs out of an Account, deletes it, or chooses another one is
# promised two things: no later Codex turn is sent on the one being left, and a
# turn already running is told to finish rather than being cut off. The second
# is said out loud, because a drop that waits silently is one nobody knows to
# wait for.


class _HeldCodex:
    """A held Codex process as the pool sees one: alive, borrowed, closable."""

    def __init__(self, borrowers: int = 0) -> None:
        self.borrowers = borrowers
        self.closed = False

    def alive(self) -> bool:
        return not self.closed

    def borrower_count(self) -> int:
        return self.borrowers

    def stop(self) -> None:
        self.closed = True


def _held_on(account_id: int, borrowers: int = 0) -> _HeldCodex:
    """Put a held process for this Account in the pool, as a turn leaves one."""
    import backend_pool

    import agent_backends

    process = _HeldCodex(borrowers)
    backend_pool.pool().keep(
        backend_pool.pool_key(agent_backends.BACKEND_CODEX, account=account_id),
        backend_pool.HeldProcess(process, agent_backends.codex_adapter()),
    )
    return process


def _soon(ready, what: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready():
            return
        time.sleep(0.005)
    raise AssertionError(what)


def test_switching_accounts_while_idle_drops_the_previous_accounts_process(frame, tmp_path):
    import wx

    import agent_backends
    from accessible_ai.storage.database import Database

    db = Database(tmp_path / "chat.sqlite3")
    work_id = db.save_account(Account(name="Work", provider=PROVIDER_CHATGPT))
    db.save_account(Account(name="Personal", provider=PROVIDER_CHATGPT))
    assert agent_backends.save_codex_plan_account(agent_backends.PlanAccount(work_id, "Work"))
    work = _held_on(work_id)
    spoken: list[str] = []
    mirrored: list[str] = []
    dialog = _codex_account_dialog(frame, db, spoken, mirrored)
    try:
        dialog.codex_account.SetSelection(_codex_items(dialog).index("Personal"))
        dialog.on_codex_account_changed(wx.CommandEvent())

        # Nothing was running, so there is no wait to announce -- and the old
        # Account's process goes before the next turn can be sent on it.
        assert spoken == mirrored == ["Codex will use the ChatGPT plan Account Personal."]
        _soon(lambda: work.closed, "the previous Account's Held process was never dropped")
    finally:
        dialog.Destroy()


def test_switching_accounts_mid_turn_says_the_running_turn_must_finish(frame, tmp_path):
    import wx

    import agent_backends
    from accessible_ai.storage.database import Database

    db = Database(tmp_path / "chat.sqlite3")
    work_id = db.save_account(Account(name="Work", provider=PROVIDER_CHATGPT))
    db.save_account(Account(name="Personal", provider=PROVIDER_CHATGPT))
    assert agent_backends.save_codex_plan_account(agent_backends.PlanAccount(work_id, "Work"))
    work = _held_on(work_id, borrowers=1)
    spoken: list[str] = []
    mirrored: list[str] = []
    dialog = _codex_account_dialog(frame, db, spoken, mirrored)
    try:
        dialog.codex_account.SetSelection(_codex_items(dialog).index("Personal"))
        dialog.on_codex_account_changed(wx.CommandEvent())

        # The switch is not made while the turn runs: that turn finishes on the
        # Account it began on, so the saved choice is still the one being left.
        assert agent_backends.codex_plan_account().name == "Work", (
            "the switch was saved while a turn was still running on the old Account"
        )
        # Said once, with the wait it comes with, and shown on the control as
        # the choice that is going to happen.
        assert len(spoken) == 1 and spoken == mirrored
        assert "Personal" in spoken[0] and "has to finish" in spoken[0], spoken
        assert dialog.codex_account.GetStringSelection() == "Personal"
        assert not work.closed, "the process was closed underneath a running turn"

        # And the switch, with the drop, happens once that turn is over: a
        # finished turn asks for the work a wait was leaving.
        work.borrowers = 0
        agent_backends.drop_pending_codex_processes()

        assert agent_backends.codex_plan_account().name == "Personal", (
            "the switch never happened after the running turn finished"
        )
        _soon(lambda: work.closed, "the drop never landed after the turn had finished")
    finally:
        dialog.Destroy()


def test_deleting_the_chosen_account_leaves_the_saved_value_and_control_agreeing(
    frame, tmp_path, monkeypatch
):
    """What is saved and what the control shows must be one answer."""
    import wx

    import agent_backends
    from accessible_ai.storage.database import Database

    db = Database(tmp_path / "chat.sqlite3")
    account_id = db.save_account(Account(name="Work", provider=PROVIDER_CHATGPT))
    assert agent_backends.save_codex_plan_account(agent_backends.PlanAccount(account_id, "Work"))
    work = _held_on(account_id)
    monkeypatch.setattr(wx, "MessageBox", lambda *_args, **_kwargs: wx.YES)
    dialog = _codex_account_dialog(frame, db, [], [])
    try:
        assert dialog.codex_account.GetStringSelection() == "Work"
        dialog.on_delete(wx.CommandEvent())

        assert agent_backends.codex_plan_account() is None, (
            "the deleted Account stayed stored as the Codex backend's Account"
        )
        assert dialog.codex_account.GetStringSelection() == "Codex's own login", (
            "the saved value and the control disagree about which Account Codex uses"
        )
        _soon(lambda: work.closed, "the deleted Account's Held process was never dropped")
    finally:
        dialog.Destroy()


def test_signing_out_of_the_chosen_account_leaves_the_saved_value_and_control_agreeing(
    frame, tmp_path, monkeypatch
):
    import wx

    import agent_backends
    import backend_pool
    from accessible_ai.storage.database import Database
    from accessible_ai.ui.accounts import AccountEditorDialog

    db = Database(tmp_path / "chat.sqlite3")
    account_id = db.save_account(Account(name="Work", provider=PROVIDER_CHATGPT))
    assert agent_backends.save_codex_plan_account(agent_backends.PlanAccount(account_id, "Work"))
    credentials = FakeCredentials()
    chatgpt.save_session(
        credentials,
        account_id,
        {"client_id": "oaiapp_issued", "email": "w@example.com", "refresh_token": "refresh-1"},
    )  # type: ignore[arg-type]
    work = _held_on(account_id, borrowers=1)
    boxes: list[str] = []
    monkeypatch.setattr(wx, "MessageBox", lambda message, *_a, **_k: boxes.append(message))

    listing = _codex_account_dialog(frame, db, [], [])
    editor = AccountEditorDialog(frame, db, credentials, db.get_account(account_id))  # type: ignore[arg-type]
    try:
        assert listing.codex_account.GetStringSelection() == "Work"
        editor._chatgpt_signed_out(True)  # what on_chatgpt_sign_out reports

        assert agent_backends.codex_plan_account() is None, (
            "the signed-out Account stayed stored as the Codex backend's Account"
        )
        assert boxes and "Codex will use its own login" in boxes[0], boxes
        assert "has to finish" in boxes[0], boxes
        assert not work.closed, "the process was closed underneath a running turn"

        # The dialog that offers the choice says the same thing as the file.
        listing.reload()
        assert listing.codex_account.GetStringSelection() == "Codex's own login"

        work.borrowers = 0
        backend_pool.pool().drop_pending()
        _soon(lambda: work.closed, "the drop never landed after the turn had finished")
    finally:
        editor.Destroy()
        listing.Destroy()


# ----- A failed plan turn is said once, and kept where it can be read -----


def test_a_failed_plan_turn_is_announced_once_and_kept_visible():
    """One error, spoken once and mirrored once, and still in the list after.

    The usage-limit sentence is the chat half's own, so what a listener hears
    here is what they would have heard from a chat, the page to review usage on
    included.
    """
    import blindpilot_app as app
    from doubles import panel_stub

    panel = panel_stub()
    sentence = chatgpt.PLAN_ERRORS["subscription_sharing_usage_limit_exceeded"]

    app.SessionPanel._on_failed(panel, sentence)

    assert panel.announced == [f"Error: {sentence}"], "the failure was said twice or not at all"
    assert panel.status == [f"Error: {sentence}"], "the status bar did not mirror it exactly once"
    errors = [row for row in panel._rows if row.kind == "error"]
    assert len(errors) == 1, "the failure was not kept as one row of its own"
    assert chatgpt.USAGE_URL in errors[0].payload


# ----- Thinking, web search and answer length on the plan -----
#
# Measured live on 2026-10-14: plan models list their own thinking levels
# (gpt-6.1-sol: low to max; "none" and "minimal" are a 400), summaries stream
# as reasoning_summary_text, web search is the only hosted tool accepted, and
# text.verbosity is honoured.

LIVE_ENTRY = {
    "slug": "gpt-6.1-sol",
    "display_name": "GPT-6.1-Sol",
    "default_reasoning_level": "low",
    "supported_reasoning_levels": [
        {"effort": e} for e in ("low", "medium", "high", "xhigh", "max", "ultra")
    ],
    "support_verbosity": True,
    "supports_reasoning_summary_parameter": True,
    "visibility": "list",
}


def test_profile_choices_become_plan_reasoning_and_web_search():
    from accessible_ai.models import OpenRouterFeatures, SERVER_TOOL_WEB_SEARCH

    features = OpenRouterFeatures(
        reasoning_effort="high",
        show_reasoning=True,
        server_tools=[SERVER_TOOL_WEB_SEARCH, "openrouter:datetime"],
        search_context="high",
    )
    assert chatgpt.plan_reasoning(features) == {"effort": "high", "summary": "auto"}
    assert chatgpt.plan_tools(features) == [{"type": "web_search", "search_context_size": "high"}]
    assert chatgpt.plan_reasoning(OpenRouterFeatures(show_reasoning=False)) is None
    assert chatgpt.plan_tools(OpenRouterFeatures()) == [{"type": "web_search"}]


def test_a_level_the_model_does_not_offer_falls_back_with_a_notice(monkeypatch):
    monkeypatch.setitem(chatgpt._catalog, "gpt-6.1-sol", LIVE_ENTRY)
    reasoning, verbosity, notice = chatgpt.fit_to_model(
        "gpt-6.1-sol", {"effort": "none", "summary": "auto"}, "low"
    )
    assert reasoning == {"summary": "auto"}
    assert verbosity == "low"
    assert (
        notice
        == "GPT-6.1-Sol does not offer the thinking effort No thinking, so it used its default, low."
    )
    kept, _verbosity, quiet = chatgpt.fit_to_model("gpt-6.1-sol", {"effort": "max"}, "")
    assert kept == {"effort": "max"} and quiet == ""


def test_thinking_search_and_sources_reach_the_listener(monkeypatch):
    monkeypatch.setitem(chatgpt._catalog, "gpt-6.1-sol", LIVE_ENTRY)
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            content=_sse(
                {"type": "response.reasoning_summary_text.delta", "delta": "**Checking**"},
                {"type": "response.reasoning_summary_part.added", "summary_index": 1},
                {"type": "response.reasoning_summary_text.delta", "delta": "**Answering**"},
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "web_search_call",
                        "status": "completed",
                        "action": {"type": "search", "query": "latest NVDA release"},
                    },
                },
                {"type": "response.output_text.delta", "delta": "NVDA 2026.2."},
                {
                    "type": "response.output_text.annotation.added",
                    "annotation": {
                        "type": "url_citation",
                        "title": "NV Access | Releases",
                        "url": "https://www.nvaccess.org/category/releases/",
                    },
                },
                {"type": "response.completed", "response": {}},
            ),
        )

    provider = _provider(monkeypatch, handler)
    settings = GenerationSettings(
        model="gpt-6.1-sol",
        messages=[{"role": "user", "content": "Latest NVDA?"}],
        reasoning={"effort": "medium", "summary": "auto"},
        tools=[{"type": "web_search"}],
        verbosity="low",
    )
    events = list(provider.generate(settings, Event()))  # type: ignore[arg-type]
    body = bodies[0]
    assert body["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert body["tools"][0] == {"type": "web_search"}
    assert body["text"] == {"verbosity": "low"}
    by_kind = {
        kind: "".join(e.text for e in events if e.kind == kind)
        for kind in ("reasoning", "tool", "sources")
    }
    assert by_kind["reasoning"] == "**Checking**\n\n**Answering**"
    assert by_kind["tool"] == "Searched the web for latest NVDA release"
    assert "NV Access | Releases" in by_kind["sources"]


# ----- Research: search, read what was found, answer -----
#
# Measured live on 2026-10-14: a plan turn that ends in a function call takes
# its own output items back unchanged (store false) and finishes from the
# tool's result; leaving the web_search_call out makes it search again.


def test_a_tool_call_is_run_here_and_the_turn_carried_on(monkeypatch):
    monkeypatch.setattr(chatgpt, "fetch_url", lambda url: f"Page text of {url}")
    bodies: list[dict] = []
    search_item = {
        "type": "web_search_call",
        "id": "ws_1",
        "status": "completed",
        "action": {"type": "search", "query": "gluten free garlic bread"},
    }
    call_item = {
        "type": "function_call",
        "id": "fc_1",
        "call_id": "call_1",
        "name": "fetch_url",
        "arguments": '{"url": "https://example.com/bread"}',
    }

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            events = [
                {"type": "response.output_item.done", "item": search_item},
                {"type": "response.output_item.done", "item": call_item},
                {"type": "response.completed", "response": {}},
            ]
        else:
            events = [
                {"type": "response.output_text.delta", "delta": "Schar makes one."},
                {"type": "response.completed", "response": {}},
            ]
        return httpx.Response(200, content=_sse(*events))

    provider = _provider(monkeypatch, handler)
    settings = GenerationSettings(
        model="gpt-6.1-sol", messages=[{"role": "user", "content": "Find GF garlic bread"}]
    )
    events = list(provider.generate(settings, Event()))  # type: ignore[arg-type]

    first, second = bodies
    names = {tool.get("name") or tool["type"] for tool in first["tools"]}
    assert {"web_search", "fetch_url", "current_time"} <= names
    assert first["include"] == ["reasoning.encrypted_content"]
    assert second["input"][1:] == [
        search_item,
        call_item,
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "Page text of https://example.com/bread",
        },
    ]
    said = [e.text for e in events if e.kind == "tool"]
    assert said == ["Searched the web for gluten free garlic bread", "Reading example.com/bread"]
    assert "".join(e.text for e in events if e.kind == "text") == "Schar makes one."


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/",
        "http://192.168.1.10/admin",
        "http://[::1]/",
        "file:///C:/secret.txt",
    ],
)
def test_page_reading_refuses_this_computer_and_the_local_network(url):
    assert chatgpt._public_address(url)
    assert not chatgpt.fetch_url(url).startswith("Address:")


def test_page_text_drops_scripts_and_markup():
    parser = chatgpt._PageText()
    parser.feed("<html><script>evil()</script><h1>Bread</h1><p>Gluten   free</p></html>")
    assert parser.text() == "Bread\nGluten free"


def test_the_clock_tool_answers_with_the_local_date():
    output, said = chatgpt.run_local_tool("current_time", "{}")
    assert str(time.localtime().tm_year) in output
    assert said == "Checked the date and time"


@pytest.mark.parametrize(
    "action, said",
    [
        ({"type": "search", "query": "bread"}, "Searched the web for bread"),
        (
            {"type": "open_page", "url": "https://www.nvaccess.org/download/"},
            "Opened www.nvaccess.org",
        ),
        ({"type": "find_in_page", "pattern": "2026.2"}, "Looked in the page for 2026.2"),
        ({}, "Searched the web"),
    ],
)
def test_each_web_search_step_is_said_for_what_it_did(action, said):
    assert chatgpt._search_said(action) == said
