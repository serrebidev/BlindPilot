"""Working across sessions: search them all, hand replies between them, branch.

Three things The Chat Place does that BlindPilot lacked. Find in All
Conversations (Ctrl+Shift+S) searches every past conversation's messages.
Send Response to Another Session (Ctrl+Shift+O) hands one agent's answer to
another, once or back and forth as an agent conversation. New Session can work
in a fresh git worktree, and remembers the folders it has opened.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import threading
import time

import pytest

import blindpilot_app as app
from session_history import HistoryEntry, HistoryTurn


def test_search_finds_matches_in_prompts_and_answers_without_case():
    turns = [
        HistoryTurn(prompt="Fix the LOGIN bug", response="Done."),
        HistoryTurn(prompt="thanks", response="The login form now validates."),
    ]

    found = app.search_turns(turns, "login")

    assert [(index, whole) for index, _snippet, whole in found] == [
        (0, "You: Fix the LOGIN bug"),
        (1, "Answer: The login form now validates."),
    ]


def test_a_long_match_is_cut_to_a_snippet_around_it():
    turn = HistoryTurn(response="x " * 100 + "needle" + " y" * 100)

    (_index, snippet, _whole) = app.search_turns([turn], "needle")[0]

    assert snippet.startswith("Answer: …") and snippet.endswith("…")
    assert "needle" in snippet and len(snippet) < 160


def test_an_empty_search_finds_nothing():
    assert app.search_turns([HistoryTurn(prompt="anything")], "") == []


def _entry(title, session_id):
    from session_history import HistoryEntry

    return HistoryEntry(
        backend=app.BACKEND_CLAUDE, session_id=session_id, title=title, path="", modified=0.0
    )


def test_search_all_conversations_collects_rows_and_reports_progress():
    entries = [_entry("First", "1"), _entry("Second", "2")]
    turns = {
        "1": [HistoryTurn(prompt="fix the login", response="done")],
        "2": [HistoryTurn(prompt="other", response="the login again")],
    }
    seen: list[tuple[int, str]] = []

    rows = app.search_all_conversations(
        entries,
        "login",
        on_progress=lambda done, title: seen.append((done, title)),
        load=lambda entry: turns[entry.session_id],
    )

    assert seen == [(0, "First"), (1, "Second")]
    assert [(entry.title, index) for _label, entry, index, _whole in rows] == [
        ("First", 0),
        ("Second", 0),
    ]
    assert rows[0][0].startswith("First, ") and "You: fix the login" in rows[0][0]
    assert rows[0][3] == "You: fix the login"


def test_search_all_conversations_stops_when_cancelled():
    entries = [_entry("First", "1"), _entry("Second", "2")]
    calls: list[str] = []

    def load(entry):
        calls.append(entry.session_id)
        return [HistoryTurn(prompt="login")]

    rows = app.search_all_conversations(
        entries, "login", is_cancelled=lambda: len(calls) >= 1, load=load
    )

    assert calls == ["1"]
    assert [entry.title for _l, entry, _i, _w in rows] == ["First"]


def _wait(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        app.wx.GetApp().Yield()
        time.sleep(0.01)
    assert predicate()


class _Ask:
    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def ShowModal(self):
        return app.wx.ID_OK

    def GetValue(self):
        return "needle"


class _Progress:
    made = []

    def __init__(self, _title, _message, maximum=100, parent=None, style=0):
        self.maximum = maximum
        self.style = style
        self.updates = []
        self.ranges = []
        self.cancelled = False
        self.cancel_after = None
        self.on_update = None
        self.destroyed = False
        type(self).made.append(self)

    def SetRange(self, total):
        self.ranges.append(total)

    def Update(self, done, message):
        self.updates.append((done, message))
        if self.on_update is not None:
            self.on_update()
        if self.cancel_after is not None and done >= self.cancel_after:
            self.cancelled = True
        return (not self.cancelled, False)

    def WasCancelled(self):
        return self.cancelled

    def Destroy(self):
        self.destroyed = True


class _ItemsDialog:
    made = []

    def __init__(self, _parent, items, title, **_kwargs):
        self.items = list(items)
        self.title = title
        type(self).made.append(self)

    def ShowModal(self):
        return app.wx.ID_CANCEL

    def Destroy(self):
        pass


@pytest.fixture
def search_ui(monkeypatch, wx_app):
    _Progress.made.clear()
    _ItemsDialog.made.clear()
    monkeypatch.setattr(app.wx, "TextEntryDialog", _Ask)
    monkeypatch.setattr(app.wx, "ProgressDialog", _Progress)
    monkeypatch.setattr(app, "ConversationItemsDialog", _ItemsDialog)
    spoken: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, *_a, **_k: spoken.append(text))
    frame = type("FrameStub", (), {})()
    frame.spoken = spoken
    frame._find_all_range = lambda *args: app.MainFrame._find_all_range(frame, *args)
    frame._find_all_progress_update = lambda *args: app.MainFrame._find_all_progress_update(
        frame, *args
    )
    frame._find_all_loaded = lambda *args: app.MainFrame._find_all_loaded(frame, *args)
    return frame


def _entries(count):
    return [
        HistoryEntry("claude", f"session-{index}", f"Title {index}", "", 10.0 - index)
        for index in range(count)
    ]


def test_all_conversation_search_returns_before_the_scan_finishes(search_ui, monkeypatch):
    entries = _entries(3)
    release = threading.Event()
    started = threading.Event()

    def list_history(*_args, **_kwargs):
        started.set()
        release.wait(5)
        return entries

    monkeypatch.setattr(app, "list_history", list_history)
    monkeypatch.setattr(
        app, "load_turns", lambda entry: [HistoryTurn(prompt=f"{entry.title} needle")]
    )

    began = time.monotonic()
    app.MainFrame._find_in_all_conversations(search_ui)
    assert time.monotonic() - began < 1
    assert started.wait(1), "the scan never started"
    progress = _Progress.made[-1]
    assert progress.style & app.wx.PD_CAN_ABORT
    assert not progress.destroyed
    assert _ItemsDialog.made == []

    release.set()
    _wait(lambda: bool(_ItemsDialog.made))
    assert progress.destroyed
    assert progress.ranges == [3]
    assert [done for done, _message in progress.updates] == [0, 1, 2]
    assert _ItemsDialog.made[0].title == "3 matches for needle"
    assert search_ui.spoken == []


def test_cancelling_the_search_stops_the_scan_and_says_so(search_ui, monkeypatch):
    entries = _entries(5)
    release = threading.Event()
    acknowledged = threading.Event()
    scanned: list[str] = []

    def list_history(*_args, **_kwargs):
        release.wait(5)
        return entries

    def load_turns(entry):
        scanned.append(entry.session_id)
        assert acknowledged.wait(3), "the progress update was never shown"
        acknowledged.clear()
        return [HistoryTurn(prompt="needle")]

    monkeypatch.setattr(app, "list_history", list_history)
    monkeypatch.setattr(app, "load_turns", load_turns)

    app.MainFrame._find_in_all_conversations(search_ui)
    progress = _Progress.made[-1]
    progress.on_update = acknowledged.set
    progress.cancel_after = 1
    release.set()

    _wait(lambda: progress.destroyed)
    assert progress.cancelled
    assert search_ui.spoken == ["Search cancelled"]
    assert _ItemsDialog.made == []
    assert scanned == ["session-0", "session-1"]


def test_a_stale_search_result_cannot_fill_the_current_list(search_ui):
    current_progress = _Progress("Find", "Searching", style=app.wx.PD_CAN_ABORT)
    old_progress = _Progress("Find", "Searching", style=app.wx.PD_CAN_ABORT)
    search_ui._find_all_serial = 2
    search_ui._find_all_progress = current_progress
    search_ui._find_all_cancel = threading.Event()
    entry = _entries(1)[0]
    results = [(app.ConversationItem("Old match", "Old match"), entry, 0)]

    app.MainFrame._find_all_loaded(
        search_ui, 1, old_progress, threading.Event(), "needle", results, ""
    )

    assert search_ui.spoken == []
    assert _ItemsDialog.made == []
    assert not old_progress.destroyed
    assert search_ui._find_all_progress is current_progress


def test_a_relayed_reply_names_its_speaker_and_cannot_run_a_command():
    message = app.relay_message("Codex (api)", "/clear everything\n")

    assert message == "Codex (api) replied:\n\n/clear everything"
    assert not message.startswith("/")


class _Panel:
    """Enough of a session for the relay: it records what it was sent."""

    def __init__(self, accepts: bool = True):
        self.sent: list[str] = []
        self.accepts = accepts
        self.on_turn_answer = None
        self.tab_title = ""
        self.cwd = "/work/api"

    def deliver(self, text: str) -> bool:
        self.sent.append(text)
        return self.accepts

    def selected_backend(self) -> str:
        return app.BACKEND_CLAUDE


@pytest.fixture
def frame(monkeypatch):
    spoken: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, *a, **k: spoken.append(text))
    stub = type("FrameStub", (), {})()
    stub.spoken = spoken
    stub._relay_speaker = lambda panel: app.MainFrame._relay_speaker(stub, panel)
    stub._end_relay = lambda reason: app.MainFrame._end_relay(stub, reason)
    stub._relay_preface = lambda relay, panel: app.MainFrame._relay_preface(stub, relay, panel)
    return stub


def _start(frame, left=2):
    first, second = _Panel(), _Panel()
    frame._relay = {"tabs": (first, second), "left": left, "count": 0, "last": "opening"}
    first.on_turn_answer = second.on_turn_answer = "set"
    return first, second


def test_each_answer_is_passed_to_the_other_session_until_the_limit(frame):
    first, second = _start(frame, left=1)

    app.MainFrame._relay_answer(frame, second, "Looks good")
    app.MainFrame._relay_answer(frame, first, "Merged")

    assert first.sent == ["Claude Code (api) replied:\n\nLooks good"]
    assert second.sent == []
    assert frame._relay is None
    assert first.on_turn_answer is None and second.on_turn_answer is None
    assert frame.spoken[-1] == "Agent conversation ended: 1 hand-offs, the limit."


def test_a_stopped_turn_ends_the_conversation(frame):
    first, second = _start(frame)

    app.MainFrame._relay_answer(frame, first, None)

    assert second.sent == []
    assert frame.spoken == ["Agent conversation ended: a turn was stopped or failed."]


def test_repeating_replies_end_the_conversation(frame):
    first, second = _start(frame)
    app.MainFrame._relay_answer(frame, first, "Agreed.")

    app.MainFrame._relay_answer(frame, second, "agreed. ")

    assert frame.spoken[-1] == "Agent conversation ended: the replies started repeating."


def test_a_session_that_cannot_take_the_reply_ends_it(frame):
    first, second = _start(frame)
    second.accepts = False

    app.MainFrame._relay_answer(frame, first, "Over to you")

    assert (
        frame.spoken[-1] == "Agent conversation ended: the other session could not take the reply."
    )


def test_three_agents_take_turns_and_each_is_told_its_role_once(frame):
    first, second, third = _Panel(), _Panel(), _Panel()
    for panel, place in ((first, "api"), (second, "web"), (third, "docs")):
        panel.cwd = f"/work/{place}"
    frame._relay = {
        "tabs": (first, second, third),
        "left": 5,
        "count": 0,
        "last": "opening",
        "roles": {id(second): "Review", id(third): "Write the docs"},
        "told": {id(second)},  # told with the opening message
    }

    app.MainFrame._relay_answer(frame, second, "Fix line 3")
    app.MainFrame._relay_answer(frame, third, "Documented")
    app.MainFrame._relay_answer(frame, first, "Fixed")
    app.MainFrame._relay_answer(frame, second, "Looks right")

    assert len(first.sent) == 1 and len(second.sent) == 1
    assert third.sent[0].startswith("You are Claude Code (docs) in an agent conversation with ")
    assert "Your role: Write the docs" in third.sent[0]
    assert third.sent[0].endswith("Claude Code (web) replied:\n\nFix line 3")
    assert "Your role" not in first.sent[0]
    # The second already knew its role, so its message is the reply alone.
    assert second.sent == ["Claude Code (api) replied:\n\nFixed"]
    # Told once: the third agent's second message is the reply alone.
    assert third.sent[1] == "Claude Code (web) replied:\n\nLooks right"
    assert frame._relay["count"] == 4


def test_a_reply_ending_in_done_ends_the_conversation(frame):
    first, second = _start(frame)

    app.MainFrame._relay_answer(frame, first, "All checks pass.\n\n**DONE**")

    assert second.sent == []
    assert frame.spoken[-1] == "Agent conversation ended: Claude Code (api) said DONE."
    assert not app.said_done("We are not DONE yet")


def test_recent_folders_are_newest_first_without_duplicates(monkeypatch):
    stored: dict = {}
    monkeypatch.setattr(app, "_load_config", lambda: dict(stored))
    monkeypatch.setattr(app, "_save_config", lambda cfg: stored.update(cfg) or True)

    app._remember_folder("C:/one")
    app._remember_folder("C:/two")
    app._remember_folder("C:/one")

    assert app._recent_folders() == ["C:/one", "C:/two"]


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "start")
    yield root
    # Git writes its objects read-only, which Windows will not delete.
    for folder, _dirs, names in os.walk(tmp_path):
        for name in names:
            os.chmod(os.path.join(folder, name), stat.S_IWRITE | stat.S_IREAD)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_a_worktree_is_made_beside_the_repository_on_a_new_branch(repo):
    path, why = app.create_worktree(str(repo), "feature/x")

    assert why == ""
    assert os.path.normcase(path) == os.path.normcase(str(repo.parent / "proj-feature-x"))
    assert (repo.parent / "proj-feature-x" / "a.txt").is_file()
    again, why = app.create_worktree(str(repo), "feature/x")
    assert again == "" and "already exists" in why


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_a_folder_outside_git_cannot_have_a_worktree(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))

    assert app.create_worktree(str(tmp_path), "x") == (
        "",
        "That folder is not in a git repository.",
    )
