# Responses List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Responses `wx.ListBox` with a wrapping, owner-drawn list that NVDA reads exactly as it reads the native list today, and let the text-field mode wrap.

**Architecture:** A new module `conversation_list.py` holds `ConversationList(wx.VListBox)` and `RowsAccessible(wx.Accessible)`. `SessionPanel` swaps the constructor and passes `Row` objects; the text mode maps caret to row by character offsets; the three other single-select lists reuse the class. The spoken sequence is recorded through the NVDA bridge before the swap and diffed after.

**Tech Stack:** Python 3.13, wxPython 4.3.1 (wxWidgets 3.3.3), `wx.lib.wordwrap`, pytest, ruff, mypy, the NVDA MCP bridge (`mcp__screen-reader-testing__*` tools), `docs/visual-audit/tools/capture.ps1` and `sendkeys.ps1`.

**Spec:** `docs/visual-audit/04-responses-list-design.md`

## Global Constraints

- Branch `visual/responses-list`, created from `visual/dark-mode` once that is committed. Never commit with `--no-gpg-sign`; 1Password signs.
- Nothing a screen reader hears may change, and no key may change what it does. Row labels are untouched.
- No hard-coded colours. Every colour comes from `wx.SystemSettings.GetColour`; every size goes through `FromDIP`.
- One system font. Bold, grey text, and the teletype family are the only variations.
- Comments are plain: no em dashes, no puffery, no colons as mid-sentence connectors.
- Do not run the full pytest suite inside a task; run the named files. The full suite, `ruff check .`, `ruff format --check .`, `mypy`, and `python blind_pilot.py --startup-gui-smoke` run once in Task 11.
- Files are a mix of LF and CRLF; preserve each file's own ending. The Bash tool mangles backticks in heredocs; use the Edit tool or a Python patch script written with the Write tool.
- Tests that need real widgets use a hidden `wx.App` and `wx.Frame`; copy the `_running_app` fixture from `tests/test_tab_strip_focus.py`.

---

## File Structure

- Create `conversation_list.py`: the list control, its accessible object, the style table, the measure cache. Imports `markdown_rows.Row` only.
- Create `tests/test_conversation_list.py`: everything about the control and the accessible object.
- Create `tests/test_responses_text_mode.py`: the offset mapping for the text-field mode.
- Modify `blindpilot_app.py`: `SessionPanel.__init__` (the two responses controls, the activity indicator), `_refresh_list`, `_append_rows`, `_selected_row`, `_select_row`, the earcon progress call sites, `HistoryDialog.__init__`, the Hermes conversations dialog `__init__`, `SlashCommandDialog.__init__`.
- Modify `markdown_rows.py`: nothing but the `kind` comment if `error` is not yet listed.
- Create `docs/visual-audit/nvda-list-before.json` and `nvda-list-after.json`: the spoken sequences.
- Create `docs/visual-audit/applied-responses-list.md`: what was done.

---

### Task 0: Record what NVDA says today

**Files:**
- Create: `docs/visual-audit/nvda-list-before.json`

**Interfaces:**
- Produces: the baseline that Task 10 diffs against. JSON list of `{"key": str, "spoken": [str]}`.

- [ ] **Step 1: Launch an audit copy of the app**

Run the PowerShell recipe in `docs/visual-audit/README.md` (sandboxed `APPDATA`, retitled window). Note the pid. The sandbox config already has a conversation history from the earlier audit.

- [ ] **Step 2: Connect the reader**

Call `mcp__screen-reader-testing__connect_reader` with `reader="nvda"`, `mode="live"`. Then `mcp__screen-reader-testing__get_next_speech_index` and keep the index.

- [ ] **Step 3: Load a past conversation**

With `docs\visual-audit\tools\sendkeys.ps1 -ProcessId <pid> -Keys "^+h"`, then `-Keys "{ENTER}"` to open the first conversation. Wait two seconds.

- [ ] **Step 4: Drive the list, one key per call, two seconds apart, reading speech after each**

For each key in this order, send it with sendkeys.ps1, wait, call `get_speech` with `since_index` set to the index you noted before the key, and store the texts under that key:

```
"^{UP}"        (Ctrl+Up: enter the responses from the prompt)
"{DOWN}" x5    (record each separately as "DOWN-1" .. "DOWN-5")
"{HOME}"
"{END}"
"^{DOWN}"      (Ctrl+Down: next response)
"{ENTER}"      (Read View opens)
"{ESC}"
"+{F10}"       (row menu)
"{ESC}"
"+{TAB}"       (out of the list)
"^f"           (find dialog) then type "the" and "{ENTER}", then "^{UP}", "{DOWN}"
```

- [ ] **Step 5: Save the baseline**

Write the list to `docs/visual-audit/nvda-list-before.json` as `[{"key": "^{UP}", "spoken": ["..."]}, ...]`. Call `disconnect_reader`. Close the audit copy with `Stop-Process`.

- [ ] **Step 6: Commit**

```bash
git add docs/visual-audit/nvda-list-before.json
git commit -m "Record what NVDA says in the Responses list before it is replaced"
```

---

### Task 1: The style table and row normalisation

**Files:**
- Create: `conversation_list.py`
- Test: `tests/test_conversation_list.py`

**Interfaces:**
- Produces: `RowStyle(bold: bool, muted: bool, mono: bool, indented: bool, error: bool)`, `style_for(kind: str) -> RowStyle`, `as_rows(items: Sequence[Row | str]) -> list[Row]`.

- [ ] **Step 1: Write the failing tests**

```python
"""The wrapping Responses list and what it tells a screen reader."""

from __future__ import annotations

from markdown_rows import Row

import conversation_list as cl


def test_every_row_kind_has_a_style():
    for kind in ("header", "prose", "heading", "list", "quote", "code",
                 "you", "thinking", "tool", "result", "error"):
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
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_conversation_list.py -q -p no:randomly`
Expected: FAIL with `ModuleNotFoundError: No module named 'conversation_list'`

- [ ] **Step 3: Write the module header, the style table and `as_rows`**

```python
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
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `python -m pytest tests/test_conversation_list.py -q -p no:randomly`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add conversation_list.py tests/test_conversation_list.py
git commit -m "Start the wrapping conversation list with its style table"
```

---

### Task 2: The control, its rows, and the measure cache

**Files:**
- Modify: `conversation_list.py`
- Test: `tests/test_conversation_list.py`

**Interfaces:**
- Consumes: `style_for`, `as_rows`, `RowStyle` from Task 1.
- Produces: `ConversationList(wx.VListBox)` with `Set(items)`, `AppendItems(items)`, `GetRows() -> list[Row]`, `GetCount() -> int`, `GetSelection() -> int`, `SetSelection(int) -> None`, `OnMeasureItem(n) -> int`, `OnDrawItem(dc, rect, n)`, `OnDrawBackground(dc, rect, n)`, `_font_for(style) -> wx.Font`, `_wrapped(dc, n) -> str`. `GetSelection` returns `wx.NOT_FOUND` when nothing is selected.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_conversation_list.py`:

```python
import pytest
import wx


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
    assert lst._measured == {}


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
    assert lst._font_for(cl.style_for("code")).GetFamily() == wx.FONTFAMILY_TELETYPE
    assert lst._font_for(cl.style_for("prose")).GetPointSize() == base.GetPointSize()
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_conversation_list.py -q -p no:randomly`
Expected: the five new tests FAIL with `AttributeError: module 'conversation_list' has no attribute 'ConversationList'`

- [ ] **Step 3: Write the control**

Append to `conversation_list.py`:

```python
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
        self._measured.clear()
        self.SetItemCount(len(self._rows))
        if self._rows and keep != wx.NOT_FOUND:
            self.SetSelection(min(keep, len(self._rows) - 1))
        self.RefreshAll()

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
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `python -m pytest tests/test_conversation_list.py -q -p no:randomly`
Expected: 10 passed. If `GetCount`/`SetSelection` overrides upset mypy, keep the `# type: ignore[override]` comments; run `python -m mypy` and fix any other complaint.

- [ ] **Step 5: Commit**

```bash
git add conversation_list.py tests/test_conversation_list.py
git commit -m "Draw conversation rows wrapped, by kind, with a measure cache"
```

---

### Task 3: The accessible object

**Files:**
- Modify: `conversation_list.py`
- Test: `tests/test_conversation_list.py`

**Interfaces:**
- Consumes: a control with `GetRows()`, `GetCount()`, `GetSelection()`, `GetName()`, `HasFocus()`, `IsVisible(n)`, `GetItemRect(n)`, `ClientToScreen(pt)`, `ScreenToClient(pt)`, `GetScreenRect()`, `VirtualHitTest(y)`.
- Produces: `RowsAccessible(wx.Accessible)` taking the control in its constructor. The fake in the tests is the contract.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_conversation_list.py`:

```python
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


def test_a_row_is_named_by_its_label_and_described_by_its_position():
    acc = cl.RowsAccessible(_FakeList(["first row", "second row", "third row"]))
    assert acc.GetName(0) == (wx.ACC_OK, "Responses")
    assert acc.GetName(2) == (wx.ACC_OK, "second row")
    assert acc.GetDescription(2) == (wx.ACC_OK, "2 of 3")
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
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_conversation_list.py -q -p no:randomly`
Expected: the seven new tests FAIL with `AttributeError: ... 'RowsAccessible'`

- [ ] **Step 3: Write the accessible object**

Append to `conversation_list.py`:

```python
class RowsAccessible(wx.Accessible):
    """Each row is a list item to MSAA; the control is the list.

    Child ids are 1-based row indexes. Zero is the list itself. NVDA speaks
    the name on every move and the description after it, so the position
    goes in the description, which is how "3 of 40" is heard.
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
        if childId == 0:
            return (wx.ACC_OK, "")
        return (wx.ACC_OK, f"{childId} of {self._count()}")

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

    def GetDescription_(self, childId):  # pragma: no cover - not part of the protocol
        raise NotImplementedError

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
```

Remove the `GetDescription_` stub before committing; it is listed here only so nobody adds a second description method by accident.

- [ ] **Step 4: Run the tests to see them pass**

Run: `python -m pytest tests/test_conversation_list.py -q -p no:randomly`
Expected: 17 passed

- [ ] **Step 5: Commit**

```bash
git add conversation_list.py tests/test_conversation_list.py
git commit -m "Tell a screen reader that each conversation row is a list item, N of M"
```

---

### Task 4: Attach the accessible object and raise focus events

**Files:**
- Modify: `conversation_list.py`
- Test: `tests/test_conversation_list.py`

**Interfaces:**
- Consumes: `ConversationList`, `RowsAccessible`.
- Produces: `ConversationList._announce_selection()` which calls `wx.Accessible.NotifyEvent` twice; `ConversationList.notified: list[tuple[int, int]]` is not kept, tests monkeypatch `wx.Accessible.NotifyEvent`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_conversation_list.py`:

```python
def test_the_control_carries_the_accessible_object(frame):
    lst = cl.ConversationList(frame)
    assert isinstance(lst.GetAccessible(), cl.RowsAccessible)


def test_moving_the_selection_tells_the_screen_reader_which_row_has_focus(frame, monkeypatch):
    events = []
    monkeypatch.setattr(wx.Accessible, "NotifyEvent",
                        staticmethod(lambda ev, win, objid, child: events.append((ev, child))))
    lst = cl.ConversationList(frame)
    lst.Set(["a", "b", "c"])
    lst.SetSelection(2)
    lst._announce_selection()
    assert events == [(wx.ACC_EVENT_OBJECT_FOCUS, 3), (wx.ACC_EVENT_OBJECT_SELECTION, 3)]


def test_focus_with_nothing_selected_lands_on_the_first_row(frame, monkeypatch):
    events = []
    monkeypatch.setattr(wx.Accessible, "NotifyEvent",
                        staticmethod(lambda ev, win, objid, child: events.append((ev, child))))
    lst = cl.ConversationList(frame)
    lst.Set(["a", "b"])
    lst._on_focus(wx.FocusEvent())
    assert lst.GetSelection() == 0
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_conversation_list.py -q -p no:randomly`
Expected: 3 FAIL (no accessible object, no `_announce_selection`, no `_on_focus`)

- [ ] **Step 3: Wire it up**

In `ConversationList.__init__`, after `self.Bind(wx.EVT_SIZE, ...)`:

```python
        self._accessible = RowsAccessible(self)
        self.SetAccessible(self._accessible)
        self.Bind(wx.EVT_LISTBOX, self._on_select)
        self.Bind(wx.EVT_SET_FOCUS, self._on_focus)
```

And add to the class:

```python
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
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `python -m pytest tests/test_conversation_list.py -q -p no:randomly`
Expected: 20 passed

- [ ] **Step 5: Commit**

```bash
git add conversation_list.py tests/test_conversation_list.py
git commit -m "Raise focus and selection events so the reader follows the row"
```

---

### Task 5: Swap the list into SessionPanel

**Files:**
- Modify: `blindpilot_app.py` (`SessionPanel.__init__` where `self.responses = wx.ListBox(...)`, `_refresh_list`, `_append_rows`)
- Test: `tests/test_live_rows.py` (append)

**Interfaces:**
- Consumes: `ConversationList` with `Set(rows)`, `AppendItems(rows)`, `GetCount()`.
- Produces: `_refresh_list` and `_append_rows` pass `Row` objects to the list; the text-mode branch is untouched in this task.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_live_rows.py`:

```python
def test_the_list_is_given_rows_not_labels(monkeypatch):
    import blindpilot_app
    from markdown_rows import Row

    given = {}

    class _List:
        def GetCount(self):
            return 0

        def Set(self, rows):
            given["set"] = rows

        def AppendItems(self, rows):
            given["append"] = rows

    rows = [Row(kind="you", label="You: hi", payload="hi", response_number=1)]
    panel = type("PanelStub", (), {
        "responses": _List(),
        "_rows": rows,
        "_displayed": [],
        "_search_term": "",
        "_selected_row": lambda self: -1,
        "_select_row": lambda self, i: None,
    })()
    monkeypatch.setattr(blindpilot_app.SETTINGS, "text_view", False)

    blindpilot_app.SessionPanel._refresh_list(panel)

    assert given["set"] == rows, "the list should receive Row objects so it can draw by kind"
```

- [ ] **Step 2: Run the test to see it fail**

Run: `python -m pytest tests/test_live_rows.py -q -p no:randomly -k rows_not_labels`
Expected: FAIL, `given["set"]` is a list of strings

- [ ] **Step 3: Swap the control and pass rows**

In `SessionPanel.__init__` replace

```python
        self.responses = wx.ListBox(self, style=wx.LB_SINGLE | wx.LB_NEEDED_SB)
```

with

```python
        self.responses = ConversationList(self)
```

and add `from conversation_list import ConversationList` with the other project imports at the top of `blindpilot_app.py`.

In `_refresh_list`, build the displayed rows once and hand the list rows instead of labels. The rebuild branch becomes:

```python
        if SETTINGS.text_view:
            self.responses_text.ChangeValue("\n".join(_one_line(label) for label in labels))
        else:
            self.responses.Set(self._displayed)
```

The append fast path keeps comparing `labels` and calls `self._append_rows(added_rows)` where `added_rows = self._displayed[len(previous):]`. Change `_append_rows` to take rows:

```python
    def _append_rows(self, rows: List[Row]) -> None:
        """Add rows to the end, leaving the reader exactly where they are."""
        if not SETTINGS.text_view:
            self.responses.AppendItems(rows)
            return
        text = "\n".join(_one_line(row.label) for row in rows)
        ...unchanged...
```

`_selected_row` and `_select_row` need no change: `GetSelection` and `SetSelection` keep their meaning.

- [ ] **Step 4: Run the tests to see them pass**

Run: `python -m pytest tests/test_live_rows.py tests/test_reading_while_streaming.py tests/test_error_cue.py tests/test_narration_modes.py -q -p no:randomly`
Expected: all pass. Then `python blind_pilot.py --startup-gui-smoke` exits 0.

- [ ] **Step 5: Commit**

```bash
git add blindpilot_app.py tests/test_live_rows.py
git commit -m "Show the conversation in the wrapping list"
```

---

### Task 6: Let the text-field mode wrap, mapping rows by offset

**Files:**
- Modify: `blindpilot_app.py` (`SessionPanel.__init__` responses_text style, `_refresh_list`, `_append_rows`, `_selected_row`, `_select_row`)
- Create: `tests/test_responses_text_mode.py`

**Interfaces:**
- Produces: `SessionPanel._row_starts: list[int]`, `SessionPanel._row_at(position: int) -> int` (module-level helper `_row_at(starts, position)` for tests).

- [ ] **Step 1: Write the failing tests**

```python
"""The read-only text field: one row per line, found by character offset.

Lines wrap now, so the control's own line numbers count wrapped lines and no
longer match rows. Each row's start offset does.
"""

from __future__ import annotations

import blindpilot_app as app


def test_a_caret_anywhere_in_a_row_names_that_row():
    starts = [0, 6, 14]  # "hello\nworld!!\nlast"
    assert app._row_at(starts, 0) == 0
    assert app._row_at(starts, 3) == 0
    assert app._row_at(starts, 5) == 0
    assert app._row_at(starts, 6) == 1
    assert app._row_at(starts, 13) == 1
    assert app._row_at(starts, 14) == 2
    assert app._row_at(starts, 99) == 2


def test_no_rows_means_no_row():
    assert app._row_at([], 0) == -1


class _Text:
    def __init__(self):
        self.value = ""
        self.caret = 0

    def ChangeValue(self, text):
        self.value = text

    def AppendText(self, text):
        self.value += text

    def GetLastPosition(self):
        return len(self.value)

    def GetNumberOfLines(self):
        return self.value.count("\n") + 1 if self.value else 1

    def GetInsertionPoint(self):
        return self.caret

    def SetInsertionPoint(self, pos):
        self.caret = pos


def _panel(rows):
    from markdown_rows import Row

    panel = type("PanelStub", (), {})()
    panel.responses_text = _Text()
    panel.responses = None
    panel._rows = [Row(kind="prose", label=r, payload=r, response_number=1) for r in rows]
    panel._displayed = []
    panel._search_term = ""
    panel._row_starts = []
    panel._selected_row = lambda: app.SessionPanel._selected_row(panel)
    panel._select_row = lambda i: app.SessionPanel._select_row(panel, i)
    panel._append_rows = lambda rows: app.SessionPanel._append_rows(panel, rows)
    return panel


def test_offsets_follow_the_rows_through_a_rebuild_and_an_append(monkeypatch):
    monkeypatch.setattr(app.SETTINGS, "text_view", True)
    panel = _panel(["hello", "world!!"])
    app.SessionPanel._refresh_list(panel)
    assert panel._row_starts == [0, 6]
    panel._rows.append(type(panel._rows[0])(kind="prose", label="last", payload="last", response_number=1))
    app.SessionPanel._refresh_list(panel)
    assert panel._row_starts == [0, 6, 14]
    assert panel.responses_text.value == "hello\nworld!!\nlast"


def test_selecting_a_row_puts_the_caret_at_its_start(monkeypatch):
    monkeypatch.setattr(app.SETTINGS, "text_view", True)
    panel = _panel(["hello", "world!!", "last"])
    app.SessionPanel._refresh_list(panel)
    app.SessionPanel._select_row(panel, 2)
    assert panel.responses_text.caret == 14
    assert app.SessionPanel._selected_row(panel) == 2
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_responses_text_mode.py -q -p no:randomly`
Expected: FAIL with `AttributeError: module 'blindpilot_app' has no attribute '_row_at'`

- [ ] **Step 3: Implement the offsets**

Near `_one_line` in `blindpilot_app.py` add:

```python
def _row_at(starts: List[int], position: int) -> int:
    """Which row a caret position is in, by each row's start offset."""
    if not starts:
        return -1
    return max(0, bisect.bisect_right(starts, position) - 1)
```

with `import bisect` among the standard imports. In `SessionPanel.__init__`, initialise `self._row_starts: List[int] = []` next to `self._displayed`, and change the text control style to `wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2` (no `TE_DONTWRAP`).

In `_refresh_list`, the text-mode rebuild:

```python
        if SETTINGS.text_view:
            lines = [_one_line(row.label) for row in self._displayed]
            self._row_starts = _starts_of(lines)
            self.responses_text.ChangeValue("\n".join(lines))
```

with the helper next to `_row_at`:

```python
def _starts_of(lines: List[str]) -> List[int]:
    starts: List[int] = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line) + 1
    return starts
```

In `_append_rows`, the text-mode branch appends offsets before the text: for each row, `self._row_starts.append(base)` where `base` starts at `GetLastPosition() + (1 if GetLastPosition() else 0)` and grows by `len(line) + 1`.

`_selected_row` in text mode becomes `return _row_at(self._row_starts, self.responses_text.GetInsertionPoint())` guarded to `wx.NOT_FOUND` when out of range of `_displayed`. `_select_row` in text mode becomes `self.responses_text.SetInsertionPoint(self._row_starts[index])`.

The `trustworthy` check in `_refresh_list` for text mode keeps using `GetNumberOfLines` only to detect an emptied control; replace it with `shown = len(self._row_starts) if self.responses_text.GetLastPosition() else 0`, which is exact now.

- [ ] **Step 4: Run the tests to see them pass**

Run: `python -m pytest tests/test_responses_text_mode.py tests/test_reading_while_streaming.py tests/test_live_rows.py -q -p no:randomly`
Expected: all pass. Fix any existing text-view stub that lacks `_row_starts` by adding the attribute to the stub.

- [ ] **Step 5: Commit**

```bash
git add blindpilot_app.py tests/test_responses_text_mode.py
git commit -m "Let the read-only text view wrap, finding the row by offset instead of line"
```

---

### Task 7: A working indicator beside Stop

**Files:**
- Modify: `blindpilot_app.py` (`SessionPanel.__init__` bottom row; the six `_earcons.start_progress()`/`stop_progress()` sites inside `SessionPanel`)
- Test: `tests/test_error_cue.py` (append)

**Interfaces:**
- Produces: `SessionPanel.working: wx.ActivityIndicator`, `SessionPanel._show_working()`, `SessionPanel._hide_working()`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_error_cue.py`:

```python
def test_the_working_indicator_runs_with_the_progress_cue():
    import blindpilot_app

    state = []

    class _Indicator:
        def Start(self):
            state.append("start")

        def Stop(self):
            state.append("stop")

        def IsRunning(self):
            return bool(state) and state[-1] == "start"

    panel = type("PanelStub", (), {"working": _Indicator()})()
    blindpilot_app.SessionPanel._show_working(panel)
    blindpilot_app.SessionPanel._hide_working(panel)
    blindpilot_app.SessionPanel._hide_working(panel)
    assert state == ["start", "stop"], "stopping twice must not stop twice"
```

- [ ] **Step 2: Run the test to see it fail**

Run: `python -m pytest tests/test_error_cue.py -q -p no:randomly -k working_indicator`
Expected: FAIL, no `_show_working`

- [ ] **Step 3: Add the indicator**

In `SessionPanel.__init__` after `self.stop_btn` is created:

```python
        # Sighted only. It is never focusable and has no name for the reader;
        # the earcon and the status line already say a turn is running.
        self.working = wx.ActivityIndicator(self)
        self.working.Hide()
```

and add it to `bottom_row` right after `self.stop_btn`: `bottom_row.Add(self.working, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(PAD))`.

Add the two methods to `SessionPanel`:

```python
    def _show_working(self) -> None:
        if not self.working.IsRunning():
            self.working.Show()
            self.working.Start()
            self.Layout()

    def _hide_working(self) -> None:
        if self.working.IsRunning():
            self.working.Stop()
            self.working.Hide()
            self.Layout()
```

Call `self._show_working()` on the line after every `self._earcons.start_progress()` inside `SessionPanel`, and `self._hide_working()` after every `self._earcons.stop_progress()` inside `SessionPanel`. Existing panel stubs in tests that reach those sites need `working` and the two methods; add `"_show_working": lambda self: None, "_hide_working": lambda self: None` to the stubs the failing tests point at.

- [ ] **Step 4: Run the tests to see them pass**

Run: `python -m pytest tests/test_error_cue.py tests/test_live_rows.py tests/test_reading_while_streaming.py tests/test_narration_modes.py tests/test_permission_mode.py -q -p no:randomly`
Expected: all pass. `python blind_pilot.py --startup-gui-smoke` exits 0.

- [ ] **Step 5: Commit**

```bash
git add blindpilot_app.py tests/test_error_cue.py
git commit -m "Show a working indicator while a turn runs, for eyes only"
```

---

### Task 8: The other single-select lists

**Files:**
- Modify: `blindpilot_app.py` (`HistoryDialog.__init__` `self.list_box = wx.ListBox(...)`, the Hermes conversations dialog `self.list_box = wx.ListBox(...)`, `SlashCommandDialog` `self.list = wx.ListBox(...)`)
- Test: `tests/test_conversation_list.py` (append)

**Interfaces:**
- Consumes: `ConversationList(parent, name=...)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_conversation_list.py`:

```python
def test_the_conversation_pickers_use_the_wrapping_list():
    import inspect

    import blindpilot_app as app

    for cls in (app.HistoryDialog, app.HermesSessionsDialog, app.SlashCommandDialog):
        source = inspect.getsource(cls.__init__)
        assert "ConversationList(" in source, f"{cls.__name__} still builds a wx.ListBox"
        assert "wx.ListBox(" not in source
```

If a class is named differently in the file, use the real names (grep `wx.ListBox(` to find the three).

- [ ] **Step 2: Run the test to see it fail**

Run: `python -m pytest tests/test_conversation_list.py -q -p no:randomly -k pickers`
Expected: FAIL

- [ ] **Step 3: Swap the three constructors**

Each `wx.ListBox(self, style=wx.LB_SINGLE | wx.LB_NEEDED_SB)` becomes `ConversationList(self, name="Conversations")` (or the name the following `SetName` line sets; then delete that `SetName` line). The existing `Set([...])`, `SetSelection`, `GetSelection`, `SetFocus` and `EVT_LISTBOX`/`EVT_LISTBOX_DCLICK` binds work unchanged.

- [ ] **Step 4: Run the tests to see them pass**

Run: `python -m pytest tests/test_conversation_list.py tests/test_history_dialog_keys.py tests/test_hermes_sessions_ui.py tests/test_hermes_sessions_dialog_keys.py tests/test_session_history.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add blindpilot_app.py tests/test_conversation_list.py
git commit -m "Use the wrapping list for past conversations and slash commands"
```

---

### Task 9: Lint, types, and a live look

**Files:**
- Create: `docs/visual-audit/shots/80-list-default.png`, `81-list-700x500.png`, `82-list-dark.png`

- [ ] **Step 1: Static checks**

Run: `python -m ruff check conversation_list.py blindpilot_app.py tests/test_conversation_list.py tests/test_responses_text_mode.py && python -m ruff format conversation_list.py tests/test_conversation_list.py tests/test_responses_text_mode.py && python -m mypy`
Expected: clean. Fix anything reported.

- [ ] **Step 2: Screenshots**

Launch an audit copy (README recipe), open the first past conversation (`^+h`, `{ENTER}`), capture with `capture.ps1 -ProcessId <pid> -NoFocus -Out docs/visual-audit/shots/80-list-default.png`; resize to 700x500 with the windows-mcp App resize tool or `sendkeys.ps1` Alt+Space then S, capture `81-list-700x500.png`; set the dark-mode preference to Dark in the sandbox config, relaunch, capture `82-list-dark.png`. View each PNG with the Read tool and compare with shots 30, 31 and 03. Rows must wrap, bold headers and muted reasoning must be visible, the selected row must show the highlight, and nothing may be clipped at the right edge.

- [ ] **Step 3: Commit**

```bash
git add docs/visual-audit/shots/80-list-default.png docs/visual-audit/shots/81-list-700x500.png docs/visual-audit/shots/82-list-dark.png
git commit -m "Screenshots of the wrapping list at three sizes"
```

---

### Task 10: Record what NVDA says now, and diff

**Files:**
- Create: `docs/visual-audit/nvda-list-after.json`
- Create: `docs/visual-audit/applied-responses-list.md`

- [ ] **Step 1: Repeat Task 0 exactly** on the new build, saving to `nvda-list-after.json`.

- [ ] **Step 2: Diff**

```python
import json
before = json.load(open("docs/visual-audit/nvda-list-before.json", encoding="utf-8"))
after = json.load(open("docs/visual-audit/nvda-list-after.json", encoding="utf-8"))
for b, a in zip(before, after):
    if b["spoken"] != a["spoken"]:
        print(b["key"], "\n  before:", b["spoken"], "\n  after: ", a["spoken"])
```

Expected: no differences in row names or positions. The list's own announcement must read "Responses list" in both. Any other difference is a defect: fix it in `conversation_list.py`, add a unit test for it, and record again.

- [ ] **Step 3: Write the applied report**

`docs/visual-audit/applied-responses-list.md`: what changed per file, the test names, the diff result verbatim, the screenshots, and anything skipped.

- [ ] **Step 4: Commit**

```bash
git add docs/visual-audit/nvda-list-after.json docs/visual-audit/applied-responses-list.md
git commit -m "Prove NVDA reads the wrapping list as it read the native one"
```

---

### Task 11: The whole suite, then the pull request

- [ ] **Step 1: Run everything**

Run: `python -m pytest -q -p no:randomly --ignore=docs && python -m ruff check . && python -m ruff format --check . && python -m mypy && python blind_pilot.py --startup-gui-smoke`
Expected: all clean, exit 0.

- [ ] **Step 2: Push and open the PR**

```bash
git push -u fork visual/responses-list
gh pr create --repo serrebidev/BlindPilot --base main --head blindndangerous:visual/responses-list --title "Wrap the Responses list and keep it a list for the screen reader" --body-file <body written from applied-responses-list.md>
```

State in the body that it stacks on the dark-mode and safe-wins PRs, link the spec, and paste the NVDA diff result.

---

## Self-review

- Spec coverage: components (Tasks 1 to 4), SessionPanel swap (5), text mode offsets (6), activity indicator (7), other lists (8), error row drawing (2, `OnDrawBackground`), testing unit (1 to 8) and live (0, 9, 10), no hard-coded colours (Global Constraints). Error handling: empty label draws one padded line (Task 2, `or " "`), narrow width floors at 20 DIP (Task 2, `_text_width`). The missing-accessibility warning from the spec is not implemented; every supported build has accessibility, so it is dropped from scope here and noted in the applied report.
- Placeholders: none; every step carries its code.
- Names: `ConversationList`, `RowsAccessible`, `RowStyle`, `style_for`, `as_rows`, `_announce_selection`, `_on_focus`, `_row_at`, `_starts_of`, `_row_starts`, `_show_working`, `_hide_working`, `working` are used consistently across tasks.
