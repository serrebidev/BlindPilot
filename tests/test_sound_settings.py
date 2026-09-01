"""Turning the earcons off, one cue at a time.

There was no way to silence them at all: no setting, no menu item, no flag.
The only route was deleting files out of the `EarCons` folder.

The three cues are not equivalent, which is why this is three switches rather
than one. "Sent" and "Answer received" are one-shots that confirm something
happened. "Working" is a loop that runs for the whole turn, so it is both the
one most likely to wear thin and the only one that says a long turn is still
alive without being asked.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app

CUES = ["send", "working", "received"]


@pytest.fixture
def earcons(monkeypatch, tmp_path):
    """An Earcons that records what it would have played instead of playing it."""
    box = app.Earcons(str(tmp_path))
    played: list[str] = []
    monkeypatch.setattr(box, "_play_once", lambda path: played.append(str(path)))
    # Pretend every cue file is present; which file is irrelevant here.
    box.send = "send.wav"
    box.received = "received.wav"
    box.in_progress = "in-progress.wav"
    box.played = played
    return box


@pytest.fixture(autouse=True)
def sounds_start_on(monkeypatch):
    monkeypatch.setattr(app.SETTINGS, "sounds", dict.fromkeys(CUES, True))


def test_every_cue_is_on_for_a_fresh_configuration(monkeypatch):
    """Nobody who never opens the menu notices any change."""
    monkeypatch.setattr(app, "_load_config", dict)
    settings = app._Settings()

    assert settings.sounds == {"send": True, "working": True, "received": True}


def test_a_configuration_written_before_this_existed_still_reads(monkeypatch):
    """An older config has no `sounds` key at all, and must not crash or mute."""
    monkeypatch.setattr(app, "_load_config", lambda: {"live_rows": False})
    settings = app._Settings()

    assert settings.sounds == dict.fromkeys(CUES, True)


def test_an_unknown_cue_in_the_config_is_ignored(monkeypatch):
    """A newer version's config must not be able to break an older one."""
    monkeypatch.setattr(app, "_load_config", lambda: {"sounds": {"send": False, "kazoo": True}})
    settings = app._Settings()

    assert settings.sounds == {"send": False, "working": True, "received": True}


def test_the_choice_is_saved(monkeypatch):
    saved: dict = {}
    monkeypatch.setattr(app, "_load_config", dict)
    monkeypatch.setattr(app, "_save_config", saved.update)
    settings = app._Settings()
    settings.sounds["working"] = False

    settings.save()

    assert saved["sounds"] == {"send": True, "working": False, "received": True}


def test_the_sent_cue_is_silent_when_it_is_switched_off(earcons):
    app.SETTINGS.sounds["send"] = False

    earcons.play_send()

    assert earcons.played == []


def test_the_answer_cue_is_silent_when_it_is_switched_off(earcons):
    app.SETTINGS.sounds["received"] = False

    earcons.play_received()

    assert earcons.played == []


def test_the_cues_still_play_when_they_are_on(earcons):
    earcons.play_send()
    earcons.play_received()

    assert earcons.played == ["send.wav", "received.wav"]


def test_the_working_loop_does_not_start_when_it_is_switched_off(earcons):
    earcons._system = "Linux"  # the branch that keeps a thread we can look at
    app.SETTINGS.sounds["working"] = False

    earcons.start_progress()

    assert earcons._loop_thread is None


def test_the_working_loop_starts_when_it_is_on(earcons):
    earcons._system = "Linux"

    earcons.start_progress()

    assert earcons._loop_thread is not None
    earcons.stop_progress()


def test_the_answer_still_stops_the_loop_even_with_its_own_cue_off(earcons):
    """Otherwise the loop outlives the turn and nothing ever ends it."""
    earcons._system = "Linux"
    earcons.start_progress()
    app.SETTINGS.sounds["received"] = False

    earcons.play_received()

    assert earcons._loop_thread is None
    assert earcons.played == []


# ----- the menu itself -----
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


def test_the_menu_offers_one_switch_per_cue(frame):
    menu = app.MainFrame._build_sounds_menu(frame)
    try:
        items = menu.GetMenuItems()
        assert [item.GetKind() for item in items] == [wx.ITEM_CHECK] * 3
        assert all(item.IsChecked() for item in items), "the menu opened with a cue already off"
        # Every cue is named, so the list cannot silently lose one.
        labels = " ".join(item.GetItemLabelText().lower() for item in items)
        for word in ("sent", "working", "received"):
            assert word in labels
    finally:
        menu.Destroy()


def test_the_menu_opens_showing_what_is_actually_set(frame, monkeypatch):
    monkeypatch.setitem(app.SETTINGS.sounds, "working", False)

    menu = app.MainFrame._build_sounds_menu(frame)
    try:
        checked = {
            item.GetItemLabelText().lower(): item.IsChecked() for item in menu.GetMenuItems()
        }
        assert checked["working"] is False
        assert all(state for label, state in checked.items() if label != "working")
    finally:
        menu.Destroy()
