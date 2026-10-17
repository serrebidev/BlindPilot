# BlindPilot backend update discovery design

## Goal

Give every supported agent backend one consistent, accessible way to answer:

- Is this backend installed?
- What exact executable does BlindPilot use?
- What version is installed?
- Can BlindPilot identify a newer stable version?
- If checking fails, what happened and when should the next check run?

This design covers checking and announcing updates. Installation remains a
manual, user-triggered action through the existing update button.

## Current behavior and problems

BlindPilot currently discovers a backend executable, asks most CLIs for
`--version`, and renders the result in the setup wizard. Updating is split
between npm commands and official installers. The generic update path has no
shared representation of an available version, package source, check time, or
failure reason. Some backends have special discovery or installer behavior,
and Command Code has separate protection against its own detached updater.

This produces four user-visible risks:

1. A successful installer exit can be treated as success without proving that
   the executable BlindPilot launches changed.
2. Startup checks can repeatedly launch slow CLIs and network/package-manager
   probes.
3. A late worker result can describe a backend or wizard instance that is no
   longer current.
4. New backends need another set of UI conditionals instead of implementing a
   stable update contract.

## Proposed architecture

Add one update service with backend adapters. Keep backend-specific commands
and parsing in adapters; keep orchestration, caching, comparison, and UI
state in the service.

```text
Setup wizard / settings UI
          |
          v
BackendUpdateService
  - resolve backend adapter
  - cache and throttle checks
  - run off the UI thread
  - reject stale results
  - compare versions
          |
          +-- NpmBackendAdapter
          +-- OfficialInstallerAdapter (Hermes, Muse)
          +-- ExistingSpecialBackendAdapter
```

The service owns a small immutable result object:

```text
BackendUpdateResult
  backend_id
  executable_path
  installed_version
  available_version
  state: unavailable | current | update_available | failed
  source: npm | official_installer | cli | unavailable
  checked_at
  failure_reason
```

An adapter owns:

- executable resolution;
- installed-version probing;
- latest-version lookup from a trustworthy source;
- the existing manual update command;
- post-update verification;
- whether prereleases or non-semver versions are supported.

Adapters must not update during a check. Checks use the same hidden,
non-interactive environment and timeout conventions as existing probes.

## Version and source rules

- npm adapters query package metadata without installing it, then compare the
  package version to the version reported by the resolved executable.
- Official-installer adapters use the installer’s documented release metadata
  when available. If no trustworthy metadata endpoint exists, they report
  `available_version` as unknown and do not guess.
- CLI-only backends report the installed version and `unavailable` for update
  discovery until a stable source is defined.
- Stable releases are the default channel. Prereleases are ignored unless an
  adapter explicitly declares a prerelease channel.
- A package update is not considered applied until the same resolved
  executable reports the expected version or a newer compatible version.
- Version parsing is centralized. Invalid or missing versions become a
  failed check with a readable reason; they never compare lexicographically.

## Cache and concurrency

- Cache successful and failed checks separately with a short configurable TTL.
- A manual “Check Again” bypasses the success TTL but still prevents duplicate
  in-flight checks for the same backend.
- Each request carries a generation token containing the selected backend and
  wizard instance. UI callbacks discard results whose token is no longer
  current or whose window has closed.
- Cache writes are atomic and contain no credentials or command output.
- A network/package-manager failure leaves the last known installed version
  visible and announces a concise retryable failure.

## UI and accessibility behavior

- The existing status paragraph remains the single spoken status surface.
- Status wording is stable and explicit:
  - “Command Code version 1.82.4 is current.”
  - “Command Code update 1.84.0 is available.”
  - “Could not check Command Code for updates. The last check was …”
- The update button is shown only when an update is available and the adapter
  supports manual installation. It remains keyboard and screen-reader
  reachable.
- Checking never blocks the GUI thread. Progress is announced once when a
  check starts only when the user explicitly asks; startup checks stay quiet
  until a new version or failure needs attention.
- A selected-backend change cancels visibility of the previous result without
  needing to terminate a child process that is already safely bounded by a
  timeout.

## Error handling and safety

- Do not expose credentials, full environment variables, or raw installer
  output in the cached result.
- Preserve the existing self-update safeguards, including Command Code’s
  `COMMANDCODE_SKIP_UPDATES` probe environment and server shutdown before
  replacing a running executable.
- Keep the manual update path unchanged until the service can verify the
  post-update executable.
- If package metadata and executable identity disagree, report the mismatch
  and do not offer an automatic action.

## Test plan

Add focused unit tests for:

1. semver comparison, invalid versions, prerelease filtering, and equal
   versions;
2. npm metadata parsing and package/executable mismatch;
3. adapter source selection and command construction;
4. cache TTL, failed-check caching, forced refresh, and duplicate suppression;
5. stale callback suppression after backend selection or window close;
6. post-update verification when the installer exits zero but the version is
   unchanged;
7. accessible status text and update-button visibility for each result state;
8. existing Command Code probe/update safeguards remaining intact.

The inner loop is the focused update-service test module plus Ruff and mypy.
The project gate remains the README command `python -m pytest -q -W error`,
Ruff check/format, and mypy. No physical screen-reader validation is implied
by the automated tests; it remains a manual gate for the final UI change.

## Rollout

Implement the service behind the existing wizard checks first. Keep the old
adapter-specific update commands as the execution layer. Add one backend at a
time, starting with npm backends and Command Code because their current probe
and update behavior is already well covered. Then add official-installer
backends. A backend without a trustworthy latest-version source stays
installed-version-only until its source is documented and tested.

## Out of scope

- unattended installation;
- changing backend package versions or release channels;
- replacing the existing installer implementations;
- automatic rollback or backup of third-party CLIs;
- provider/model catalog freshness, which remains separate from CLI package
  freshness;
- physical device or screen-reader validation in CI.
