"""Silencing one sound cue without silencing the other two.

`sounds_enabled` turns all three off together, which is the right master
switch and stays exactly as it is. But the three are not interchangeable.
Sent and Answer received are one-shots that confirm something happened;
Working is a *loop* that runs for the whole turn. It is therefore both the one
most likely to wear thin over a long fan-out and the only one that says a turn
is still alive without being asked — so wanting the loop gone is not the same
wish as wanting silence, and one switch cannot express both.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app

CUES = ["send", "working", "received"]


@pytest.fixture
def earcons(monkeypatch, tmp_path):
    """An Earcons that records what it would have played."""
    box = app.Earcons(str(tmp_path))
    played: list[str] = []
    monkeypatch.setattr(box, "_play_once", lambda path: played.append(str(path)))
    box.send = "send.wav"
    box.received = "received.wav"
    box.in_progress = "in-progress.wav"
    box.played = played
    return box


# ----- the setting -----
def test_every_cue_is_on_for_a_fresh_configuration(monkeypatch):
    """Nobody who never opens the menu notices any change."""
    monkeypatch.setattr(app, "_load_config", dict)

    settings = app._Settings()

    assert settings.sounds_enabled is True
    assert settings.sound_cues == dict.fromkeys(CUES, True)


def test_a_configuration_written_before_this_existed_still_reads(monkeypatch):
    """0.9.0 wrote `sounds_enabled` and no per-cue key at all."""
    monkeypatch.setattr(app, "_load_config", lambda: {"sounds_enabled": False})

    settings = app._Settings()

    assert settings.sounds_enabled is False
    assert settings.sound_cues == dict.fromkeys(CUES, True)


def test_a_cue_this_version_does_not_know_is_dropped(monkeypatch):
    """A newer version's configuration must not break an older one."""
    monkeypatch.setattr(app, "_load_config", lambda: {"sound_cues": {"send": False, "kazoo": True}})

    settings = app._Settings()

    assert settings.sound_cues == {"send": False, "working": True, "received": True}


def test_the_choice_is_saved_alongside_the_master_switch(monkeypatch):
    written: list[dict] = []
    monkeypatch.setattr(app, "_load_config", lambda: {"backend": "codex"})
    monkeypatch.setattr(app, "_save_config", written.append)
    settings = app._Settings()
    settings.sound_cues["working"] = False

    settings.save()

    assert written[-1]["sound_cues"] == {"send": True, "working": False, "received": True}
    assert written[-1]["sounds_enabled"] is True
    assert written[-1]["backend"] == "codex"


# ----- the cues themselves -----
def test_all_three_play_when_nothing_is_switched_off(earcons):
    earcons.play_send()
    earcons.play_received()

    assert earcons.played == ["send.wav", "received.wav"]


@pytest.mark.parametrize(("cue", "play"), [("send", "play_send"), ("received", "play_received")])
def test_a_one_shot_is_silent_when_its_own_cue_is_off(earcons, cue, play):
    earcons.set_cues({cue: False})

    getattr(earcons, play)()

    assert earcons.played == []


def test_the_other_cues_are_untouched_by_silencing_one(earcons):
    """The whole point: this is not the master switch under another name."""
    earcons.set_cues({"send": False})

    earcons.play_send()
    earcons.play_received()

    assert earcons.played == ["received.wav"]


def test_the_working_loop_does_not_start_when_its_cue_is_off(earcons):
    earcons._system = "Linux"  # the branch that leaves a thread to look at
    earcons.set_cues({"working": False})

    earcons.start_progress()

    assert earcons._loop_thread is None


def test_the_working_loop_starts_when_its_cue_is_on(earcons):
    earcons._system = "Linux"

    earcons.start_progress()

    assert earcons._loop_thread is not None
    earcons.stop_progress()


def test_silencing_the_working_cue_stops_a_loop_already_playing(earcons):
    """Somebody reaching for that switch means now, not at the end of the turn."""
    earcons._system = "Linux"
    earcons.start_progress()

    earcons.set_cues({"working": False})

    assert earcons._loop_thread is None


def test_the_master_switch_still_wins(earcons):
    """`sounds_enabled` means silence, whatever the individual cues say."""
    earcons._system = "Linux"
    earcons.set_cues(dict.fromkeys(CUES, True))
    earcons.set_enabled(False)

    earcons.play_send()
    earcons.play_received()
    earcons.start_progress()

    assert earcons.played == []
    assert earcons._loop_thread is None


def test_the_answer_still_stops_the_loop_with_every_cue_off(earcons):
    """Otherwise the loop outlives the turn and nothing is left to end it."""
    earcons._system = "Linux"
    earcons.start_progress()
    earcons.set_cues({"received": False})

    earcons.play_received()

    assert earcons._loop_thread is None


# ----- the menu -----
wx = pytest.importorskip("wx")


@pytest.fixture(scope="module")
def wx_app():
    try:
        return wx.App(False)
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"no display for wxPython: {exc}")


@pytest.fixture
def frame(wx_app):
    window = wx.Frame(None)
    try:
        yield window
    finally:
        window.Destroy()


def test_the_submenu_offers_one_switch_per_cue(frame):
    menu = app.MainFrame._build_sound_cue_menu(frame)
    try:
        items = menu.GetMenuItems()
        assert [item.GetKind() for item in items] == [wx.ITEM_CHECK] * 3
        labels = " ".join(item.GetItemLabelText().lower() for item in items)
        for word in ("sent", "working", "received"):
            assert word in labels
    finally:
        menu.Destroy()


def test_the_submenu_is_greyed_out_while_the_master_switch_is_off(frame, monkeypatch):
    """Three live switches under a master that mutes them all would be a lie."""
    monkeypatch.setattr(app.SETTINGS, "sounds_enabled", False)

    menu = app.MainFrame._build_sound_cue_menu(frame)
    try:
        assert not any(item.IsEnabled() for item in menu.GetMenuItems())
    finally:
        menu.Destroy()
