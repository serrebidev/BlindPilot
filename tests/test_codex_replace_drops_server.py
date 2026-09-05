"""Installing or updating Codex lets go of the held app-server first.

The app-server BlindPilot keeps between turns is the executable npm is about
to replace, and Windows will not overwrite an executable that is running. The
update then failed with a permissions error and the old version stayed.
"""

from __future__ import annotations

import pytest

import backend_pool
import blindpilot_app as app


@pytest.fixture
def dropped(monkeypatch):
    keys: list[tuple] = []
    pool = type("PoolStub", (), {"drop": lambda self, key: keys.append(key)})()
    monkeypatch.setattr(backend_pool, "pool", lambda: pool)
    monkeypatch.setattr(app, "_find_npm", lambda: "npm")
    monkeypatch.setattr(app, "find_backend_cli", lambda backend: "C:/managed/codex.cmd")
    # None is "could not be started": both paths give up there, after the drop.
    monkeypatch.setattr(app, "_run_logged_process", lambda *args, **kwargs: None)
    return keys


def test_updating_codex_drops_the_held_app_server(dropped, monkeypatch):
    monkeypatch.setattr(app, "_npm_update_argv", lambda backend: ["npm", "install", "codex"])
    log: list[str] = []

    assert app.update_backend(app.BACKEND_CODEX, log.append) is False

    assert dropped == [backend_pool.pool_key(app.BACKEND_CODEX)]
    assert any("app-server" in line for line in log)


def test_installing_codex_drops_the_held_app_server(dropped, monkeypatch):
    monkeypatch.setattr(app, "_npm_install_argv", lambda backend: ["npm", "install", "codex"])

    assert app.install_backend(app.BACKEND_CODEX, lambda _line: None) is None

    assert dropped == [backend_pool.pool_key(app.BACKEND_CODEX)]


def test_other_backends_leave_the_codex_server_alone(dropped, monkeypatch):
    monkeypatch.setattr(app, "_npm_update_argv", lambda backend: ["npm", "install", "freebuff"])
    monkeypatch.setattr(app, "freebuff_model_options", lambda: ([], [], "", "", ""))

    app.update_backend(app.BACKEND_FREEBUFF, lambda _line: None)

    assert dropped == []
