# BlindPilot 0.20.9

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release fixes one defect with a wide blast radius: on macOS, every HTTPS request
BlindPilot made failed to verify a certificate. Checking for updates, downloading
Node.js, and installing or updating a backend all ended in the same sentence —
"CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate" — on any Mac that
had never had python.org's Python installed.

## What was actually wrong

None of those three features. PyInstaller freezes the build machine's OpenSSL into the
application, and OpenSSL looks for its trusted roots at the directory it was compiled
with. For the macOS release that directory is inside the python.org framework on the
GitHub runner that builds it: `/Library/Frameworks/Python.framework/Versions/3.12/etc/
openssl/cert.pem`. It exists on the runner. It exists on nobody else's Mac. So the
shipped application started life with a trust store holding zero certificates and
refused every server it was pointed at — which is the correct thing for a program with
no roots to do, and the reason the error named the certificate rather than the feature.

## What now happens

certifi's root list already ships inside the same application folder. It is now handed
to OpenSSL when, and only when, the store OpenSSL found for itself is empty. A system
store with certificates in it is used untouched, because a managed Mac, a Linux
distribution, or a corporate proxy put them there deliberately, and `SSL_CERT_FILE` is
honoured on the way through exactly as before.

Verification is never turned off and no check is skipped: an empty store is replaced
with a real one, not disabled. certifi is also a declared dependency now rather than
one inherited from httpx, since a module of this application names it directly, and the
release build must not lose it to a change in somebody else's requirements.

## Verification

Eight new tests, failing-first: the empty store falls back to the bundled roots, a
populated system store is left alone, a build with no bundle still returns a verifying
context, and both internet-facing modules ask for that store by default. The last of
them is a sweep of the source that fails if a new `urlopen` is added to either module
without it — this outage was one call site's default argument, and the next one would
look the same. The failure itself was reproduced by emptying the trust store, then the
update check and the Node.js LTS lookup were both run through the fix against GitHub
and nodejs.org and both answered. The full suite is green under `-W error`, and ruff
and mypy are clean.
