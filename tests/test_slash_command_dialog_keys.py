"""Enter, Escape, double click and construction, in the slash-command picker.

SlashCommandDialog replaced a stock wx.SingleChoiceDialog in
_pick_slash_command. HistoryDialog and HermesSessionsDialog each got a
dedicated test_*_dialog_keys.py file when the same EVT_CHAR_HOOK Enter and
Escape handling was added to them; this one had none.

The key-handling tests below stub the dialog the way test_history_dialog_keys.py
does, so they run without a display and call the real bound _on_key and
_accept methods on the stub. The remaining tests build a real dialog, the way
test_hermes_sessions_ui.py does with its frame fixture, because selecting the
first row, setting the title, and showing the message all happen in __init__,
and double click has to be proven through the real EVT_LISTBOX_DCLICK
binding, not reimplemented.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

wx = pytest.importorskip("wx")

import blindpilot_app as app  # noqa: E402

LABELS = ["/help. Show help", "/clear. Clear the conversation", "/model. Switch model"]
MESSAGE = "Choose a slash command. It will be placed in the prompt ready to send."


# -- Enter and Escape, stubbed, no display needed --------------------------


class _Button:
    """Stands in for `wx.Button`, which the handler has to recognise."""


class _Event:
    def __init__(self, key):
        self._key = key
        self.skipped = False

    def GetKeyCode(self):
        return self._key

    def Skip(self):
        self.skipped = True


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.setattr(app.wx, "Button", _Button)
    dialog = type("DialogStub", (), {})()
    dialog.ended: list[int] = []
    dialog.EndModal = dialog.ended.append
    dialog.focus = None
    dialog.FindFocus = lambda: dialog.focus
    # Exercise the real _accept rather than reimplementing what it does.
    dialog._accept = lambda: app.SlashCommandDialog._accept(dialog)
    return dialog


def _press(dialog, key):
    event = _Event(key)
    app.SlashCommandDialog._on_key(dialog, event)
    return event


def test_enter_accepts_the_selection(stub):
    stub.focus = object()

    _press(stub, app.wx.WXK_RETURN)

    assert stub.ended == [app.wx.ID_OK]


def test_enter_from_the_numpad_also_accepts(stub):
    stub.focus = object()

    _press(stub, app.wx.WXK_NUMPAD_ENTER)

    assert stub.ended == [app.wx.ID_OK]


def test_enter_on_a_button_does_not_accept(stub):
    """Tabbing to Cancel and pressing Enter must cancel, not choose a command."""
    stub.focus = _Button()

    event = _press(stub, app.wx.WXK_RETURN)

    assert stub.ended == []
    assert event.skipped, "the button never got the key it was focused for"


def test_escape_cancels_from_anywhere(stub):
    stub.focus = _Button()

    _press(stub, app.wx.WXK_ESCAPE)

    assert stub.ended == [app.wx.ID_CANCEL]


def test_an_unrelated_key_is_left_for_the_focused_control(stub):
    stub.focus = object()

    event = _press(stub, app.wx.WXK_TAB)

    assert stub.ended == []
    assert event.skipped


# -- title, message, initial selection and double click, for real ---------


@pytest.fixture(scope="module")
def wx_app():
    try:
        application = wx.App(False)
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"no display for wxPython: {exc}")
    yield application


@pytest.fixture
def frame(wx_app):
    window = wx.Frame(None)
    try:
        yield window
    finally:
        window.Destroy()


@pytest.fixture
def dialog(frame):
    dlg = app.SlashCommandDialog(frame, MESSAGE, LABELS)
    try:
        yield dlg
    finally:
        dlg.Destroy()


def _static_texts(dlg):
    return [child.GetLabel() for child in dlg.GetChildren() if isinstance(child, wx.StaticText)]


def test_the_title_is_slash_commands(dialog):
    assert dialog.GetTitle() == "Slash Commands"


def test_the_message_is_shown_above_the_list(dialog):
    assert MESSAGE in _static_texts(dialog)


def test_the_first_row_is_selected_on_open(dialog):
    assert dialog.GetSelection() == 0


def test_no_labels_means_no_selection(frame):
    empty = app.SlashCommandDialog(frame, MESSAGE, [])
    try:
        assert empty.GetSelection() == wx.NOT_FOUND
    finally:
        empty.Destroy()


def test_get_selection_reports_the_lists_selection(dialog):
    dialog.list_box.SetSelection(2)

    assert dialog.GetSelection() == 2


def test_double_click_on_the_list_ends_the_modal_with_ok(dialog):
    # A dialog built without ShowModal cannot EndModal; the repo's other
    # dialog tests capture the call the same way.
    ended: list[int] = []
    dialog.EndModal = ended.append
    dialog.list_box.SetSelection(1)

    event = wx.CommandEvent(wx.EVT_LISTBOX_DCLICK.typeId, dialog.list_box.GetId())
    dialog.list_box.GetEventHandler().ProcessEvent(event)

    assert ended == [wx.ID_OK]
