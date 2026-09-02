"""Properties of the terminal-text surgery, over inputs nobody enumerated.

These take a screen scraped from a terminal — text laid out to a box width,
revised as it grows, scrolling once it is taller than the window — and work out
which part of the answer has not been read aloud. That is index arithmetic over
strings nobody wrote by hand.

This file exists because it paid for itself. Property testing found the
`casefold` length bug in `_keyed` within seconds; the example-based tests
beside it were written afterwards, from what it produced. The properties are
kept because the next edit to this arithmetic deserves the same treatment, and
because no one writing examples by hand thinks to put "Straße" in one.

Kept deliberately small: a few hundred examples, a couple of seconds. The
segmenter was tried the same way and found nothing over eighteen thousand
examples, so it is not tested here.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from agent_backends import (
    _append_delta,
    _complete_sentences,
    _keyed,
    _strip_terminal_noise,
    _unspoken_tail,
    _unwrap_screen_text,
)

MODEST = settings(max_examples=300, deadline=None)

# Nasty on purpose: the characters that fold to a different length, terminal
# escapes, and the whitespace a box-drawn layout inserts and then revises.
screen_text = st.one_of(
    st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=400),
    st.lists(
        st.sampled_from(
            [
                "Stra\u00dfe",  # folds to one character more
                "\u0130stanbul",  # and this to one more again
                "\ufb01le",  # ligature
                "hello",
                "world.",
                " ",
                "\n",
                "\r\n",
                "\t",
                "\u2026",
                "\x1b[31m",
                "x" * 40,
            ]
        ),
        max_size=40,
    ).map("".join),
)


@given(screen_text)
@MODEST
def test_none_of_the_helpers_raise(text):
    _strip_terminal_noise(text)
    _unwrap_screen_text(text)
    _complete_sentences(text)
    _keyed(text)
    _keyed(text, letters_only=True)


@given(screen_text)
@MODEST
def test_the_map_has_one_position_per_key_character_and_a_sentinel(text):
    """The invariant the bug broke. Without it the index used to cut an answer
    points at the wrong letter, or past the end."""
    for letters_only in (False, True):
        key, positions = _keyed(text, letters_only)
        assert len(positions) == len(key) + 1
        assert all(0 <= position <= len(text) for position in positions)


@given(screen_text, screen_text)
@MODEST
def test_whatever_is_left_to_read_comes_out_of_the_answer(narrated, answer):
    """It must be part of the answer, never something assembled."""
    tail = _unspoken_tail(narrated, answer)
    assert not tail or tail in answer


@given(screen_text, screen_text)
@MODEST
def test_the_delta_comes_off_the_screen_it_was_given(spoken, current):
    delta = _append_delta(spoken, current)
    assert not delta or delta in current
