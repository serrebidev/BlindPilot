"""Features brought over from The Chat Place 0.6.1 to 0.6.3.

Local files named in a reply open from the Links list (#253); a Codex
message over its 32,000-character limit goes as a file (#625).
"""

from __future__ import annotations

import os
from pathlib import Path

from backend_workers.codex_worker import CODEX_INPUT_LIMIT, codex_input
from conversation_items import can_open_file, collect_items, file_target


def test_file_links_resolve_against_the_working_folder(tmp_path: Path):
    (tmp_path / "src").mkdir()
    notes = tmp_path / "src" / "notes.md"
    notes.write_text("x", encoding="utf-8")
    (tmp_path / "run.bat").write_text("x", encoding="utf-8")
    reply = (
        "See [the notes](src/notes.md), `run.bat`, [gone](missing.txt), "
        "`src/notes.md:12` and https://example.com/page."
    )
    items = collect_items([("Claude 1", reply)], "links", str(tmp_path))
    by_address = {item.payload: item for item in items}

    assert by_address["src/notes.md"].path == str(notes)
    assert by_address["src/notes.md"].label.endswith("src/notes.md, file")
    assert "runs code" in by_address["run.bat"].label
    assert not can_open_file(by_address["run.bat"].path)  # folder and copy only
    assert "not found" in by_address["missing.txt"].label
    assert by_address["https://example.com/page"].path == ""
    assert "src/notes.md:12" in by_address  # a position after the name still finds it


def test_web_addresses_and_other_schemes_are_not_files():
    assert file_target("https://example.com/a.txt") is None
    assert file_target("mailto:a@b.c") is None
    assert file_target("relative.txt") is None  # no working folder to find it in
    assert file_target("file:///tmp/a.txt") is not None
    assert file_target(r"C:\work\a.txt") is not None


def test_a_short_codex_message_goes_as_text():
    files: list[str] = []
    assert codex_input("hello", files) == [{"type": "text", "text": "hello"}]
    assert files == []


def test_a_long_codex_message_goes_as_a_file_codex_is_told_to_read():
    files: list[str] = []
    text = "log line\n" * (CODEX_INPUT_LIMIT // 9 + 10)
    items = codex_input(text, files)
    try:
        assert len(files) == 1
        assert Path(files[0]).read_text(encoding="utf-8") == text
        assert files[0] in items[0]["text"]
        assert len(items[0]["text"]) < CODEX_INPUT_LIMIT
    finally:
        for path in files:
            os.unlink(path)
