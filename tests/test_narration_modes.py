"""Choosing how much of a run is read out.

Every tool call, every result and every subagent's running commentary was
spoken, in order, with `interrupt=False`. On a short turn that is exactly
right. On a fan-out it is minutes of backlog, and the screen reader's queue is
not something BlindPilot can measure, shorten, or pop from — it can only purge
it wholesale, which would silence other applications too.

So this is a choice rather than a cleverness. Follow everything is the default
and is what BlindPilot has always done. Keep up speaks what the turn is
*saying* — your message, the answer, notices, errors — and leaves the
step-by-step in the list to be read.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app


def _panel(monkeypatch, mode):
    """A panel stub that records what it would speak."""
    monkeypatch.setattr(app.SETTINGS, "narration", mode)
    monkeypatch.setattr(app.SETTINGS, "live_rows", True)
    monkeypatch.setattr(app.SETTINGS, "speak_live", True)
    monkeypatch.setattr(app.SETTINGS, "show_thinking", True)

    panel = type("PanelStub", (), {})()
    panel.spoken: list[str] = []
    panel.status: list[str] = []
    panel._rows = []
    panel._response_count = 1
    panel._stream_response = 1
    panel._streamed_assistant = ""
    panel._stopping = False
    panel._assistant_narrated_this_turn = False
    panel._session_backend = app.BACKEND_CLAUDE
    panel._set_status = panel.status.append
    panel._refresh_list = lambda: None
    panel._begin_stream_response = lambda: 1
    # The real one, minus the wx parent lookup a stub has no way to satisfy.
    panel._say = lambda text, kind="assistant": app.SessionPanel._say(panel, text, kind)
    panel.GetParent = lambda: None
    panel.announce_calls = panel.spoken
    return panel


@pytest.fixture(autouse=True)
def _capture(monkeypatch):
    said: list[str] = []
    monkeypatch.setattr(app, "announce", said.append)
    return said


# ----- the setting -----
def test_following_everything_is_the_default(monkeypatch):
    """Nobody's narration goes quiet because of an upgrade they did not ask for."""
    monkeypatch.setattr(app, "_load_config", dict)

    assert app._Settings().narration == app.NARRATION_EVERYTHING


def test_a_mode_this_version_does_not_know_falls_back(monkeypatch):
    monkeypatch.setattr(app, "_load_config", lambda: {"narration": "interpretive dance"})

    assert app._Settings().narration == app.NARRATION_EVERYTHING


def test_the_choice_is_saved(monkeypatch):
    written: list[dict] = []
    monkeypatch.setattr(app, "_load_config", lambda: {"backend": "codex"})
    monkeypatch.setattr(app, "_save_config", written.append)
    settings = app._Settings()
    settings.narration = app.NARRATION_KEEP_UP

    settings.save()

    assert written[-1]["narration"] == app.NARRATION_KEEP_UP
    assert written[-1]["backend"] == "codex"


# ----- what each mode speaks -----
FILTERED = ["tool", "result", "subagent"]
ALWAYS = ["assistant", "notice"]


@pytest.mark.parametrize("kind", FILTERED + ALWAYS)
def test_following_everything_speaks_every_kind(monkeypatch, _capture, kind):
    panel = _panel(monkeypatch, app.NARRATION_EVERYTHING)

    app.SessionPanel._say(panel, "a line", kind)

    assert _capture == ["a line"], f"{kind} was not spoken in Follow everything"


@pytest.mark.parametrize("kind", FILTERED)
def test_keeping_up_leaves_the_step_by_step_in_the_list(monkeypatch, _capture, kind):
    """Skipped, not lost: every one of these is still a row to read."""
    panel = _panel(monkeypatch, app.NARRATION_KEEP_UP)

    app.SessionPanel._say(panel, "a line", kind)

    assert _capture == [], f"{kind} was spoken in Keep up"
    assert panel.status, "and it should still reach the status bar"


@pytest.mark.parametrize("kind", ALWAYS)
def test_keeping_up_still_speaks_what_the_turn_is_saying(monkeypatch, _capture, kind):
    panel = _panel(monkeypatch, app.NARRATION_KEEP_UP)

    app.SessionPanel._say(panel, "a line", kind)

    assert _capture == ["a line"], f"{kind} was silenced in Keep up"


def test_a_notice_is_how_blindpilot_speaks_for_itself(monkeypatch, _capture):
    """ "Waiting for 3 background agents" is not tool narration, and muting it
    with the tool narration would lose the one line that explains a long wait."""
    panel = _panel(monkeypatch, app.NARRATION_KEEP_UP)

    app.SessionPanel._say(panel, "Waiting for 3 background agents to finish.", "notice")

    assert _capture == ["Waiting for 3 background agents to finish."]


# ----- the rows are never affected -----
@pytest.mark.parametrize("mode", [app.NARRATION_EVERYTHING, app.NARRATION_KEEP_UP])
def test_every_step_is_still_a_row_in_both_modes(monkeypatch, mode):
    panel = _panel(monkeypatch, mode)

    app.SessionPanel._on_activity(panel, "tool", "Reading config.json")
    app.SessionPanel._on_activity(panel, "result", "a hundred lines of output")

    kinds = [row.kind for row in panel._rows]
    assert "tool" in kinds and "result" in kinds, kinds


def test_nothing_is_narrated_after_stop_was_pressed(monkeypatch, _capture):
    """Queued narration kept talking after "Stopping", which sounds exactly
    like a stop that did not work."""
    panel = _panel(monkeypatch, app.NARRATION_EVERYTHING)
    panel._stopping = True

    app.SessionPanel._on_activity(panel, "tool", "Reading config.json")

    assert _capture == []


# ----- the menu -----
wx = pytest.importorskip("wx")


@pytest.fixture(scope="module")
def wx_app():
    try:
        return wx.App(False)
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"no display for wxPython: {exc}")


@pytest.fixture
def frame(wx_app):
    window = wx.Frame(None)
    try:
        yield window
    finally:
        window.Destroy()


def test_the_menu_offers_both_modes_as_an_exclusive_choice(frame):
    menu = app.MainFrame._build_narration_menu(frame)
    try:
        items = menu.GetMenuItems()
        assert [item.GetKind() for item in items] == [wx.ITEM_RADIO] * 2
        labels = " ".join(item.GetItemLabelText().lower() for item in items)
        assert "everything" in labels and "keep up" in labels
    finally:
        menu.Destroy()


def test_the_menu_opens_on_the_mode_actually_in_use(frame, monkeypatch):
    monkeypatch.setattr(app.SETTINGS, "narration", app.NARRATION_KEEP_UP)

    menu = app.MainFrame._build_narration_menu(frame)
    try:
        chosen = [mode for mode, item in frame._narration_items.items() if item.IsChecked()]
        assert chosen == [app.NARRATION_KEEP_UP]
    finally:
        menu.Destroy()
