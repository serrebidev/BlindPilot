# SPDX-License-Identifier: MIT
"""About BlindPilot names every backend the code ships.

The sentence a person hears when they ask BlindPilot what it is had the
backends written into it by hand, and it went stale twice: Muse Code and
Command Code were both shipped without it. It is read aloud, so nothing on
screen gives the omission away.

`README.md` has had a test like this one since the same thing happened to it
(`test_readme_matches_code.py`). The sentence is built from `BACKEND_LABELS`
now, which is itself derived from `BACKENDS`, so the two cannot drift; these
tests hold it to that.
"""

from __future__ import annotations

from agent_backends import BACKEND_IDS, BACKEND_LABELS
from blindpilot_app import about_description

OPENING = "An accessible desktop frontend for "


def _opening_sentence() -> str:
    return about_description().split("\n", 1)[0]


def test_every_backend_is_named_in_about() -> None:
    sentence = _opening_sentence()
    missing = [label for label in BACKEND_LABELS.values() if label not in sentence]
    assert not missing, missing


def test_about_names_them_in_the_order_the_backend_menu_offers_them() -> None:
    """Written out by hand the list also drifts in order, which reads as a
    different set of agents to anyone who knows the menu."""
    labels = [BACKEND_LABELS[backend] for backend in BACKEND_IDS]
    listed = ", ".join(labels[:-1] + [f"and {labels[-1]}"])

    assert _opening_sentence() == f"{OPENING}{listed}."


def test_about_keeps_the_rest_of_what_it_says() -> None:
    text = about_description()
    assert "Claude Code Reader" in text
    assert "Licensed under the MIT License" in text
