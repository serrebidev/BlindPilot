"""Accessible updater dialog states and control names."""

from __future__ import annotations

import sys
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
