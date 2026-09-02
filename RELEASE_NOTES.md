# BlindPilot 0.15.0

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

Nothing you hear changes in this release. What changed is the test infrastructure
underneath it: a hung test now fails in a minute instead of running out the clock, and the
order is shuffled so one test cannot quietly depend on another having run first.

## A hung test fails in a minute, not in six hours

There was no timeout of any kind, anywhere. GitHub's default job timeout is 360 minutes,
so a deadlocked wait or a subprocess that never exits cost six hours of runner time and
reported nothing useful at the end. That is not theoretical for this suite: it drives
real subprocesses, pseudo-terminals and worker threads, and the code under test now
includes a thirty-second shutdown wait and an hour-long FreeBuff deadline.

Two timeouts at two scales:

- 60 seconds per test, against a suite whose slowest test takes about three.
- 20 minutes per CI job, against one that finishes green in one to three.

The release workflow gets them too — it runs the same tests, takes about fifteen minutes
with PyInstaller and Inno Setup, and could hang identically. A test now asserts that
anything running pytest has a job timeout, and that test is how the release workflow's
absence was noticed.

## Shuffled ordering

Test-order coupling has already happened here twice in one day: a module-level flag
left set between tests, and a test calling `main()` which started real logging for every
test that ran after it, writing into the installed application's own log folder. The
second only appeared in a full run, never in isolation — exactly the failure shuffling
makes immediate. The seed is printed on every run, and `-p no:randomly` restores fixed
order for bisecting.

## ruff 0.15.10 → 0.16.5

Checked before changing: no new violations, nothing reformatted. The pin itself is right
and stays — a linter that gains rules on its own schedule would otherwise turn somebody
else's release into a red build here.