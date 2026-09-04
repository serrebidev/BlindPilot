"""What the New Session dialog says, and does not say.

Three announcements a screen reader user can hear from this dialog, judged
against the standard the install wizard was held to after a captured NVDA log:
no sentence may name something that has nothing to do with it, context a dialog
is built around cannot be silent, and nothing may be spoken twice.

1. The help under the name field said "let Hermes name it after your first
   message" in BOTH modes. True for Hermes. False for a local Claude, Codex,
   FreeBuff or opencode session, where the first message names the tab and
   Hermes is not involved in naming anything at all -- the same class of false
   statement as the wizard's "npm could not be installed".

2. In remote mode the dialog is built around an explanation -- the session
   will run on another machine, so folders on this computer do not apply --
   and it was a StaticText, which no screen reader announces when the dialog
   opens. The user landed on the name field having heard none of the context
   that makes the dialog's shape make sense.

3. A refused folder was announced explicitly and then shown in a modal
   dialog, which announces it again -- the same duplicate-speech class as the
   backend-selection announcement removed in an earlier release.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

wx = pytest.importorskip("wx")

import blindpilot_app  # noqa: E402
from blindpilot_app import NewSessionDialog  # noqa: E402

REMOTE = "ws://garfield:9119/api/ws"


@pytest.fixture
def frame():
    app = wx.App()
    top = wx.Frame(None)
    yield top
    top.Destroy()
    app.Destroy()


@pytest.fixture
def spoken(monkeypatch) -> list[str]:
    """What the dialog said on the announcement stream, in order."""
    captured: list[str] = []
    monkeypatch.setattr(
        blindpilot_app,
        "announce",
        lambda text, urgent=False: captured.append(text),
    )
    return captured


def _open(frame, **kwargs) -> NewSessionDialog:
    return NewSessionDialog(frame, **kwargs)


def _labels(dlg: NewSessionDialog) -> list[str]:
    return [child.GetLabel() for child in dlg.GetChildren() if isinstance(child, wx.StaticText)]


def _name_help(dlg: NewSessionDialog) -> str:
    return next(label for label in _labels(dlg) if label.startswith("Leave it empty"))


# --------------------------------------------------------------------------
# The help under the name field names no backend it is not about
# --------------------------------------------------------------------------


def test_the_name_help_names_no_backend_in_local_mode(frame) -> None:
    """A local Codex or Claude session is not named by Hermes, and the help
    must not claim it is."""
    dlg = _open(frame)
    try:
        help_text = _name_help(dlg)
        assert "Hermes" not in help_text, help_text
        assert "first message" in help_text, help_text
    finally:
        dlg.Destroy()


def test_the_name_help_names_no_backend_in_remote_mode(frame) -> None:
    dlg = _open(frame, remote_label=REMOTE)
    try:
        help_text = _name_help(dlg)
        assert "Hermes" not in help_text, help_text
        assert "first message" in help_text, help_text
    finally:
        dlg.Destroy()


# --------------------------------------------------------------------------
# Remote mode says where the session will run; local mode says nothing extra
# --------------------------------------------------------------------------


def test_remote_mode_announces_where_the_session_will_run(frame, spoken) -> None:
    """The only explanation of the remote shape was a StaticText, which no
    screen reader announces on its own -- the user landed on the name field
    with no context at all."""
    dlg = _open(frame, remote_label=REMOTE)
    try:
        assert any("session will run on" in line and REMOTE in line for line in spoken), (
            f"the remote context was never announced: {spoken}"
        )
    finally:
        dlg.Destroy()


def test_local_mode_announces_nothing_extra_on_open(frame, spoken) -> None:
    """Negative control: the fix must not turn the ordinary local dialog into
    a commentary track."""
    dlg = _open(frame)
    try:
        assert spoken == [], f"the local dialog announced something: {spoken}"
    finally:
        dlg.Destroy()


# --------------------------------------------------------------------------
# A refused folder is said once, by the dialog that asks for a correction
# --------------------------------------------------------------------------


def test_a_refused_folder_is_said_once_by_the_dialog(frame, spoken, monkeypatch) -> None:
    """The refusal was announced explicitly and then shown in a modal, which
    announces the same sentence again. The modal is the announcement; the
    explicit one was a duplicate."""
    shown: list[str] = []

    class _FakeMessageDialog:
        def __init__(self, _parent, message, _caption, style=0):
            shown.append(message)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def ShowModal(self):
            return wx.ID_OK

    monkeypatch.setattr(wx, "MessageDialog", _FakeMessageDialog)

    dlg = _open(frame)
    try:
        event = wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK)
        dlg._on_ok(event)
        assert shown == ["Type a folder path, or use the Browse button."]
        assert spoken == [], f"the refusal was announced twice: {spoken}"
    finally:
        dlg.Destroy()
