"""Stable test fixtures for Windows and screen-reader development machines."""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def worker_notes_stay_out_of_the_real_config(monkeypatch):
    """Keep a test's account of a failed turn out of the installed app's folder.

    A Claude turn that ends without finishing writes a note next to the app's
    own settings. Tests drive that path deliberately, and the person running
    them should not find test wreckage in the directory the application they
    use every day keeps its configuration in.
    """
    try:
        import blindpilot_app
    except Exception:
        # No wxPython on this machine: the tests that need it cannot run here
        # anyway, and collection should not fail on this fixture.
        yield
        return
    # Made the same way as `tmp_path` below, for the same reason: pytest's own
    # temporary directories carry an ACL these machines cannot write through.
    worker = getattr(blindpilot_app, "ClaudeWorker", None)
    if worker is None or not hasattr(worker, "_diagnostic_path"):
        # A build that does not keep these notes has nothing to redirect, and
        # this fixture must not be the reason its tests cannot run.
        yield
        return
    notes = Path(tempfile.mkdtemp(prefix="blindpilot-worker-notes-"))
    monkeypatch.setattr(
        worker,
        "_diagnostic_path",
        staticmethod(lambda: notes / "claude-worker.log"),
    )
    try:
        yield
    finally:
        shutil.rmtree(notes, ignore_errors=True)


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
