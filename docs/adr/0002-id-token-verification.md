# ADR 0002: In-house ID-token verification with the standard library

- **Status:** Accepted
- **Date:** 2026-11-05
- **Context:** The ChatGPT-plan sign-in receives an ID token from OpenAI and must
  verify its RS256 signature against OpenAI's published keys before trusting the
  identity it names. The design document proposed doing this with a maintained
  verifier and said not to implement signature verification from scratch. The
  shipped code instead verifies with the standard library alone.

## Decisions

The ID token's RS256 signature is verified in-house, with no added dependency.
OpenAI's JWKS is read over HTTPS from an `openai.com` address; the RSA public key
is rebuilt from its `n` and `e`; and the signature is checked as
RSASSA-PKCS1-v1_5 over SHA-256 by comparing the padded block against
`pow(signature, e, n)`. A key smaller than 2048 bits, or a signature number not
below the modulus, is refused. Issuer, audience, expiry, nonce and subject are
checked separately from the signature.

## Considered Options

- **A maintained JWT or cryptography library**, as the design document proposed.
  Rejected: it adds a compiled dependency to every build and package for a single
  verification path, where the standard library already supplies the one
  primitive needed and the construction is fixed by RFC 8017.

## Consequences

The project owns this small, verification-only cryptographic surface. It stays
auditable and dependency-free, but a defect here could accept a forged token, so
its correctness rests on known-answer tests rather than on a vetted library: the
signature published in RFC 7515 Appendix A.2, checked against a key this code
did not build, plus cases for a key under 2048 bits, a signature not below the
modulus, a wrong-length signature, and a key address outside `openai.com`.
Revisit this decision only with test-vector-backed evidence, not for convenience.
