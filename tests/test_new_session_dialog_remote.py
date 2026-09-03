"""The New Session dialog, driven through a real ``wx`` dialog object.

Not a mock: the tab order, which control has focus, and whether Browse exists
at all are the substance of the change, and none of them survive being
stubbed. wxPython is skipped where it is absent, exactly as the other GUI
suites here do.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

wx = pytest.importorskip("wx")

import blindpilot_app  # noqa: E402
from blindpilot_app import NewSessionDialog, _tab_label  # noqa: E402

REMOTE = "ws://garfield:9119/api/ws"


@pytest.fixture
def frame():
    app = wx.App()
    top = wx.Frame(None)
    yield top
    top.Destroy()
    app.Destroy()


@pytest.fixture(autouse=True)
def never_open_a_modal_box(monkeypatch):
    """Record refusals instead of showing them, for EVERY test in this file.

    Not tidiness — measured. With the remote branch reverted (the defect this
    file guards against), ``_on_ok`` refused an empty folder and ``_reject``
    opened a modal ``wx.MessageDialog``. Nothing was there to dismiss it, so
    the test HUNG until pytest-timeout killed the run: the suite reported a
    timeout with no failing test rather than naming the broken behaviour.

    A test that cannot fail in bounded time is not a guard, so the modal is
    replaced globally here and the refusals are collected in
    :func:`refusals` for the tests that assert on them.
    """
    collected: list[str] = []
    monkeypatch.setattr(
        NewSessionDialog,
        "_reject",
        lambda _self, message: collected.append(message),
    )
    _REFUSALS.clear()
    _REFUSALS.append(collected)
    yield collected


# Shared with the ``refusals`` fixture below; a list of one so the autouse
# fixture can hand its collector over without depending on ordering.
_REFUSALS: list[list[str]] = []


@pytest.fixture
def refusals(never_open_a_modal_box) -> list[str]:
    """What the dialog refused, in order."""
    return never_open_a_modal_box


def _open(frame, **kwargs) -> NewSessionDialog:
    return NewSessionDialog(frame, **kwargs)


def _labels(dlg: NewSessionDialog) -> list[str]:
    return [child.GetLabel() for child in dlg.GetChildren() if isinstance(child, wx.StaticText)]


def _buttons(dlg: NewSessionDialog) -> list[str]:
    return [child.GetLabel() for child in dlg.GetChildren() if isinstance(child, wx.Button)]


# --------------------------------------------------------------------------
# Remote mode: a name is the question
# --------------------------------------------------------------------------


def test_remote_mode_focuses_the_name_not_the_folder(frame) -> None:
    """The field that can be answered from this machine gets the focus.

    A remote session cannot be described by a folder here, so landing in the
    folder box means the first thing a screen reader user is asked for is the
    one thing they cannot usefully supply.

    Asserted on ``initial_focus()`` rather than ``FindFocus()``: the latter
    reports the focus of a window the platform has SHOWN, and on macOS it
    answered None for a dialog that was only constructed -- the same assertion
    passed on Windows and Linux and failed on macOS for a reason that had
    nothing to do with this code.
    """
    dlg = _open(frame, remote_label=REMOTE)
    try:
        assert dlg.initial_focus() is dlg.name_box
    finally:
        dlg.Destroy()


def test_remote_mode_offers_no_browse_button(frame) -> None:
    """Browse would open a picker on the WRONG computer.

    Leaving it enabled is not merely useless: every visit to this dialog costs
    a screen reader user an extra stop in the tab order for a control that
    cannot do what its label promises.
    """
    dlg = _open(frame, remote_label=REMOTE)
    try:
        assert not any("Browse" in label for label in _buttons(dlg))
    finally:
        dlg.Destroy()


def test_local_mode_still_offers_browse(frame) -> None:
    """Negative control: removing Browse everywhere would be a regression, not
    a fix. Locally the folder is the right question and a picker can answer it."""
    dlg = _open(frame)
    try:
        assert any("Browse" in label for label in _buttons(dlg))
        assert dlg.initial_focus() is dlg.folder_box
    finally:
        dlg.Destroy()


def test_remote_mode_says_which_machine_the_session_will_run_on(frame) -> None:
    dlg = _open(frame, remote_label=REMOTE)
    try:
        assert any(REMOTE in label for label in _labels(dlg))
    finally:
        dlg.Destroy()


def test_remote_mode_accepts_an_empty_folder(frame, refusals) -> None:
    """The ordinary remote case: no name, no folder, straight to OK.

    Locally an empty folder is refused, so this is the behaviour that had no
    way to happen before — and it is the one the request was about.
    """
    dlg = _open(frame, remote_label=REMOTE)
    try:
        event = wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK)
        dlg._on_ok(event)
        assert refusals == [], f"OK was refused in remote mode: {refusals}"
        assert dlg.path == ""
        assert dlg.title_text == ""
        assert event.GetSkipped(), "OK was refused, so the dialog would not close"
    finally:
        dlg.Destroy()


def test_a_remote_folder_travels_exactly_as_typed(frame) -> None:
    """No expanduser, no abspath, no expandvars.

    Every one of those resolves against THIS machine: ``~`` is a different home,
    ``%USERPROFILE%`` may not exist on the server, and abspath would prefix a
    Linux path with a Windows drive. The remote end is the only thing that can
    interpret it.
    """
    dlg = _open(frame, remote_label=REMOTE)
    try:
        dlg.folder_box.SetValue("~/projects/radio")
        dlg._on_ok(wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK))
        assert dlg.path == "~/projects/radio"
    finally:
        dlg.Destroy()


def test_a_remote_folder_that_does_not_exist_here_is_not_refused(frame, refusals) -> None:
    """The defect in one line: this path is perfectly valid on the server."""
    dlg = _open(frame, remote_label=REMOTE)
    try:
        dlg.folder_box.SetValue("/srv/app")
        event = wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK)
        dlg._on_ok(event)
        assert refusals == [], f"a valid server path was refused here: {refusals}"
        assert dlg.path == "/srv/app"
        assert event.GetSkipped()
    finally:
        dlg.Destroy()


def test_a_name_is_carried_out_of_the_dialog(frame) -> None:
    dlg = _open(frame, remote_label=REMOTE)
    try:
        dlg.name_box.SetValue("  Radio pipeline  ")
        dlg._on_ok(wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK))
        assert dlg.title_text == "Radio pipeline"
    finally:
        dlg.Destroy()


# --------------------------------------------------------------------------
# Local mode keeps its contract
# --------------------------------------------------------------------------


def test_local_mode_still_refuses_a_folder_that_does_not_exist(frame, refusals) -> None:
    """The existing guard must survive: locally the path IS checkable, and a
    session opened on a missing directory fails after the fact instead of in
    the dialog."""
    dlg = _open(frame)
    try:
        dlg.folder_box.SetValue(os.path.join(os.sep, "definitely", "not", "here"))
        event = wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK)
        dlg._on_ok(event)
        assert refusals, "a missing local folder was accepted"
        assert not event.GetSkipped()
    finally:
        dlg.Destroy()


def test_local_mode_accepts_a_name_too(frame, tmp_path) -> None:
    """Naming is not remote-only. Nothing about a local session makes the
    automatic title better, so the field is offered in both shapes."""
    dlg = _open(frame)
    try:
        dlg.folder_box.SetValue(str(tmp_path))
        dlg.name_box.SetValue("Nightly build")
        dlg._on_ok(wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK))
        assert dlg.title_text == "Nightly build"
        assert dlg.path == str(tmp_path)
    finally:
        dlg.Destroy()


def test_local_mode_still_requires_a_folder(frame, refusals) -> None:
    dlg = _open(frame)
    try:
        event = wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK)
        dlg._on_ok(event)
        assert refusals
        assert not event.GetSkipped()
    finally:
        dlg.Destroy()


# --------------------------------------------------------------------------
# The tab has to be tellable from its neighbour
# --------------------------------------------------------------------------


def test_a_tab_with_neither_name_nor_folder_is_still_labelled() -> None:
    """A remote session can have both parts empty, and an empty tab label is
    the one label a screen reader cannot distinguish from the next one."""
    assert _tab_label("", "") == "New session"


def test_a_name_wins_over_the_folder_on_the_tab() -> None:
    assert _tab_label("Radio pipeline", "/srv/app") == "Radio pipeline"


def test_the_folder_is_still_used_when_there_is_no_name() -> None:
    """Negative control for the placeholder: it must not swallow the folder."""
    assert _tab_label("", "/srv/app") == "app"


def test_the_status_report_explains_a_missing_folder(frame, tmp_path) -> None:
    """ "Folder: " with nothing after it reads as a value that failed to load.

    Driven through the real panel method rather than asserting on a string
    literal, so a change to the report's shape cannot leave this passing
    while the line itself goes back to being empty. Earcons are pointed at an
    empty directory and disabled, so the test makes no sound.
    """
    panel = blindpilot_app.SessionPanel(
        frame,
        "",
        on_status=lambda *_a: None,
        on_title=lambda *_a: None,
        earcons=blindpilot_app.Earcons(str(tmp_path), enabled=False),
        on_side_chat=lambda *_a: None,
        get_backend=lambda: blindpilot_app.BACKEND_HERMES,
        focus_before=lambda: None,
        focus_after=lambda: None,
    )
    try:
        folder_lines = [
            line for line in panel._session_status_lines() if line.startswith("Folder:")
        ]
        assert folder_lines, "the status report no longer mentions the folder"
        assert folder_lines[0].strip() != "Folder:"
    finally:
        panel.Destroy()
