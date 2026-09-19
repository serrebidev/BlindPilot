# BlindPilot 0.29.6

`HERMES_HOME` is resolved one way, so a tilde in it names one directory - plus the dead code a whole-repo audit found and two test-infrastructure fixes.

- `HERMES_HOME` is how one machine runs several Hermes profiles side by side, and three parts of BlindPilot read it. Two of them stripped the value and expanded a leading `~`; the status report and the Settings menu took it exactly as written. With `HERMES_HOME=~/alt`, Hermes itself and your session history used the real directory while those two looked for `auth.json` and `config.yaml` under a folder literally named `~` - so a Hermes you were signed in to could be reported as signed out of it. All three now resolve it the same way.
- Dead code and duplicate helpers are gone, with no behaviour change for any caller that exists: an unused `certificates` import and two unused helpers in `muse_backend`, two Chat-panel history-view handlers that nothing bound, and two helpers in the main window that were copies of ones it already imports.
- A bare `pytest` now collects only `tests/`, so an untracked scratch folder under `docs/` can no longer take collection down with a `RecursionError` before a single test runs. Explicit paths still win.
- The setup-helper test now waits for a sentinel that is renamed into place once closed, rather than one that exists from the moment it is created, so its teardown can no longer race Windows Script Host under load.
