"""Enter, Escape and F5 in the two dialogs that list past conversations.

Both bind `EVT_CHAR_HOOK`, which fires before the focused control sees the
key, and both unconditionally treated Enter as "open the selected
conversation". That is right in the filter box and right in the list. It is
wrong on a button, and each dialog has two: Open (or Connect) and Cancel.

So tabbing to Cancel and pressing Enter -- the ordinary way to leave a dialog
without doing anything, and the only way for somebody who cannot see that
focus has moved -- opened a conversation instead, possibly attaching to a live
turn. The dialog closed either way, which is what made it hard to notice.

`HistoryDialog._on_key` was fixed first and `HermesSessionsDialog` repeated the
defect, so both dialogs are driven through the same tests here rather than
through two files that had to be kept in step by hand.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app
from doubles import Button, KeyEvent


def _stub(monkeypatch):
    """The state `_on_key` reads, on a dialog neither of them has to build.

    The handler asks whether the focused window is a button; the stand-in has
    to answer that question the same way.
    """
    monkeypatch.setattr(app.wx, "Button", Button)

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


def _history_stub(monkeypatch):
    stub = _stub(monkeypatch)
    stub.dialog = app.HistoryDialog
    return stub


def _hermes_stub(monkeypatch):
    """The Hermes dialog also reloads its list, and says so out loud."""
    stub = _stub(monkeypatch)
    stub.dialog = app.HermesSessionsDialog
    stub.said: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, urgent=False: stub.said.append(text))
    stub.reload_result = True
    stub._reload = lambda: stub.reload_result
    return stub


@pytest.fixture(params=[_history_stub, _hermes_stub], ids=["history", "hermes"])
def dialog(request, monkeypatch):
    return request.param(monkeypatch)


@pytest.fixture
def hermes(monkeypatch):
    return _hermes_stub(monkeypatch)


def _press(dialog, key):
    event = KeyEvent(key)
    dialog.dialog._on_key(dialog, event)
    return event


def test_enter_on_a_button_does_not_open_a_conversation(dialog):
    """The bug: Cancel opened the thing it was there to not open."""
    dialog.focus = Button()

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
    dialog.focus = Button()

    _press(dialog, app.wx.WXK_ESCAPE)

    assert dialog.ended == [app.wx.ID_CANCEL]


def test_f5_says_refreshed_only_when_the_reload_worked(hermes):
    """`_reload` speaks the error itself; "Refreshed" on top of it is a lie.

    Only the Hermes dialog reloads: the past-conversations list is read from
    disk when it opens and has nothing to go back to.
    """
    _press(hermes, app.wx.WXK_F5)
    assert hermes.said == ["Refreshed"]

    hermes.said.clear()
    hermes.reload_result = False
    _press(hermes, app.wx.WXK_F5)
    assert hermes.said == []
