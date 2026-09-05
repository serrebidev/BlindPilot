"""A name typed in the New Session dialog stays on the tab.

The dialog puts the name on the tab when the session is created, and then the
first message took it away: a conversation is normally named after that message,
and nothing checked whether it already had a name. The visible result was a tab
labelled with the first words sent -- "start" for a one-word message -- while
the conversation itself was still called what the person typed, so the tab and
the conversation list disagreed about the same conversation.

Driven through the real ``SessionPanel._on_send`` rather than asserting on
``_tab_label``: the label function was already correct, and a test of it passed
throughout the defect. The thing that was wrong is WHEN the label is recomputed,
which only the send path shows.
"""

from __future__ import annotations

import blindpilot_app as app


class _Prompt:
    def __init__(self, text: str):
        self._text = text

    def GetValue(self) -> str:
        return self._text

    def SetValue(self, text: str) -> None:
        self._text = text


class _Button:
    def Enable(self, value: bool = True) -> None:
        pass

    def Disable(self) -> None:
        pass

    def __bool__(self) -> bool:
        return True


class _Earcons:
    def play_send(self) -> None:
        pass

    def start_progress(self) -> None:
        pass


class _Label:
    def SetLabel(self, _text: str) -> None:
        pass


class _Worker:
    """Stands in for a backend process: built and started, never run.

    Answers the liveness questions the send path asks, so a second send in the
    same test is refused for the reason under test rather than by an
    AttributeError.
    """

    def __init__(self, *_args, **_kwargs):
        pass

    def start(self) -> None:
        pass

    def is_alive(self) -> bool:
        return False

    def accepting_input(self) -> bool:
        return False

    def cancel(self) -> None:
        pass


def _panel(session_title: str, prompt: str = "start", session_id=None):
    """A fresh tab, about to send its first message."""
    panel = type("PanelStub", (), {})()
    panel._worker = None
    panel.prompt = _Prompt(prompt)
    panel._attachments = []
    panel._turns = []
    panel._rows = []
    panel._response_count = 0
    panel._stream_response = None
    panel._streamed_assistant = ""
    panel._stopping = False
    panel._session_id = session_id
    panel._session_backend = app.BACKEND_HERMES
    panel._session_title = session_title
    panel._assistant_narrated_this_turn = False
    panel.model = ""
    panel.effort = ""
    panel._cli_model = ""
    panel._cli_effort = ""
    panel.cwd = ""
    panel.mode = "default"
    panel._earcons = _Earcons()
    panel.send_btn = _Button()
    panel.steer_btn = _Button()
    panel.stop_btn = _Button()
    panel.backend_status = _Label()
    panel.titles: list[str] = []
    panel.announced: list[str] = []
    panel._announce = lambda text: panel.announced.append(text)
    panel._set_status = lambda _text: None
    panel._refresh_list = lambda: None
    panel._say = lambda _text: False
    panel._run_in_progress = lambda: False
    panel.selected_backend = lambda: app.BACKEND_HERMES
    panel._on_steer = lambda: None
    panel._build_send_text = lambda text: text
    panel._backend_uploads_attachments = lambda: False
    panel._attachment_summary = lambda: ""
    panel._add_your_message = lambda *_a, **_k: None
    panel._queue_worker_event = lambda *_a, **_k: None
    panel._ask_questions = None
    panel._held_hermes = object()  # already held: no real connection is opened
    # The real method, not a stand-in: what a turn is GIVEN is half of what this
    # file is about, so the name reaching session.create is measured too.
    panel._hermes_worker_extra = lambda files: app.SessionPanel._hermes_worker_extra(panel, files)
    panel._on_title = lambda _panel, title: panel.titles.append(title)
    return panel


def _send(panel) -> None:
    real = app.worker_class
    app.worker_class = lambda *_a, **_k: _Worker
    try:
        app.SessionPanel._on_send(panel)
    finally:
        app.worker_class = real


# --------------------------------------------------------------------------
# The defect itself
# --------------------------------------------------------------------------


def test_the_first_message_does_not_rename_a_named_session() -> None:
    """The reported bug: a session named "Uti research" sent "start" and the
    tab became "start"."""
    panel = _panel("Uti Zrozumialosc tekstu research")

    _send(panel)

    assert panel.titles == [], f"the tab was relabelled away from its name: {panel.titles}"


def test_a_one_word_message_cannot_replace_a_name() -> None:
    """The exact shape observed: one word in, and it must not reach the tab."""
    panel = _panel("Radio pipeline", prompt="start")

    _send(panel)

    assert "start" not in panel.titles


# --------------------------------------------------------------------------
# Negative controls: the fix must not swallow the automatic naming
# --------------------------------------------------------------------------


def test_an_unnamed_session_is_still_named_by_its_first_message() -> None:
    """Without this, the fix would leave every unnamed tab labelled "New
    session" forever -- a worse defect than the one being fixed."""
    panel = _panel("")

    _send(panel)

    assert panel.titles == ["start"], panel.titles


def test_a_whitespace_only_name_counts_as_no_name() -> None:
    """The dialog strips its input, but the panel must not treat a blank name
    as a real one and suppress the automatic title."""
    panel = _panel("   ")

    _send(panel)

    assert panel.titles == ["start"], panel.titles


def test_a_panel_without_the_attribute_still_sends() -> None:
    """Upstream drives this method on stand-in panels; an AttributeError here
    would refuse the send outright rather than mislabel a tab."""
    panel = _panel("")
    del panel._session_title

    _send(panel)

    assert panel.titles == ["start"], panel.titles


def test_the_second_message_never_touched_the_title() -> None:
    """Guards the ``len(self._turns) == 1`` half of the condition: the fix
    changed that line, and losing it would rename the tab on every turn."""
    panel = _panel("")
    _send(panel)
    panel.prompt.SetValue("a second question")

    _send(panel)

    assert panel.titles == ["start"], panel.titles


# --------------------------------------------------------------------------
# A name belongs to ONE conversation
# --------------------------------------------------------------------------


def test_starting_a_new_conversation_drops_the_name() -> None:
    """Ctrl+N in a named tab: the next conversation is a different one, and
    keeping the name would hand it to that conversation's session.create as
    well -- two conversations with one name, and the tab unable to take the
    name of the new first message."""
    panel = _panel("Radio pipeline", prompt="the first question")
    _send(panel)
    panel._run_in_progress = lambda: False
    panel._worker = None
    panel._drop_held_backends = lambda: None

    app.SessionPanel.clear_conversation(panel)
    panel.titles.clear()
    panel.prompt.SetValue("a different subject")
    _send(panel)

    assert panel.titles == ["a different subject"], panel.titles


def test_switching_backend_mid_conversation_drops_the_name() -> None:
    """Leaving a conversation behind by changing backend is the same boundary
    as clearing it."""
    panel = _panel("Radio pipeline", session_id="hermes-1")
    panel._drop_held_backends = lambda: None
    panel.selected_backend = lambda: app.BACKEND_CLAUDE

    _send(panel)

    assert panel._session_title == ""
    assert panel.titles == ["start"], panel.titles


def test_switching_backend_before_the_first_message_keeps_the_name() -> None:
    """Negative control for the one above: with no conversation yet there is
    nothing to leave behind, so a name typed seconds ago must survive picking
    a different backend."""
    panel = _panel("Radio pipeline", session_id=None)
    panel._drop_held_backends = lambda: None
    panel.selected_backend = lambda: app.BACKEND_CLAUDE

    _send(panel)

    assert panel._session_title == "Radio pipeline"
    assert panel.titles == [], panel.titles


def test_reopening_a_hermes_conversation_drops_the_name() -> None:
    """Ctrl+Shift+H in a named tab: the tab becomes a conversation Hermes
    already named. Keeping the typed name would suppress the automatic title
    if the tab were later cleared, and would send a title on a resume that
    does not take one."""
    panel = _panel("Radio pipeline")
    panel._drop_held_backends = lambda: None
    panel.backend_changed = lambda: None
    panel.stop_btn = _Button()
    panel._worker = None

    real = app.worker_class
    app.worker_class = lambda *_a, **_k: _Worker
    try:
        app.SessionPanel.open_hermes_session(panel, "sess-9", "Uti research", False)
    finally:
        app.worker_class = real

    assert panel._session_title == ""
    assert panel.titles == ["Uti research"], panel.titles


def test_restoring_a_past_conversation_drops_the_name() -> None:
    """Ctrl+H in a named tab: same boundary, a conversation with a title of
    its own."""
    panel = _panel("Radio pipeline")
    panel._drop_held_backends = lambda: None
    panel.backend_changed = lambda: None
    entry = app.HistoryEntry(
        backend=app.BACKEND_CLAUDE,
        session_id="past-1",
        title="Yesterday's audit",
        path="",
        modified=0.0,
    )

    app.SessionPanel.restore_history(panel, entry, [])

    assert panel._session_title == ""
    assert panel.titles == ["Yesterday's audit"], panel.titles
