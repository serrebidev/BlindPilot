"""The window side of going back to a Hermes conversation.

Kept apart from the protocol tests because these need wxPython: the label a
screen reader reads, and the dialog's own filtering, are the whole feature from
where the user sits. A machine without a display skips them rather than failing
the suite.
"""

from __future__ import annotations

import threading
import time

import pytest

wx = pytest.importorskip("wx")

import blindpilot_app  # noqa: E402
from blindpilot_app import HermesSessionsDialog, hermes_session_label  # noqa: E402


CATALOG = [
    {
        "id": "20260902_142652_5d15e2",
        "title": "Working on the gateway",
        "preview": "start",
        "message_count": 152,
        "source": "tui",
        "started_at": 1788352014.0,
    },
    {
        "id": "20260902_060036_2baac2",
        "title": "Healthcheck",
        "preview": "OK",
        "message_count": 1,
        "source": "cli",
        "started_at": 1788318036.0,
    },
    {
        "id": "20260901_231500_aa11bb",
        "title": "Telegram thread",
        "preview": "hi",
        "message_count": 8,
        "source": "telegram",
        "started_at": 1788300000.0,
    },
]
LIVE = {"20260902_142652_5d15e2"}


def _install_catalog(monkeypatch, sessions=CATALOG, live=LIVE, error=""):
    monkeypatch.setattr(
        blindpilot_app,
        "hermes_session_catalog",
        lambda *a, **k: (list(sessions), set(live), error),
        raising=True,
    )


def _wait(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        wx.GetApp().Yield()
        time.sleep(0.01)
    assert predicate()


def _loaded(dialog):
    _wait(lambda: not dialog._loading and not dialog.summary.GetLabel().startswith("Asking Hermes"))


# -- the spoken label -----------------------------------------------------


def test_the_label_says_running_first_for_a_live_conversation():
    """Whether it is running changes what opening it does, so it leads."""
    label = hermes_session_label(CATALOG[0], live=True)

    assert label.startswith("Running now — Working on the gateway")
    assert "152 messages" in label
    assert "Hermes TUI" in label


def test_the_label_names_where_a_conversation_was_started():
    """A conversation from a terminal on the server is not the same thing."""
    assert "terminal" in hermes_session_label(CATALOG[1], live=False)
    assert "Telegram" in hermes_session_label(CATALOG[2], live=False)


def test_the_label_never_starts_with_running_when_it_is_not():
    assert not hermes_session_label(CATALOG[1], live=False).startswith("Running now")


def test_a_conversation_with_no_title_falls_back_to_its_first_words():
    label = hermes_session_label({"id": "x", "title": "", "preview": "fix the build"}, live=False)

    assert label.startswith("fix the build")


def test_one_message_is_singular():
    assert "1 message" in hermes_session_label(CATALOG[1], live=False)
    assert "1 messages" not in hermes_session_label(CATALOG[1], live=False)


# -- the dialog -----------------------------------------------------------


def test_the_dialog_lists_every_surface_and_marks_the_live_one(monkeypatch, frame):
    _install_catalog(monkeypatch)
    dialog = HermesSessionsDialog(frame, cwd=".")
    _loaded(dialog)
    try:
        labels = [row.label for row in dialog.list_box.GetRows()]
        assert len(labels) == 3
        assert labels[0].startswith("Running now")
        # The point of the dialog: a CLI conversation on the far machine is
        # reachable, which Recent Conversations could never show in remote mode.
        assert any("terminal" in label for label in labels)
        assert "3 conversations, 1 running now" in dialog.summary.GetLabel()
    finally:
        dialog.Destroy()


def test_running_only_narrows_the_list_to_what_can_be_attached(monkeypatch, frame):
    _install_catalog(monkeypatch)
    dialog = HermesSessionsDialog(frame, cwd=".")
    _loaded(dialog)
    try:
        dialog.running_only.SetValue(True)
        dialog._refresh()

        labels = [row.label for row in dialog.list_box.GetRows()]
        assert len(labels) == 1
        assert labels[0].startswith("Running now")
    finally:
        dialog.Destroy()


def test_the_filter_matches_the_first_message_too(monkeypatch, frame):
    _install_catalog(monkeypatch)
    dialog = HermesSessionsDialog(frame, cwd=".")
    _loaded(dialog)
    try:
        dialog.filter_box.SetValue("healthcheck")
        dialog._refresh()
        assert dialog.list_box.GetCount() == 1

        # Matching the preview matters: a conversation whose title is a bare
        # greeting is found by what it was actually about.
        dialog.filter_box.SetValue("start")
        dialog._refresh()
        assert dialog.list_box.GetCount() == 1
    finally:
        dialog.Destroy()


def test_choosing_a_live_conversation_reports_that_it_is_attaching(monkeypatch, frame):
    """The window has to know, because attaching takes over the event stream."""
    _install_catalog(monkeypatch)
    dialog = HermesSessionsDialog(frame, cwd=".")
    _loaded(dialog)
    try:
        # A dialog built without ShowModal cannot EndModal; the repo's other
        # dialog tests capture the call the same way.
        ended: list[int] = []
        dialog.EndModal = ended.append
        dialog.list_box.SetSelection(0)
        dialog._accept()

        assert ended == [wx.ID_OK]
        assert dialog.entry is not None
        assert dialog.entry["id"] == "20260902_142652_5d15e2"
        assert dialog.attaching is True
    finally:
        dialog.Destroy()


def test_choosing_a_finished_conversation_is_not_attaching(monkeypatch, frame):
    _install_catalog(monkeypatch)
    dialog = HermesSessionsDialog(frame, cwd=".")
    _loaded(dialog)
    try:
        ended: list[int] = []
        dialog.EndModal = ended.append
        dialog.list_box.SetSelection(1)
        dialog._accept()

        assert ended == [wx.ID_OK]
        assert dialog.entry is not None
        assert dialog.entry["id"] == "20260902_060036_2baac2"
        assert dialog.attaching is False
    finally:
        dialog.Destroy()


def test_a_failure_is_spoken_and_leaves_no_openable_row(monkeypatch, frame):
    """A blind user cannot read a console to find out why the list is empty."""
    spoken: list[str] = []
    monkeypatch.setattr(blindpilot_app, "announce", spoken.append, raising=True)
    _install_catalog(monkeypatch, sessions=[], live=set(), error="Nothing is listening at ws://x")

    dialog = HermesSessionsDialog(frame, cwd=".")
    _loaded(dialog)
    try:
        assert dialog.list_box.GetCount() == 0
        assert "Nothing is listening" in dialog.summary.GetLabel()
        assert any("Nothing is listening" in message for message in spoken)
        open_button = dialog.FindWindowById(wx.ID_OK)
        assert open_button is not None and open_button.IsEnabled() is False
    finally:
        dialog.Destroy()


def test_an_empty_catalog_says_so_rather_than_looking_broken(monkeypatch, frame):
    _install_catalog(monkeypatch, sessions=[], live=set())
    dialog = HermesSessionsDialog(frame, cwd=".")
    _loaded(dialog)
    try:
        assert "no conversations" in dialog.summary.GetLabel()
    finally:
        dialog.Destroy()


def test_the_dialog_opens_before_hermes_answers(monkeypatch, frame):
    """Opening the list must not wait for a gateway on the other end."""
    started = threading.Event()
    release = threading.Event()
    spoken: list[str] = []
    monkeypatch.setattr(blindpilot_app, "announce", lambda text, **_k: spoken.append(text))

    def slow_catalog(*_a, **_k):
        started.set()
        assert release.wait(5)
        return list(CATALOG), set(LIVE), ""

    monkeypatch.setattr(blindpilot_app, "hermes_session_catalog", slow_catalog)
    began = time.monotonic()
    dialog = HermesSessionsDialog(frame, cwd=".")
    try:
        assert time.monotonic() - began < 1
        assert started.wait(1), "Hermes was never asked for its conversations"
        assert dialog.list_box.GetCount() == 0
        assert "Asking Hermes" in dialog.summary.GetLabel()
        assert any("Asking Hermes" in message for message in spoken)

        release.set()
        _wait(lambda: dialog.list_box.GetCount() == len(CATALOG))
        assert dialog.FindWindow(wx.ID_OK).IsEnabled()
        assert any("3 conversations" in message for message in spoken)
    finally:
        release.set()
        dialog.Destroy()


def test_a_refresh_returns_at_once_and_says_refreshed_when_the_answer_arrives(monkeypatch, frame):
    """F5 used to wait for the gateway; the spoken success must wait for the answer."""
    calls: list[int] = []
    started = threading.Event()
    release = threading.Event()
    spoken: list[str] = []
    monkeypatch.setattr(blindpilot_app, "announce", lambda text, **_k: spoken.append(text))

    def catalog(*_a, **_k):
        calls.append(1)
        if len(calls) == 1:
            return list(CATALOG), set(LIVE), ""
        started.set()
        assert release.wait(5)
        return list(CATALOG), set(LIVE), ""

    monkeypatch.setattr(blindpilot_app, "hermes_session_catalog", catalog)
    dialog = HermesSessionsDialog(frame, cwd=".")
    _loaded(dialog)
    spoken.clear()
    try:
        began = time.monotonic()
        dialog._reload(refreshing=True)
        assert time.monotonic() - began < 1
        assert started.wait(1), "the refresh never asked Hermes"
        assert "Refreshed" not in spoken, "success was announced before the answer"

        release.set()
        _wait(lambda: "Refreshed" in spoken)
        assert "3 conversations, 1 running now" in dialog.summary.GetLabel()
    finally:
        release.set()
        dialog.Destroy()


def test_a_failed_refresh_speaks_the_error_instead_of_refreshed(monkeypatch, frame):
    calls: list[int] = []
    spoken: list[str] = []
    monkeypatch.setattr(blindpilot_app, "announce", lambda text, **_k: spoken.append(text))

    def catalog(*_a, **_k):
        calls.append(1)
        if len(calls) == 1:
            return list(CATALOG), set(LIVE), ""
        return [], set(), "Gateway went away"

    monkeypatch.setattr(blindpilot_app, "hermes_session_catalog", catalog)
    dialog = HermesSessionsDialog(frame, cwd=".")
    _loaded(dialog)
    spoken.clear()
    try:
        dialog._reload(refreshing=True)
        _wait(lambda: any("Gateway went away" in message for message in spoken))

        assert "Refreshed" not in spoken
        assert dialog.list_box.GetCount() == 0
        assert not dialog.FindWindow(wx.ID_OK).IsEnabled()
    finally:
        dialog.Destroy()


def test_a_catalog_exception_is_spoken_as_an_error(monkeypatch, frame):
    spoken: list[str] = []
    monkeypatch.setattr(blindpilot_app, "announce", lambda text, **_k: spoken.append(text))

    def catalog(*_a, **_k):
        raise OSError("socket closed")

    monkeypatch.setattr(blindpilot_app, "hermes_session_catalog", catalog)
    dialog = HermesSessionsDialog(frame, cwd=".")
    try:
        _wait(lambda: any("socket closed" in message for message in spoken))
        assert dialog.list_box.GetCount() == 0
        assert "socket closed" in dialog.summary.GetLabel()
    finally:
        dialog.Destroy()


def test_a_stale_result_does_not_fill_the_current_list(monkeypatch, frame):
    _install_catalog(monkeypatch)
    dialog = HermesSessionsDialog(frame, cwd=".")
    _loaded(dialog)
    before = [row.label for row in dialog.list_box.GetRows()]
    spoken: list[str] = []
    monkeypatch.setattr(blindpilot_app, "announce", lambda text, **_k: spoken.append(text))
    try:
        dialog._sessions_loaded(0, [{"id": "stale", "title": "Stale"}], set(), "")

        assert [row.label for row in dialog.list_box.GetRows()] == before
        assert spoken == []
    finally:
        dialog.Destroy()


def test_closing_during_loading_discards_the_result(monkeypatch, frame):
    started = threading.Event()
    release = threading.Event()
    spoken: list[str] = []
    monkeypatch.setattr(blindpilot_app, "announce", lambda text, **_k: spoken.append(text))

    def catalog(*_a, **_k):
        started.set()
        release.wait(3)
        return list(CATALOG), set(LIVE), ""

    monkeypatch.setattr(blindpilot_app, "hermes_session_catalog", catalog)
    dialog = HermesSessionsDialog(frame, cwd=".")
    assert started.wait(1), "Hermes was never asked for its conversations"
    delivered = threading.Event()
    loaded = dialog._sessions_loaded

    def receive(*args):
        loaded(*args)
        delivered.set()

    monkeypatch.setattr(dialog, "_sessions_loaded", receive)
    dialog.Destroy()
    _wait(lambda: not dialog)
    spoken.clear()
    release.set()
    _wait(delivered.is_set)

    assert spoken == [], "a callback on a destroyed dialog updated or spoke"


def test_reopening_a_finished_conversation_closes_the_replayed_response():
    """The replay streams through the live path, so `_stream_response` holds
    the replay's number when the empty completion arrives. Left set, the next
    message and its answer landed under the replayed response: no new header,
    and Ctrl+R could not reach the answer as a response of its own."""
    panel = type("PanelStub", (), {})()
    panel._stopping = False
    panel._turns = []
    panel._stream_response = 1
    panel._flush_live_answer = lambda: None
    panel._rows = [
        blindpilot_app.Row(kind="header", label="Response 1", payload="", response_number=1)
    ]
    panel._earcons = type("Earcons", (), {"play_received": lambda self: None})()
    statuses: list[str] = []
    panel._set_status = statuses.append

    blindpilot_app.SessionPanel._on_response_complete(panel, "")

    assert panel._stream_response is None
    assert statuses == ["Reopened, 1 rows"]
