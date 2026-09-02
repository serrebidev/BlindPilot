"""Live speech has to survive the screen reader going away and coming back.

`_SPEAKER` was built once, at import, and `announce()` swallowed every failure
with a bare `except Exception: pass`. So when the reader's connection dropped —
NVDA restarting, a JAWS COM object disconnecting, which shows up as
RPC_E_DISCONNECTED and was seen in a real crash log — the object was dead for
the rest of the session. The Options menu still showed narration enabled,
nothing was ever spoken again, and restarting BlindPilot was the only way back.

On an application driven entirely by ear, that is not a degraded state. It is
the whole thing failing, silently, while claiming to work.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app


class _Speaker:
    """A reader connection that can be made to drop, the way a real one does."""

    def __init__(self, alive: bool = True):
        self.alive = alive
        self.said: list[str] = []

    def speak(self, text, interrupt=False):
        if not self.alive:
            raise OSError("the object invoked has disconnected from its clients")
        self.said.append(text)


@pytest.fixture(autouse=True)
def _restore():
    """Start each test from "a rebuild is allowed right now", and put back what
    was there.

    `_speaker_retry_after` is module state, and any test anywhere that reaches
    `announce()` on Windows with no reader sets it five seconds into the
    future. A test here would then be testing the throttle rather than what it
    is about, depending only on which file ran first.
    """
    before = (app._SPEAKER, app._speaker_retry_after)
    app._speaker_retry_after = 0.0
    try:
        yield
    finally:
        app._SPEAKER, app._speaker_retry_after = before


def test_a_working_speaker_is_not_rebuilt(monkeypatch):
    """The ordinary path must not pay for the unusual one."""
    speaker = _Speaker()
    builds: list[int] = []
    monkeypatch.setattr(app, "_SPEAKER", speaker)
    monkeypatch.setattr(app, "_make_speaker", lambda: builds.append(1) or _Speaker())

    app.announce("a line of narration")

    assert speaker.said == ["a line of narration"]
    assert builds == []


def test_a_dropped_connection_is_rebuilt_and_the_line_still_spoken(monkeypatch):
    """The line that discovers the drop should not be the one that is lost."""
    dead = _Speaker(alive=False)
    fresh = _Speaker()
    monkeypatch.setattr(app, "_SPEAKER", dead)
    monkeypatch.setattr(app, "_make_speaker", lambda: fresh)

    app.announce("Error: the turn stopped")

    assert fresh.said == ["Error: the turn stopped"]
    assert app._SPEAKER is fresh


def test_later_lines_go_to_the_rebuilt_speaker(monkeypatch):
    """The point of it: the session keeps talking afterwards."""
    dead = _Speaker(alive=False)
    fresh = _Speaker()
    monkeypatch.setattr(app, "_SPEAKER", dead)
    monkeypatch.setattr(app, "_make_speaker", lambda: fresh)

    app.announce("first")
    app.announce("second")
    app.announce("third")

    assert fresh.said == ["first", "second", "third"]


def test_a_rebuild_that_also_fails_is_not_an_error(monkeypatch):
    """The reader may genuinely be gone. That must not take the app with it."""
    monkeypatch.setattr(app, "_SPEAKER", _Speaker(alive=False))
    monkeypatch.setattr(app, "_make_speaker", lambda: None)

    app.announce("nobody is listening")  # must not raise

    assert app._SPEAKER is None


def test_a_reader_that_starts_late_is_picked_up(monkeypatch):
    """BlindPilot launched before NVDA did. Today that is silence for good."""
    fresh = _Speaker()
    monkeypatch.setattr(app, "_SPEAKER", None)
    monkeypatch.setattr(app.platform, "system", lambda: "Windows")
    monkeypatch.setattr(app, "_speaker_retry_after", 0.0)
    monkeypatch.setattr(app, "_make_speaker", lambda: fresh)

    app.announce("the reader arrived")

    assert fresh.said == ["the reader arrived"]


def test_a_missing_reader_is_not_retried_on_every_single_line(monkeypatch):
    """Building the output scans for a reader. Doing that per narration line
    during a fan-out would cost more than the speech."""
    builds: list[int] = []
    monkeypatch.setattr(app, "_SPEAKER", None)
    monkeypatch.setattr(app.platform, "system", lambda: "Windows")
    monkeypatch.setattr(app, "_speaker_retry_after", 0.0)
    monkeypatch.setattr(app, "_make_speaker", lambda: builds.append(1) or None)

    for _ in range(20):
        app.announce("still nobody")

    assert len(builds) == 1, f"rebuilt {len(builds)} times for 20 lines"


def test_nothing_is_rebuilt_on_a_platform_that_has_no_speaker(monkeypatch):
    """macOS and Linux reach the reader another way entirely."""
    builds: list[int] = []
    monkeypatch.setattr(app, "_SPEAKER", None)
    monkeypatch.setattr(app.platform, "system", lambda: "Linux")
    monkeypatch.setattr(app, "_speaker_retry_after", 0.0)
    monkeypatch.setattr(app, "_make_speaker", lambda: builds.append(1) or None)
    monkeypatch.setattr(app, "_MAC_ANNOUNCE", False)

    app.announce("said another way")

    assert builds == []


def test_a_reader_that_never_speaks_again_is_not_rebuilt_on_every_line(monkeypatch):
    """The rebuild has to be throttled too, not only the first look.

    A connection can be built and still be unable to say anything - NVDA gone
    while its controller client is still loadable is exactly that shape. Every
    line then paid two failed `speak` calls and a full scan for a reader, on
    the path added to stop a fan-out from being silent. That is the cost the
    throttle above was written to avoid, arriving through the other door.
    """
    builds: list[int] = []
    monkeypatch.setattr(app, "_SPEAKER", _Speaker(alive=False))
    monkeypatch.setattr(app.platform, "system", lambda: "Windows")
    monkeypatch.setattr(app, "_speaker_retry_after", 0.0)
    monkeypatch.setattr(app, "_make_speaker", lambda: builds.append(1) or _Speaker(alive=False))

    for _ in range(20):
        app.announce("a line nobody will hear")

    assert len(builds) == 1, f"rebuilt {len(builds)} times for 20 lines"


def test_a_connection_that_cannot_speak_is_let_go_of(monkeypatch):
    """Held on to, it would be tried first on every later line for good."""
    monkeypatch.setattr(app, "_SPEAKER", _Speaker(alive=False))
    monkeypatch.setattr(app.platform, "system", lambda: "Windows")
    monkeypatch.setattr(app, "_speaker_retry_after", 0.0)
    monkeypatch.setattr(app, "_make_speaker", lambda: _Speaker(alive=False))

    app.announce("into the void")

    assert app._SPEAKER is None
