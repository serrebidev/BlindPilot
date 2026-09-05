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

    # ----- rows -----
    def GetRows(self) -> list[Row]:
        return list(self._rows)

    def GetCount(self) -> int:  # type: ignore[override]
        return len(self._rows)

    def Set(self, items: Sequence[Union[Row, str]]) -> None:
        keep = self.GetSelection()
        self._rows = as_rows(items)
        self.SetItemCount(len(self._rows))
        if self._rows and keep != wx.NOT_FOUND:
            self.SetSelection(min(keep, len(self._rows) - 1))
        self.RefreshAll()
        # SetItemCount and RefreshAll both call OnMeasureItem on their own to lay
        # out and scroll the list, so the cache is cleared last, once those are
        # done, rather than left holding heights from before the rows changed.
        self._measured.clear()

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
