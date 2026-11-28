"""Command+C in the formatted view, and number boxes named in every part."""

from __future__ import annotations

import pytest

import markdown_rows


def test_only_the_viewers_page_lets_wx_evaluate_its_copy_script() -> None:
    """wx's Copy() on macOS is a script it evaluates; the viewer must allow
    eval and nothing more, and an exported page stays as strict as before."""
    viewer = markdown_rows.markdown_page("<script>alert(1)</script>", "R", viewer=True)
    exported = markdown_rows.markdown_page("text", "R")
    assert "script-src 'unsafe-eval';" in viewer
    assert "'unsafe-inline'; " not in viewer.split("script-src", 1)[1].split(";", 1)[0]
    assert "<script>" not in viewer, "raw HTML in an answer is shown as text"
    assert "unsafe-eval" not in exported


def test_a_number_box_is_named() -> None:
    wx = pytest.importorskip("wx")
    import blindpilot_app

    owns_app = wx.GetApp() is None
    app = wx.GetApp() or wx.App(False)
    frame = wx.Frame(None)
    try:
        box = wx.SpinCtrl(frame, min=1, max=9, initial=3)
        blindpilot_app.name_number_box(box, "Port")
        assert box.GetName() == "Port"
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()
        if owns_app:
            app.Destroy()
