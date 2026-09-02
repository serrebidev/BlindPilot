"""Every error the window reports has to be said out loud, not just written down.

`_set_status` puts text in the status bar and stops there. Neither NVDA nor
JAWS reads a status-bar change on its own - the file says so itself, next to
the speaker it had to add to work around it - so an error sent that way is
seen by nobody. Copying is the plainest case: a copy that worked announced
itself, and a copy that failed said nothing at all, so the difference between
them was silence and the clipboard still holding whatever was in it before.
"""

from __future__ import annotations

import re

import pytest

import blindpilot_app
from blindpilot_app import SessionPanel
from markdown_rows import Row


class _Panel:
    """Just enough of a session panel to drive one of its error paths."""

    def __init__(self, **attributes):
        self.spoken: list[str] = []
        self.status: list[str] = []
        for name, value in attributes.items():
            setattr(self, name, value)

    def _announce(self, text: str) -> None:
        self.spoken.append(text)
        self.status.append(text)

    def _set_status(self, text: str) -> None:
        self.status.append(text)

    _copy_message = SessionPanel._copy_message


class _Prompt:
    def __init__(self, text: str = ""):
        self._text = text

    def GetValue(self) -> str:
        return self._text


class _Worker:
    def __init__(self, alive: bool = True, steered: bool = True):
        self._alive = alive
        self._steered = steered

    def is_alive(self) -> bool:
        return self._alive

    def steer(self, _text: str) -> bool:
        return self._steered


def _row(payload: str = "some text") -> Row:
    return Row(kind="text", label="a line", payload=payload, response_number=1)


def test_steering_with_nothing_running_says_so():
    panel = _Panel(_worker=None, prompt=_Prompt("go on then"))

    SessionPanel._on_steer(panel)

    assert panel.spoken == ["Error: Nothing is running to steer"]


def test_steering_an_empty_prompt_says_so():
    panel = _Panel(_worker=_Worker(), prompt=_Prompt("   "))

    SessionPanel._on_steer(panel)

    assert panel.spoken == ["Error: Type a message first, then steer"]


def test_steering_a_run_that_just_finished_says_so():
    """The narrowest window there is, and the one nobody would guess at."""
    panel = _Panel(_worker=_Worker(steered=False), prompt=_Prompt("go on then"))

    SessionPanel._on_steer(panel)

    assert panel.spoken and "already finished" in panel.spoken[0]


def test_stopping_with_nothing_running_says_so():
    panel = _Panel(_worker=None)

    SessionPanel._on_stop(panel)

    assert panel.spoken == ["Error: Nothing is running to stop"]


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda panel: SessionPanel._copy_row(panel, 0), id="copy row"),
        pytest.param(lambda panel: SessionPanel._copy_response(panel, 0), id="copy response"),
        pytest.param(
            lambda panel: SessionPanel._action_copy_response(panel, _row()),
            id="copy response from the row menu",
        ),
        pytest.param(
            lambda panel: SessionPanel._action_copy_conversation(panel),
            id="copy conversation",
        ),
    ],
)
def test_a_copy_that_failed_is_as_audible_as_one_that_worked(call, monkeypatch):
    monkeypatch.setattr(blindpilot_app, "_copy_to_clipboard", lambda _text: False)
    row = _row()
    panel = _Panel(_displayed=[row], _rows=[row])

    call(panel)

    assert panel.spoken == ["Error: Could not access clipboard"]


def test_copying_a_conversation_that_has_nothing_in_it_says_so():
    panel = _Panel(_displayed=[], _rows=[])

    SessionPanel._action_copy_conversation(panel)

    assert panel.spoken == ["Error: Nothing to copy yet"]


def test_no_error_is_left_going_to_the_status_bar_alone():
    """The status bar is a mirror for these, never the only place they land.

    A scan rather than a drive, because it is asking about the whole file
    rather than one path: an error added anywhere, in a handler no test has
    thought of yet, still has to be spoken. The paths above are driven
    individually - `_action_save_code` among them, further down.
    """
    source = (blindpilot_app.__file__ or "").replace(".pyc", ".py")
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    silent = re.findall(r'_set_status\(\s*f?"Error[^"]*"', text)

    assert not silent, f"these errors are written down but never spoken: {silent}"


# ----- saving a code row to a file -----
#
# This was the one error path covered only by the source scan above, on the
# grounds that its file dialog cannot be opened headlessly. The dialog is a
# context manager, so it can be stood in for exactly as `open_find`'s is, and
# the action driven for real: the file it writes, the sentence it says, and
# what it does when the write fails.


class _FileDialog:
    """`wx.FileDialog` as `_action_save_code` uses it."""

    def __init__(self, path, accepted=True):
        self._path = str(path)
        self._accepted = accepted
        self.defaults = {}

    def __call__(self, _parent, _title, **kwargs):
        self.defaults = kwargs
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def ShowModal(self):
        return blindpilot_app.wx.ID_OK if self._accepted else blindpilot_app.wx.ID_CANCEL

    def GetPath(self):
        return self._path


def _code_row(payload="print('hi')", language="Python"):
    return Row(
        kind="code",
        label="Code, Python, 1 line",
        payload=payload,
        response_number=1,
        language=language,
        lang_token="python",
    )


def _save(monkeypatch, tmp_path, row=None, name="snippet.py", accepted=True):
    dialog = _FileDialog(tmp_path / name, accepted=accepted)
    monkeypatch.setattr(blindpilot_app.wx, "FileDialog", dialog)
    panel = _Panel(cwd=str(tmp_path))

    SessionPanel._action_save_code(panel, row or _code_row())
    return panel, dialog


def test_saving_a_snippet_writes_it_and_says_where(monkeypatch, tmp_path):
    panel, _dialog = _save(monkeypatch, tmp_path)

    assert (tmp_path / "snippet.py").read_text(encoding="utf-8") == "print('hi')"
    assert panel.spoken == ["Saved code to snippet.py"]


def test_the_file_holds_exactly_what_the_row_held(monkeypatch, tmp_path):
    """A code row promises to be the code as written. This is where that
    promise reaches a disk, and somebody who cannot see the file has no way to
    check it against the original."""
    code = "def go():\n\treturn 1  # trailing spaces:   \n"
    _panel, _dialog = _save(monkeypatch, tmp_path, row=_code_row(payload=code))

    assert (tmp_path / "snippet.py").read_text(encoding="utf-8") == code


def test_cancelling_writes_nothing_and_says_nothing(monkeypatch, tmp_path):
    panel, _dialog = _save(monkeypatch, tmp_path, accepted=False)

    assert list(tmp_path.iterdir()) == []
    assert panel.spoken == []


def test_a_write_that_fails_is_spoken_not_just_written_down(monkeypatch, tmp_path):
    """The reason this file exists: an error in the status bar reaches nobody.

    The parent is a file rather than a directory, so `open` raises
    `NotADirectoryError` on every platform without any permission tricks.
    """
    blocker = tmp_path / "in-the-way"
    blocker.write_text("not a directory", encoding="utf-8")
    dialog = _FileDialog(blocker / "snippet.py")
    monkeypatch.setattr(blindpilot_app.wx, "FileDialog", dialog)
    panel = _Panel(cwd=str(tmp_path))

    SessionPanel._action_save_code(panel, _code_row())

    assert panel.spoken, "a failed save said nothing at all"
    assert panel.spoken[0].startswith("Error saving file")
    assert panel.spoken == panel.status, "the error reached the status bar only"


def test_the_offered_filename_matches_the_language(monkeypatch, tmp_path):
    """Somebody who cannot see the dialog is relying on the name it opens with."""
    _panel, dialog = _save(monkeypatch, tmp_path)

    assert dialog.defaults.get("defaultFile") == "snippet.py"
    assert dialog.defaults.get("defaultDir") == str(tmp_path)


def test_an_unknown_language_still_offers_a_name(monkeypatch, tmp_path):
    _panel, dialog = _save(monkeypatch, tmp_path, row=_code_row(language="Brainfuck"))

    assert dialog.defaults.get("defaultFile") == "snippet.txt"
