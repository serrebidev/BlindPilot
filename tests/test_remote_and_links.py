"""Remote Control, other machines, session links and conversation groups.

Remote Control is Claude Code's ``remote_control`` control request; other
machines are reached through Claude's ListAgents and SendMessage tools;
``blindpilot://open/<backend>/<id>`` reopens a conversation and does nothing
else; groups are BlindPilot's own, kept beside its names and hidden list.
Modelled on The Chat Place (TheIdeaPlace/AIChat).
"""

from __future__ import annotations

import pytest

import blindpilot_app as app
import remote_sessions as rs

LISTING = """This session is Laptop [aaaa]
  Desk build [1f2e3d]  ·  Remote Control  ·  idle
  Desk build [9a9a9a]  ·  Remote Control  ·  requires_action
  Here [abcd12]  ·  Claude Desktop session  ·  busy
not a row at all
"""


def test_listagents_rows_are_read_loosely_and_shared_names_get_their_ref():
    sessions = rs.parse_list(LISTING)

    assert [s.describe() for s in sessions] == ["Desk build, idle", "Desk build, needs you"]
    assert rs.addresses(sessions) == ["Desk build [1f2e3d]", "Desk build [9a9a9a]"]
    assert rs.latest_list([]) is None
    assert rs.latest_list(["old", LISTING])[0].ref == "1f2e3d"


def test_the_send_prompt_fences_the_words_and_quotes_the_address_safely():
    prompt = rs.send_prompt('Desk "build"', "  hostname?  ")

    assert "to set to \"Desk 'build'\"" in prompt
    assert prompt.endswith("<<<MESSAGE\nhostname?\nMESSAGE>>>")


def test_session_links_round_trip_and_refuse_anything_else():
    link = rs.session_link("claude", "a/b%c")
    assert link == "blindpilot://open/claude/a%2Fb%25c"
    assert rs.parse_session_link(link) == ("claude", "a/b%c")
    # Session ids never hold spaces or control characters.
    assert rs.parse_session_link("blindpilot://open/claude/a%20b") is None
    for bad in ("https://x", "blindpilot://run/claude/x", "blindpilot://open/claude/", ""):
        assert rs.parse_session_link(bad) is None
    assert rs.parse_session_link("blindpilot://open/claude/a%0Ab") is None


class _Session:
    def __init__(self, response):
        self.remote_control_on = False
        self.response = response
        self.asked: list = []

    def send_control(self, subtype, timeout=None, **fields):
        self.asked.append((subtype, fields))
        return self.response


def _worker(remote):
    worker = app.ClaudeWorker(
        "hi",
        None,
        ".",
        "bypassPermissions",
        on_session=lambda _s: None,
        on_started=lambda: None,
        on_activity=lambda *_a: None,
        on_complete=lambda _t: None,
        on_failed=lambda _t: None,
        on_done=lambda: None,
        remote_control=remote,
    )
    reports: list = []
    worker.on_remote = reports.append
    return worker, reports


def test_remote_control_is_asked_for_once_per_process_and_rejoins_the_same_session():
    answer = {
        "subtype": "success",
        "response": {"bridge_session_id": "br1", "session_url": "https://claude.ai/code/x"},
    }
    session = _Session(answer)
    worker, reports = _worker({"enabled": True, "name": "Build", "reattach": "br0"})

    worker._apply_remote_control(session)
    worker._apply_remote_control(session)

    assert session.asked == [
        (
            "remote_control",
            {
                "enabled": True,
                "name": "Build",
                "keep_session_on_exit": True,
                "reattach_session_id": "br0",
            },
        )
    ]
    assert session.remote_control_on
    assert reports == [answer["response"]]


def test_a_refused_remote_control_is_reported_and_off_is_never_sent_needlessly():
    session = _Session({"subtype": "error", "error": "Unsupported control request"})
    worker, reports = _worker({"enabled": True, "name": "", "reattach": ""})
    worker._apply_remote_control(session)
    assert reports == [{"error": "Unsupported control request"}]
    assert not session.remote_control_on

    quiet = _Session(None)
    worker, reports = _worker({"enabled": False, "name": "", "reattach": ""})
    worker._apply_remote_control(quiet)
    assert quiet.asked == [] and reports == []


def test_the_tab_remembers_where_its_remote_control_session_is(monkeypatch):
    saved: dict = {}
    monkeypatch.setattr(app, "_load_config", lambda: dict(saved))
    monkeypatch.setattr(app, "_save_config", lambda cfg: saved.update(cfg) or True)
    spoken: list[str] = []
    stub = type("PanelStub", (), {})()
    stub._announce = lambda text, urgent=False: spoken.append(text)
    stub.remote_bridge = stub.remote_url = ""
    stub._session_id = "sess"

    app.SessionPanel._on_remote(stub, {"bridge_session_id": "br", "session_url": "https://u"})
    app.SessionPanel._on_remote(stub, {"bridge_session_id": "br", "session_url": "https://u"})
    app.SessionPanel._on_remote(stub, {"error": "no"})
    app.SessionPanel._on_remote(stub, {"error": "no"})

    assert saved["remote_control"] == {"sess": {"bridge": "br", "url": "https://u"}}
    assert spoken[0].startswith("This session is on Remote Control.")
    assert spoken[1:] == ["Could not turn on Remote Control: no"]


def test_listagents_results_are_found_among_the_rows():
    from markdown_rows import Row

    stub = type("PanelStub", (), {})()
    stub._rows = [
        Row("tool", "Using ListAgents", "Using ListAgents", 1),
        Row("result", "first", "first", 1),
        Row("tool", "Running: ls", "Running: ls", 1),
        Row("result", "files", "files", 1),
        Row("tool", "Using ListAgents", "Using ListAgents", 2),
        Row("result", LISTING, LISTING, 2),
    ]
    assert app.SessionPanel.list_agents_results(stub) == ["first", LISTING]


def test_a_link_from_a_second_copy_is_handed_over_and_taken_once(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_links_dir", lambda: tmp_path / "links")
    assert app.take_handed_links() == []
    assert app.hand_over_link("blindpilot://open/claude/abc")

    assert app.take_handed_links() == ["blindpilot://open/claude/abc"]
    assert app.take_handed_links() == []


def test_a_link_only_ever_reopens_a_conversation(monkeypatch):
    spoken: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, *a, **k: spoken.append(text))
    stub = type("FrameStub", (), {})()
    stub._session_panels = list
    app.MainFrame.open_session_link(stub, "blindpilot://open/nosuch/abc")
    app.MainFrame.open_session_link(stub, "javascript:alert(1)")
    assert spoken == ["That link isn't one BlindPilot knows."] * 2

    opened: list = []
    stub._resume_history = opened.append
    app.MainFrame._open_linked(stub, None)
    assert spoken[-1].startswith("The conversation that link names was not found")
    assert opened == []


def test_groups_are_read_defensively_and_listed_alphabetically():
    cfg = {"conversation_groups": {"claude:a": ["Work", "home"], "codex:b": [], "x": "bad"}}
    groups = app.conversation_groups(cfg)
    assert groups == {"claude:a": ["Work", "home"]}
    assert app.group_names(groups) == ["home", "Work"]


def test_recent_conversations_filters_and_names_groups(frame, monkeypatch):
    wx = pytest.importorskip("wx")
    from session_history import HistoryEntry

    one = HistoryEntry("claude", "a", "Fix login", "", 2.0)
    two = HistoryEntry("claude", "b", "Write docs", "", 1.0)
    cfg = {"conversation_groups": {"claude:a": ["Work"]}}
    monkeypatch.setattr(app, "list_history", lambda *_a, **_k: [])
    monkeypatch.setattr(app, "_load_config", lambda: dict(cfg))
    wx_app = wx.GetApp()
    old = wx_app.GetAssertMode()
    wx_app.SetAssertMode(wx.APP_ASSERT_EXCEPTION)
    dialog = app.HistoryDialog(frame, backend="claude", cwd=".")
    try:
        dialog._loading = False
        dialog._entries = [one, two]
        dialog._refresh()
        assert dialog.group_picker.GetStrings() == ["All groups", "Work"]
        assert "group Work" in dialog.list_box.GetRows()[0].label

        dialog.group_picker.SetSelection(1)
        dialog._refresh()
        assert dialog._shown == [one]
    finally:
        dialog.Destroy()
        wx_app.SetAssertMode(old)
