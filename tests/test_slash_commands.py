"""What the slash-command picker offers, and for which backend.

The picker is the only place the commands are written down, so a command that
works everywhere has to be listed everywhere — a blind user cannot discover one
by noticing it in a menu they were not looking at.

Run from the project root:

    python -m pytest tests/ -q
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from agent_backends import (
    BACKEND_CLAUDE,
    BACKEND_CODEX,
    BACKEND_FREEBUFF,
    BACKEND_IDS,
    BACKEND_MUSE,
)
from blindpilot_app import SessionPanel, _slash_commands_for_backend
import blindpilot_app as app
import commandcode_backend
from doubles import Prompt, panel_stub
import pytest


@pytest.fixture(autouse=True)
def clear_slash_cache():
    app._slash_commands_cache.clear()
    yield
    app._slash_commands_cache.clear()


def _names(backend: str) -> list[str]:
    return [command.split()[0] for command, _description in _slash_commands_for_backend(backend)]


def _tab(backend: str, **overrides):
    """A stand-in for a session tab, for the parts of /status it reports.

    The report is read off a live panel, and building one needs a window; these
    are the fields it reads, and nothing else about the tab is involved.
    """
    fields = {
        "model": "",
        "effort": "",
        "_cli_model": "sonnet",
        "_cli_effort": "",
        "mode": "bypassPermissions",
        "cwd": "/work",
        "_session_id": None,
        "_session_backend": backend,
    }
    fields.update(overrides)
    state = SimpleNamespace(**fields)
    state.selected_backend = lambda: backend
    return state


def test_status_is_offered_for_every_backend(monkeypatch):
    """/status is BlindPilot's own, and every backend can answer it.

    None of them answers it themselves in the headless mode they are driven in
    — Claude Code's /status is interactive-only, and the other three have no
    status command at all — so BlindPilot asks each one the way it can answer
    and reports all four the same way.
    """
    monkeypatch.setattr(commandcode_backend, "commandcode_agent_commands", lambda _cwd: [])
    for backend in BACKEND_IDS:
        assert "/status" in _names(backend), backend


@pytest.mark.parametrize("command,expected", [("/history", "history"), ("/usage", "status")])
def test_freebuff_history_and_usage_open_accessible_frontend_views(monkeypatch, command, expected):
    opened = []
    panel = panel_stub(
        prompt=Prompt(command),
        selected_backend=lambda: BACKEND_FREEBUFF,
        open_status_dialog=lambda: opened.append("status"),
        _attachments=[],
        _build_send_text=lambda text: text,
        _session_backend=BACKEND_FREEBUFF,
        _session_id=None,
        _on_title=lambda *_: pytest.fail("command became a model prompt"),
        _add_your_message=lambda *_a, **_k: pytest.fail("command became a model prompt"),
        _launch_turn=lambda *_: pytest.fail("terminal-only command was sent as an agent turn"),
    )
    monkeypatch.setattr(
        app.wx,
        "GetTopLevelParent",
        lambda _: SimpleNamespace(_open_history=lambda: opened.append("history")),
    )
    monkeypatch.setattr(app.wx, "CallAfter", lambda fn, *a: fn(*a))
    SessionPanel._on_send(panel)
    assert opened == [expected]
    assert panel.prompt.GetValue() == ""


def test_status_is_listed_once_for_claude():
    """It used to be Claude's alone; moving it must not leave a second copy."""
    assert _names(BACKEND_CLAUDE).count("/status") == 1


def test_status_is_marked_as_handled_by_blindpilot(monkeypatch):
    """The tag is how the picker says which commands never reach the provider."""
    monkeypatch.setattr(commandcode_backend, "commandcode_agent_commands", lambda _cwd: [])
    for backend in BACKEND_IDS:
        descriptions = [
            description
            for command, description in _slash_commands_for_backend(backend)
            if command == "/status"
        ]
        assert descriptions and all(text.endswith("[BlindPilot]") for text in descriptions)


def test_status_reports_what_the_next_message_will_do():
    """The provider block says who is signed in; this says what the tab is set to."""
    lines = SessionPanel._session_status_lines(_tab(BACKEND_CLAUDE))
    assert "Model: sonnet" in lines
    assert "Effort: CLI default" in lines
    assert "Permission mode: Bypass permissions" in lines
    assert "Folder: /work" in lines
    assert "Conversation: new, nothing sent yet" in lines


def test_status_says_a_permission_mode_the_backend_does_not_take_is_not_in_use():
    """FreeBuff's picker is disabled, so naming the remembered mode would mislead."""
    lines = SessionPanel._session_status_lines(_tab(BACKEND_FREEBUFF))
    assert "Permission mode: not offered by this backend" in lines


def test_muse_lists_the_skills_its_cli_offers(monkeypatch):
    """The picker shows whatever this directory's Muse has: bundled, user,
    and project skills, invoked the way the TUI invokes them."""
    import blindpilot_app

    monkeypatch.setattr(blindpilot_app, "muse_skills", lambda cwd: [("doctor", "Diagnose it.")])

    commands = dict(_slash_commands_for_backend(BACKEND_MUSE, "/work"))

    assert commands["/doctor"] == "Diagnose it."
    assert "/status" in commands


def test_status_says_a_conversation_the_backend_switch_has_already_ended():
    """Switching backend starts a new conversation on the next message."""
    tab = _tab(BACKEND_CODEX, _session_id="abc")
    tab._session_backend = BACKEND_CLAUDE
    lines = SessionPanel._session_status_lines(tab)
    assert "Conversation: new, the backend changed since the last message" in lines

    tab._session_backend = BACKEND_CODEX
    assert "Conversation: continuing" in SessionPanel._session_status_lines(tab)


def _wait(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        app.wx.GetApp().Yield()
        time.sleep(0.01)
    assert predicate()


class _SlashDialog:
    made = []
    actions = []

    def __init__(self, _parent, message, labels):
        self.message = message
        self.labels = list(labels)
        self.selection = 0 if labels else -1
        type(self).made.append(self)

    def SetLabels(self, labels):
        selected = self.labels[self.selection] if 0 <= self.selection < len(self.labels) else ""
        self.labels = list(labels)
        if selected in self.labels:
            self.selection = self.labels.index(selected)
        elif self.labels:
            self.selection = 0
        else:
            self.selection = -1

    def ShowModal(self):
        action = type(self).actions.pop(0) if type(self).actions else app.wx.ID_CANCEL
        if action == "refresh":
            self.selection = self.labels.index(app._SLASH_COMMANDS_REFRESH)
            return app.wx.ID_OK
        if callable(action):
            return action(self)
        return action

    def GetSelection(self):
        return self.selection

    def Destroy(self):
        pass


@pytest.fixture
def slash_dialog(monkeypatch):
    _SlashDialog.made.clear()
    _SlashDialog.actions.clear()
    monkeypatch.setattr(app, "SlashCommandDialog", _SlashDialog)
    yield _SlashDialog
    _SlashDialog.made.clear()
    _SlashDialog.actions.clear()


class _SlashUI:
    def __init__(self, cwd, backend=BACKEND_CLAUDE):
        self.cwd = cwd
        self.backend = backend
        self.spoken = []
        self.prompt = SimpleNamespace(
            SetFocus=lambda: None,
            ChangeValue=lambda _value: None,
            SetInsertionPointEnd=lambda: None,
        )
        self._prompt_text = ""
        self._slash_commands_serial = 0
        self._slash_dialog = None

    def _announce(self, text, urgent=False):
        self.spoken.append(text)

    def selected_backend(self):
        return self.backend

    def _pick_slash_command(self, refresh=False):
        SessionPanel._pick_slash_command(self, refresh)

    def _show_slash_commands(self, *args, **kwargs):
        SessionPanel._show_slash_commands(self, *args, **kwargs)

    def _load_slash_commands(self, *args, **kwargs):
        SessionPanel._load_slash_commands(self, *args, **kwargs)

    def _slash_commands_loaded(self, *args, **kwargs):
        SessionPanel._slash_commands_loaded(self, *args, **kwargs)


def test_the_picker_opens_before_the_backend_probe_finishes(
    tmp_path, monkeypatch, wx_app, slash_dialog
):
    cwd = str(tmp_path)
    started = threading.Event()
    release = threading.Event()
    found = [("/from-backend", "From the backend")]

    def slow_probe(_backend, _cwd=None):
        started.set()
        assert release.wait(5)
        return found

    monkeypatch.setattr(app, "_slash_commands_for_backend", slow_probe)
    ui = _SlashUI(cwd)

    began = time.monotonic()
    ui._pick_slash_command()

    assert time.monotonic() - began < 1
    assert started.wait(1), "the probe never started"
    assert len(slash_dialog.made) == 1
    assert any("/status" in label for label in slash_dialog.made[0].labels)
    assert app.cached_slash_commands(BACKEND_CLAUDE, cwd) is None

    release.set()
    _wait(lambda: app.cached_slash_commands(BACKEND_CLAUDE, cwd) == found)


def test_an_open_picker_fills_with_the_worker_result(tmp_path, monkeypatch, wx_app, slash_dialog):
    cwd = str(tmp_path)
    started = threading.Event()
    release = threading.Event()
    found = [("/from-backend", "From the backend")]

    def slow_probe(_backend, _cwd=None):
        started.set()
        assert release.wait(5)
        return found

    def wait_for_fill(dialog):
        assert started.wait(1)
        dialog.opened_before_result = not release.is_set()
        release.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            app.wx.GetApp().Yield()
            if any("From the backend" in label for label in dialog.labels):
                break
            time.sleep(0.01)
        return app.wx.ID_CANCEL

    monkeypatch.setattr(app, "_slash_commands_for_backend", slow_probe)
    slash_dialog.actions.append(wait_for_fill)
    ui = _SlashUI(cwd)

    ui._pick_slash_command()

    dialog = slash_dialog.made[0]
    assert dialog.opened_before_result
    assert any("From the backend" in label for label in dialog.labels)
    assert any(app._SLASH_COMMANDS_REFRESH in label for label in dialog.labels)


def test_a_warm_backend_and_folder_opens_without_probing(
    tmp_path, monkeypatch, wx_app, slash_dialog
):
    cwd = str(tmp_path)
    found = [("/warm", "Warm result")]
    app.remember_slash_commands(BACKEND_CLAUDE, cwd, found)
    monkeypatch.setattr(
        app,
        "_slash_commands_for_backend",
        lambda *_args, **_kwargs: pytest.fail("the warm list was discarded"),
    )
    ui = _SlashUI(cwd)

    ui._pick_slash_command()

    assert len(slash_dialog.made) == 1
    assert any("Warm result" in label for label in slash_dialog.made[0].labels)
    assert app.cached_slash_commands(BACKEND_CLAUDE, cwd) == found


def test_warm_lists_belong_to_their_own_backend_and_folder(tmp_path):
    first = (str(tmp_path / "first"), [("/first", "First")])
    second = (str(tmp_path / "second"), [("/second", "Second")])
    app.remember_slash_commands(BACKEND_CLAUDE, first[0], first[1])
    app.remember_slash_commands(BACKEND_CODEX, first[0], second[1])

    assert app.cached_slash_commands(BACKEND_CLAUDE, first[0]) == first[1]
    assert app.cached_slash_commands(BACKEND_CODEX, first[0]) == second[1]
    assert app.cached_slash_commands(BACKEND_CODEX, second[0]) is None

    app.forget_slash_commands(BACKEND_CLAUDE, first[0])

    assert app.cached_slash_commands(BACKEND_CLAUDE, first[0]) is None
    assert app.cached_slash_commands(BACKEND_CODEX, first[0]) == second[1]


def test_refresh_discards_the_warm_list_and_asks_the_backend_again(
    tmp_path, monkeypatch, wx_app, slash_dialog
):
    cwd = str(tmp_path)
    app.remember_slash_commands(BACKEND_CLAUDE, cwd, [("/old", "Old result")])
    fresh = [("/new", "New result")]
    probes = []

    def probe(_backend, _cwd=None):
        probes.append(True)
        return fresh

    monkeypatch.setattr(app, "_slash_commands_for_backend", probe)
    slash_dialog.actions.extend(["refresh", app.wx.ID_CANCEL])
    ui = _SlashUI(cwd)

    ui._pick_slash_command()

    assert len(slash_dialog.made) == 2
    _wait(lambda: app.cached_slash_commands(BACKEND_CLAUDE, cwd) == fresh)
    assert probes == [True]
    assert not any("Old result" in label for label in slash_dialog.made[1].labels)


def test_a_stale_result_does_not_fill_the_current_picker_or_cache(tmp_path, wx_app, slash_dialog):
    cwd = str(tmp_path)
    dialog = _SlashDialog(None, "Choose", ["unchanged"])
    ui = _SlashUI(cwd)
    ui._slash_commands_serial = 2
    ui._slash_dialog = dialog

    ui._slash_commands_loaded(1, BACKEND_CLAUDE, cwd, dialog, [], [("/old", "Old")], "")

    assert dialog.labels == ["unchanged"]
    assert app.cached_slash_commands(BACKEND_CLAUDE, cwd) is None
    assert ui.spoken == []


def test_a_probe_failure_is_announced_without_replacing_the_list(tmp_path, wx_app, slash_dialog):
    cwd = str(tmp_path)
    dialog = _SlashDialog(None, "Choose", ["unchanged"])
    ui = _SlashUI(cwd)
    ui._slash_commands_serial = 1
    ui._slash_dialog = dialog

    ui._slash_commands_loaded(1, BACKEND_CLAUDE, cwd, dialog, [], [], "backend probe failed")

    assert any("backend probe failed" in text for text in ui.spoken)
    assert dialog.labels == ["unchanged"]
    assert app.cached_slash_commands(BACKEND_CLAUDE, cwd) is None
