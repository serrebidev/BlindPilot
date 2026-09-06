"""A conversation list that wraps its rows and still reads as a list.

wx.ListBox draws one native line per row and cannot wrap, so a paragraph is
cut off at the right edge of the window. wx.VListBox draws whatever it is
told, but a screen reader sees nothing inside it unless it is given an
accessible object. This module is both: the drawing and the accessible
object, kept together because they must agree on what a row is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Union

import wx
from wx.lib.wordwrap import wordwrap

from markdown_rows import Row


@dataclass(frozen=True)
class RowStyle:
    bold: bool = False
    muted: bool = False
    mono: bool = False
    indented: bool = False
    error: bool = False


_STYLES = {
    "you": RowStyle(bold=True),
    "header": RowStyle(bold=True),
    "heading": RowStyle(bold=True),
    "prose": RowStyle(),
    "list": RowStyle(),
    "quote": RowStyle(),
    "thinking": RowStyle(muted=True),
    "tool": RowStyle(indented=True),
    "result": RowStyle(indented=True),
    "code": RowStyle(mono=True),
    "error": RowStyle(error=True),
}


def style_for(kind: str) -> RowStyle:
    return _STYLES.get(kind, _STYLES["prose"])


def as_rows(items: Sequence[Union[Row, str]]) -> list[Row]:
    """Rows as given; bare strings become prose rows so plain lists can use this."""
    rows: list[Row] = []
    for item in items:
        if isinstance(item, Row):
            rows.append(item)
        else:
            rows.append(Row(kind="prose", label=item, payload=item, response_number=0))
    return rows


class ConversationList(wx.VListBox):
    """Rows that wrap to the width of the control and are drawn by kind."""

    def __init__(self, parent: wx.Window, name: str = "Responses"):
        super().__init__(parent, style=wx.BORDER_THEME)
        self.SetName(name)
        self._rows: list[Row] = []
        # (row index, client width) -> height. Cleared when either changes.
        self._measured: dict[tuple[int, int], int] = {}
        self.Bind(wx.EVT_SIZE, self._on_size)
        self._accessible = RowsAccessible(self)
        self.SetAccessible(self._accessible)
        self.Bind(wx.EVT_LISTBOX, self._on_select)
        self.Bind(wx.EVT_SET_FOCUS, self._on_focus)

    # ----- rows -----
    def GetRows(self) -> list[Row]:
        return list(self._rows)

    def GetCount(self) -> int:  # type: ignore[override]
        return len(self._rows)

    def Set(self, items: Sequence[Union[Row, str]]) -> None:
        keep = self.GetSelection()
        self._rows = as_rows(items)
        self._measured.clear()
        self.SetItemCount(len(self._rows))
        if self._rows and keep != wx.NOT_FOUND:
            self.SetSelection(min(keep, len(self._rows) - 1))
        self.RefreshAll()
        # The cache is cleared before the count changes so no stale height survives.

    def AppendItems(self, items: Sequence[Union[Row, str]]) -> None:
        self._rows.extend(as_rows(items))
        # Heights of the rows that stayed are still right. Only the count grows.
        self.SetItemCount(len(self._rows))
        self.RefreshAll()

    def SetSelection(self, index: int) -> None:  # type: ignore[override]
        if not self._rows:
            return
        index = max(0, min(index, len(self._rows) - 1))
        super().SetSelection(index)
        if not self.IsVisible(index):
            self.ScrollToRow(index)

    # ----- measuring and drawing -----
    def _pad(self) -> int:
        return self.FromDIP(6)

    def _side(self) -> int:
        return self.FromDIP(8)

    def _indent(self) -> int:
        return self.FromDIP(16)

    def _font_for(self, style: RowStyle) -> wx.Font:
        font = self.GetFont()
        if style.mono:
            font = wx.Font(wx.FontInfo(font.GetPointSize()).Family(wx.FONTFAMILY_TELETYPE))
        if style.bold:
            font = font.Bold()
        return font

    def _text_width(self, style: RowStyle) -> int:
        width = self.GetClientSize().width - 2 * self._side()
        if style.indented:
            width -= self._indent()
        return max(width, self.FromDIP(20))

    def _wrapped(self, dc: wx.DC, n: int) -> str:
        row = self._rows[n]
        style = style_for(row.kind)
        dc.SetFont(self._font_for(style))
        return wordwrap(row.label, self._text_width(style), dc) if row.label else ""

    def OnMeasureItem(self, n: int) -> int:
        key = (n, self.GetClientSize().width)
        cached = self._measured.get(key)
        if cached is not None:
            return cached
        dc = wx.ClientDC(self)
        text = self._wrapped(dc, n) or " "
        _w, h = dc.GetMultiLineTextExtent(text)
        height = h + 2 * self._pad()
        self._measured[key] = height
        return height

    def OnDrawBackground(self, dc: wx.DC, rect: wx.Rect, n: int) -> None:
        if self.IsSelected(n):
            super().OnDrawBackground(dc, rect, n)
            return
        style = style_for(self._rows[n].kind)
        if style.error:
            dc.SetBrush(wx.Brush(wx.SystemSettings.GetColour(wx.SYS_COLOUR_INFOBK)))
            dc.SetPen(wx.TRANSPARENT_PEN)
            dc.DrawRectangle(rect)
        else:
            super().OnDrawBackground(dc, rect, n)

    def OnDrawItem(self, dc: wx.DC, rect: wx.Rect, n: int) -> None:
        style = style_for(self._rows[n].kind)
        text = self._wrapped(dc, n)
        if self.IsSelected(n):
            colour = wx.SYS_COLOUR_HIGHLIGHTTEXT
        elif style.muted:
            colour = wx.SYS_COLOUR_GRAYTEXT
        else:
            colour = wx.SYS_COLOUR_WINDOWTEXT
        dc.SetTextForeground(wx.SystemSettings.GetColour(colour))
        inner = wx.Rect(rect)
        inner.Deflate(self._side(), self._pad())
        if style.indented:
            inner.x += self._indent()
            inner.width -= self._indent()
        dc.DrawLabel(text, inner, wx.ALIGN_LEFT | wx.ALIGN_TOP)

    def _on_size(self, event: wx.SizeEvent) -> None:
        self._measured.clear()
        self.RefreshAll()
        event.Skip()

    # ----- what the screen reader is told -----
    def _announce_selection(self) -> None:
        sel = self.GetSelection()
        if sel == wx.NOT_FOUND:
            return
        wx.Accessible.NotifyEvent(wx.ACC_EVENT_OBJECT_FOCUS, self, wx.OBJID_CLIENT, sel + 1)
        wx.Accessible.NotifyEvent(wx.ACC_EVENT_OBJECT_SELECTION, self, wx.OBJID_CLIENT, sel + 1)

    def _on_select(self, event: wx.CommandEvent) -> None:
        self._announce_selection()
        event.Skip()

    def _on_focus(self, event: wx.FocusEvent) -> None:
        if self.GetSelection() == wx.NOT_FOUND and self._rows:
            super().SetSelection(0)
        # After the focus change has settled, so the reader hears the list
        # first and the row second, as it does for the native control.
        wx.CallAfter(self._announce_selection)
        event.Skip()


class RowsAccessible(wx.Accessible):
    """Each row is a list item to MSAA; the control is the list.

    Child ids are 1-based row indexes. Zero is the list itself. NVDA speaks
    the name on every move and the description after it, so the description
    stays empty to match the native list.
    """

    def __init__(self, ctrl):
        super().__init__(ctrl if isinstance(ctrl, wx.Window) else None)
        self._ctrl = ctrl

    def _count(self) -> int:
        return self._ctrl.GetCount()

    def GetChildCount(self):
        return (wx.ACC_OK, self._count())

    def GetChild(self, childId):
        return (wx.ACC_OK, None)

    def GetRole(self, childId):
        return (wx.ACC_OK, wx.ROLE_SYSTEM_LIST if childId == 0 else wx.ROLE_SYSTEM_LISTITEM)

    def GetName(self, childId):
        if childId == 0:
            return (wx.ACC_OK, self._ctrl.GetName())
        rows = self._ctrl.GetRows()
        if not 1 <= childId <= len(rows):
            return (wx.ACC_INVALID_ARG, "")
        return (wx.ACC_OK, rows[childId - 1].label)

    def GetDescription(self, childId):
        return (wx.ACC_OK, "")

    def GetState(self, childId):
        focused = self._ctrl.HasFocus()
        if childId == 0:
            state = wx.ACC_STATE_SYSTEM_FOCUSABLE
            if focused:
                state |= wx.ACC_STATE_SYSTEM_FOCUSED
            return (wx.ACC_OK, state)
        state = wx.ACC_STATE_SYSTEM_SELECTABLE | wx.ACC_STATE_SYSTEM_FOCUSABLE
        if self._ctrl.GetSelection() == childId - 1:
            state |= wx.ACC_STATE_SYSTEM_SELECTED
            if focused:
                state |= wx.ACC_STATE_SYSTEM_FOCUSED
        if not self._ctrl.IsVisible(childId - 1):
            state |= wx.ACC_STATE_SYSTEM_INVISIBLE
        return (wx.ACC_OK, state)

    def GetLocation(self, childId):
        if childId == 0:
            return (wx.ACC_OK, self._ctrl.GetScreenRect())
        rect = self._ctrl.GetItemRect(childId - 1)
        pos = self._ctrl.ClientToScreen(rect.GetPosition())
        return (wx.ACC_OK, wx.Rect(pos, rect.GetSize()))

    def GetFocus(self, childId):
        sel = self._ctrl.GetSelection()
        return (wx.ACC_OK, 0 if sel == wx.NOT_FOUND else sel + 1, None)

    def GetSelections(self):
        sel = self._ctrl.GetSelection()
        return (wx.ACC_OK, None if sel == wx.NOT_FOUND else sel + 1)

    def GetDefaultAction(self, childId):
        return (wx.ACC_OK, "Open" if childId else "")

    def GetValue(self, childId):
        return (wx.ACC_OK, "")

    def GetHelpText(self, childId):
        return (wx.ACC_OK, "")

    def GetKeyboardShortcut(self, childId):
        return (wx.ACC_OK, "")

    def HitTest(self, pt):
        item = self._ctrl.VirtualHitTest(self._ctrl.ScreenToClient(pt).y)
        return (wx.ACC_OK, 0 if item == wx.NOT_FOUND else item + 1, None)

    def Navigate(self, navDir, fromId):
        count = self._count()
        if fromId == 0 and navDir == wx.NAVDIR_FIRSTCHILD:
            return (wx.ACC_OK, 1, None) if count else (wx.ACC_FALSE, 0, None)
        if fromId == 0 and navDir == wx.NAVDIR_LASTCHILD:
            return (wx.ACC_OK, count, None) if count else (wx.ACC_FALSE, 0, None)
        if navDir in (wx.NAVDIR_NEXT, wx.NAVDIR_DOWN) and 0 < fromId < count:
            return (wx.ACC_OK, fromId + 1, None)
        if navDir in (wx.NAVDIR_PREVIOUS, wx.NAVDIR_UP) and fromId > 1:
            return (wx.ACC_OK, fromId - 1, None)
        return (wx.ACC_FALSE, 0, None)
