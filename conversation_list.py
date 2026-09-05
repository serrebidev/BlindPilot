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
