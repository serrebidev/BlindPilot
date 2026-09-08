# BlindPilot 0.23.0

Session Status now says how much of your account's allowance is left and when it comes back - for every backend that meters one.

## What /status was not saying

The report named the backend, its version and the account signed in to it, and stopped there. How much of the plan was spent, and how long until it refilled, was the question people were actually opening it to ask, and the only way to find out was to leave BlindPilot and ask the provider's own tool.

Claude Code and Codex both answer it, and neither answers it on a command line. Claude Code takes a `get_usage` control request on the same stream-json channel a turn is driven over; Codex answers `account/rateLimits/read` on its app-server, which BlindPilot borrows from the pool where a tab already has one running. Both report the same shape: a five-hour window, a weekly one, sometimes a weekly one for a single model, each with how full it is and when it empties.

```
Five-hour limit: 41% used, resets Tue 08 Sep 00:59 (in 3 hours)
Weekly limit: 8% used, resets Mon 14 Sep 13:59 (in 6 days 16 hours)
```

The reset is said as a time and as a wait, because "resets at 08:50" is no help to somebody who does not already know what time it is now. A window the backend does not report is left out rather than written as unknown, and an account the windows do not apply to at all - an API key, Bedrock, Vertex - gets no usage section, which is what says there is nothing to show.

## The other three backends

The issue this began from said FreeBuff, opencode and Hermes have no plan of their own and cannot answer. Two of the three do meter an account. What they do not do is meter it in windows.

FreeBuff counts credits off a balance that refills on a date. Its CLI has no command for that either, but its own usage banner reads an account endpoint, and one request against the credentials it signed in with returns what has been spent this cycle, what is left, and when the cycle turns over. What is left is the figure worth hearing first, so the line leads with it. The percentage is derived from the two figures together, and only where they say what the cycle held - a balance that top-ups and referrals move around is the less honest half of the answer.

```
Credits: 750 credits left, 250 credits used this cycle, resets Wed 07 Oct 21:25 (in 29 days 23 hours)
```

Hermes runs on a pool of provider credentials rather than on one account, so there is no single figure for a percentage to be a fraction of. What it does have is the outcome it wrote against each credential, and a spent one carries the time it may be used again where the provider said so. Those are the lines it reports, with a rate limit and an empty wallet named apart because one comes back on its own and the other does not. A pool with nothing spent reports nothing.

```
Provider anthropic (claude_code): rate limit reached, resets Tue 08 Sep 05:49 (in 1 hour)
Provider opencode-go: out of credits
```

opencode is the one backend that really meters nothing. It spends whichever provider account is connected, its server offers no route that reports one, and the rate-limit headers it does read go into deciding a retry rather than anywhere that could be asked. Saying nothing there is the truthful answer.

None of this waits on a CLI it does not have to. FreeBuff's balance is one HTTP request and Hermes' pool is a file read - measured at 0.19 and 0.01 seconds against live accounts - and Codex reuses a running app-server rather than starting one.

## Two other things this fixed

A Hermes tab's /status was reporting opencode's connected providers as its own: Hermes had no branch of its own in the status report and fell through into opencode's. It now names the providers Hermes holds credentials for and the one it is set to use.

The Claude Code usage probe never handed back the pipes of the process it started, leaking two file handles every time /status was pressed.

## What was verified

Live-checked on Windows against Claude Code 2.1.263, codex-cli 0.153.4, FreeBuff 0.0.171 and Hermes 0.21.0, with real windows, a real credit balance and a real exhausted credential coming back in each. Not run on macOS or Linux.

Verified with the full regression suite (1521 tests with warnings as errors), ruff's checks and formatting, and mypy over sixteen files.
