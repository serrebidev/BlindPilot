# BlindPilot 0.20.3

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release finishes what the last one started: every backend the setup wizard offers
now installs from the setup wizard, on Windows, macOS and Linux — Hermes included,
through its own official installer.

## The last backend the wizard could not install

Hermes is not distributed on npm. Until this release, that fact meant the setup wizard
could not install it — and the captured NVDA session behind 0.20.2 showed what the user
heard instead: an install that ran nothing, a report that npm could not be installed on
a machine that has npm, and an instruction spliced mid-sentence. The honest answer —
"BlindPilot cannot install this" — was still the wrong answer, because Hermes ships
installers of its own.

They are the same shape as the Claude installer this application has driven since its
early versions, which made the work one of generalisation rather than invention:

- Native Windows, through PowerShell: `iex (irm
  https://hermes-agent.nousresearch.com/install.ps1)`
- macOS, Linux and WSL2, through curl and bash:
  `curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash`

The prerequisites are the ones Claude's installer has always needed, and they already
ship with the platform: PowerShell comes with Windows; curl and bash come with macOS.
No administrator rights and no Node.js in either case.

## What an install here means

The same discipline as every other install in this application. The installer's output
streams into the accessible log as it happens, so nothing between the first line and
the last is silent. The installer's exit code is treated as advisory — what counts is
a working `hermes` launcher afterwards, searched for with the install directories on
PATH, and a version check that proves it starts. On success the launcher's folder is
put on the user's persistent PATH, so `hermes` works in a terminal the same way the
other backends' CLIs do. On failure the log holds the installer's own words, and the
user is told what to do with them.

The Update button had the same hole as Install and is fixed with it: updating a found
Hermes used to fall through to npm and report that npm could not be found — for a
backend with no npm package. It now re-runs the official installer, which upgrades in
place, and measures the result the same way.

And npm is never named in any Hermes branch. It has nothing to do with that install,
and the last release showed exactly what happens when a message names the wrong
prerequisite: somebody is sent looking for a thing that was never missing.

## Verification

pytest 1017 passed, 3 skipped; ruff check, ruff format --check, mypy,
`--startup-smoke` and `--startup-gui-smoke` all clean at the release commit. Sixteen
new tests, written failing-first: per-platform installer argv, missing prerequisites
named by name, binary-not-exit-code success, the install and update routes, and what
the wizard says in each branch.
