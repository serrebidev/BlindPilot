"""Stable test fixtures for Windows and screen-reader development machines."""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def diagnostics_stay_out_of_the_real_log(monkeypatch):
    """Keep everything a test writes down out of the installed app's own log.

    A turn that ends without finishing leaves an account of itself, and tests
    drive that path deliberately. The person running them should not find test
    wreckage in the folder the application they use every day writes to.

    This has to be a blanket redirect rather than a patch on one method:
    `test_startup` calls `main()`, which starts logging for the whole process,
    and any test that reaches an error path writes through it from then on.
    """
    try:
        import diagnostics
    except Exception:
        # A build without it has nothing to redirect, and this fixture must not
        # be the reason its tests cannot run.
        yield
        return
    # Made the same way as `tmp_path` below, for the same reason: pytest's own
    # temporary directories carry an ACL these machines cannot write through.
    logs = Path(tempfile.mkdtemp(prefix="blindpilot-test-logs-"))
    monkeypatch.setattr(diagnostics, "log_dir", lambda: logs)
    try:
        yield
    finally:
        # Every handle released before the directory goes, or Windows refuses
        # to delete a file faulthandler is still holding.
        diagnostics.stop_logging()
        shutil.rmtree(logs, ignore_errors=True)


@pytest.fixture
def tmp_path() -> Path:
    """Use ordinary workspace ACLs instead of pytest's restrictive Windows ACL."""
    root = Path.cwd() / ".test-tmp"
    root.mkdir(exist_ok=True)
    temporary = root / uuid.uuid4().hex
    temporary.mkdir()
    try:
        yield temporary
    finally:
        shutil.rmtree(temporary)
        try:
            root.rmdir()
        except OSError:
            # Parallel tests may still own another child directory.
            pass
