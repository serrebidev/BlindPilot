"""The per-row actions menu, and where its bindings are put.

Each item used to bind on the panel that popped the menu up. A binding made
on the panel outlives the menu that needed it, so every open of the menu left
five more EVT_MENU handlers behind for as long as the conversation stayed on
screen.
"""

from __future__ import annotations

import wx

import blindpilot_app as app
from markdown_rows import Row


class _PanelStub:
    """Stands in for a SessionPanel: records binds and the actions fired."""

    def __init__(self) -> None:
        self.bound: list[tuple] = []
        self.calls: list[str] = []

    def Bind(self, event, handler, source=None):  # noqa: N802 - wx spells it Bind
        self.bound.append((event, handler, source))

    def _read_formatted(self, row: Row) -> None:
        self.calls.append("read_formatted")

    def _action_save_code(self, row: Row) -> None:
        self.calls.append("save_code")

    def _action_insert(self, row: Row) -> None:
        self.calls.append("insert")

    def _action_copy_response(self, row: Row) -> None:
        self.calls.append("copy_response")

    def _action_copy_conversation(self) -> None:
        self.calls.append("copy_conversation")


def test_the_row_menu_binds_its_items_to_the_menu_itself(wx_app):
    """The handler still fires, and the panel gains nothing from the open."""
    stub = _PanelStub()
    row = Row(kind="code", label="a", payload="first", response_number=1, language="python")

    menu = app.SessionPanel._build_row_menu(stub, row)
    try:
        first = menu.FindItemByPosition(0)
        handled = menu.ProcessEvent(wx.CommandEvent(wx.wxEVT_MENU, first.GetId()))
    finally:
        menu.Destroy()

    assert stub.bound == [], f"the panel kept bindings it does not own: {stub.bound}"
    assert handled, "the menu item's own handler did not fire"
    assert stub.calls == ["read_formatted"]
