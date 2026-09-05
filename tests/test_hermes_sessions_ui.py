"""The window side of going back to a Hermes conversation.

Kept apart from the protocol tests because these need wxPython: the label a
screen reader reads, and the dialog's own filtering, are the whole feature from
where the user sits. A machine without a display skips them rather than failing
the suite.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

wx = pytest.importorskip("wx")

import blindpilot_app  # noqa: E402
from blindpilot_app import HermesSessionsDialog, hermes_session_label  # noqa: E402


@pytest.fixture(scope="module")
def wx_app():
    try:
        application = wx.App(False)
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"no display for wxPython: {exc}")
    yield application


@pytest.fixture
def frame(wx_app):
    window = wx.Frame(None)
    try:
        yield window
    finally:
        window.Destroy()


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
    try:
        labels = [dialog.list_box.GetString(i) for i in range(dialog.list_box.GetCount())]
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
    try:
        dialog.running_only.SetValue(True)
        dialog._refresh()

        labels = [dialog.list_box.GetString(i) for i in range(dialog.list_box.GetCount())]
        assert len(labels) == 1
        assert labels[0].startswith("Running now")
    finally:
        dialog.Destroy()


def test_the_filter_matches_the_first_message_too(monkeypatch, frame):
    _install_catalog(monkeypatch)
    dialog = HermesSessionsDialog(frame, cwd=".")
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
    try:
        assert "no conversations" in dialog.summary.GetLabel()
    finally:
        dialog.Destroy()


def test_reopening_a_finished_conversation_closes_the_replayed_response():
    """The replay streams through the live path, so `_stream_response` holds
    the replay's number when the empty completion arrives. Left set, the next
    message and its answer landed under the replayed response: no new header,
    and Ctrl+R could not reach the answer as a response of its own."""
    panel = type("PanelStub", (), {})()
    panel._stopping = False
    panel._turns = []
    panel._stream_response = 1
    panel._rows = [
        blindpilot_app.Row(kind="header", label="Response 1", payload="", response_number=1)
    ]
    panel._earcons = type("Earcons", (), {"play_received": lambda self: None})()
    statuses: list[str] = []
    panel._set_status = statuses.append

    blindpilot_app.SessionPanel._on_response_complete(panel, "")

    assert panel._stream_response is None
    assert statuses == ["Reopened, 1 rows"]
