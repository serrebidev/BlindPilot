"""How much an edit changed, said out loud.

The narration says *that* a file was edited and not *how much*. For a sighted
user the diff is on screen; for somebody listening, that count is the only
available sense of scale, and the difference between "changed a line" and
"rewrote the file" is most of what you want to know when an agent reports back.

Two things constrain the shape. It goes in the *existing* line rather than a
new one, because the running narration is already long enough to fall behind
on a fan-out and a second utterance per edit would make that worse. And it is
a real diff of the block, not a count of the lines in it: replacing a
twenty-line function to change two of its lines is a two-line edit, and saying
"twenty added, twenty removed" would be worse than saying nothing.

Claude Code only, deliberately. Its `Edit` carries `old_string` and
`new_string`, so the numbers are there to be counted. Codex and opencode report
edits differently and FreeBuff is scraped off a terminal, so the same claim
cannot honestly be made for them yet.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app


def _edit(old: str, new: str, path="app/server.py"):
    return app._tool_use_label("Edit", {"file_path": path, "old_string": old, "new_string": new})


# ----- the counts -----
def test_an_edit_says_what_it_added_and_removed():
    label = _edit("one\ntwo\nthree\n", "1\n2\n3\n4\n5\n")

    assert label == "Editing server.py, 5 lines added, 3 removed"


def test_lines_that_did_not_change_are_not_counted():
    """The whole reason this is a diff. Two lines changed in a block of five
    is a two-line edit, whatever the block's size."""
    old = "keep\nkeep\nold one\nold two\nkeep\n"
    new = "keep\nkeep\nnew one\nnew two\nkeep\n"

    assert _edit(old, new) == "Editing server.py, 2 lines added, 2 removed"


def test_a_pure_insertion_says_only_what_it_added():
    """ "0 removed" is a word spoken for no reason, every time."""
    assert _edit("keep\n", "keep\nand this\n") == "Editing server.py, 1 line added"


def test_a_pure_deletion_says_only_what_it_removed():
    assert _edit("keep\ngoing\n", "keep\n") == "Editing server.py, 1 line removed"


def test_one_line_is_a_line_not_lines():
    assert _edit("", "only\n") == "Editing server.py, 1 line added"
    assert _edit("gone\n", "") == "Editing server.py, 1 line removed"


def test_an_edit_that_changes_nothing_says_nothing_extra():
    assert _edit("same\n", "same\n") == "Editing server.py"


# ----- the other ways a file gets written -----
def test_writing_a_file_says_how_much_was_written():
    label = app._tool_use_label(
        "Write", {"file_path": "notes/plan.md", "content": "one\ntwo\nthree\n"}
    )

    assert label == "Writing plan.md, 3 lines"


def test_writing_an_empty_file_does_not_claim_a_line():
    assert app._tool_use_label("Write", {"file_path": "a.txt", "content": ""}) == "Writing a.txt"


def test_several_edits_at_once_are_added_up():
    label = app._tool_use_label(
        "MultiEdit",
        {
            "file_path": "app/server.py",
            "edits": [
                {"old_string": "a\n", "new_string": "a\nb\n"},
                {"old_string": "x\ny\n", "new_string": "x\n"},
            ],
        },
    )

    assert label == "Editing server.py, 1 line added, 1 removed"


# ----- nothing else changes -----
def test_an_edit_without_the_strings_reads_as_it_always_did():
    """Older CLIs, and NotebookEdit, do not always carry both."""
    assert app._tool_use_label("Edit", {"file_path": "app/server.py"}) == "Editing server.py"


def test_an_edit_with_no_path_still_works():
    assert app._tool_use_label("Edit", {"old_string": "a\n", "new_string": "b\n"}).startswith(
        "Editing a file"
    )


@pytest.mark.parametrize(
    "name,params,expected",
    [
        ("Read", {"file_path": "a/b.py"}, "Reading b.py"),
        ("Bash", {"command": "ls -la"}, "Running: ls -la"),
        ("Grep", {"pattern": "TODO"}, "Searching for TODO"),
        ("TodoWrite", {}, "Updating the task list"),
    ],
)
def test_every_other_tool_is_narrated_exactly_as_before(name, params, expected):
    assert app._tool_use_label(name, params) == expected


def test_it_is_still_one_line():
    """A second utterance per edit is the thing this must not become."""
    label = _edit("one\ntwo\n", "three\nfour\nfive\n")

    assert "\n" not in label
