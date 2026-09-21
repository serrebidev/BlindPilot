# BlindPilot 0.29.14

One fix, to the test suite. Nothing a user of BlindPilot would notice changes.

- The update dialog's worker test stopped calling GitHub. Its stand-in carried a
  `check` that nothing read, left over from the injectable check removed in
  0.29.9, while `UpdateDialog._check_worker` asks the module's own
  `fetch_latest_release`. Every run therefore made an unauthenticated request to
  the GitHub API. On a machine that has spent its anonymous rate limit that
  request answers 403, and the `HTTPError` holds an unopened response body that
  warns when it is collected. The suite runs with `-W error`, as CI and this
  release workflow both do, so that warning was a failure - in a test about what
  the dialog does after `wx.App` is gone. It reproduced five times out of five
  here once the limit was reached, and passed on the runners only because their
  limit was not.
- The answer is stubbed where the worker actually looks for it, and the test
  still fails if `_call_after`'s no-application guard is removed, which is what
  it exists to cover.
