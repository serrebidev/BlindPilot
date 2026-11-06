"""Installing or updating Codex lets go of the held app-server first.

The app-server BlindPilot keeps between turns is the executable npm is about
to replace, and Windows will not overwrite an executable that is running. The
update then failed with a permissions error and the old version stayed.

Codex may now be holding one process per ChatGPT-plan Account rather than one
for the app, so this has to let go of all of them: leaving an Account's
app-server running fails the update in exactly the same way.
"""

from __future__ import annotations

import pytest

import backend_pool
import blindpilot_app as app


class _Handle:
    """A process the pool can stop, counting the stops."""

    def __init__(self) -> None:
        self.running = True
        self.stops = 0

    def stop(self) -> None:
        self.stops += 1
        self.running = False


class _AnotherBackendsTab:
    """A tab holding a process of another backend, as the pool keys one."""


def _held(pool: backend_pool.BackendPool, key: tuple) -> _Handle:
    adapter = backend_pool.Adapter(
        alive=lambda handle: handle.running,
        interrupt=lambda _handle, _timeout: True,
        stop=lambda handle: handle.stop(),
        busy=lambda _handle: False,
    )
    handle = _Handle()
    pool.keep(key, backend_pool.HeldProcess(handle, adapter))
    return handle


@pytest.fixture
def held(monkeypatch):
    """One process for Codex's own login and one per Account, plus another
    backend's, in a pool the update is pointed at."""
    pool = backend_pool.BackendPool()
    monkeypatch.setattr(backend_pool, "pool", lambda: pool)
    monkeypatch.setattr(app, "_find_npm", lambda: "npm")
    monkeypatch.setattr(app, "find_backend_cli", lambda backend: "C:/managed/codex.cmd")
    # None is "could not be started": both paths give up there, after the drop.
    monkeypatch.setattr(app, "_run_logged_process", lambda *args, **kwargs: None)
    try:
        yield pool
    finally:
        pool.drop_all()


def test_updating_codex_drops_every_held_app_server(held, monkeypatch):
    monkeypatch.setattr(app, "_npm_update_argv", lambda backend: ["npm", "install", "codex"])
    login = _held(held, backend_pool.pool_key(app.BACKEND_CODEX))
    work = _held(held, backend_pool.pool_key(app.BACKEND_CODEX, account=7))
    personal = _held(held, backend_pool.pool_key(app.BACKEND_CODEX, account=9))
    elsewhere = _held(held, backend_pool.pool_key(app.BACKEND_CLAUDE, _AnotherBackendsTab()))
    log: list[str] = []

    assert app.update_backend(app.BACKEND_CODEX, log.append) is False

    assert (login.stops, work.stops, personal.stops) == (1, 1, 1), (
        "an Account's app-server was left running over the executable npm replaces"
    )
    assert elsewhere.stops == 0
    assert any("app-server" in line for line in log)


def test_installing_codex_drops_every_held_app_server(held, monkeypatch):
    monkeypatch.setattr(app, "_npm_install_argv", lambda backend: ["npm", "install", "codex"])
    login = _held(held, backend_pool.pool_key(app.BACKEND_CODEX))
    work = _held(held, backend_pool.pool_key(app.BACKEND_CODEX, account=7))

    assert app.install_backend(app.BACKEND_CODEX, lambda _line: None) is None

    assert (login.stops, work.stops) == (1, 1)


def test_other_backends_leave_the_codex_server_alone(held, monkeypatch):
    monkeypatch.setattr(app, "_npm_update_argv", lambda backend: ["npm", "install", "freebuff"])
    monkeypatch.setattr(app, "freebuff_model_options", lambda: ([], "", ""))
    work = _held(held, backend_pool.pool_key(app.BACKEND_CODEX, account=7))

    app.update_backend(app.BACKEND_FREEBUFF, lambda _line: None)

    assert work.stops == 0
