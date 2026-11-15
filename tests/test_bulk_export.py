"""Bulk exports never overwrite a file or escape the chosen folder."""

from threading import Event

import pytest


def test_equal_and_pathlike_titles_never_overwrite_existing_files(tmp_path):
    from conversation_export import ExportEntry, export_many

    original = tmp_path / "Same.md"
    original.write_text("keep")
    entries = [
        ExportEntry(title, lambda: [("User", "hello"), ("Assistant", "answer")])
        for title in ("Same", "Same", "../CON", "", "nul")
    ]
    result = export_many(entries, tmp_path, "md", Event(), lambda *_: None)
    assert len(result.paths) == 5 and not result.errors
    assert original.read_text() == "keep"
    assert len(set(result.paths)) == 5
    for path in result.paths:
        assert path.parent == tmp_path
        assert "hello" in path.read_text(encoding="utf-8")


def test_html_uses_the_escaped_existing_renderer(tmp_path):
    from conversation_export import ExportEntry, export_many

    result = export_many(
        [ExportEntry("Title", lambda: [("Assistant", "<script>bad()</script>")])],
        tmp_path,
        "html",
        Event(),
        lambda *_: None,
    )
    text = result.paths[0].read_text(encoding="utf-8")
    assert "<script>" not in text and "&lt;script&gt;" in text


def test_one_failed_source_does_not_hide_other_exports(tmp_path):
    from conversation_export import ExportEntry, export_many

    def broken():
        raise OSError("source gone")

    result = export_many(
        [ExportEntry("Bad", broken), ExportEntry("Good", lambda: [("User", "hi")])],
        tmp_path,
        "txt",
        Event(),
        lambda *_: None,
    )
    assert len(result.paths) == 1 and len(result.errors) == 1
    assert "source gone" in result.errors[0]


def test_cancellation_keeps_completed_exports_but_no_partial_file(tmp_path):
    from conversation_export import ExportEntry, export_many

    cancel = Event()
    result = export_many(
        [ExportEntry("One", lambda: [("User", "hi")])] * 3,
        tmp_path,
        "md",
        cancel,
        lambda *_: cancel.set(),
    )
    assert result.cancelled and len(result.paths) == 1
    assert sorted(tmp_path.iterdir()) == result.paths


def test_invalid_format_is_rejected_before_reading(tmp_path):
    from conversation_export import export_many

    with pytest.raises(ValueError):
        export_many([], tmp_path, "exe", Event(), lambda *_: None)
