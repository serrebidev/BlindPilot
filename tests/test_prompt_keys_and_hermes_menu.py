"""Two things a screen-reader user asked for, and what keeps them true.

1. Up in the prompt must move the caret, not jump to the responses. A
   multi-line prompt cannot be read back if reaching the top line throws focus
   out of the field.
2. Hermes' conversation list belongs in the File menu only while Hermes is the
   backend. With anything else selected there is no Hermes to ask, so the item
   is not merely unavailable — it is one more arrow press for nothing, in a
   menu already at the ten-item ceiling upstream set.

Both were shipped defects that no test covered, which is why they are covered
here from both sides: the wanted behaviour AND the behaviour that must not come
back.
"""

from __future__ import annotations

import pytest

import agent_backends
import blindpilot_app as app
from agent_backends import BACKEND_CLAUDE, BACKEND_HERMES

wx = pytest.importorskip("wx")


class _KeyEvent:
    """Enough of wx.KeyEvent for the prompt handler, with skip recorded.

    `Skip()` is the whole point of test one: it is how the key reaches the text
    control and moves the caret. A handler that returns without skipping has
    swallowed the keystroke.
    """

    def __init__(self, key: int, cmd: bool = False, alt: bool = False, shift: bool = False):
        self._key = key
        self._cmd = cmd
        self._alt = alt
        self._shift = shift
        self.skipped = False

    def GetKeyCode(self) -> int:
        return self._key

    def CmdDown(self) -> bool:
        return self._cmd

    def ControlDown(self) -> bool:
        return self._cmd

    def AltDown(self) -> bool:
        return self._alt

    def ShiftDown(self) -> bool:
        return self._shift

    def Skip(self) -> None:
        self.skipped = True


class _PromptStub:
    def __init__(self, text: str = "", caret: int = 0):
        self._text = text
        self._caret = caret

    def GetInsertionPoint(self) -> int:
        return self._caret

    def GetRange(self, start: int, end: int) -> str:
        return self._text[start:end]

    def GetValue(self) -> str:
        return self._text


class _PanelStub:
    """A panel that answers only what the prompt key handler asks of it."""

    def __init__(self, rows: int = 5, text: str = "one\ntwo\nthree", caret: int = 0):
        self.prompt = _PromptStub(text, caret)
        self._rows = rows
        self.focused_row: int | None = None
        self.sent = False

    def _row_count(self) -> int:
        return self._rows

    def _focus_row(self, index: int) -> None:
        self.focused_row = index

    def _focus_before(self) -> None:
        pass

    def _on_send(self) -> None:
        self.sent = True

    def _try_paste_attachment(self) -> bool:
        return False


def _press(panel: _PanelStub, key: int, **mods) -> _KeyEvent:
    event = _KeyEvent(key, **mods)
    app.SessionPanel._on_prompt_key(panel, event)
    return event


# ----- 1. Up in the prompt -----


@pytest.mark.parametrize("caret", [0, 4, 8])
def test_up_in_the_prompt_moves_the_caret_wherever_it_is(caret):
    """The reported defect: Up left the field instead of moving the caret.

    Parametrised across the first line and later ones because the old code was
    conditional on being on the first line -- which is exactly the position
    somebody reviewing a multi-line prompt keeps arriving at.
    """
    panel = _PanelStub(caret=caret)

    event = _press(panel, wx.WXK_UP)

    assert panel.focused_row is None, "Up left the prompt and entered the responses"
    assert event.skipped, "Up was swallowed, so the caret did not move either"


def test_down_in_the_prompt_is_left_alone_too():
    panel = _PanelStub()

    event = _press(panel, wx.WXK_DOWN)

    assert panel.focused_row is None
    assert event.skipped


def test_the_responses_are_still_reachable_from_the_prompt_by_chord():
    """Removing the bare-Up jump must not remove the ability itself."""
    panel = _PanelStub(rows=5)

    event = _press(panel, wx.WXK_UP, cmd=True)

    assert panel.focused_row == 4, "Ctrl+Up no longer reaches the newest response"
    assert not event.skipped, "the chord was handled, so it must not also fall through"


def test_a_chord_with_no_responses_yet_does_not_move_focus():
    panel = _PanelStub(rows=0)

    _press(panel, wx.WXK_UP, cmd=True)

    assert panel.focused_row is None


def test_enter_still_sends_and_shift_enter_still_inserts_a_newline():
    """Control: the keys around the changed one are untouched."""
    panel = _PanelStub()
    _press(panel, wx.WXK_RETURN)
    assert panel.sent

    other = _PanelStub()
    event = _press(other, wx.WXK_RETURN, shift=True)
    assert not other.sent
    assert event.skipped


# ----- 2. The Hermes list in the File menu -----


class _MenuItemStub:
    def __init__(self, label: str):
        self._label = label
        self._menu: _MenuStub | None = None

    def GetItemLabelText(self) -> str:
        return self._label

    def GetMenu(self):
        return self._menu


class _MenuStub:
    def __init__(self, labels: list[str]):
        self._items = []
        for label in labels:
            item = _MenuItemStub(label)
            item._menu = self
            self._items.append(item)

    def GetMenuItems(self):
        return list(self._items)

    def Remove(self, item):
        self._items.remove(item)
        item._menu = None

    def Insert(self, position: int, item):
        self._items.insert(position, item)
        item._menu = self

    def labels(self) -> list[str]:
        return [item.GetItemLabelText() for item in self._items]


class _FrameStub:
    def __init__(self, backend: str, mode: str = app.APP_MODE_AGENT):
        self._backend = backend
        self._app_mode = mode
        self._file_menu = _MenuStub(
            ["New Session", "Recent Conversations", "Hermes Conversations", "Side Chat", "Quit"]
        )
        self._hermes_sessions_item = self._file_menu.GetMenuItems()[2]
        self.bound = 0

    def Bind(self, *_args, **_kwargs):
        self.bound += 1

    def _open_hermes_sessions(self):
        pass

    _refresh_hermes_sessions_item = app.MainFrame._refresh_hermes_sessions_item
    _insert_hermes_sessions_item = app.MainFrame._insert_hermes_sessions_item


def test_the_hermes_list_is_offered_when_hermes_is_the_backend():
    frame = _FrameStub(BACKEND_HERMES)

    frame._refresh_hermes_sessions_item()

    assert "Hermes Conversations" in frame._file_menu.labels()


def test_the_hermes_list_is_gone_for_another_backend():
    """The request: with Claude selected, asking a Hermes makes no sense."""
    frame = _FrameStub(BACKEND_CLAUDE)

    frame._refresh_hermes_sessions_item()

    assert "Hermes Conversations" not in frame._file_menu.labels()


def test_removing_it_leaves_the_rest_of_the_menu_alone():
    """A removal that takes a neighbour with it would be worse than the item."""
    frame = _FrameStub(BACKEND_CLAUDE)

    frame._refresh_hermes_sessions_item()

    assert frame._file_menu.labels() == [
        "New Session",
        "Recent Conversations",
        "Side Chat",
        "Quit",
    ]


def test_it_comes_back_in_its_own_place_when_hermes_is_chosen_again():
    """Switching backends twice must not drift the item down the menu.

    The item is re-inserted after Recent Conversations by name rather than by
    remembering an index, because the menu it returns to is one item shorter
    than the one it left.
    """
    frame = _FrameStub(BACKEND_HERMES)
    frame._backend = BACKEND_CLAUDE
    frame._refresh_hermes_sessions_item()
    frame._backend = BACKEND_HERMES
    frame._refresh_hermes_sessions_item()

    assert frame._file_menu.labels() == [
        "New Session",
        "Recent Conversations",
        "Hermes Conversations",
        "Side Chat",
        "Quit",
    ]


def test_switching_back_and_forth_never_leaves_two_of_them():
    """Insert-without-remove would duplicate the item, which reads twice."""
    frame = _FrameStub(BACKEND_HERMES)
    for backend in (BACKEND_CLAUDE, BACKEND_HERMES, BACKEND_CLAUDE, BACKEND_HERMES):
        frame._backend = backend
        frame._refresh_hermes_sessions_item()

    assert frame._file_menu.labels().count("Hermes Conversations") == 1


def test_chat_mode_has_no_hermes_list_even_on_the_hermes_backend():
    """Chat mode talks to a provider directly; there is no backend conversation."""
    frame = _FrameStub(BACKEND_HERMES, mode=app.APP_MODE_CHAT)

    frame._refresh_hermes_sessions_item()

    assert "Hermes Conversations" not in frame._file_menu.labels()


def test_refreshing_twice_for_the_same_backend_changes_nothing():
    """Called from three places, so it has to be idempotent."""
    frame = _FrameStub(BACKEND_HERMES)
    frame._refresh_hermes_sessions_item()
    first = frame._file_menu.labels()
    frame._refresh_hermes_sessions_item()

    assert frame._file_menu.labels() == first


def test_a_frame_without_the_item_yet_does_not_raise():
    """It runs while the menu bar is still being built."""
    frame = _FrameStub(BACKEND_HERMES)
    del frame._hermes_sessions_item

    frame._refresh_hermes_sessions_item()  # must not raise


def test_every_backend_that_is_not_hermes_hides_it():
    """Control across the real registry, so a fifth backend cannot slip through."""
    for backend in agent_backends.BACKEND_IDS:
        frame = _FrameStub(backend)
        frame._refresh_hermes_sessions_item()
        present = "Hermes Conversations" in frame._file_menu.labels()
        assert present == (backend == BACKEND_HERMES), backend
