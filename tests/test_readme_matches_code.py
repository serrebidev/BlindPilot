# SPDX-License-Identifier: MIT
"""The README names every backend and every Chat provider the code ships.

A backend was added to the code without a line in the README, and nothing
noticed: the README went on saying "six coding agents" while the Backend
menu offered seven. These tests read the README the way a person does and
fail the moment the code and the prose disagree.
"""

from __future__ import annotations

import re
from pathlib import Path

from accessible_ai.models import PROVIDER_LABELS
from agent_backends import BACKEND_IDS, BACKEND_LABELS

README = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")

# The README states the count in words, as prose does.
NUMBER_WORDS = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
)


def test_readme_names_every_backend_wherever_it_lists_them() -> None:
    opening = README.split("\n\n")[1]
    by_hand = re.search(r"To do it by hand:\n\n```powershell\n(.*?)```", README, re.S)
    assert opening.startswith("A screen-reader-first"), opening[:60]
    assert by_hand is not None
    table_rows = {
        line.split("|")[1].strip() for line in README.splitlines() if line.startswith("| ")
    }

    missing = []
    for label in BACKEND_LABELS.values():
        if label not in opening:
            missing.append(f"{label}: opening sentence")
        if not re.search(rf"^# .*{re.escape(label)}", by_hand.group(1), re.M):
            missing.append(f"{label}: by-hand setup block")
        if label not in table_rows:
            missing.append(f"{label}: Backends table")
    count = NUMBER_WORDS[len(BACKEND_IDS)]
    for phrase in (f"Runs {count} coding agents", f"any of the {count} backends"):
        if phrase not in README:
            missing.append(f"count: expected {phrase!r}")
    assert not missing, "\n".join(missing)


def test_readme_lists_every_chat_provider() -> None:
    # To the end of the line, not the first full stop: "Z.AI" has one inside it.
    sentence = re.search(r"Supported providers are.*$", README, re.M)
    assert sentence is not None
    missing = [label for label in PROVIDER_LABELS.values() if label not in sentence.group(0)]
    assert not missing, missing
