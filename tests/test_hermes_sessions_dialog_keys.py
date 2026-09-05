"""Enter and F5 in the Hermes conversations dialog.

The dialog binds `EVT_CHAR_HOOK`, which sees Enter before the focused button
does, and treated every Enter as "open the selected conversation". On Cancel
that opened a conversation, possibly attaching to a live turn, instead of
leaving. `HistoryDialog._on_key` had already fixed exactly this; the newer
dialog repeated it. The tests mirror tests/test_history_dialog_keys.py.
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
    monkeypatch.setattr(app.wx, "Button", _Button)
    said: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, urgent=False: said.append(text))

    stub = type("DialogStub", (), {})()
    stub.said = said
    stub._shown = ["a conversation"]
    stub.accepted = 0
    stub.ended: list[int] = []
    stub.focus = None
    stub.reload_result = True
    stub._accept = lambda: setattr(stub, "accepted", stub.accepted + 1)
    stub._reload = lambda: stub.reload_result
    stub.EndModal = stub.ended.append
    stub.FindFocus = lambda: stub.focus
    stub.filter_box = type("Box", (), {"HasFocus": lambda self: False})()
    stub.list_box = type(
        "List", (), {"SetFocus": lambda self: None, "SetSelection": lambda self, i: None}
    )()
    return stub


def _press(dialog, key):
    event = _Event(key)
    app.HermesSessionsDialog._on_key(dialog, event)
    return event


def test_enter_on_a_button_does_not_open_a_conversation(dialog):
    dialog.focus = _Button()

    event = _press(dialog, app.wx.WXK_RETURN)

    assert dialog.accepted == 0, "Enter on a button opened a conversation"
    assert event.skipped, "the button never got the key it was focused for"


def test_enter_in_the_list_still_opens_the_conversation(dialog):
    dialog.focus = object()

    _press(dialog, app.wx.WXK_RETURN)

    assert dialog.accepted == 1


def test_enter_in_the_filter_box_still_opens_the_conversation(dialog):
    dialog.focus = object()

    _press(dialog, app.wx.WXK_NUMPAD_ENTER)

    assert dialog.accepted == 1


def test_escape_still_cancels_from_anywhere(dialog):
    dialog.focus = _Button()

    _press(dialog, app.wx.WXK_ESCAPE)

    assert dialog.ended == [app.wx.ID_CANCEL]


def test_f5_says_refreshed_only_when_the_reload_worked(dialog):
    """`_reload` speaks the error itself; "Refreshed" on top of it is a lie."""
    _press(dialog, app.wx.WXK_F5)
    assert dialog.said == ["Refreshed"]

    dialog.said.clear()
    dialog.reload_result = False
    _press(dialog, app.wx.WXK_F5)
    assert dialog.said == []
