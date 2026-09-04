"""The certificate store a packaged BlindPilot can actually verify against.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
Based on the original Claude Code Reader application by doubletaponair:
https://github.com/doubletaponair/claude-code-reader
SPDX-License-Identifier: MIT

PyInstaller freezes the build machine's OpenSSL, and OpenSSL looks for its
trusted roots at the directory it was compiled with. For the macOS release that
directory is inside the python.org framework on the GitHub runner, so on
anybody else's Mac the trust store comes up empty and every HTTPS request out
of BlindPilot dies with CERTIFICATE_VERIFY_FAILED - no update check, no Node.js
download, no backend install. certifi's bundle ships in the same application
folder; this module hands it to OpenSSL when, and only when, the store OpenSSL
found for itself is empty.
"""

from __future__ import annotations

import os
import ssl
import urllib.request
from functools import lru_cache
from typing import Optional


def _certifi_bundle() -> Optional[str]:
    """The path to the bundled Mozilla root list, if this build carries one."""
    try:
        import certifi
    except ImportError:
        return None
    try:
        path = certifi.where()
    except Exception:
        return None
    return path if path and os.path.isfile(path) else None


def _store_is_empty(context: ssl.SSLContext) -> bool:
    try:
        return not context.cert_store_stats().get("x509")
    except Exception:  # A store that cannot be counted is left alone.
        return False


@lru_cache(maxsize=1)
def certificate_context() -> ssl.SSLContext:
    """A verifying TLS context that still verifies inside a frozen build.

    A system store with certificates in it is used untouched: a managed Mac, a
    Linux distribution, or a corporate proxy put them there deliberately, and
    SSL_CERT_FILE is honoured on the way through. certifi is the fallback for
    the one case that is otherwise fatal - a store with nothing in it at all.
    """
    context = ssl.create_default_context()
    bundle = _certifi_bundle()
    if bundle and _store_is_empty(context):
        try:
            context.load_verify_locations(cafile=bundle)
        except OSError:  # An unreadable bundle leaves the empty store as it was.
            pass
    return context


def open_url(request, timeout: Optional[float] = None):
    """urlopen, verifying against the store above rather than OpenSSL's guess."""
    return urllib.request.urlopen(request, timeout=timeout, context=certificate_context())
