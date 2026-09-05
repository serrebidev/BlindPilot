"""Stable test fixtures for Windows and screen-reader development machines."""

from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

import backend_pool


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


@pytest.fixture(autouse=True)
def chat_data_stays_out_of_the_real_folder(monkeypatch):
    """Point Chat mode's data folder at a throwaway directory for every test.

    `accessible_ai.storage.paths.app_data_dir` is where chat.sqlite3 and the
    chat log go, and it creates the folder when asked. Left alone, any test
    that opens Chat mode writes into the installed app's own %APPDATA% folder.
    """
    try:
        from accessible_ai.storage import paths
    except Exception:
        yield
        return
    data = Path(tempfile.mkdtemp(prefix="blindpilot-test-chat-"))
    monkeypatch.setattr(paths, "system_config_dir", lambda: data)
    try:
        yield
    finally:
        # The chat log handler holds the file open on Windows; drop it first.
        chat_logger = logging.getLogger("accessible_ai")
        for handler in list(chat_logger.handlers):
            if (
                isinstance(handler, logging.FileHandler)
                and Path(handler.baseFilename).parent == data
            ):
                chat_logger.removeHandler(handler)
                handler.close()
        shutil.rmtree(data, ignore_errors=True)


def _forget_every_held_process() -> None:
    """Stop everything the pool is holding and start it again from nothing.

    Replacing the singleton rather than only draining it, because a pool
    carries more than its processes: `on_reap` is a callback the window
    installs, and `drop_all` leaves it in place.
    """
    running = backend_pool._pool
    if running is not None:
        running.drop_all()
    backend_pool._pool = None


@pytest.fixture(autouse=True)
def no_backend_process_outlives_its_test():
    """Empty the shared pool around every test.

    Backends hold their process across turns now, so a test that drives a
    worker leaves its stand-in in a process-wide registry. The next test to
    ask for that backend would be handed the previous test's fake instead of
    starting its own - and with `pytest-randomly` shuffling the order, which
    test that is changes run to run.
    """
    _forget_every_held_process()
    try:
        yield
    finally:
        _forget_every_held_process()


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
