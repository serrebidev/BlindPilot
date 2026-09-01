"""The working sound: how it behaves, and how it is turned down or off.

Loaded with a stub for wx, like the other settings tests -- the cue's behaviour
is a timer and a setting, and neither needs a window. What is under test is what
a listener actually gets: a cue that repeats end to end for the whole turn, one
that plays occasionally, or none at all.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


def _install_wx_stub() -> None:
    """Minimal stand-in for wx, so the module under test can be imported."""
    if "wx" in sys.modules:
        return

    class _Stub:
        def __init__(self, *args, **kwargs):
            pass

        def __getattr__(self, name):
            return _Stub()

        def __call__(self, *args, **kwargs):
            return _Stub()

    class _Module(types.ModuleType):
        def __getattr__(self, name):
            value = type(name, (_Stub,), {})
            setattr(self, name, value)
            return value

    wx = _Module("wx")
    sys.modules["wx"] = wx
    for name in ("wx.adv", "wx.lib", "wx.lib.newevent", "wx.html", "wx.richtext", "wx.stc"):
        sys.modules[name] = _Module(name)


_install_wx_stub()

import blindpilot_app  # noqa: E402


@pytest.fixture
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(blindpilot_app, "_config_dir", lambda: tmp_path)
    monkeypatch.setattr(blindpilot_app, "_legacy_config_path", lambda: tmp_path / "absent.json")
    return tmp_path


class _NullThread:
    """Stands in for a timer thread, so nothing actually runs in a test."""

    def start(self):
        return None


class _Recording(blindpilot_app.Earcons):
    """Earcons with the audio replaced by a record of what would have played."""

    def __init__(self, system: str = "Windows") -> None:
        super().__init__("/nonexistent")
        self._system = system
        # Resolved cue paths, so the code under test believes it has sounds.
        self.send = "send.wav"
        self.received = "received.wav"
        self.in_progress = "in-progress.wav"
        self.played: list[str] = []
        self.looped = 0
        self.purged = 0

    def _play_once(self, path):
        if path:
            self.played.append(path)

    def _loop_windows(self):  # pragma: no cover - overridden below
        self.looped += 1


# -- the setting itself ---------------------------------------------------


def test_the_cue_is_periodic_by_default(config_dir: Path) -> None:
    """Nobody has to find the setting to stop the cue repeating every 0.75s."""
    settings = blindpilot_app._Settings()

    assert settings.progress_cue == blindpilot_app.CUE_PERIODIC
    assert settings.progress_cue_seconds == blindpilot_app.CUE_SECONDS_DEFAULT


def test_a_choice_survives_a_restart(config_dir: Path) -> None:
    settings = blindpilot_app._Settings()
    settings.progress_cue = blindpilot_app.CUE_OFF
    settings.progress_cue_seconds = 45
    settings.save()

    assert blindpilot_app._Settings().progress_cue == blindpilot_app.CUE_OFF
    assert blindpilot_app._Settings().progress_cue_seconds == 45


def test_an_unreadable_setting_does_not_stop_the_app(config_dir: Path) -> None:
    """A config from a newer version, or edited by hand, must not be fatal."""
    assert blindpilot_app._valid_progress_cue("something-else") == blindpilot_app.CUE_PERIODIC
    assert blindpilot_app._valid_progress_cue(None) == blindpilot_app.CUE_PERIODIC
    assert blindpilot_app._valid_progress_cue("") == blindpilot_app.CUE_PERIODIC
    # And a valid one is kept as it is.
    assert blindpilot_app._valid_progress_cue("off") == blindpilot_app.CUE_OFF
    assert blindpilot_app._valid_progress_cue("LOOP") == blindpilot_app.CUE_LOOP


def test_an_interval_of_zero_is_refused() -> None:
    """A zero or negative interval would spin the cue thread at full speed."""
    assert blindpilot_app._valid_cue_seconds(0) >= blindpilot_app.CUE_SECONDS_MIN
    assert blindpilot_app._valid_cue_seconds(-5) >= blindpilot_app.CUE_SECONDS_MIN
    assert blindpilot_app._valid_cue_seconds("nonsense") == blindpilot_app.CUE_SECONDS_DEFAULT
    # An absurd interval is capped rather than accepted.
    assert blindpilot_app._valid_cue_seconds(99999) <= blindpilot_app.CUE_SECONDS_MAX
    # A sensible one is left alone.
    assert blindpilot_app._valid_cue_seconds(30) == 30


# -- what actually plays --------------------------------------------------


def test_off_plays_no_working_cue(config_dir: Path, monkeypatch) -> None:
    """The whole point of the setting: silence while the turn runs.

    Every way the cue can be produced is watched, not just the one this
    platform happens to use: the looping path calls the sound API directly
    rather than going through the one-shot player, so a test that only counted
    one-shots passed while the loop played.
    """
    settings = blindpilot_app._Settings()
    settings.progress_cue = blindpilot_app.CUE_OFF
    monkeypatch.setattr(blindpilot_app, "SETTINGS", settings)

    for system in ("Windows", "Darwin", "Linux"):
        cues = _Recording(system=system)
        started: list[object] = []
        # The looping path calls the platform sound API directly, so that is
        # watched too -- not only the one-shot player and the periodic timer.
        monkeypatch.setattr(
            blindpilot_app.Earcons, "_start_periodic",
            lambda _self, seconds: started.append(("periodic", seconds)),
        )
        def _play_sound(sound, flags):
            # PlaySound(None, SND_PURGE) is how a cue is SILENCED, which
            # start_progress does first. Only an actual sound counts as played.
            if sound is not None:
                started.append(("winsound", sound))

        monkeypatch.setitem(
            sys.modules, "winsound",
            types.SimpleNamespace(
                PlaySound=_play_sound,
                SND_FILENAME=1, SND_ASYNC=2, SND_LOOP=8, SND_PURGE=0,
            ),
        )
        monkeypatch.setattr(cues, "_unix_player", lambda: ["would-play"])
        monkeypatch.setattr(
            blindpilot_app.threading, "Thread",
            lambda *a, **k: started.append(("thread", k.get("target"))) or _NullThread(),
        )

        cues.start_progress()

        assert cues.played == [], f"one-shot cue played on {system}"
        assert started == [], f"a working cue was started on {system}: {started}"


def test_periodic_plays_once_immediately_then_waits(config_dir: Path, monkeypatch) -> None:
    """A cue on send says the message went; the rest is silence until the wait.

    The wait is checked rather than slept through: what matters is that the
    interval comes from the setting, not that the test takes that long.
    """
    settings = blindpilot_app._Settings()
    settings.progress_cue = blindpilot_app.CUE_PERIODIC
    settings.progress_cue_seconds = 37
    monkeypatch.setattr(blindpilot_app, "SETTINGS", settings)

    cues = _Recording()
    waits: list[float] = []

    class _Stop:
        def __init__(self):
            self._set = False

        def clear(self):
            self._set = False

        def set(self):
            self._set = True

        def is_set(self):
            return self._set

        def wait(self, seconds):
            waits.append(seconds)
            # End the loop after the first interval, so the test does not run on.
            self._set = True
            return True

    # The stop event is passed in rather than read off the object: a fresh event
    # per run is what keeps a replaced thread from looping alongside the new one,
    # so the test hands over the same event the real caller would.
    stop = _Stop()
    cues._loop_stop = stop
    cues._periodic(settings.progress_cue_seconds, stop)

    assert cues.played == ["in-progress.wav"]
    assert waits == [37]


def test_stopping_ends_the_periodic_cue_on_windows(config_dir: Path, monkeypatch) -> None:
    """Windows used to return before the thread's stop was ever set.

    The looping cue is stopped by the sound API, so the early return was
    harmless until a cue of ours ran on a thread -- which would then have kept
    playing over the answer it was announcing.
    """
    settings = blindpilot_app._Settings()
    settings.progress_cue = blindpilot_app.CUE_PERIODIC
    monkeypatch.setattr(blindpilot_app, "SETTINGS", settings)

    cues = _Recording(system="Windows")
    cues.stop_progress()

    assert cues._loop_stop.is_set() is True


def test_the_send_and_received_cues_survive_switching_the_cue_off(
    config_dir: Path, monkeypatch
) -> None:
    """Off means the repeating cue, not every sound in the application."""
    settings = blindpilot_app._Settings()
    settings.progress_cue = blindpilot_app.CUE_OFF
    monkeypatch.setattr(blindpilot_app, "SETTINGS", settings)

    cues = _Recording()
    cues.play_send()
    cues.play_received()

    assert cues.played == ["send.wav", "received.wav"]


def _purge_watching_winsound(monkeypatch, log: list) -> None:
    """Install a winsound whose every call is recorded, purges included."""
    def _play_sound(sound, flags):
        log.append(("purge",) if sound is None else ("play", sound))

    monkeypatch.setitem(
        sys.modules, "winsound",
        types.SimpleNamespace(
            PlaySound=_play_sound,
            SND_FILENAME=1, SND_ASYNC=2, SND_LOOP=8, SND_PURGE=0,
        ),
    )


def test_ending_a_turn_does_not_purge_the_received_cue(config_dir: Path, monkeypatch) -> None:
    """The cue announcing the answer must not be cut off by the turn ending.

    Purging is per process on Windows, so a purge issued to stop a working cue
    also silences the one-shot that has just started. The window ends a turn in
    two steps -- the response arrives, then the worker finishes -- and the
    second step used to purge unconditionally, cutting the 'received' cue after
    however long the gap between the two happened to be. Measured: 290 ms of
    audio reduced to 50 ms once a held connection made that gap almost nothing.

    So the property under test is not 'stop_progress purges' but 'no purge is
    issued after the received cue starts, when no working cue is playing'.
    """
    settings = blindpilot_app._Settings()
    settings.progress_cue = blindpilot_app.CUE_OFF
    monkeypatch.setattr(blindpilot_app, "SETTINGS", settings)
    calls: list = []
    _purge_watching_winsound(monkeypatch, calls)

    cues = blindpilot_app.Earcons("/nonexistent")
    cues._system = "Windows"
    cues.received, cues.in_progress, cues.send = "received.wav", "in-progress.wav", "send.wav"

    cues.start_progress()      # off: nothing to play, nothing to stop
    cues.play_received()       # the answer arrived
    cues.stop_progress()       # the worker finished, a moment later

    assert ("play", "received.wav") in calls, calls
    # Nothing of ours is playing in this mode, so no purge is warranted at any
    # point of the turn. The weaker property -- 'no purge after the received cue
    # starts' -- let a mutation through that raised the flag unconditionally:
    # that purges while the send cue is still sounding, which on a fast turn
    # cuts the send cue instead of the received one. Same defect, different
    # victim, so the flag has to mean 'a cue of ours is playing' exactly.
    assert ("purge",) not in calls, f"purged with no cue of ours playing: {calls}"


def test_a_working_cue_is_still_silenced_when_the_answer_arrives(
    config_dir: Path, monkeypatch
) -> None:
    """The guard must not leave the looping cue playing over the answer.

    The counterpart to the test above: not purging is only correct when there
    is nothing of ours to stop. While a working cue loops, the purge is what
    ends it, and it has to happen before the received cue is played.
    """
    settings = blindpilot_app._Settings()
    settings.progress_cue = blindpilot_app.CUE_LOOP
    monkeypatch.setattr(blindpilot_app, "SETTINGS", settings)
    calls: list = []
    _purge_watching_winsound(monkeypatch, calls)

    cues = blindpilot_app.Earcons("/nonexistent")
    cues._system = "Windows"
    cues.received, cues.in_progress, cues.send = "received.wav", "in-progress.wav", "send.wav"

    cues.start_progress()
    assert ("play", "in-progress.wav") in calls, calls
    calls.clear()

    cues.play_received()

    assert calls[0] == ("purge",), f"the looping cue was left playing: {calls}"
    assert ("play", "received.wav") in calls, calls
    # And the second step of ending the turn adds no further purge.
    calls.clear()
    cues.stop_progress()
    assert calls == [], f"the finished turn purged again: {calls}"


def test_a_periodic_cue_is_silenced_when_the_answer_arrives(
    config_dir: Path, monkeypatch
) -> None:
    """The periodic mode plays through the one-shot player, and still needs the
    purge: its cue may be sounding at the moment the answer arrives."""
    settings = blindpilot_app._Settings()
    settings.progress_cue = blindpilot_app.CUE_PERIODIC
    monkeypatch.setattr(blindpilot_app, "SETTINGS", settings)
    calls: list = []
    _purge_watching_winsound(monkeypatch, calls)
    monkeypatch.setattr(
        blindpilot_app.threading, "Thread", lambda *a, **k: _NullThread()
    )

    cues = blindpilot_app.Earcons("/nonexistent")
    cues._system = "Windows"
    cues.received, cues.in_progress, cues.send = "received.wav", "in-progress.wav", "send.wav"

    cues.start_progress()
    calls.clear()
    cues.play_received()

    assert calls[0] == ("purge",), f"a periodic cue was not silenced: {calls}"
