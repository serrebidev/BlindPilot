"""The wrapping Responses list and what it tells a screen reader."""

from __future__ import annotations

import pytest
import wx

from markdown_rows import Row

import conversation_list as cl


def test_every_row_kind_has_a_style():
    for kind in (
        "header",
        "prose",
        "heading",
        "list",
        "quote",
        "code",
        "you",
        "thinking",
        "tool",
        "result",
        "error",
    ):
        style = cl.style_for(kind)
        assert isinstance(style, cl.RowStyle)


def test_headings_and_the_persons_own_messages_are_bold():
    assert cl.style_for("you").bold
    assert cl.style_for("header").bold
    assert cl.style_for("heading").bold
    assert not cl.style_for("prose").bold


def test_reasoning_is_muted_code_is_mono_tools_are_indented_errors_are_marked():
    assert cl.style_for("thinking").muted
    assert cl.style_for("code").mono
    assert cl.style_for("tool").indented and cl.style_for("result").indented
    assert cl.style_for("error").error


def test_an_unknown_kind_draws_as_prose():
    assert cl.style_for("whatever") == cl.style_for("prose")


def test_strings_become_prose_rows_and_rows_pass_through():
    row = Row(kind="code", label="Code, Python, 2 lines", payload="x=1", response_number=1)
    out = cl.as_rows(["first", row])
    assert out[0].kind == "prose" and out[0].label == "first" and out[0].payload == "first"
    assert out[1] is row


@pytest.fixture
def frame():
    app = wx.App.Get() or wx.App(False)
    frame = wx.Frame(None)
    frame.SetSize(wx.Size(400, 300))
    yield frame
    frame.Destroy()
    app.ProcessPendingEvents()


LONG = "The audit found seventy-seven items across five areas and the first fix is the worker that killed the CLI five seconds after a turn ended."


def test_the_list_holds_rows_and_counts_them(frame):
    lst = cl.ConversationList(frame)
    lst.Set(["one", "two"])
    assert lst.GetCount() == 2
    assert [r.label for r in lst.GetRows()] == ["one", "two"]
    lst.AppendItems(["three"])
    assert lst.GetCount() == 3


def test_a_long_row_is_taller_when_the_control_is_narrower(frame):
    lst = cl.ConversationList(frame)
    lst.Set([LONG])
    lst.SetSize(wx.Size(600, 200))
    wide = lst.OnMeasureItem(0)
    lst.SetSize(wx.Size(200, 200))
    narrow = lst.OnMeasureItem(0)
    assert narrow > wide


def test_measurements_are_cached_until_the_rows_or_width_change(frame):
    lst = cl.ConversationList(frame)
    lst.Set([LONG, "short"])
    lst.SetSize(wx.Size(300, 200))
    lst.OnMeasureItem(0)
    lst.OnMeasureItem(1)
    assert set(lst._measured) == {(0, lst.GetClientSize().width), (1, lst.GetClientSize().width)}
    lst.AppendItems(["more"])
    assert (0, lst.GetClientSize().width) in lst._measured, "append kept the rows that stayed"
    lst.Set(["fresh"])
    assert all(n < lst.GetCount() for n, _w in lst._measured)


def test_set_keeps_the_selection_index_when_it_still_exists(frame):
    lst = cl.ConversationList(frame)
    lst.Set(["a", "b", "c"])
    lst.SetSelection(1)
    lst.Set(["a", "b", "c", "d"])
    assert lst.GetSelection() == 1
    lst.Set(["only"])
    assert lst.GetSelection() in (0, wx.NOT_FOUND)


def test_fonts_follow_the_style(frame):
    lst = cl.ConversationList(frame)
    base = lst.GetFont()
    assert lst._font_for(cl.style_for("you")).GetWeight() == wx.FONTWEIGHT_BOLD
    assert lst._font_for(cl.style_for("code")).IsFixedWidth()
    assert not lst._font_for(cl.style_for("prose")).IsFixedWidth()
    assert lst._font_for(cl.style_for("prose")).GetPointSize() == base.GetPointSize()


class _FakeList:
    """What the accessible object needs from the control, and nothing else."""

    def __init__(self, labels, selected=wx.NOT_FOUND, focused=False):
        self._rows = cl.as_rows(labels)
        self._sel = selected
        self._focused = focused

    def GetRows(self):
        return list(self._rows)

    def GetCount(self):
        return len(self._rows)

    def GetSelection(self):
        return self._sel

    def GetName(self):
        return "Responses"

    def HasFocus(self):
        return self._focused

    def IsVisible(self, n):
        return True

    def GetItemRect(self, n):
        return wx.Rect(0, 20 * n, 100, 20)

    def ClientToScreen(self, pt):
        return wx.Point(pt.x + 5, pt.y + 5)

    def ScreenToClient(self, pt):
        return wx.Point(pt.x - 5, pt.y - 5)

    def GetScreenRect(self):
        return wx.Rect(5, 5, 100, 100)

    def VirtualHitTest(self, y):
        n = y // 20
        return n if 0 <= n < len(self._rows) else wx.NOT_FOUND


def test_the_list_and_its_rows_have_the_roles_a_screen_reader_expects():
    acc = cl.RowsAccessible(_FakeList(["a", "b"]))
    assert acc.GetRole(0) == (wx.ACC_OK, wx.ROLE_SYSTEM_LIST)
    assert acc.GetRole(1) == (wx.ACC_OK, wx.ROLE_SYSTEM_LISTITEM)
    assert acc.GetChildCount() == (wx.ACC_OK, 2)


def test_a_row_is_named_by_its_label_and_has_no_description():
    acc = cl.RowsAccessible(_FakeList(["first row", "second row", "third row"]))
    assert acc.GetName(0) == (wx.ACC_OK, "Responses")
    assert acc.GetName(2) == (wx.ACC_OK, "second row")
    # The native list speaks no position on NVDA, so neither may this one.
    assert acc.GetDescription(2) == (wx.ACC_OK, "")
    assert acc.GetDescription(0) == (wx.ACC_OK, "")


def test_states_say_which_row_is_selected_and_whether_it_has_focus():
    acc = cl.RowsAccessible(_FakeList(["a", "b"], selected=1, focused=True))
    ok, state = acc.GetState(2)
    assert state & wx.ACC_STATE_SYSTEM_SELECTED and state & wx.ACC_STATE_SYSTEM_FOCUSED
    ok, state = acc.GetState(1)
    assert not state & wx.ACC_STATE_SYSTEM_SELECTED and state & wx.ACC_STATE_SYSTEM_SELECTABLE
    ok, state = acc.GetState(0)
    assert state & wx.ACC_STATE_SYSTEM_FOCUSED
    acc = cl.RowsAccessible(_FakeList(["a", "b"], selected=1, focused=False))
    ok, state = acc.GetState(2)
    assert state & wx.ACC_STATE_SYSTEM_SELECTED and not state & wx.ACC_STATE_SYSTEM_FOCUSED


def test_focus_and_selection_report_the_selected_row_or_nothing():
    acc = cl.RowsAccessible(_FakeList(["a", "b"], selected=0))
    assert acc.GetFocus(0) == (wx.ACC_OK, 1, None)
    assert acc.GetSelections() == (wx.ACC_OK, 1)
    acc = cl.RowsAccessible(_FakeList(["a", "b"]))
    assert acc.GetFocus(0) == (wx.ACC_OK, 0, None)
    assert acc.GetSelections() == (wx.ACC_OK, None)


def test_navigation_walks_the_rows_and_stops_at_the_ends():
    acc = cl.RowsAccessible(_FakeList(["a", "b", "c"]))
    assert acc.Navigate(wx.NAVDIR_FIRSTCHILD, 0) == (wx.ACC_OK, 1, None)
    assert acc.Navigate(wx.NAVDIR_LASTCHILD, 0) == (wx.ACC_OK, 3, None)
    assert acc.Navigate(wx.NAVDIR_NEXT, 1) == (wx.ACC_OK, 2, None)
    assert acc.Navigate(wx.NAVDIR_PREVIOUS, 1)[0] == wx.ACC_FALSE
    assert acc.Navigate(wx.NAVDIR_DOWN, 3)[0] == wx.ACC_FALSE


def test_a_row_is_located_on_screen_and_found_under_a_point():
    acc = cl.RowsAccessible(_FakeList(["a", "b"]))
    assert acc.GetLocation(2) == (wx.ACC_OK, wx.Rect(5, 25, 100, 20))
    assert acc.GetLocation(0) == (wx.ACC_OK, wx.Rect(5, 5, 100, 100))
    assert acc.HitTest(wx.Point(10, 30)) == (wx.ACC_OK, 2, None)
    assert acc.HitTest(wx.Point(10, 500)) == (wx.ACC_OK, 0, None)


def test_the_default_action_is_open_and_the_rest_is_silent():
    acc = cl.RowsAccessible(_FakeList(["a"]))
    assert acc.GetDefaultAction(1) == (wx.ACC_OK, "Open")
    assert acc.GetDefaultAction(0) == (wx.ACC_OK, "")
    for method in (acc.GetValue, acc.GetHelpText, acc.GetKeyboardShortcut):
        assert method(1) == (wx.ACC_OK, "")


def test_the_control_carries_the_accessible_object(frame):
    lst = cl.ConversationList(frame)
    assert isinstance(lst.GetAccessible(), cl.RowsAccessible)


def test_moving_the_selection_tells_the_screen_reader_which_row_has_focus(frame, monkeypatch):
    events = []
    monkeypatch.setattr(
        wx.Accessible,
        "NotifyEvent",
        staticmethod(lambda ev, win, objid, child: events.append((ev, child))),
    )
    lst = cl.ConversationList(frame)
    lst.Set(["a", "b", "c"])
    lst.SetSelection(2)
    ev = wx.CommandEvent(wx.wxEVT_LISTBOX, lst.GetId())
    lst.GetEventHandler().ProcessEvent(ev)
    assert events == [(wx.ACC_EVENT_OBJECT_FOCUS, 3), (wx.ACC_EVENT_OBJECT_SELECTION, 3)]


def test_focus_with_nothing_selected_lands_on_the_first_row(frame, monkeypatch):
    events = []
    monkeypatch.setattr(
        wx.Accessible,
        "NotifyEvent",
        staticmethod(lambda ev, win, objid, child: events.append((ev, child))),
    )
    lst = cl.ConversationList(frame)
    lst.Set(["a", "b"])
    lst._on_focus(wx.FocusEvent())
    wx.GetApp().ProcessPendingEvents()
    assert lst.GetSelection() == 0
    assert events == [(wx.ACC_EVENT_OBJECT_FOCUS, 1), (wx.ACC_EVENT_OBJECT_SELECTION, 1)]
