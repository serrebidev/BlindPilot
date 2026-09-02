"""Matching what was read aloud against what was written, letter by letter.

`_keyed` reduces text to a comparable form and records where each kept
character came from, so FreeBuff can work out which part of a finished answer
was never spoken. The two lists have to stay in step.

They did not. `casefold()` is not length-preserving — German "ß" folds to "ss",
Turkish "İ" to two characters, the "fi" ligature to two — but the position list
got one entry per *input* character. Any answer containing one of those made
the key longer than the map, and the index used to cut the answer then pointed
somewhere else entirely, or off the end.

Found by property testing, not by reading: the shapes are ordinary words, but
nobody writing examples by hand thinks to put "Straße" in one.
"""

from __future__ import annotations

import pytest

from agent_backends import _keyed, _unspoken_tail

# Each folds to more characters than it occupies.
GERMAN = "Stra\u00dfe"  # Straße  -> strasse
TURKISH = "\u0130stanbul"  # İstanbul -> i̇stanbul
LIGATURE = "\ufb01le"  # ﬁle -> file


@pytest.mark.parametrize("text", [GERMAN, TURKISH, LIGATURE, "ordinary words", ""])
def test_there_is_one_position_for_every_character_of_the_key(text):
    """Plus the sentinel, which is what makes a fully-spoken answer indexable."""
    key, positions = _keyed(text, letters_only=True)

    assert len(positions) == len(key) + 1, (
        f"{len(key)} key characters but {len(positions)} positions for {text!r}"
    )


@pytest.mark.parametrize("text", [GERMAN, TURKISH, LIGATURE])
def test_an_answer_read_out_in_full_leaves_nothing_over(text):
    """The commonest case there is, and it raised IndexError."""
    assert _unspoken_tail(text, text) == ""


def test_the_rest_of_the_answer_is_cut_at_the_right_letter():
    """Not merely "does not crash": it cut in the wrong place and ate a letter."""
    assert _unspoken_tail("Gr\u00fc\u00dfe", "Gr\u00fc\u00dfe gehen raus") == "gehen raus"


def test_every_position_points_inside_the_text():
    for text in (GERMAN, TURKISH, LIGATURE, "plain"):
        _key, positions = _keyed(text, letters_only=True)
        for position in positions:
            assert 0 <= position <= len(text)
