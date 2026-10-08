"""A long turn's progress is the last row of the conversation, kept current.

muse.ai's working steps carry no text, so the worker reports their count as a
"step". It is one row at the bottom, changed in place each time: rebuilding
the list would put the selection back and NVDA would re-read the row being
read every few seconds. The turn ending removes it.
"""

from __future__ import annotations

import blindpilot_app as app
from doubles import panel_stub
from markdown_rows import Row


class _ListBox:
    def __init__(self):
        self.rows = []
        self.rebuilds = 0
        self.selection_events = 0

    def Set(self, rows):
        self.rows = list(rows)
        self.rebuilds += 1

    def AppendItems(self, rows):
        self.rows.extend(rows)

    def ReplaceLast(self, row):
        self.rows[-1] = row

    def SetSelection(self, index):
        self.selection_events += 1

    def GetSelection(self):
        return 0

    def GetCount(self):
        return len(self.rows)


def _panel(monkeypatch):
    monkeypatch.setattr(app.SETTINGS, "text_view", False)
    said = Row(kind="prose", label="Working on it.", payload="Working on it.", response_number=1)
    panel = panel_stub(_rows=[said], _displayed=[], _search_term="")
    panel.responses = _ListBox()
    panel._refresh_list = lambda: app.SessionPanel._refresh_list(panel)
    panel._show_step_row = lambda text: app.SessionPanel._show_step_row(panel, text)
    panel._refresh_list()
    return panel


def test_progress_updates_one_row_at_the_bottom_in_place(monkeypatch):
    panel = _panel(monkeypatch)

    panel._show_step_row("muse.ai is working: 1 step so far")
    panel._show_step_row("muse.ai is working: 2 steps so far")
    panel._show_step_row("muse.ai is working: 3 steps so far")

    labels = [row.label for row in panel.responses.rows]
    assert labels == ["Working on it.", "muse.ai is working: 3 steps so far"]
    assert panel.responses.rebuilds == 0
    assert panel.responses.selection_events == 0


def test_the_turn_ending_removes_it(monkeypatch):
    panel = _panel(monkeypatch)
    panel._show_step_row("muse.ai is working: 4 steps so far")

    app.SessionPanel._drop_step_rows(panel)

    assert [row.label for row in panel._rows] == ["Working on it."]


def test_the_real_list_replaces_its_last_row_without_moving_the_reader(frame):
    from conversation_list import make_conversation_list

    rows = [Row(kind="prose", label=t, payload=t, response_number=1) for t in ("One", "Two")]
    control = make_conversation_list(frame)
    control.AppendItems(rows)
    control.SetSelection(0)

    control.ReplaceLast(Row(kind="step", label="Step 5", payload="Step 5", response_number=1))

    assert [row.label for row in control.GetRows()] == ["One", "Step 5"]
    assert control.GetSelection() == 0


def test_an_idle_muse_ai_tab_follows_the_chat_again_when_it_moves():
    """muse.ai often answers and keeps working; an open tab notices."""
    followed, rescheduled = [], []
    panel = panel_stub(_session_id="chat-9", _session_backend=app.BACKEND_MUSEAI)
    panel._run_in_progress = lambda: False
    panel._follow_museai = lambda: followed.append(True)
    panel._watch_museai_later = lambda seen=None: rescheduled.append(seen)

    panel._museai_seen = None
    app.SessionPanel._museai_checked(panel, "chat-9", 40)  # first look: remember
    assert panel._museai_seen == 40 and not followed

    app.SessionPanel._museai_checked(panel, "chat-9", 40)  # nothing new
    assert not followed

    app.SessionPanel._museai_checked(panel, "chat-9", 44)  # muse.ai moved on
    assert followed == [True] and panel._museai_seen == 44
