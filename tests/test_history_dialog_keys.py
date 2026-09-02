"""Enter in the past-conversations dialog.

The dialog binds `EVT_CHAR_HOOK`, which fires before the focused control sees
the key, and unconditionally treated Enter as "open the selected conversation".
That is right in the filter box and right in the list. It is wrong on a button,
and the dialog has two: Open and Cancel.

So tabbing to Cancel and pressing Enter — the ordinary way to leave a dialog
without doing anything, and the only way for somebody who cannot see that focus
has moved — opened a conversation instead. The dialog closed either way, which
is what made it hard to notice.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app


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
def dialog(monkeypatch):
    # The handler asks whether the focused window is a button; the stub's
    # stand-in has to answer that question the same way.
    monkeypatch.setattr(app.wx, "Button", _Button)

    stub = type("DialogStub", (), {})()
    stub._shown = ["a conversation"]
    stub.accepted = 0
    stub.ended: list[int] = []
    stub.focus = None
    stub._accept = lambda: setattr(stub, "accepted", stub.accepted + 1)
    stub.EndModal = stub.ended.append
    stub.FindFocus = lambda: stub.focus
    stub.filter_box = type("Box", (), {"HasFocus": lambda self: False})()
    stub.list_box = type(
        "List", (), {"SetFocus": lambda self: None, "SetSelection": lambda self, i: None}
    )()
    return stub


def _press(dialog, key):
    event = _Event(key)
    app.HistoryDialog._on_key(dialog, event)
    return event


def test_enter_on_a_button_does_not_open_a_conversation(dialog):
    """The bug: Cancel opened the thing it was there to not open."""
    dialog.focus = _Button()

    event = _press(dialog, app.wx.WXK_RETURN)

    assert dialog.accepted == 0, "Enter on a button opened a conversation"
    assert event.skipped, "the button never got the key it was focused for"


def test_enter_in_the_list_still_opens_the_conversation(dialog):
    dialog.focus = object()

    _press(dialog, app.wx.WXK_RETURN)

    assert dialog.accepted == 1


def test_enter_in_the_filter_box_still_opens_the_conversation(dialog):
    """Type a filter, press Enter, get the first match. That is the point."""
    dialog.focus = object()

    _press(dialog, app.wx.WXK_NUMPAD_ENTER)

    assert dialog.accepted == 1


def test_escape_still_cancels_from_anywhere(dialog):
    dialog.focus = _Button()

    _press(dialog, app.wx.WXK_ESCAPE)

    assert dialog.ended == [app.wx.ID_CANCEL]
