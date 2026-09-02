"""Hearing that something went wrong, without waiting to hear why.

An error was spoken with exactly the urgency of a tool call: `interrupt=False`,
at the back of a queue that a fan-out can make minutes deep. There was also no
error sound at all — the cues are sent, working and received, and failure had
none — so the only signal that a turn had died was a sentence that might be a
long time coming.

Interrupting was considered and rejected: it purges the reader's whole queue,
including speech belonging to other applications. A sound costs nobody else
anything and arrives immediately.

macOS was the opposite problem. Every announcement posted at
`NSAccessibilityPriorityHigh` — the tier VoiceOver treats as speak-now — so
the same code queued politely on Windows and chopped off the previous line on
macOS. High is now what an error gets, not what everything gets.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app


class _Earcons:
    def __init__(self):
        self.played: list[str] = []
        self.enabled = True
        self.cues = {}

    def stop_progress(self):
        self.played.append("stop")

    def play_error(self):
        self.played.append("error")


def _panel(monkeypatch):
    panel = type("PanelStub", (), {})()
    panel._stopping = False
    panel._turns = []
    panel._stream_response = None
    panel._earcons = _Earcons()
    panel.announced: list[tuple] = []
    panel._announce = lambda text, urgent=False: panel.announced.append((text, urgent))
    return panel


# ----- the cue -----
def test_failure_is_one_of_the_cues_that_can_be_switched_off():
    """It belongs with the other three, not bolted on beside them."""
    assert "error" in {cue for cue, _label, _help in app.SOUND_CUES}


def test_a_failed_turn_plays_the_error_cue(monkeypatch):
    panel = _panel(monkeypatch)

    app.SessionPanel._on_failed(panel, "the turn stopped")

    assert "error" in panel._earcons.played


def test_a_turn_the_user_stopped_is_not_an_error(monkeypatch):
    """Stopping is not a failure, and it should not sound like one."""
    panel = _panel(monkeypatch)
    panel._stopping = True

    app.SessionPanel._on_failed(panel, "FreeBuff reported that the response was interrupted")

    assert panel._earcons.played == []
    assert panel.announced == []


def test_the_error_is_still_spoken_as_well_as_sounded(monkeypatch):
    panel = _panel(monkeypatch)

    app.SessionPanel._on_failed(panel, "the turn stopped")

    assert panel.announced and "the turn stopped" in panel.announced[0][0]


def test_an_error_is_announced_urgently(monkeypatch):
    """Which is what tells the platform layer to treat it differently."""
    panel = _panel(monkeypatch)

    app.SessionPanel._on_failed(panel, "the turn stopped")

    assert panel.announced[0][1] is True


# ----- the cue needs no shipped audio file -----
def test_the_error_cue_uses_the_platform_sound_rather_than_a_file(tmp_path):
    """`EarCons/` ships three files. Authoring a fourth is not something to
    fake, and the system error sound is the one already associated with
    failure on whichever platform this is."""
    box = app.Earcons(str(tmp_path))

    assert box._resolve("error") is None, "no error file is shipped, by design"
    box.play_error()  # must not raise on any platform


def test_a_muted_application_plays_no_error_cue(tmp_path, monkeypatch):
    played: list[str] = []
    box = app.Earcons(str(tmp_path), enabled=False)
    monkeypatch.setattr(box, "_play_system_error", lambda: played.append("system"))

    box.play_error()

    assert played == []


def test_the_error_cue_obeys_its_own_switch(tmp_path, monkeypatch):
    played: list[str] = []
    box = app.Earcons(str(tmp_path), cues={"error": False})
    monkeypatch.setattr(box, "_play_system_error", lambda: played.append("system"))

    box.play_error()

    assert played == []


# ----- macOS urgency -----
@pytest.mark.parametrize("urgent", [True, False])
def test_announce_takes_an_urgency(monkeypatch, urgent):
    """Windows still never interrupts - that was decided deliberately, because
    interrupting purges other applications' speech too. Urgency is what macOS
    reads, and what an error sound accompanies."""
    said: list[str] = []
    monkeypatch.setattr(app, "_SPEAKER", None)
    monkeypatch.setattr(app.platform, "system", lambda: "Linux")
    monkeypatch.setattr(app, "_linux_announce", lambda text: said.append(text) or True)

    app.announce("something", urgent=urgent)

    assert said == ["something"]
