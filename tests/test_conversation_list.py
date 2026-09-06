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
