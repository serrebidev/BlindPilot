"""When the settings file cannot be written.

`_save_config` caught `OSError` and did nothing with it: no return value, no
log line, no announcement. Ten call sites had no way to know, and several of
them speak a sentence that the failed write has just made untrue — "BlindPilot
will check for updates at startup" is a claim about a file that was not saved.

The worst of them is the first-run wizard. Completing it is recorded by setting
`setup_complete` and calling this; startup shows the wizard when that key is
missing. So a profile the settings cannot be written to — a roaming profile on
an unreachable share, a full disk, a directory something else has locked — puts
somebody through the whole wizard, CLI install and browser sign-in included,
every single time they open the application, with nothing anywhere saying why.
The same write carries `backend`, so a Codex or FreeBuff user is also dropped
back to the Claude default and sent through the Claude checks again.

The write was also not atomic: `open(path, "w")` truncates before `json.dump`
fills it, so an interruption partway leaves a half-written file. `_load_config`
catches the `ValueError` that then comes out of `json.load` and returns `{}`,
which resets every setting at once — including `setup_complete`.
"""

from __future__ import annotations

import json
import logging

import pytest

import blindpilot_app as app


@pytest.fixture
def config_at(tmp_path, monkeypatch):
    """Point the settings file somewhere disposable."""

    def place(path):
        monkeypatch.setattr(app, "_config_path", lambda: path)
        monkeypatch.setattr(app, "_config_dir", lambda: path.parent)
        monkeypatch.setattr(app, "_legacy_config_path", lambda: tmp_path / "nothing-here.json")
        return path

    return place


@pytest.fixture
def unwritable(tmp_path, config_at):
    """A settings path that cannot exist: its parent directory is a file.

    `open` raises `NotADirectoryError` on every platform, so this needs no
    permission tricks and behaves the same on the three the tests run on.
    """
    blocker = tmp_path / "in-the-way"
    blocker.write_text("not a directory", encoding="utf-8")
    return config_at(blocker / "config.json")


def test_a_write_that_failed_says_so(unwritable):
    assert app._save_config({"setup_complete": True}) is False


def test_a_write_that_worked_says_so(config_at, tmp_path):
    path = config_at(tmp_path / "config.json")

    assert app._save_config({"backend": "codex"}) is True
    assert json.loads(path.read_text(encoding="utf-8")) == {"backend": "codex"}


def test_the_failure_is_recorded_where_a_bug_report_can_find_it(unwritable, caplog):
    """The user gets a sentence; the log gets the reason."""
    with caplog.at_level(logging.WARNING, logger="blindpilot"):
        app._save_config({"setup_complete": True})

    assert any("settings" in record.getMessage().lower() for record in caplog.records), [
        record.getMessage() for record in caplog.records
    ]


# ----- the wizard loop -----
def test_finishing_the_wizard_when_it_cannot_be_saved_is_reported(unwritable, monkeypatch):
    """Otherwise the only symptom is the whole wizard again next launch, and
    nothing connects that to a settings file nobody can write."""
    said: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, urgent=False: said.append(text))

    app._record_setup_complete({"setup_complete": True})

    assert said, "a wizard that cannot be remembered said nothing about it"
    spoken = " ".join(said).lower()
    assert "settings" in spoken or "saved" in spoken, said


def test_a_saved_wizard_says_nothing(config_at, tmp_path, monkeypatch):
    said: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, urgent=False: said.append(text))
    config_at(tmp_path / "config.json")

    app._record_setup_complete({"setup_complete": True})

    assert said == []


# ----- a half-written file must not reset everything -----
def test_an_interrupted_write_leaves_the_previous_settings_alone(config_at, tmp_path, monkeypatch):
    """Truncate-then-fill means a crash costs every setting, not just the new
    one: `_load_config` cannot read the remains and starts over from empty."""
    path = config_at(tmp_path / "config.json")
    app._save_config({"setup_complete": True, "backend": "codex"})

    def die(*_args, **_kwargs):
        raise OSError("the disk filled up halfway through")

    monkeypatch.setattr(app.json, "dump", die)
    app._save_config({"setup_complete": True, "backend": "freebuff"})

    assert path.exists(), "the settings file was destroyed by a failed write"
    assert app._load_config() == {"setup_complete": True, "backend": "codex"}


# ----- the switches, the dialog and the history list, over a failed write -----
class _Item:
    """A checkable menu item, as `_toggle_setting` reads it."""

    def __init__(self, checked: bool) -> None:
        self._checked = checked

    def IsChecked(self) -> bool:
        return self._checked


class _Frame:
    """Just what `_toggle_setting` says on."""

    def __init__(self) -> None:
        self.announced: list[str] = []

    def _announce_setting(self, text: str) -> None:
        self.announced.append(text)


def test_a_menu_toggle_that_cannot_be_saved_says_so(unwritable, monkeypatch):
    """The checkmark moves and the setting stays on in memory, but the
    sentence claiming it was written is not said over a file that refused it:
    "Live activity in the list on" would be untrue."""
    frame = _Frame()
    monkeypatch.setattr(app.SETTINGS, "live_rows", False)

    app.MainFrame._toggle_setting(
        frame,
        _Item(True),
        "live_rows",
        "Live activity in the list on",
        "Live activity in the list off",
    )

    assert app.SETTINGS.live_rows is True
    assert frame.announced == ["Error: could not save settings"]


def test_a_menu_toggle_that_saves_says_what_it_did(config_at, tmp_path, monkeypatch):
    """The other half of the guard: a write that works still claims it."""
    config_at(tmp_path / "config.json")
    frame = _Frame()
    monkeypatch.setattr(app.SETTINGS, "live_rows", False)

    app.MainFrame._toggle_setting(
        frame,
        _Item(True),
        "live_rows",
        "Live activity in the list on",
        "Live activity in the list off",
    )

    assert frame.announced == ["Live activity in the list on"]


class _ListBox:
    def __init__(self) -> None:
        self.focused = False

    def SetFocus(self) -> None:
        self.focused = True

    def GetSelection(self) -> int:
        return 0


class _Entry:
    """`wx.TextEntryDialog` as `_rename` uses it: a context manager."""

    def __init__(self, value: str) -> None:
        self._value = value

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def ShowModal(self) -> int:
        return app.wx.ID_OK

    def GetValue(self) -> str:
        return self._value


def _history_dialog():
    """The three methods `_rename` and `_toggle_hidden` call on the dialog."""
    from session_history import HistoryEntry

    entry = HistoryEntry(
        backend="claude", session_id="s-1", title="Original", path="t.jsonl", modified=0.0
    )
    stub = type("HistoryStub", (), {})()
    stub.entry = entry
    stub._name = "Original"
    stub._current = lambda: entry
    stub._title = lambda _entry: stub._name
    stub.hidden: set = set()
    stub._hidden = lambda: stub.hidden
    stub.refreshed = False

    def _refresh(_entry=None):
        stub.refreshed = True
        stub._name = "Renamed"

    stub._refresh = _refresh
    stub.list_box = _ListBox()
    return stub


def test_renaming_a_conversation_that_cannot_be_saved_says_so(unwritable, monkeypatch):
    """The old name stays on the list: refreshing would re-read the file the
    rename never reached, and the success sentence would claim a name the
    file does not hold."""
    monkeypatch.setattr(app, "_load_config", dict)
    monkeypatch.setattr(app.wx, "TextEntryDialog", lambda *a, **k: _Entry("Renamed"))
    said: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, urgent=False: said.append(text))
    dialog = _history_dialog()

    app.HistoryDialog._rename(dialog)

    assert said == ["Error: could not save settings"]
    assert not dialog.refreshed, "the list was refreshed as if the name was saved"
    assert dialog.list_box.focused


def test_renaming_a_conversation_that_saves_announces_the_new_name(
    config_at, tmp_path, monkeypatch
):
    config_at(tmp_path / "config.json")
    monkeypatch.setattr(app, "_load_config", dict)
    monkeypatch.setattr(app.wx, "TextEntryDialog", lambda *a, **k: _Entry("Renamed"))
    said: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, urgent=False: said.append(text))
    dialog = _history_dialog()

    app.HistoryDialog._rename(dialog)

    assert dialog.refreshed
    assert said == ["Renamed to Renamed"]


def test_hiding_a_conversation_that_cannot_be_saved_says_so(unwritable, monkeypatch):
    """The hidden set the list reads goes back the way it was, so the
    conversation is not hidden by a save that never happened."""
    monkeypatch.setattr(app, "_load_config", dict)
    said: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, urgent=False: said.append(text))
    dialog = _history_dialog()

    app.HistoryDialog._toggle_hidden(dialog)

    assert dialog.hidden == set()
    assert said == ["Error: could not save settings"]
    assert not dialog.refreshed


@pytest.fixture
def restored_settings():
    """Put the process-wide SETTINGS singleton back after each test here."""
    before = dict(app.SETTINGS.__dict__)
    yield
    app.SETTINGS.__dict__.clear()
    app.SETTINGS.__dict__.update(before)


def test_applying_preferences_that_cannot_be_saved_says_so(
    unwritable, monkeypatch, restored_settings, wx_app
):
    """ "Preferences applied" would be a claim about two writes, neither of
    which reached the file."""
    from test_preferences_dialog import _FrameStub

    frame = _FrameStub()

    class _Dialog:
        def narration_selection(self):
            return app.SETTINGS.narration

        live_rows = False
        speak_live = False
        show_thinking = True
        sounds_enabled = False
        sound_cues = {cue: True for cue, _label, _help in app.SOUND_CUES}
        text_view = True
        ask_written_questions = False
        notify_in_background = False
        read_back_sent = False
        appearance = app.APPEARANCE_SYSTEM
        progress_cue = app.CUE_OFF
        progress_interval = 30
        check_updates_startup = False

    app.MainFrame._apply_preferences(frame, _Dialog())

    assert frame.announced == ["Error: could not save settings"]


# ----- the shared guard over a bare save -----
def test_the_helper_announces_a_write_the_file_refused(unwritable, monkeypatch):
    said: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, urgent=False: said.append(text))

    assert app._save_or_announce({"setup_complete": True}) is False
    assert said == ["Error: could not save settings"]


def test_the_helper_stays_quiet_when_the_write_lands(config_at, tmp_path, monkeypatch):
    config_at(tmp_path / "config.json")
    said: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, urgent=False: said.append(text))

    assert app._save_or_announce({"backend": "codex"}) is True
    assert said == []


# ----- the history sort -----
def test_changing_the_sort_that_cannot_be_saved_says_so(unwritable, monkeypatch):
    """The picker's order still applies to the open dialog; only the file
    refuses it, and the conversation count that follows must not be the
    only thing the user hears."""
    monkeypatch.setattr(app, "_load_config", dict)
    said: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, urgent=False: said.append(text))
    refreshed: list[bool] = []

    class _Stub:
        sort_picker = type("Picker", (), {"GetSelection": lambda self: 0})()
        _shown = ["entry"]

        def _current(self):
            return "entry"

        def _refresh(self, keep):
            refreshed.append(keep is not None)

    app.HistoryDialog._on_sort(_Stub())

    assert said == ["Error: could not save settings"]
    assert refreshed == [True], "the open dialog still sorts; only the save failed"


# ----- switching between Agent and Chat -----
class _NoopWindow:
    def Show(self, _show: bool) -> None:
        pass

    def SetSelection(self, _index: int) -> None:
        pass

    def Layout(self) -> None:
        pass


def _mode_frame():
    """The surface `_set_app_mode` touches on the way to the save, in Agent
    mode, where there is no chat panel to build."""
    stub = type(
        "ModeFrameStub",
        (),
        {
            "_announce_setting": lambda self, text: self.announced.append(text),
            "_refresh_compact_item": lambda self: None,
            "_refresh_connect_item": lambda self: None,
            "_refresh_hermes_sessions_item": lambda self: None,
            "_refresh_museai_menu": lambda self: None,
            "focus_for_mode": lambda self: None,
        },
    )()
    stub.announced = []
    stub._app_mode = app.APP_MODE_AGENT
    stub.chat_panel = None
    stub.tab_switcher = _NoopWindow()
    stub.notebook = _NoopWindow()
    stub.mode_combo = _NoopWindow()
    stub._root = _NoopWindow()
    stub._chat_menu_items = []
    stub._agent_menu_items = []
    return stub


def test_switching_mode_when_the_file_refuses_says_so(unwritable, monkeypatch):
    monkeypatch.setattr(app, "_load_config", dict)
    frame = _mode_frame()

    app.MainFrame._set_app_mode(frame, app.APP_MODE_AGENT)

    assert frame.announced == ["Error: could not save settings"]


def test_a_startup_mode_restore_stays_quiet_over_a_refused_file(unwritable, monkeypatch):
    """The restore claims nothing, so there is nothing to correct - and it
    runs before the window is on screen, where an error would talk over
    whoever is starting the application."""
    monkeypatch.setattr(app, "_load_config", dict)
    frame = _mode_frame()

    app.MainFrame._set_app_mode(frame, app.APP_MODE_AGENT, announce_change=False)

    assert frame.announced == []


# ----- the permission mode picker -----
def test_changing_the_mode_says_what_it_does_then_the_refusal(unwritable, monkeypatch):
    """Both lines are needed: the description is what the new mode does, the
    refusal is that it will not survive a restart. The refusal goes last so
    it is not followed by anything that sounds like success."""
    monkeypatch.setattr(app, "_load_config", dict)
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app,
        "announce",
        lambda text, urgent=False: events.append(("said", text)),
    )
    panel = type(
        "PanelStub",
        (),
        {
            "mode_picker": type("Picker", (), {"SetSelection": lambda self, index: None})(),
            "_announce": lambda self, text: events.append(("described", text)),
        },
    )()

    app.SessionPanel._set_mode(panel, "plan")

    assert events == [
        ("described", app._MODE_DESCRIPTIONS["plan"]),
        ("said", "Error: could not save settings"),
    ]


# ----- finishing the setup flow with a folder that cannot be remembered -----
def test_a_projects_folder_that_cannot_be_saved_is_reported_after_the_backend(
    unwritable, monkeypatch
):
    """Both announcements queue, so the failure has to be the last one said:
    ending on "Backend changed to Codex" would leave the user believing the
    whole setup was stored."""
    monkeypatch.setattr(app, "_load_config", dict)

    class _Wizard:
        def __init__(self, *_args, **_kwargs):
            self.backend = "codex"
            self.projects_folder = "C:/projects"

        def ShowModal(self):
            return app.wx.ID_OK

        def Destroy(self):
            pass

    monkeypatch.setattr(app, "SetupWizard", _Wizard)
    events: list[str] = []
    frame = type(
        "ManageStub",
        (),
        {
            "_backend": "claude",
            "_projects_folder": "",
            "_set_backend": lambda self, backend: events.append(f"backend {backend}"),
            "_announce_setting": lambda self, text: events.append(text),
        },
    )()

    app.MainFrame._manage_backends(frame)

    assert events == ["backend codex", "Error: could not save settings"]
    assert frame._projects_folder == "C:/projects", "the session keeps the chosen folder"


# ----- the remote Hermes dialog -----
def _remote_fields():
    """The dialog fields `RemoteHermesDialog.apply` reads."""

    def field(value):
        return type("Field", (), {"GetValue": lambda self: value})()

    return type(
        "ApplyStub",
        (),
        {
            "_credential_name": lambda self: "password",
            "_enabled": field(True),
            "_host": field(" host.example "),
            "_port": field("1234"),
            "_secure": field(True),
            "_user": field("me"),
            "_key": field("secret"),
        },
    )()


def test_applying_the_remote_address_carries_the_save_answer(monkeypatch):
    """`apply` is how the dialog handler learns the file refused the
    address; dropping the return value makes it describe a connection that
    was never saved."""
    monkeypatch.setattr(
        app, "REMOTE_HERMES", type("RemoteStub", (), {"save": lambda self: False})()
    )

    assert app.RemoteHermesDialog.apply(_remote_fields()) is False

    monkeypatch.setattr(app, "REMOTE_HERMES", type("RemoteStub", (), {"save": lambda self: True})())

    assert app.RemoteHermesDialog.apply(_remote_fields()) is True


def test_a_remote_address_that_cannot_be_saved_says_so_not_the_connection(unwritable, monkeypatch):
    monkeypatch.setattr(app, "_load_config", dict)
    monkeypatch.setattr(app, "invalidate_model_options", lambda *_a, **_k: None)

    class _Dialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def ShowModal(self):
            return app.wx.ID_OK

        def apply(self):
            return False

        def Destroy(self):
            pass

    monkeypatch.setattr(app, "RemoteHermesDialog", _Dialog)
    said: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, urgent=False: said.append(text))

    app.MainFrame._configure_remote_hermes(type("FrameStub", (), {})())

    assert said == ["Error: could not save settings"]


def test_a_remote_address_that_saves_describes_the_connection(monkeypatch):
    """The other half of the guard: a write that works still says where
    Hermes will be found."""
    monkeypatch.setattr(app, "invalidate_model_options", lambda *_a, **_k: None)

    class _Dialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def ShowModal(self):
            return app.wx.ID_OK

        def apply(self):
            return True

        def Destroy(self):
            pass

    monkeypatch.setattr(app, "RemoteHermesDialog", _Dialog)
    said: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, urgent=False: said.append(text))

    app.MainFrame._configure_remote_hermes(type("FrameStub", (), {})())

    assert len(said) == 1, said
    assert said[0].startswith("Remote Hermes: "), said


@pytest.fixture
def restored_remote():
    """Put the process-wide REMOTE_HERMES singleton back after each test here."""
    before = dict(app.REMOTE_HERMES.__dict__)
    yield
    app.REMOTE_HERMES.__dict__.clear()
    app.REMOTE_HERMES.__dict__.update(before)


def test_a_key_write_that_fails_is_spoken_once_like_any_other_save_failure(
    config_at, tmp_path, monkeypatch, restored_remote
):
    """The key is half of what the dialog saved.

    A settings file that lands while the key refuses its write is still a
    failed save: `apply` must not report success, and the handler must speak
    the one failure sentence -- once, whether one file refused or both.
    """
    real_apply = app.RemoteHermesDialog.apply
    key_blocker = tmp_path / "key-in-the-way"
    key_blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(
        app._RemoteHermes,
        "_key_path",
        staticmethod(lambda: key_blocker / "remote-hermes-key"),
    )
    monkeypatch.setattr(app, "invalidate_model_options", lambda *_a, **_k: None)
    said: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, urgent=False: said.append(text))

    class _Dialog(type(_remote_fields())):
        def __init__(self, *_args, **_kwargs):
            pass

        def ShowModal(self):
            return app.wx.ID_OK

        def Destroy(self):
            pass

        def apply(self):
            return real_apply(self)

    monkeypatch.setattr(app, "RemoteHermesDialog", _Dialog)

    # The settings write lands; only the key file refuses. The refusal is
    # still the sentence the caller speaks, not a success that drops the key.
    config_at(tmp_path / "config.json")
    app.MainFrame._configure_remote_hermes(type("FrameStub", (), {})())

    assert (tmp_path / "config.json").exists()
    assert said == ["Error: could not save settings"]

    # Both files refuse now: one sentence, not two.
    said.clear()
    config_at(key_blocker / "config.json")
    app.MainFrame._configure_remote_hermes(type("FrameStub", (), {})())

    assert said == ["Error: could not save settings"]


# ----- a menu choice under the SETTINGS store -----
def test_a_narration_pick_that_cannot_be_saved_says_so(unwritable, restored_settings):
    """ "Narration: ..." would claim a file that refused the change; the mode
    stays applied for this session, and the failure is what is spoken."""
    frame = _Frame()
    key, _label, _help = app.NARRATION_MODES[0]

    app.MainFrame._set_narration(frame, key)

    assert app.SETTINGS.narration == key
    assert frame.announced == ["Error: could not save settings"]


def test_a_narration_pick_that_saves_claims_it(config_at, tmp_path, restored_settings):
    config_at(tmp_path / "config.json")
    frame = _Frame()
    key, label, _help = app.NARRATION_MODES[0]

    app.MainFrame._set_narration(frame, key)

    assert frame.announced == [f"Narration: {label.replace('&', '')}"]
