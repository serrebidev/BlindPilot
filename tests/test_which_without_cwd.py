"""CLI discovery must never run a binary out of the current directory.

`shutil.which` searches the current directory first on Windows, so launching
BlindPilot from a folder that happened to contain `codex.cmd` made that file
the agent for every turn.
"""

from __future__ import annotations

import os
from pathlib import Path

import process_utils


def _binary(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_text("", encoding="utf-8")
    path.chmod(0o755)
    return path


def _name() -> str:
    return "codex.exe" if os.name == "nt" else "codex"


def test_a_binary_only_in_the_current_directory_is_ignored(tmp_path, monkeypatch):
    work = tmp_path / "work"
    elsewhere = tmp_path / "bin"
    work.mkdir()
    elsewhere.mkdir()
    _binary(work, _name())

    monkeypatch.setenv("PATH", str(elsewhere))
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD")

    original = Path.cwd()
    os.chdir(work)
    try:
        assert process_utils.which_without_cwd(_name()) is None
    finally:
        os.chdir(original)


def test_a_binary_on_the_path_is_found(tmp_path, monkeypatch):
    elsewhere = tmp_path / "bin"
    elsewhere.mkdir()
    expected = _binary(elsewhere, _name())

    monkeypatch.setenv("PATH", str(elsewhere))
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD")

    assert process_utils.which_without_cwd(_name()) == str(expected)


def test_an_empty_path_entry_does_not_mean_the_current_directory(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    _binary(work, _name())

    monkeypatch.setenv("PATH", os.pathsep.join(["", str(tmp_path / "nowhere")]))
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD")

    original = Path.cwd()
    os.chdir(work)
    try:
        assert process_utils.which_without_cwd(_name()) is None
    finally:
        os.chdir(original)


def test_a_dot_path_entry_is_not_the_current_directory(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    _binary(work, _name())

    monkeypatch.setenv("PATH", os.curdir)
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD")

    original = Path.cwd()
    os.chdir(work)
    try:
        assert process_utils.which_without_cwd(_name()) is None
    finally:
        os.chdir(original)


def test_a_relative_path_entry_is_not_resolved_from_the_current_directory(tmp_path, monkeypatch):
    work = tmp_path / "work"
    (work / "bin").mkdir(parents=True)
    _binary(work / "bin", _name())

    monkeypatch.setenv("PATH", "bin")
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD")

    original = Path.cwd()
    os.chdir(work)
    try:
        assert process_utils.which_without_cwd(_name()) is None
    finally:
        os.chdir(original)
