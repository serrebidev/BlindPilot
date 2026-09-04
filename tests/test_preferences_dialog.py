"""Preferences… (Cmd+,) opens the Options menu's settings in one dialog.

The dialog must read the live settings, and applying it must write them back
through the same switches the menu items use, so menu and dialog can never
disagree. The frame is stubbed with the menu items the applier touches.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app

wx = pytest.importorskip("wx")


@pytest.fixture(scope="module")
def wx_app():
    try:
        return wx.App(False)
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"no display for wxPython: {exc}")


@pytest.fixture(autouse=True)
def restore_settings():
    """Put the global SETTINGS singleton back after every test here.

    Applying the dialog writes through the live process-wide SETTINGS object,
    and the suite is shuffled randomly, so a test that changes it and leaves
    it changed breaks whichever test next reads the defaults -- observed as
    test_sound_cues failing only under some seeds.
    """
    before = dict(app.SETTINGS.__dict__)
    yield
    app.SETTINGS.__dict__.clear()
    app.SETTINGS.__dict__.update(before)


def _item(label: str, checked: bool = False):
    menu = wx.Menu()
    item = menu.AppendCheckItem(wx.ID_ANY, label)
    item.Check(checked)
    return item


class _FrameStub:
    """Enough of MainFrame for _apply_preferences to run against.

    The menus stay referenced: a wx.MenuItem is owned by its wx.Menu, and if
    the menu itself is collected the C++ item left behind is a dangling
    pointer that crashes on the next IsChecked().
    """

    def __init__(self):
        self._menus: list[wx.Menu] = []

        def _checked(label: str, checked: bool):
            menu = wx.Menu()
            item = menu.AppendCheckItem(wx.ID_ANY, label)
            item.Check(checked)
            self._menus.append(menu)
            return item

        self._rows_item = _checked("Live activity", app.SETTINGS.live_rows)
        self._speak_item = _checked("Speak activity", app.SETTINGS.speak_live)
        self._thinking_item = _checked("Reasoning", app.SETTINGS.show_thinking)
        self._sounds_item = _checked("Sound cues", app.SETTINGS.sounds_enabled)
        self._text_view_item = _checked("Text view", app.SETTINGS.text_view)
        self._narration_items = {
            app.NARRATION_EVERYTHING: _checked("Follow everything", True),
            app.NARRATION_KEEP_UP: _checked("Keep up", False),
        }
        self._sound_cue_items = {
            cue: _checked(label, app.SETTINGS.sound_cues.get(cue, True))
            for cue, label, _help in app.SOUND_CUES
        }
        self._cue_loop_item = _checked("Loop", app.SETTINGS.progress_cue == app.CUE_LOOP)
        self._cue_periodic_item = _checked(
            "Periodic", app.SETTINGS.progress_cue == app.CUE_PERIODIC
        )
        self._cue_off_item = _checked("Off", app.SETTINGS.progress_cue == app.CUE_OFF)
        self._automatic_updates_item = _checked("Auto updates", True)
        self.announced: list[str] = []

        class _Earcons:
            def set_enabled(self, _enabled):
                pass

            def set_cues(self, _cues):
                pass

            def start_progress(self):
                pass

            def stop_progress(self):
                pass

        self.earcons = _Earcons()

        class _Notebook:
            def GetPageCount(self):
                return 0

            def GetPage(self, _i):
                return None

        self.notebook = _Notebook()

        def _turn_in_flight():
            return False

        self._turn_in_flight = _turn_in_flight

    def _announce_setting(self, text: str) -> None:
        self.announced.append(text)


def test_the_dialog_reads_the_live_settings(wx_app):
    dialog = app.PreferencesDialog(None)

    try:
        assert dialog.live_rows == app.SETTINGS.live_rows
        assert dialog.speak_live == app.SETTINGS.speak_live
        assert dialog.sounds_enabled == app.SETTINGS.sounds_enabled
        assert dialog.narration_selection() == app.SETTINGS.narration
        assert dialog.sound_cues == app.SETTINGS.sound_cues
        assert dialog.progress_cue == app.SETTINGS.progress_cue
        assert dialog.progress_interval == app.SETTINGS.progress_cue_seconds
        assert dialog.text_view == app.SETTINGS.text_view
    finally:
        dialog.Destroy()


def test_toggling_the_sound_master_switch_disables_the_cue_checks(wx_app):
    dialog = app.PreferencesDialog(None)

    try:
        dialog._sounds.SetValue(False)
        dialog._sync_sound_checks()
        for check in dialog._cue_checks.values():
            assert not check.IsEnabled()
        dialog._sounds.SetValue(True)
        dialog._sync_sound_checks()
        for check in dialog._cue_checks.values():
            assert check.IsEnabled()
    finally:
        dialog.Destroy()


def test_the_interval_is_only_editable_for_the_periodic_cue(wx_app):
    dialog = app.PreferencesDialog(None)

    try:
        dialog._cue_box.SetSelection(0)
        dialog._sync_interval()
        assert not dialog._interval.IsEnabled()
        dialog._cue_box.SetSelection(1)
        dialog._sync_interval()
        assert dialog._interval.IsEnabled()

        # And the interval the dialog exposes always comes back valid, whatever
        # the spin control contains.
        dialog._interval.SetValue(5000)
        assert 2 <= dialog.progress_interval <= 120
    finally:
        dialog.Destroy()


def test_applying_the_dialog_updates_the_settings_and_the_menus(wx_app, monkeypatch):
    saved: list[dict] = []
    monkeypatch.setattr(app, "_load_config", dict)
    monkeypatch.setattr(app, "_save_config", lambda cfg: saved.append(dict(cfg)))
    frame = _FrameStub()

    class _Dialog:
        def narration_selection(self):
            return app.NARRATION_KEEP_UP

        live_rows = False
        speak_live = False
        show_thinking = True
        sounds_enabled = False
        sound_cues = {cue: False for cue, _label, _help in app.SOUND_CUES}
        text_view = True
        progress_cue = app.CUE_OFF
        progress_interval = 30
        check_updates_startup = False

    app.MainFrame._apply_preferences(frame, _Dialog())

    assert app.SETTINGS.narration == app.NARRATION_KEEP_UP
    assert app.SETTINGS.live_rows is False
    assert app.SETTINGS.speak_live is False
    assert app.SETTINGS.show_thinking is True
    assert app.SETTINGS.sounds_enabled is False
    assert not any(app.SETTINGS.sound_cues.values())
    assert app.SETTINGS.text_view is True
    assert app.SETTINGS.progress_cue == app.CUE_OFF
    assert app.SETTINGS.progress_cue_seconds == 30

    # Every menu item followed its setting.
    assert not frame._rows_item.IsChecked()
    assert not frame._speak_item.IsChecked()
    assert frame._thinking_item.IsChecked()
    assert not frame._sounds_item.IsChecked()
    assert frame._text_view_item.IsChecked()
    assert frame._cue_off_item.IsChecked()
    assert not frame._cue_loop_item.IsChecked()
    assert frame._automatic_updates_item.IsChecked() is False

    # The update-choice is persisted to config, not settings -- the settings
    # save happens first, the update flag is the second write.
    assert saved and saved[-1].get("check_for_updates_at_startup") is False
    assert frame.announced == ["Preferences applied"]
