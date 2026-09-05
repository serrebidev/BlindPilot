"""The terminal screen FreeBuff is read off, with pyte 0.8.2's crash repaired.

pyte 0.8.2 -- the last released version, and the one BlindPilot pins -- draws a
two-cell character (any emoji or CJK text, of which FreeBuff's interface is
full) by writing the character into one cell and an empty ``Char`` into the
stub cell after it. Reading the screen back in ``Screen.display`` then indexes
``char[0]`` on every cell, which raises ``IndexError: string index out of
range`` for an empty one. A redraw that overwrites the two-cell character's
lead cell while leaving its stub behind -- exactly what a TUI does when it
repaints a spinner or a status line cell by cell -- leaves such an orphaned
stub on the screen, and the next read of ``display`` ends the turn with
"BlindPilot stopped reading FreeBuff: string index out of range".

pyte's own unreleased fix (0.8.3, "Fixed rendering of multi code-point emoji
sequences") replaces the whole rendering path. Ours only needs the crash
repaired, so the stubs are given a visible placeholder before the screen is
read: the cell they belonged to has been repainted by whatever left them
orphaned, and the placeholder is what any terminal shows there -- a blank.

The class lives in ``agent_backends`` only for import-order reasons: FreeBuff
reads this module, and this module reads pyte only once a terminal exists.
"""

from __future__ import annotations

from typing import Any, List

import pyte


class _RepairedScreen(pyte.HistoryScreen):
    """A ``HistoryScreen`` that can always be read, orphaned stubs and all."""

    @property
    def display(self) -> List[str]:
        # The stub cell only ever reads back as a blank, so replacing the
        # empty data in place changes nothing pyte itself would have drawn.
        for line in self.buffer.values():
            for x, cell in list(line.items()):
                if cell.data == "":
                    line[x] = cell._replace(data=" ")
        return list(super().display)


def repaired_history_screen(columns: int, lines: int, history: int) -> Any:
    """A screen for reading a terminal, repaired against the pyte 0.8.2 crash.

    Typed as ``Any`` because every reader of it goes through ``pyte.Stream``
    and attribute access on the returned object; naming the pyte types here
    would drag pyte into this module's import time for a class it only ever
    needs once a terminal is running.
    """
    return _RepairedScreen(columns, lines, history=history)
