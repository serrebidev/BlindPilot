"""Every HTTPS request BlindPilot makes has to have roots to verify against.

The macOS release is frozen by PyInstaller on a GitHub runner, and the OpenSSL
it freezes carries the runner's compiled-in certificate directory —
`/Library/Frameworks/Python.framework/Versions/3.12/etc/openssl/cert.pem`, a
path that exists on the runner and on nobody else's Mac. So the trust store
came up empty in the shipped application and every outbound request died the
same way: "CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate".
Checking for updates, downloading Node.js, and installing a backend were all
impossible on a Mac that had never had python.org's Python installed.

certifi's root list ships inside the same application folder. These pin the
rule: use the system store when it has anything in it, fall back to certifi
when it is empty, and never open an internet URL without either.
"""

from __future__ import annotations

import re
import ssl
from pathlib import Path

import pytest

import app_updater
import blindpilot_app
import certificates

ROOT = Path(__file__).resolve().parent.parent
# `urllib.request.urlopen(` and a bare `urlopen(`, but not the one wrapper
# whose whole job is to add the context — its name ends in the same word.
_A_RAW_CALL = re.compile(r"(?<!\w)urlopen\(")


class _Store:
    """Stands in for an SSLContext, counting what was loaded into it."""

    def __init__(self, certificates_held: int) -> None:
        self._held = certificates_held
        self.loaded: list[str] = []

    def cert_store_stats(self) -> dict[str, int]:
        return {"x509": self._held, "crl": 0, "x509_ca": self._held}

    def load_verify_locations(self, cafile: str) -> None:
        self.loaded.append(cafile)


@pytest.fixture(autouse=True)
def _fresh_context():
    """The context is built once per run, so each test starts from nothing."""
    certificates.certificate_context.cache_clear()
    yield
    certificates.certificate_context.cache_clear()


def test_an_empty_store_falls_back_to_the_bundled_roots(monkeypatch, tmp_path):
    """The packaged Mac: OpenSSL found its own path, and there was nothing there."""
    bundle = tmp_path / "cacert.pem"
    bundle.write_text("", encoding="utf-8")
    store = _Store(certificates_held=0)
    monkeypatch.setattr(certificates.ssl, "create_default_context", lambda: store)
    monkeypatch.setattr(certificates, "_certifi_bundle", lambda: str(bundle))

    assert certificates.certificate_context() is store
    assert store.loaded == [str(bundle)]


def test_a_system_store_with_certificates_in_it_is_left_alone(monkeypatch, tmp_path):
    """A managed Mac, a Linux distribution or a corporate proxy put them there."""
    bundle = tmp_path / "cacert.pem"
    bundle.write_text("", encoding="utf-8")
    store = _Store(certificates_held=192)
    monkeypatch.setattr(certificates.ssl, "create_default_context", lambda: store)
    monkeypatch.setattr(certificates, "_certifi_bundle", lambda: str(bundle))

    assert certificates.certificate_context() is store
    assert store.loaded == [], "the system store was overridden when it did not need to be"


def test_a_build_with_no_bundle_still_returns_a_verifying_context(monkeypatch):
    """Missing certifi is a worse error message, never a context that trusts anyone."""
    store = _Store(certificates_held=0)
    monkeypatch.setattr(certificates.ssl, "create_default_context", lambda: store)
    monkeypatch.setattr(certificates, "_certifi_bundle", lambda: None)

    assert certificates.certificate_context() is store
    assert store.loaded == []


def test_this_machine_ends_up_with_roots_to_verify_against():
    context = certificates.certificate_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.cert_store_stats()["x509"] > 0, "no trusted roots at all"


def test_open_url_verifies_against_that_store(monkeypatch):
    seen: dict[str, object] = {}

    def fake_urlopen(request, timeout=None, context=None):
        seen["request"] = request
        seen["timeout"] = timeout
        seen["context"] = context
        return "response"

    monkeypatch.setattr(certificates.urllib.request, "urlopen", fake_urlopen)
    assert certificates.open_url("https://example.invalid/", timeout=7) == "response"
    assert seen["timeout"] == 7
    assert seen["context"] is certificates.certificate_context()


def test_the_updater_asks_for_that_store_by_default(monkeypatch):
    """The update check was the first thing to fail on a Mac; it is the first pinned."""
    seen: dict[str, object] = {}

    def fake_open_url(request, timeout=None):
        seen["context_used"] = True
        raise OSError("no network in a test")

    monkeypatch.setattr(app_updater, "open_url", fake_open_url)
    with pytest.raises(app_updater.UpdateError):
        app_updater.fetch_latest_release("0.0.0")
    assert seen.get("context_used"), "fetch_latest_release opened GitHub some other way"


def test_the_node_download_asks_for_that_store_by_default(monkeypatch):
    """The Node.js LTS lookup is where this was first seen: nodejs.org over TLS."""
    asked: list[str] = []

    def fake_open_url(request, timeout=None):
        asked.append(request.full_url)
        raise OSError("no network in a test")

    monkeypatch.setattr(blindpilot_app, "open_url", fake_open_url)
    with pytest.raises(OSError):
        blindpilot_app._fetch_url_bytes("https://nodejs.org/dist/index.json")
    assert asked == ["https://nodejs.org/dist/index.json"]


def test_no_internet_call_bypasses_the_verified_opener():
    """A new `urlopen` in either of these modules is the same outage returning.

    Both talk to nodejs.org and GitHub over TLS; the Hermes backend's own
    `urlopen` is deliberately not swept, because it addresses a gateway on this
    machine over plain HTTP where no certificate is involved.
    """
    for name in ("blindpilot_app.py", "app_updater.py"):
        source = (ROOT / name).read_text(encoding="utf-8")
        offenders = [
            line.strip()
            for line in source.splitlines()
            if _A_RAW_CALL.search(line) and not line.lstrip().startswith("#")
        ]
        assert not offenders, f"{name} opens a URL without the verified store: {offenders}"
