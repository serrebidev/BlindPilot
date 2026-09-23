"""The stand-ins this suite shares, instead of a copy per file.

Every one of these was written out by hand in several test files at once, so
a change to the thing being stood in for meant finding all the copies. They
are deliberately small and deliberately not `unittest.mock`: a test asserts on
what a stub recorded, and a plain list of names reads better in a failure than
a call-args repr does.
"""

from __future__ import annotations


class Earcons:
    """The sound player, recording each cue by name in the order it played.

    One list rather than a counter per cue: the order the cues came in is
    itself something tests assert on, and a count is `calls.count(...)` away.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.enabled = True
        self.cues: dict = {}

    def play_send(self, backend: str = "") -> None:
        self.calls.append("send")

    def play_received(self) -> None:
        self.calls.append("received")

    def play_error(self) -> None:
        self.calls.append("error")

    def start_progress(self) -> None:
        self.calls.append("start")

    def stop_progress(self) -> None:
        self.calls.append("stop")

    def set_enabled(self, enabled) -> None:
        self.enabled = enabled

    def set_cues(self, cues) -> None:
        self.cues = cues


class Button:
    """A `wx.Button` stand-in that remembers whether it is enabled.

    `__bool__` is always True because the panel checks `if self.send_btn:`
    before touching it, and a stub that answered by its own emptiness would
    make those guards skip the very call under test.
    """

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def Enable(self, value: bool = True) -> None:
        self.enabled = bool(value)

    def Disable(self) -> None:
        self.enabled = False

    def __bool__(self) -> bool:
        return True


class Prompt:
    """A `wx.TextCtrl` stand-in over a single string."""

    def __init__(self, text: str = "") -> None:
        self._text = text

    def GetValue(self) -> str:
        return self._text

    def SetValue(self, text: str) -> None:
        self._text = text

    def SetInsertionPointEnd(self) -> None:
        """Where the caret goes after text is put in is not a stub's business."""


class KeyEvent:
    """A key event a dialog's `_on_key` can read and skip."""

    def __init__(self, key: int) -> None:
        self._key = key
        self.skipped = False

    def GetKeyCode(self) -> int:
        return self._key

    def Skip(self) -> None:
        self.skipped = True


def panel_stub(base=None, **overrides):
    """A `SessionPanel` stand-in carrying the state its handlers touch.

    A plain function rather than a fixture: a test builds one where it needs
    one, often two in the same test, and says in the call what makes this
    panel different. Anything named in `overrides` is set last and wins, so a
    file that needs a different worker, backend or recorder passes it here
    instead of restating the twenty attributes it agrees with.

    Defaults match what the real panel would be read as: where the panel
    itself reads an attribute through `getattr(self, name, default)`, the
    value here is that same default, so carrying the attribute cannot change
    what a handler does.

    `base` is for the handlers that ask `isinstance(page, SessionPanel)`: a
    test that stands in for the class itself passes its own, and the state
    below is put on an instance of that instead of a throwaway type.
    """
    import blindpilot_app as app

    panel = (base or type("PanelStub", (), {}))()

    # ----- the turn -----
    panel._worker = None
    panel._turns = []
    panel._rows = []
    panel._response_count = 0
    panel._stream_response = None
    panel._streamed_assistant = ""
    panel._stopping = False
    panel._assistant_narrated_this_turn = True
    panel._session_backend = app.BACKEND_CLAUDE
    panel._session_title = ""
    panel._claude_generation = 0
    panel._held_hermes = None

    # ----- what it would show and say -----
    panel._earcons = Earcons()
    panel._show_working = lambda: None
    panel._hide_working = lambda: None
    panel.send_btn = Button()
    panel.steer_btn = Button()
    panel.stop_btn = Button()
    panel.announced: list[str] = []
    panel.status: list[str] = []
    # The real `_announce` speaks and mirrors to the status bar; a stub that
    # only recorded one of the two would hide which of them a caller used.
    panel._announce = lambda text, urgent=False: (
        panel.announced.append(text),
        panel.status.append(text),
    )
    panel._set_status = lambda text: panel.status.append(text)
    panel._say = lambda _text, _kind="assistant": False

    # ----- what a tab closing has to put down -----
    panel._dictation_timer = None
    panel._close_question_dialog = lambda: None

    # ----- the list of rows -----
    panel._displayed = []
    panel._search_term = ""
    panel._row_starts = []
    panel._refresh_list = lambda: None
    panel._row_count = lambda: len(panel._displayed)
    panel._selected_row = lambda: app.SessionPanel._selected_row(panel)
    panel._select_row = lambda index: app.SessionPanel._select_row(panel, index)
    panel._append_rows = lambda rows: app.SessionPanel._append_rows(panel, rows)

    # ----- the real methods, where a stub could quietly disagree with the
    # window about whether a turn is running or how one ends -----
    panel._run_in_progress = lambda: app.SessionPanel._run_in_progress(panel)
    panel._finish_stopped_turn = lambda: app.SessionPanel._finish_stopped_turn(panel)

    for name, value in overrides.items():
        setattr(panel, name, value)
    return panel
