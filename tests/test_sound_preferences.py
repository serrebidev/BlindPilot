"""Sound-cue preference tests."""

from __future__ import annotations

import blindpilot_app as app


def test_sound_cues_are_enabled_for_a_fresh_configuration(monkeypatch):
    monkeypatch.setattr(app, "_load_config", dict)

    settings = app._Settings()

    assert settings.sounds_enabled is True


def test_sound_preference_is_saved_with_the_other_settings(monkeypatch):
    written = []
    monkeypatch.setattr(app, "_load_config", lambda: {"backend": "codex"})
    monkeypatch.setattr(app, "_save_config", lambda cfg: written.append(cfg))
    settings = app._Settings()
    settings.sounds_enabled = False

    settings.save()

    assert written[-1]["sounds_enabled"] is False
    assert written[-1]["backend"] == "codex"


def test_muted_earcons_do_not_play(monkeypatch, tmp_path):
    earcons = app.Earcons(str(tmp_path), enabled=False)
    earcons.send = "send.wav"
    played = []
    monkeypatch.setattr(earcons, "_play_once", lambda path: played.append(path))

    earcons.play_send()

    assert played == []


def test_disabling_earcons_stops_a_sound_already_in_progress(monkeypatch, tmp_path):
    earcons = app.Earcons(str(tmp_path))
    stopped = []
    monkeypatch.setattr(earcons, "stop_progress", lambda: stopped.append(True))

    earcons.set_enabled(False)

    assert earcons.enabled is False
    assert stopped == [True]


def test_sound_menu_updates_the_saved_setting_and_live_earcons(monkeypatch):
    enabled_states = []
    announcements = []
    saved = []
    frame = type(
        "FrameStub",
        (),
        {
            "_sounds_item": type("MenuItemStub", (), {"IsChecked": lambda self: False})(),
            "earcons": type(
                "EarconsStub",
                (),
                {"set_enabled": lambda self, enabled: enabled_states.append(enabled)},
            )(),
            "_announce_setting": lambda self, message: announcements.append(message),
        },
    )()
    monkeypatch.setattr(app.SETTINGS, "sounds_enabled", True)
    monkeypatch.setattr(app.SETTINGS, "save", lambda: saved.append(True))

    app.MainFrame._toggle_sounds(frame)

    assert app.SETTINGS.sounds_enabled is False
    assert enabled_states == [False]
    assert saved == [True]
    assert announcements == ["Sound cues off"]
