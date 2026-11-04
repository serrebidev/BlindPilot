"""Setup wizard CLI discovery keeps the dialog responsive."""

from __future__ import annotations

import pytest

import blindpilot_app as app

wx = pytest.importorskip("wx")


def test_cli_check_runs_discovery_on_worker_and_fills_result(wx_app, monkeypatch):
    workers = []
    callbacks = []
    spoken = []

    class Thread:
        def __init__(self, target, daemon=False):
            assert daemon
            self.target = target

        def start(self):
            workers.append(self.target)

    monkeypatch.setattr(app.threading, "Thread", Thread)
    monkeypatch.setattr(
        app, "_call_after_alive", lambda callback, *args: callbacks.append((callback, args))
    )
    monkeypatch.setattr(app, "announce", spoken.append)
    monkeypatch.setattr(app.SetupWizard, "_find_selected_cli_for_backend", lambda _backend: None)
    monkeypatch.setattr(
        app.SetupWizard,
        "_selected_install_argv_for_backend",
        lambda _backend: ["installer"],
    )
    monkeypatch.setattr(app, "_find_npm", lambda: None)
    wizard = app.SetupWizard(None)
    try:
        wizard._step = 1
        wizard._check_cli()
        assert len(workers) == 1
        assert wizard._cli_status.GetLabel() == "Checking for Claude Code CLI…"
        assert "several seconds" in wizard._cli_detail.GetLabel()
        assert not wizard._next_btn.IsEnabled()

        workers.pop()()
        callback, args = callbacks.pop()
        callback(*args)
        assert wizard._cli_status.GetLabel() == "Claude Code is not installed on this computer."
        assert wizard._cli_install_btn.IsShown()
        assert not wizard._cli_check_pending
        assert spoken[-1].startswith("Claude Code is not installed")
    finally:
        wizard.Destroy()


def test_cli_check_error_is_spoken_and_can_be_retried(wx_app, monkeypatch):
    workers = []
    callbacks = []
    spoken = []

    class Thread:
        def __init__(self, target, daemon=False):
            assert daemon
            self.target = target

        def start(self):
            workers.append(self.target)

    monkeypatch.setattr(app.threading, "Thread", Thread)
    monkeypatch.setattr(
        app, "_call_after_alive", lambda callback, *args: callbacks.append((callback, args))
    )
    monkeypatch.setattr(app, "announce", spoken.append)

    def fail(_backend):
        raise RuntimeError("CLI probe failed")

    monkeypatch.setattr(app.SetupWizard, "_find_selected_cli_for_backend", fail)
    wizard = app.SetupWizard(None)
    try:
        wizard._step = 1
        wizard._check_cli()
        workers.pop()()
        callback, args = callbacks.pop()
        callback(*args)
        assert wizard._cli_status.GetLabel() == "Could not check for Claude Code CLI."
        assert "CLI probe failed" in wizard._cli_detail.GetLabel()
        assert wizard._cli_check_btn.IsShown()
        assert not wizard._cli_check_pending
        assert spoken[-1].startswith("Could not check for Claude Code CLI")
    finally:
        wizard.Destroy()


def test_cli_result_is_discarded_after_leaving_the_setup_step(wx_app, monkeypatch):
    workers = []
    callbacks = []

    class Thread:
        def __init__(self, target, daemon=False):
            assert daemon
            self.target = target

        def start(self):
            workers.append(self.target)

    monkeypatch.setattr(app.threading, "Thread", Thread)
    monkeypatch.setattr(
        app, "_call_after_alive", lambda callback, *args: callbacks.append((callback, args))
    )
    monkeypatch.setattr(app, "announce", lambda _message: None)
    monkeypatch.setattr(app.SetupWizard, "_find_selected_cli_for_backend", lambda _backend: None)
    monkeypatch.setattr(
        app.SetupWizard,
        "_selected_install_argv_for_backend",
        lambda _backend: ["installer"],
    )
    wizard = app.SetupWizard(None)
    try:
        wizard._step = 1
        wizard._check_cli()
        workers.pop()()
        wizard._show_step(0)
        callback, args = callbacks.pop()
        callback(*args)
        assert wizard._cli_status.GetLabel() == "Checking for Claude Code CLI…"
        assert not wizard._cli_check_pending
    finally:
        wizard.Destroy()
