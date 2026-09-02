# BlindPilot 0.20.2

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release repairs what the setup wizard says when a backend is not there. It was
captured, not imagined: an NVDA log of one press of Install Hermes, four lines spoken
within three milliseconds, two of them untrue and one of them not a sentence.

## Four lines in three milliseconds

Pressing Install Hermes on a machine that has Hermes missing and npm installed spoke
this:

1. "Installing Hermes. This usually takes under a minute." — a promise.
2. "npm could not be installed, so BlindPilot cannot install Hermes automatically." —
   untrue on the machine it was spoken on, which has npm.
3. "The install did not complete. Read the installer output, or install Hermes
   yourself using See https://hermes-agent.nousresearch.com/docs and click Check
   Again." — a sentence with another sentence's fragment spliced into its middle.
4. "Hermes is not installed. Tab to Install Hermes." — the offer again, which is how
   the first line came to be a promise.

The root is an ordering question the wizard answered backwards. Its check asked
whether Node was installable before asking whether the backend comes from npm at all,
and its install helper answers a managed-Node sentinel for any backend whenever Node
is installable — so Hermes, which ships its own installer and is not on npm, was
offered an install that runs nothing, fails immediately, and reports npm as the reason
whether or not npm exists. The offer came back after the failure, which made the
whole thing a loop: promise, lie, splice, offer.

## What it says now

The check asks npm-membership first. A backend BlindPilot does not install — Hermes is
the only one today — gets its own guidance: BlindPilot could not find it, here are its
own instructions, install it and choose Check Again. No Install button is offered,
because there is nothing for one to do.

`install_backend` refuses such a backend in its own terms before any npm machinery is
consulted, so the npm sentence can never again be spoken about a backend that has no
npm package. Pressing an Install button that a dialog built before this fix still
holds refuses at once — "BlindPilot cannot install Hermes itself. See
https://..." — instead of promising a minute it does not have.

And the failure message is built from parts that are sentences on their own. The
install command reaches the user exactly as each backend documents it — npm one-liners
for Codex, FreeBuff and opencode, the Hermes instructions for Hermes — but never
spliced into the middle of another sentence again. Every backend in the registry is
pinned by a test: no splice, command always present.

## Verification

pytest 1005 passed, 3 skipped; ruff check, ruff format --check, mypy, `--startup-smoke`
and `--startup-gui-smoke` all clean at the release commit. Five new tests, written
failing-first against the captured NVDA behaviour.
