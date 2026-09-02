"""What BlindPilot writes down about itself, and what it must never write.

The application's content is somebody's source code and the questions they
asked about it. So the line this draws matters more here than in most
programs: it records what BlindPilot *did*, never what the person said or what
the answer was. `_log_unfinished_turn` already drew that line by hand for one
backend; this is the same line, drawn once, for all of them.
"""

from __future__ import annotations

import logging
import platform
from pathlib import Path

import pytest

import diagnostics


@pytest.fixture(autouse=True)
def _quiet_root(tmp_path):
    """Never let a test leave a handler pointing at a real log file.

    `start_logging` is deliberately once-only, so each test also has to put
    that back or the second one silently configures nothing.

    It takes `tmp_path` it does not use so that pytest builds that directory
    first and therefore tears it down last: Windows will not delete a file
    faulthandler still holds open.
    """
    root = logging.getLogger()
    before = (list(root.handlers), root.level)
    diagnostics._started = False
    try:
        yield
    finally:
        for handler in list(root.handlers):
            if handler not in before[0]:
                handler.close()
                root.removeHandler(handler)
        root.handlers[:] = before[0]
        root.setLevel(before[1])
        diagnostics.stop_logging()


# ----- where it goes -----
def test_the_log_does_not_live_in_the_roaming_profile(monkeypatch):
    """A roaming profile syncs between machines. A log is about one machine,
    and it grows, so it is the wrong thing to carry around."""
    if platform.system() != "Windows":
        pytest.skip("roaming profiles are a Windows concept")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\someone\AppData\Local")
    monkeypatch.setenv("APPDATA", r"C:\Users\someone\AppData\Roaming")

    where = str(diagnostics.log_dir())

    assert "Local" in where
    assert "Roaming" not in where


def test_the_log_directory_is_named_after_the_application():
    assert "blindpilot" in str(diagnostics.log_dir()).casefold()


# ----- what it is allowed to say -----
def test_a_turn_that_ended_badly_is_recorded_without_a_word_of_its_content(tmp_path, monkeypatch):
    """The whole point. Metadata yes; the conversation never."""
    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    diagnostics.start_logging()

    diagnostics.log_unfinished_turn(
        "codex",
        exit_code=1,
        completed=False,
        session_id="thread-77",
        permission_mode="acceptEdits",
        model="gpt-5",
        cancelled=False,
        detail="Codex app server closed before the turn completed",
    )
    logging.shutdown()
    written = (tmp_path / diagnostics.LOG_NAME).read_text(encoding="utf-8")

    # What it is for.
    for wanted in ("codex", "thread-77", "acceptEdits", "gpt-5", "closed before"):
        assert wanted in written, f"{wanted!r} missing from {written!r}"


def test_the_recorded_fields_are_a_closed_list():
    """Adding a field is a decision, not an accident. Anything not named here
    cannot reach the log through this call."""
    allowed = {
        "exit_code",
        "completed",
        "session_id",
        "permission_mode",
        "model",
        "cancelled",
        "detail",
    }

    assert set(diagnostics.TURN_FIELDS) == allowed
    for banned in ("prompt", "answer", "response", "message", "text", "content"):
        assert banned not in diagnostics.TURN_FIELDS


def test_a_field_nobody_declared_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    diagnostics.start_logging()

    with pytest.raises(TypeError):
        diagnostics.log_unfinished_turn("codex", prompt="rewrite my private repository")


# ----- it must not grow forever -----
def test_the_log_is_capped_and_rolls_over(tmp_path, monkeypatch):
    """An append-forever log is a liability rather than an asset.

    The cap is shrunk rather than a megabyte being written, so this proves the
    rollover without the test itself becoming the slowest one in the suite.
    """
    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    monkeypatch.setattr(diagnostics, "MAX_BYTES", 4096)
    diagnostics.start_logging()
    logger = logging.getLogger("blindpilot.test")

    for index in range(400):
        logger.warning("a line that is long enough to add up %d %s", index, "x" * 120)
    logging.shutdown()

    written = sorted(p.name for p in tmp_path.glob(diagnostics.LOG_NAME + "*"))
    assert len(written) > 1, f"nothing rolled over: {written}"
    for path in tmp_path.glob(diagnostics.LOG_NAME + "*"):
        assert path.stat().st_size < diagnostics.MAX_BYTES * 2, f"{path.name} is unbounded"
    assert len(written) <= diagnostics.KEEP + 1, f"kept more than it promised: {written}"


# ----- failures that used to vanish -----
def test_an_uncaught_exception_reaches_the_log(tmp_path, monkeypatch):
    """In the packaged windowed build there is no stderr, so an uncaught
    exception is currently lost completely."""
    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    diagnostics.start_logging()

    try:
        raise RuntimeError("nobody caught this")
    except RuntimeError as exc:
        diagnostics.log_uncaught(type(exc), exc, exc.__traceback__)
    logging.shutdown()

    written = (tmp_path / diagnostics.LOG_NAME).read_text(encoding="utf-8")
    assert "nobody caught this" in written
    assert "RuntimeError" in written


def test_starting_twice_does_not_write_everything_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)

    diagnostics.start_logging()
    diagnostics.start_logging()
    logging.getLogger("blindpilot.test").warning("said once")
    logging.shutdown()

    written = (tmp_path / diagnostics.LOG_NAME).read_text(encoding="utf-8")
    assert written.count("said once") == 1


def test_a_log_directory_that_cannot_be_made_is_not_fatal(monkeypatch, tmp_path):
    """Losing the log is never worth losing the application over."""
    blocked = tmp_path / "nope"
    blocked.write_text("I am a file, not a directory", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "log_dir", lambda: blocked / "logs")

    assert diagnostics.start_logging() is None  # must not raise


def test_the_level_can_be_raised_from_the_environment(tmp_path, monkeypatch):
    """The conventional way to get more detail out of a tool for a bug report."""
    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    monkeypatch.setenv("BLINDPILOT_LOG_LEVEL", "DEBUG")

    diagnostics.start_logging()

    assert logging.getLogger("blindpilot").level == logging.DEBUG
    # Asking for more than the default is asking for a bug report, so what the
    # libraries have to say is wanted too.
    assert logging.getLogger().level == logging.DEBUG


def test_the_default_level_is_info(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    monkeypatch.delenv("BLINDPILOT_LOG_LEVEL", raising=False)

    diagnostics.start_logging()

    assert logging.getLogger("blindpilot").level == logging.INFO


def test_a_nonsense_level_falls_back_rather_than_failing(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    monkeypatch.setenv("BLINDPILOT_LOG_LEVEL", "LOUDER")

    diagnostics.start_logging()

    assert logging.getLogger("blindpilot").level == logging.INFO


def test_a_chatty_library_cannot_push_out_what_the_log_is_for(tmp_path, monkeypatch):
    """The file is capped on purpose, so what fills it matters.

    A library that logs a line per HTTP request or per COM call would roll the
    records this exists to keep straight out of the file, long before anybody
    came looking for them. BlindPilot's own logger is set to the asked-for
    level; nothing else drops below WARNING unless somebody asks for it.
    """
    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    monkeypatch.delenv("BLINDPILOT_LOG_LEVEL", raising=False)
    diagnostics.start_logging()

    chatty = logging.getLogger("some_http_library.connectionpool")
    chatty.info("Starting new HTTPS connection")
    logging.getLogger("some_com_library").debug("a COM call nobody asked about")
    logging.getLogger("blindpilot.codex").info("the sort of line this log is for")
    chatty.warning("connection pool is full")
    logging.shutdown()
    written = (tmp_path / diagnostics.LOG_NAME).read_text(encoding="utf-8")

    assert "the sort of line this log is for" in written
    assert "Starting new HTTPS connection" not in written
    assert "a COM call nobody asked about" not in written
    # Quieted, not silenced: a library with something wrong to report is still
    # exactly what somebody reading this would want to see.
    assert "connection pool is full" in written


def test_the_path_is_reported_so_it_can_be_named_to_somebody(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)

    started = diagnostics.start_logging()

    assert started == Path(tmp_path) / diagnostics.LOG_NAME


def test_a_test_run_never_writes_to_the_real_log():
    """The conftest fixture redirects every test away from the installed
    application's own log folder. `test_startup` calls `main()`, which starts
    logging for the whole process, so without that redirect an ordinary test
    run leaves wreckage where somebody's real diagnostics live.
    """
    assert "blindpilot-test-logs-" in str(diagnostics.log_dir()), (
        "tests are pointed at the real log folder"
    )


def test_the_folder_can_be_opened_without_reading_a_path_out_loud(monkeypatch, tmp_path):
    """Telling somebody a path they must then navigate to is not a way in."""
    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    opened: list[str] = []
    monkeypatch.setattr(diagnostics, "_reveal", lambda path: opened.append(str(path)) or True)

    assert diagnostics.open_log_folder() is True
    assert opened == [str(tmp_path)]


def test_opening_a_folder_that_is_not_there_yet_makes_it_first(monkeypatch, tmp_path):
    """Nothing has gone wrong yet is the commonest reason it is missing."""
    target = tmp_path / "not-yet"
    monkeypatch.setattr(diagnostics, "log_dir", lambda: target)
    monkeypatch.setattr(diagnostics, "_reveal", lambda _path: True)

    assert diagnostics.open_log_folder() is True
    assert target.is_dir()


def test_a_folder_that_will_not_open_is_reported_rather_than_raised(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)

    def refuse(_path):
        raise OSError("no file manager here")

    monkeypatch.setattr(diagnostics, "_reveal", refuse)

    assert diagnostics.open_log_folder() is False
