"""Which files a turn changed, and what changed in them.

Reading an agent's edits meant leaving for a terminal and a git command whose
output is a wall of plus and minus signs. Changed Files (Ctrl+Shift+D) lists
what differs from the last commit, with lines added and removed, and reads a
file's changes as Added, Removed and Unchanged lines, as The Chat Place does.
It asks git rather than a transcript, so it is the same for every backend.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess

import pytest

import blindpilot_app as app

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture(autouse=True)
def no_repository_above(tmp_path, monkeypatch):
    """The test folder sits inside BlindPilot's own checkout; git stops here."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("one\ntwo\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "start")
    yield tmp_path
    # Git writes its objects read-only, which Windows will not delete.
    for folder, _dirs, names in os.walk(tmp_path):
        for name in names:
            os.chmod(os.path.join(folder, name), stat.S_IWRITE | stat.S_IREAD)


def test_edited_and_new_files_are_listed_with_their_counts(repo):
    (repo / "src" / "main.py").write_text("one\nTWO\nthree\n", encoding="utf-8")
    (repo / "notes.txt").write_text("hi\n", encoding="utf-8")

    _root, files = app.changed_files(str(repo / "src"))

    assert ("src/main.py", "main.py, 2 lines added, 1 removed, in src", False) in files
    assert ("notes.txt", "notes.txt, new file", True) in files


def test_a_clean_repository_has_nothing_to_list(repo):
    assert app.changed_files(str(repo))[1] == []


def test_outside_a_repository_it_says_none(tmp_path):
    assert app.changed_files(str(tmp_path)) is None


def test_a_diff_reads_as_what_each_line_is():
    diff = (
        "diff --git a/x b/x\nindex 1..2 100644\n--- a/x\n+++ b/x\n"
        "@@ -3,2 +3,2 @@ def f():\n keep\n-old\n+new\n\\ No newline at end of file\n"
    )

    assert app.readable_diff(diff) == "At line 3:\nUnchanged: keep\nRemoved: old\nAdded: new"
