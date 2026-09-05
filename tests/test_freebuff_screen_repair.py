"""The screen FreeBuff is read off can always be read.

pyte 0.8.2 -- the pinned version, and the newest one released -- draws a
two-cell character (any emoji or CJK text, of which FreeBuff's interface is
full) into one cell and an empty ``Char`` into the stub cell after it. A
repaint that overwrites the lead cell without the stub leaves the stub
orphaned, and pyte's own ``display`` then raises ``IndexError: string index
out of range``. Through ``FreebuffWorker.run`` that was reported as
"BlindPilot stopped reading FreeBuff: string index out of range" and the turn
ended mid-answer.

The reproduction here is the terminal sequence itself: feed a line holding an
emoji, reposition the cursor over its lead cell and draw a plain character,
then read the screen the way the worker's frame loop does.
"""

from __future__ import annotations

import pyte

from freebuff_screen import repaired_history_screen


def _screen_with_wide_character_then_repaint(screen: pyte.HistoryScreen) -> None:
    stream = pyte.Stream(screen)
    # The rocket is two cells wide: lead cell at 0-based column 3, stub at 4.
    stream.feed("ab \U0001f680 cd\r\n")
    # A cell-by-cell repaint redraws the row and writes a plain-width
    # character over the lead cell. pyte 0.8.2 leaves the stub behind.
    screen.cursor_position(1, 4)
    screen.draw("Y")


def test_orphaned_stub_does_not_crash_display() -> None:
    screen = repaired_history_screen(40, 10, history=100)
    _screen_with_wide_character_then_repaint(screen)
    assert "\U0001f680" not in screen.display[0]
    assert screen.display[0].startswith("ab Y")


def test_plain_screen_is_unchanged_by_repair() -> None:
    screen = repaired_history_screen(40, 10, history=100)
    stream = pyte.Stream(screen)
    stream.feed("Hello, FreeBuff.\r\nsecond line\r\n")
    assert screen.display[0].rstrip() == "Hello, FreeBuff."
    assert screen.display[1].rstrip() == "second line"


def test_history_screen_kind_is_preserved() -> None:
    # The worker relies on HistoryScreen's scrollback; the repaired screen
    # has to remain one, not a sibling that quietly drops history.
    from freebuff_screen import _RepairedScreen

    screen = repaired_history_screen(40, 10, history=100)
    assert isinstance(screen, pyte.HistoryScreen)
    assert isinstance(screen, _RepairedScreen)
