"""Accessible updater dialog states and control names."""

from __future__ import annotations

import sys
from dataclasses import replace
from types import SimpleNamespace

import wx

from app_updater import ReleaseInfo
from update_dialog import UpdateDialog


def _release() -> ReleaseInfo:
    return ReleaseInfo(
        version="9.0.0",
        tag="v9.0.0",
        title="BlindPilot 9",
        notes="A clearly described update.",
        page_url="https://github.com/serrebidev/BlindPilot/releases/tag/v9.0.0",
        asset_name="BlindPilot-Windows-x64.zip",
        asset_url="https://github.com/download/update.zip",
        asset_size=2 * 1024 * 1024,
        sha256="0" * 64,
    )


def test_update_dialog_has_named_controls_and_readable_release_notes(monkeypatch):
    app = wx.GetApp() or wx.App(False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    spoken: list[str] = []
    dialog = UpdateDialog(None, "1.0.0", spoken.append, start_check=False)
    try:
        dialog._check_finished(_release())
        assert dialog.status.GetName() == "Update status"
        assert dialog.notes.GetName() == "Release notes"
        assert dialog.gauge.GetName() == "Download progress"
        assert dialog.primary_button.GetName() == "Update action"
        assert dialog.notes.GetValue() == "A clearly described update."
        assert dialog.primary_button.GetLabel() == "&Install update"
        assert spoken[-1].startswith("BlindPilot 9.0.0 is available")
    finally:
        dialog.Destroy()
        app.ProcessPendingEvents()


def test_showing_release_notes_lays_out_the_panel_that_holds_them(monkeypatch):
    """The sizer lives on an inner panel. Laying out only the dialog left the
    notes box, hidden at construction and shown later, at the top left corner
    at its default size, drawn over the status line."""
    app = wx.GetApp() or wx.App(False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    dialog = UpdateDialog(None, "1.0.0", lambda _text: None, start_check=False)
    try:
        laid_out: list[bool] = []
        original = dialog.panel.Layout

        def spy() -> bool:
            laid_out.append(True)
            return original()

        dialog.panel.Layout = spy
        dialog._check_finished(_release())
        assert laid_out, "showing the notes never laid the panel out"
        assert dialog.notes.IsShown()
        status = dialog.status.GetRect()
        notes = dialog.notes.GetRect()
        assert notes.GetTop() >= status.GetBottom(), (status, notes)
        assert notes.GetWidth() > dialog.panel.GetClientSize().GetWidth() // 2, notes
    finally:
        dialog.Destroy()
        app.ProcessPendingEvents()


def test_a_state_without_notes_fits_the_dialog_and_notes_bring_the_size_back():
    app = wx.GetApp() or wx.App(False)
    dialog = UpdateDialog(None, "1.0.0", lambda _text: None, start_check=False)
    try:
        full = dialog.FromDIP(wx.Size(640, 470))
        # Checking for updates: one sentence and a button, no notes.
        assert dialog.GetSize().GetHeight() < full.GetHeight()
        dialog._check_finished(_release())
        assert dialog.GetSize().GetHeight() >= full.GetHeight()
        assert dialog.GetSize().GetWidth() >= full.GetWidth()
    finally:
        dialog.Destroy()
        app.ProcessPendingEvents()


def test_a_markdown_heading_in_the_notes_is_shown_without_its_hashes(monkeypatch):
    app = wx.GetApp() or wx.App(False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    release = replace(_release(), notes="# BlindPilot 9\n\n## Fixed\n\n- A clearly described fix.")
    dialog = UpdateDialog(None, "1.0.0", lambda _text: None, start_check=False)
    try:
        dialog._check_finished(release)
        lines = dialog.notes.GetValue().splitlines()
        assert lines[0] == "BlindPilot 9"
        assert "Fixed" in lines
        assert "- A clearly described fix." in lines
        assert not any(line.startswith("#") for line in lines)
    finally:
        dialog.Destroy()
        app.ProcessPendingEvents()


def test_a_check_that_finishes_after_the_app_is_gone_stays_quiet(monkeypatch):
    """Quit within the HTTP timeout and the worker outlives wx.App.

    wx.CallAfter with no application asserts, and the thread hook logs that
    as an uncaught exception in a thread. Nobody is left to tell, so say
    nothing.
    """
    monkeypatch.setattr(wx, "GetApp", lambda: None)

    def no_app(*_args, **_kwargs):
        raise AssertionError("No wx.App created yet")

    monkeypatch.setattr(wx, "CallAfter", no_app)
    stub = SimpleNamespace(
        check=lambda _version: _release(),
        current_version="1.0.0",
        _check_finished=lambda _release: None,
        _check_failed=lambda _message: None,
    )
    UpdateDialog._check_worker(stub)

    def stalled(_version):
        raise RuntimeError("stalled")

    stub.check = stalled
    UpdateDialog._check_worker(stub)
