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

    `_action_save_code` reports through a file dialog that cannot be opened
    headlessly, so it is covered here rather than by driving it.
    """
    source = (blindpilot_app.__file__ or "").replace(".pyc", ".py")
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    silent = re.findall(r'_set_status\(\s*f?"Error[^"]*"', text)

    assert not silent, f"these errors are written down but never spoken: {silent}"
