"""What the GitHub Actions workflows have to guarantee.

The release workflow does run the tests and both static checks — but only on a
`v*` tag or a manual dispatch, which means nothing verifies a commit until
somebody has already decided to ship it. A broken commit is found at tag time,
with the release half made.

These read the workflow files as text rather than parsing YAML, so no parser
is added to the test dependencies for four assertions. They are not checking
that the workflows *work* — only CI itself can show that — but they do keep
the triggers and the platforms from being quietly dropped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _workflow_text() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(WORKFLOWS.glob("*.yml"))}


def test_there_are_workflows_at_all():
    assert WORKFLOWS.is_dir(), "no .github/workflows directory"
    assert _workflow_text(), "no workflow files"


def test_a_workflow_runs_the_tests_on_every_push():
    """Otherwise a broken commit sits undiscovered until release day."""
    files = _workflow_text()
    on_push = {
        name: text
        for name, text in files.items()
        # A tag-only trigger nests `tags:` under `push:`; this wants the plain
        # branch push that an ordinary commit produces.
        if "\n  push:\n" in text and "branches" in text
    }
    assert on_push, f"no workflow triggers on a branch push: {sorted(files)}"
    assert any("pytest" in text for text in on_push.values()), (
        "a workflow runs on push, but none of them runs the tests"
    )


def test_a_workflow_runs_the_tests_on_a_pull_request():
    """A pull request is the last point where a defect is cheap to find."""
    files = _workflow_text()
    on_pr = {name: text for name, text in files.items() if "\n  pull_request:" in text}
    assert on_pr, f"no workflow triggers on a pull request: {sorted(files)}"
    assert any("pytest" in text for text in on_pr.values()), (
        "a workflow runs on pull requests, but none of them runs the tests"
    )


def test_the_static_checks_run_wherever_the_tests_do():
    """Formatting drift is the cheapest possible thing to catch automatically."""
    for name, text in _workflow_text().items():
        if "pytest" not in text:
            continue
        assert "ruff check" in text, f"{name} runs the tests but not ruff check"
        assert "ruff format --check" in text, f"{name} runs the tests but not ruff format"


def test_something_checks_that_the_window_still_builds():
    """Unit tests drive the frame's handlers on stubs, and a stub has whatever
    it is handed — so none of them can see a menu built before the notebook it
    describes exists. Only starting the real thing catches that."""
    testing = [text for text in _workflow_text().values() if "pytest" in text]
    assert any("--startup-gui-smoke" in text for text in testing), (
        "nothing that runs the tests also checks the window can be built"
    )


@pytest.mark.parametrize("platform", ["windows", "macos", "ubuntu"])
def test_the_tests_run_on_every_platform_that_ships(platform):
    """Linux code ships — `linux_accessibility.py`, pexpect, the POSIX process
    groups — and several tests are skipped on Windows and macOS, so without a
    Linux runner they run nowhere at all.

    Scoped to the workflows that test a pull request, not every workflow that
    happens to name a platform somewhere: the release workflow publishes from
    an Ubuntu runner while testing on neither.
    """
    testing = [
        text
        for text in _workflow_text().values()
        if "\n  pull_request:" in text and "pytest" in text
    ]
    assert testing, "nothing tests a pull request, so no platform is covered"
    covered = any(platform in text for text in testing)
    assert covered, f"pull requests are tested, but never on a {platform} runner"


def test_a_job_cannot_run_for_six_hours():
    """GitHub's default job timeout is 360 minutes. This suite drives real
    subprocesses, pseudo-terminals and worker threads, so a deadlock is a
    plausible way to spend a whole afternoon of somebody's runner minutes."""
    for name, text in _workflow_text().items():
        if "pytest" not in text:
            continue
        assert "timeout-minutes:" in text, f"{name} runs the tests with no job timeout"
