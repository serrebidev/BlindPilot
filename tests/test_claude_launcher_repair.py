r"""Updating Claude Code on Windows must only touch the native launcher.

`_find_claude` can return `%APPDATA%\npm\claude.cmd` when an npm install
shadows the native one. Copying a PE image over that shim breaks it.
"""

from __future__ import annotations

from pathlib import Path

import blindpilot_app as app


def test_an_npm_shim_is_never_overwritten(monkeypatch, tmp_path):
    shim = tmp_path / "npm" / "claude.cmd"
    newest = tmp_path / ".local" / "share" / "claude" / "versions" / "2.1.226"
    shim.parent.mkdir(parents=True)
    newest.parent.mkdir(parents=True)
    shim.write_text("@echo off", encoding="utf-8")
    newest.write_bytes(b"MZ new")
    monkeypatch.setattr(app.platform, "system", lambda: "Windows")
    monkeypatch.setattr(app.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(app, "_executable_version", lambda binary: "2.1.225")

    assert app._repair_claude_native_update(str(shim), lambda _text: None) is True
    assert shim.read_text(encoding="utf-8") == "@echo off"
    assert Path(shim).suffix == ".cmd"
