# BlindPilot 0.20.7

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release is an audit come back as a fix. "Hermes does not work at all in the app"
turned out, under a live audit, to be a true report with a false conclusion: every layer
of the Hermes integration — discovery, the model picker, the session list, and full
turns through the application's own send path — was verified working against a real
install. What was actually happening was that the application sat through the provider
grind in silence.

## The grind, and what was silent about it

With an account rate-limited or out of credits, Hermes does not fail a turn. It backs
off and falls back, one provider at a time — a captured run showed claude-opus-5, then
gpt-5.6-sol, then claude-sonnet-5 all answering 429 before a fourth provider finally
answered — and most of that journey is not narrated on the gateway's wire. The only
explanations Hermes sends were being thrown away by the worker, and the fallback lines
that did arrive were decorated for a terminal: "⚠️ Model fallback: …", which a screen
reader reads as "warning sign" before every sentence.

Three fixes, each a sentence that used to be missing:

- The gateway's own "Still starting the agent (tool discovery / model setup) — your
  message will be sent as soon as it's ready" is now a spoken row that names the wait,
  instead of being dropped on the floor while a slow agent build ran.
- A turn that produces nothing for two minutes now says what it is probably waiting on
  — a rate-limited or credit-exhausted provider, or another Hermes session on the same
  account — and what to do about it: pick a different model with /model. Once, not as a
  repeating error: a silent-but-connected turn is news, never a failure.
- Terminal decorations are stripped from status lines, so the fallback chain reads
  "Model fallback: claude-opus-5 via anthropic unavailable (rate limit); using
  gpt-5.6-sol" instead of "warning sign. Model fallback: …".

## Verification

Four new tests, failing-first, each pinning one of the sentences above; the full suite
is green under `-W error`, and ruff, mypy and both startup smoke tests are clean. The
end-to-end proof that nothing else was broken: full Hermes turns, through the real
application path, completing in seconds on the same machine the report came from — with
the answer arriving over the fallback provider that still had room to answer.

If Hermes feels slow, the app will now say why. The other half of the remedy is on the
account side: the default model is still claude-opus-5, and picking a model with credits
available is what makes the wait short rather than merely explained.