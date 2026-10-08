"""Saving a conversation to a file.

Copy Whole Conversation put the transcript on the clipboard and nowhere else,
so keeping a long conversation meant pasting it into an editor and saving it
from there. Export Conversation (Ctrl+E) writes the same text straight to a
Markdown or text file, as The Chat Place's Export does.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app
from markdown_rows import Row


class _Dialog:
    """`wx.FileDialog` as `export_conversation` uses it: a context manager."""

    def __init__(self, path, accepted=True, kind=0):
        self._path = path
        self._accepted = accepted
        self._kind = kind

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def ShowModal(self):
        return app.wx.ID_OK if self._accepted else app.wx.ID_CANCEL

    def GetPath(self):
        return str(self._path)

    def GetFilterIndex(self):
        return self._kind


@pytest.fixture
def panel():
    stub = type("PanelStub", (), {})()
    stub._rows = [
        Row(kind="you", label="You: hi", payload="hi", response_number=1),
        Row(kind="header", label="Response 1", payload="Hello.", response_number=1),
        Row(kind="prose", label="Hello.", payload="Hello.", response_number=1),
    ]
    stub.tab_title = "Fix: the build?"
    stub.cwd = ""
    stub.spoken: list[str] = []
    stub._announce = lambda text, urgent=False: stub.spoken.append(text)
    return stub


def _export(panel, monkeypatch, path, accepted=True, kind=0):
    offered: dict = {}

    def dialog(*args, **kwargs):
        offered.update(kwargs)
        return _Dialog(path, accepted, kind)

    monkeypatch.setattr(app.wx, "FileDialog", dialog)
    app.SessionPanel.export_conversation(panel)
    return offered


def test_the_whole_conversation_is_written_and_the_file_named(panel, monkeypatch, tmp_path):
    target = tmp_path / "out.md"

    offered = _export(panel, monkeypatch, target)

    text = target.read_text(encoding="utf-8")
    assert "Response 1" in text and "Hello." in text
    assert panel.spoken == ["Exported conversation to out.md"]
    # A title with characters no file name may hold still makes a name.
    assert offered["defaultFile"].startswith("Fix the build ")
    assert offered["defaultFile"].endswith(".md")


def test_markdown_gives_each_response_a_heading(panel, monkeypatch, tmp_path):
    target = tmp_path / "out.md"

    _export(panel, monkeypatch, target)

    assert "## Response 1" in target.read_text(encoding="utf-8")


def test_a_web_page_is_rendered_and_never_runs_html(panel, monkeypatch, tmp_path):
    panel._rows.append(
        Row(kind="prose", label="x", payload="<script>alert(1)</script>", response_number=1)
    )
    target = tmp_path / "out.html"

    _export(panel, monkeypatch, target)

    page = target.read_text(encoding="utf-8")
    assert "<h2>Response 1</h2>" in page
    assert "<script>" not in page and "&lt;script&gt;" in page


def test_plain_text_is_the_clipboard_text(panel, monkeypatch, tmp_path):
    target = tmp_path / "out.txt"

    _export(panel, monkeypatch, target)

    assert target.read_text(encoding="utf-8") == app.reassemble_all(panel._rows) + "\n"


def test_a_name_without_an_extension_takes_the_chosen_type(panel, monkeypatch, tmp_path):
    """GTK and macOS can return the name as typed; the Save as type decides."""
    _export(panel, monkeypatch, tmp_path / "notes", kind=1)

    assert "<h2>Response 1</h2>" in (tmp_path / "notes.html").read_text(encoding="utf-8")


def test_only_real_response_headers_become_headings(panel, monkeypatch, tmp_path):
    panel._rows.append(
        Row(kind="code", label="code", payload="Response 7", response_number=1, language="text")
    )
    target = tmp_path / "out.md"

    _export(panel, monkeypatch, target)

    text = target.read_text(encoding="utf-8")
    assert "## Response 1" in text
    assert "## Response 7" not in text and "Response 7" in text


def test_nothing_to_export_says_so(panel, monkeypatch, tmp_path):
    panel._rows = []

    _export(panel, monkeypatch, tmp_path / "never.md")

    assert not (tmp_path / "never.md").exists()
    assert panel.spoken == ["Error: Nothing to export yet"]


def test_cancelling_writes_nothing(panel, monkeypatch, tmp_path):
    _export(panel, monkeypatch, tmp_path / "never.md", accepted=False)

    assert not (tmp_path / "never.md").exists()
    assert panel.spoken == []
