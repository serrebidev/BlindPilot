"""What a conversation tab is called.

Each session lives in its own tab, and a tab is named after the conversation in
it — the same first-message title Recent Conversations lists it under — because
that is the only thing that tells two tabs in the same folder apart. Until a
conversation has said anything it has no name, and the folder stands in.

Run from the project root:

    python -m pytest tests/ -q
    # or, with no pytest installed:
    python tests/test_tabs.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_reader import _short_label, _tab_label, _tab_title  # noqa: E402
from session_history import make_title  # noqa: E402


def test_a_conversation_names_its_tab():
    assert (
        _tab_label("Add tabs to this app", "/home/me/projects/blindpilot") == "Add tabs to this app"
    )


def test_a_conversation_with_no_name_yet_falls_back_to_the_folder():
    assert _tab_label("", "/home/me/projects/blindpilot") == "blindpilot"


def test_a_title_that_is_only_whitespace_is_no_title():
    assert _tab_label("   \n\t  ", "/home/me/projects/blindpilot") == "blindpilot"


def test_a_long_first_message_is_cut_to_fit_the_strip():
    label = _tab_label("Please go through the whole application and audit it for bugs", "/tmp/x")
    assert len(label) == 32
    assert label.endswith("…")
    assert label.startswith("Please go through")


def test_a_multi_line_message_becomes_one_line():
    assert _tab_label("first line\nsecond line", "/tmp/x") == "first line second line"


def test_the_tab_name_matches_what_history_calls_the_conversation():
    typed = "Fix the crash when two tabs run at once"
    assert _tab_label(make_title(typed), "/tmp/x") == _tab_title(typed)


def test_a_filesystem_root_keeps_its_whole_path():
    root = os.path.abspath(os.sep)
    assert _short_label(root) == root
    assert _tab_label("", root) == root


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if failures else 0)
