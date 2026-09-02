"""The way in to a backend's settings file.

BlindPilot does not edit these. It opens them, in whatever the person already
uses to edit files, which is the whole of the feature: the problem was never
that text editing is hard, it was that these are dotfiles in directories
nothing announces and there was no way to reach one without leaving the
application and knowing where to look.

Claude Code's is 322 lines of nested JSON and Codex's is 226 lines of TOML.
A text box holding either, navigated by ear and counted brace by brace, would
be worse than the editor somebody already has — and a stray comma written back
breaks the CLI silently until it next refuses to start.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import blindpilot_app as app
from agent_backends import SettingsFile


def _entry(tmp_path, name="settings.json", scope="global", exists=True):
    path = tmp_path / name
    if exists:
        path.write_text("{}", encoding="utf-8")
    return SettingsFile("claude", scope, path, "Every project on this machine.")


@pytest.fixture
def opened(monkeypatch):
    """What BlindPilot handed to the platform."""
    calls: list[Path] = []
    monkeypatch.setattr(app, "_open_path", lambda path: calls.append(Path(path)) or True)
    return calls


# ----- reaching one -----
def test_a_file_that_is_there_is_opened(tmp_path, opened):
    entry = _entry(tmp_path)

    said = app._reach_settings_file(entry)

    assert opened == [entry.path]
    assert "settings.json" in said


def test_a_file_that_is_not_there_is_not_invented(tmp_path, opened):
    """These belong to the CLIs, which write their own. A file created at a
    path BlindPilot chose would do nothing while looking like it did."""
    entry = _entry(tmp_path, name="absent.json", exists=False)

    said = app._reach_settings_file(entry)

    assert not entry.path.exists(), "BlindPilot created a settings file"
    assert "does not exist" in said.lower()


def test_when_the_file_is_missing_the_folder_is_offered_instead(tmp_path, opened):
    entry = _entry(tmp_path, name="absent.json", exists=False)

    app._reach_settings_file(entry)

    assert opened == [tmp_path], "the folder it belongs in was not offered"


def test_a_missing_file_in_a_missing_folder_says_where_it_would_be(tmp_path, opened):
    entry = SettingsFile("claude", "this folder", tmp_path / "nope" / "settings.json", "n/a")

    said = app._reach_settings_file(entry)

    assert opened == []
    assert str(entry.path) in said


def test_an_opener_that_fails_says_so_rather_than_claiming_success(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_open_path", lambda _path: False)
    entry = _entry(tmp_path)

    said = app._reach_settings_file(entry)

    assert "could not" in said.lower()
    assert str(entry.path) in said, "with no way in, the path is the only thing left to give"


# ----- how each one is described -----
def test_the_label_says_whose_it_is_which_one_and_whether_it_is_there(tmp_path):
    entry = _entry(tmp_path, scope="this folder, personal")

    label = app._settings_label(entry)

    assert "Claude Code" in label
    assert "this folder, personal" in label
    assert "Every project on this machine." in label, "the note is never read if it is not here"


def test_a_file_that_is_not_there_says_so_in_its_label(tmp_path):
    label = app._settings_label(_entry(tmp_path, name="absent.json", exists=False))

    assert "not created yet" in label.lower()


# ----- the dialog -----
wx = pytest.importorskip("wx")


@pytest.fixture(scope="module")
def wx_app():
    try:
        return wx.App(False)
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"no display for wxPython: {exc}")


def test_the_dialog_offers_one_row_per_settings_file(wx_app, tmp_path):
    dialog = app.SettingsFilesDialog(None, str(tmp_path))
    try:
        labels = list(dialog.list_box.GetStrings())
        assert len(labels) == len(app.settings_files(str(tmp_path)))
        assert any("Claude Code" in label for label in labels)
        assert any("Codex" in label for label in labels)
    finally:
        dialog.Destroy()


def _reached(monkeypatch):
    """Which entry the dialog decided to reach, whatever came of it.

    Watching the platform opener instead is not enough: on a machine where
    none of the settings files exist, reaching one opens nothing, so the
    opener stays silent whether or not the dialog acted.
    """
    calls = []
    monkeypatch.setattr(app, "_reach_settings_file", lambda entry: calls.append(entry) or "")
    return calls


def test_enter_on_a_button_does_not_open_a_file(wx_app, tmp_path, monkeypatch):
    """The same bug as the past-conversations dialog: CHAR_HOOK fires before
    the focused control sees the key, so Enter on Close must close."""
    reached = _reached(monkeypatch)
    dialog = app.SettingsFilesDialog(None, str(tmp_path))
    try:
        # Focus is answered rather than assumed. A dialog that was never shown
        # does not reliably hold it on every platform, and what is under test
        # is this handler's decision, not wxPython's focus behaviour.
        dialog.FindFocus = lambda: dialog.FindWindowById(wx.ID_CANCEL)
        event = _Key(wx.WXK_RETURN)

        dialog._on_key(event)

        assert reached == [], "Enter on a button opened a settings file"
        assert event.skipped, "the button never got the key it was focused for"
    finally:
        dialog.Destroy()


def test_enter_in_the_list_opens_the_chosen_file(wx_app, tmp_path, monkeypatch):
    """The other half: handing Enter back must not cost the list its Enter."""
    reached = _reached(monkeypatch)
    dialog = app.SettingsFilesDialog(None, str(tmp_path))
    try:
        dialog.FindFocus = lambda: dialog.list_box
        event = _Key(wx.WXK_RETURN)

        dialog._on_key(event)

        assert len(reached) == 1, "Enter in the list opened nothing"
        assert not event.skipped
    finally:
        dialog.Destroy()


def test_escape_closes_it(wx_app, tmp_path):
    dialog = app.SettingsFilesDialog(None, str(tmp_path))
    ended = []
    dialog.EndModal = ended.append
    try:
        dialog._on_key(_Key(wx.WXK_ESCAPE))

        assert ended == [wx.ID_CANCEL]
    finally:
        dialog.Destroy()


class _Key:
    def __init__(self, code):
        self._code = code
        self.skipped = False

    def GetKeyCode(self):
        return self._code

    def Skip(self):
        self.skipped = True
