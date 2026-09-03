"""Codex's app-server, held across turns and shared between tabs."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import agent_backends
import backend_pool
from pool_contract import check_pool_contract


class _FakeProc:
    """A Popen stand-in whose stdout is a list of JSONL lines to hand back."""

    def __init__(self, lines: list[str] | None = None) -> None:
        self.stdin = _Sink()
        self.stdout = iter(lines or [])
        self.stderr = iter(())
        self.killed = False
        self._returncode: int | None = None

    def poll(self) -> int | None:
        return self._returncode

    def kill(self) -> None:
        self.killed = True
        self._returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self._returncode = self._returncode or 0
        return self._returncode


class _Sink:
    def __init__(self) -> None:
        self.written: list[str] = []

    def write(self, text: str) -> None:
        self.written.append(text)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_a_running_app_server_is_alive_and_a_killed_one_is_not():
    proc = _FakeProc()
    server = agent_backends.CodexServer(proc)
    adapter = agent_backends.codex_adapter()
    assert adapter.alive(server) is True
    proc.kill()
    assert adapter.alive(server) is False


def test_stopping_the_server_ends_its_process_group():
    stopped: list[object] = []
    proc = _FakeProc()
    server = agent_backends.CodexServer(proc)
    original = agent_backends.end_process_group
    agent_backends.end_process_group = lambda p, timeout=0.0: stopped.append(p)
    try:
        agent_backends.codex_adapter().stop(server)
    finally:
        agent_backends.end_process_group = original
    assert stopped == [proc]


def test_an_interrupt_asks_codex_to_stop_the_named_turn():
    proc = _FakeProc()
    server = agent_backends.CodexServer(proc)
    server.confirm_interrupt = lambda _thread, _turn, _timeout: True
    assert server.interrupt("thread-1", "turn-1", 0.01) is True
    sent = [json.loads(line) for line in proc.stdin.written]
    assert any(m.get("method") == "turn/interrupt" for m in sent)
    interrupt = next(m for m in sent if m.get("method") == "turn/interrupt")
    assert interrupt["params"] == {"threadId": "thread-1", "turnId": "turn-1"}


def test_an_interrupt_codex_confirms_is_reported_confirmed():
    """The reader seeing turn/completed for that turn id is the confirmation."""
    proc = _FakeProc(
        [
            json.dumps(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turn": {"id": "turn-1", "status": "interrupted"},
                    },
                }
            )
        ]
    )
    server = agent_backends.CodexServer(proc)
    server.start_readers()

    assert server.interrupt("thread-1", "turn-1", 5.0) is True


def test_an_interrupt_codex_never_confirms_is_reported_unconfirmed():
    """The verify half of "interrupt, verify, abandon the thread if unsure".

    The app server here answers nothing at all, so the only honest report is
    that the turn was not confirmed stopped — and the only way to know that is
    to have waited the whole budget for it. An implementation that returned
    False without waiting would look identical to this one from the outside,
    which is why the wait itself is asserted.
    """
    # No reader started, so nothing can ever report the turn completed: this is
    # a live app server that simply does not answer, not one that has died.
    proc = _FakeProc()
    server = agent_backends.CodexServer(proc)

    started = time.monotonic()
    assert server.interrupt("thread-1", "turn-1", 0.3) is False
    waited = time.monotonic() - started

    assert waited >= 0.3, f"it gave up after {waited:.3f}s without waiting to be sure"
    sent = [json.loads(line) for line in proc.stdin.written]
    assert any(message.get("method") == "turn/interrupt" for message in sent)


def test_a_notification_is_kept_for_a_thread_nobody_is_reading_yet():
    """thread/start answers a fraction before its turn subscribes.

    Anything the app server sends in that gap belongs to the turn about to
    start, so it is buffered rather than dropped — otherwise a turn silently
    loses whatever Codex said first.
    """
    early = {"method": "item/started", "params": {"threadId": "thread-1", "item": {}}}
    proc = _FakeProc([json.dumps(early)])
    server = agent_backends.CodexServer(proc)
    server.start_readers()
    for reader in server._readers:
        reader.join(timeout=5)

    inbox = server.inbox()
    server.attach("thread-1", inbox)

    assert inbox.get(timeout=5) == early


def test_a_reply_goes_to_the_turn_that_asked_and_not_to_another_tab():
    """One stdout carries every tab; a reply is nobody else's business."""
    reply = {"id": 11, "result": {"thread": {"id": "thread-1"}}}
    proc = _FakeProc([json.dumps(reply)])
    server = agent_backends.CodexServer(proc)
    mine = server.inbox()
    theirs = server.inbox()
    server.attach("thread-2", theirs)
    server.expect(11, mine)
    server.start_readers()

    assert mine.get(timeout=5) == reply
    assert theirs.get(timeout=5) is agent_backends._CODEX_CLOSED


def test_each_tab_only_hears_its_own_conversation():
    mine = {"method": "item/completed", "params": {"threadId": "thread-1", "item": {}}}
    theirs = {"method": "item/completed", "params": {"threadId": "thread-2", "item": {}}}
    proc = _FakeProc([json.dumps(theirs), json.dumps(mine)])
    server = agent_backends.CodexServer(proc)
    first = server.inbox()
    second = server.inbox()
    server.attach("thread-1", first)
    server.attach("thread-2", second)
    server.start_readers()

    assert first.get(timeout=5) == mine
    assert second.get(timeout=5) == theirs


def test_a_turn_reading_a_server_that_dies_is_woken_rather_than_left_waiting():
    proc = _FakeProc([])
    server = agent_backends.CodexServer(proc)
    inbox = server.inbox()
    server.attach("thread-1", inbox)
    server.start_readers()

    assert inbox.get(timeout=5) is agent_backends._CODEX_CLOSED


def test_the_verify_budget_is_half_the_teardown_budget():
    """It has to fit inside _CANCEL_JOIN_SECONDS with room for the join."""
    import blindpilot_app

    assert agent_backends._CODEX_INTERRUPT_VERIFY_SECONDS == 1.5
    assert agent_backends._CODEX_INTERRUPT_VERIFY_SECONDS < blindpilot_app._CANCEL_JOIN_SECONDS
    # Pin the relationship the name claims, not just the current numbers: if
    # _CANCEL_JOIN_SECONDS moved, the "half" the docstring promises should
    # move with it rather than silently drift apart while both assertions
    # above kept passing.
    assert agent_backends._CODEX_INTERRUPT_VERIFY_SECONDS == blindpilot_app._CANCEL_JOIN_SECONDS / 2


def test_an_interrupt_with_no_thread_id_sends_nothing():
    proc = _FakeProc()
    server = agent_backends.CodexServer(proc)
    assert server.interrupt("", "turn-1", 0.01) is False
    assert proc.stdin.written == []


def test_an_interrupt_with_no_turn_id_sends_nothing():
    proc = _FakeProc()
    server = agent_backends.CodexServer(proc)
    assert server.interrupt("thread-1", "", 0.01) is False
    assert proc.stdin.written == []


def test_an_interrupt_that_cannot_be_sent_is_reported_unconfirmed():
    """A dead stdin means the message never reached Codex, so nothing to confirm."""
    proc = _FakeProc()
    proc.stdin = None
    server = agent_backends.CodexServer(proc)
    assert server.interrupt("thread-1", "turn-1", 0.01) is False


def test_the_codex_held_process_satisfies_the_pool_contract():
    def build() -> backend_pool.HeldProcess:
        return backend_pool.HeldProcess(
            agent_backends.CodexServer(_FakeProc()), agent_backends.codex_adapter()
        )

    check_pool_contract(build, "CodexServer")


def test_a_second_turn_reuses_the_app_server_the_first_one_left():
    """The whole point: one process, many prompts."""
    pool = backend_pool.BackendPool()
    started: list[object] = []

    def start() -> agent_backends.CodexServer:
        server = agent_backends.CodexServer(_FakeProc())
        started.append(server)
        return server

    adapter = agent_backends.codex_adapter()._replace(start=start)
    key = backend_pool.pool_key("codex")
    try:
        first = pool.take(key)
        assert first is None
        held = backend_pool.HeldProcess(adapter.start(), adapter)
        pool.keep(key, held)
        assert pool.take(key) is held
        assert len(started) == 1, "a second app-server was started for the second turn"
    finally:
        pool.drop_all()


def test_a_dead_app_server_is_replaced_on_the_next_turn():
    pool = backend_pool.BackendPool()
    adapter = agent_backends.codex_adapter()
    proc = _FakeProc()
    key = backend_pool.pool_key("codex")
    try:
        pool.keep(key, backend_pool.HeldProcess(agent_backends.CodexServer(proc), adapter))
        proc.kill()
        assert pool.take(key) is None, "a dead server was handed to the next turn"
    finally:
        pool.drop_all()


def test_the_shared_server_is_started_from_home_not_a_project(monkeypatch):
    """One server serves every tab, so it cannot belong to one project's
    directory. The per-turn cwd is passed on thread/start instead."""
    seen: dict = {}

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["cwd"] = kwargs.get("cwd")
        raise OSError("stop here, the launch arguments are what is under test")

    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _b: "codex")
    monkeypatch.setattr(agent_backends, "_codex_app_server_binary", lambda b: b)
    monkeypatch.setattr(agent_backends, "subprocess_env", lambda _b: {})
    monkeypatch.setattr(agent_backends.subprocess, "Popen", fake_popen)
    with pytest.raises(OSError):
        agent_backends._start_codex_server()
    assert seen["cwd"] == str(Path.home())
    assert "app-server" in seen["cmd"]
    assert "--stdio" in seen["cmd"]


def test_the_shared_server_is_launched_without_a_console_window(monkeypatch):
    """Regression guard for the no-window launch flags, now that the launch
    has moved out of the worker.

    The people this application is for cannot see a console window steal their
    screen reader's focus, and the shared server outlives every turn, so one
    that appeared would stay there.
    """
    seen: dict = {}

    def fake_popen(cmd, **kwargs):
        seen.update(kwargs)
        raise OSError("enough")

    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _b: "codex")
    monkeypatch.setattr(agent_backends, "_codex_app_server_binary", lambda b: b)
    monkeypatch.setattr(agent_backends, "subprocess_env", lambda _b: {})
    monkeypatch.setattr(agent_backends.subprocess, "Popen", fake_popen)
    with pytest.raises(OSError):
        agent_backends._start_codex_server()
    for key, value in agent_backends.no_window_kwargs().items():
        assert seen.get(key) == value


class _ScriptedProc(_FakeProc):
    """An app server that answers whatever the worker actually asks.

    Each line waits for the request it replies to, because the process is read
    by a thread of its own now: a fixed transcript would be routed before the
    turn had registered any interest in it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.stdout = self._script()

    def _asked(self, method: str, timeout: float = 10.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            for line in list(self.stdin.written):
                message = json.loads(line)
                if message.get("method") == method:
                    return message
            if time.monotonic() >= deadline:
                raise AssertionError(f"the worker never sent {method}")
            time.sleep(0.005)

    def _script(self):
        start = self._asked("thread/start")
        yield json.dumps({"id": start["id"], "result": {"thread": {"id": "thread-1"}}})
        turn = self._asked("turn/start")
        yield json.dumps({"id": turn["id"], "result": {"turn": {"id": "turn-1"}}})
        yield json.dumps(
            {
                "method": "item/agentMessage/delta",
                "params": {"threadId": "thread-1", "itemId": "m1", "delta": "hello"},
            }
        )
        yield json.dumps(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            }
        )


def test_a_turn_borrows_the_server_and_leaves_it_running(monkeypatch):
    """The turn no longer owns the process, so it must not stop it either."""
    proc = _ScriptedProc()
    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _b: "codex")
    monkeypatch.setattr(agent_backends, "_codex_app_server_binary", lambda b: b)
    monkeypatch.setattr(agent_backends, "subprocess_env", lambda _b: {})
    monkeypatch.setattr(agent_backends.subprocess, "Popen", lambda *_a, **_k: proc)
    completed: list[str] = []
    failures: list[str] = []
    worker = agent_backends.CodexWorker(
        "hello",
        None,
        ".",
        "default",
        on_session=lambda _s: None,
        on_started=lambda: None,
        on_activity=lambda _k, _v: None,
        on_complete=completed.append,
        on_failed=failures.append,
        on_done=lambda: None,
    )

    worker.run()

    assert not failures, failures
    assert completed == ["hello"]
    assert not proc.killed, "the turn killed a process every other tab is using"
    held = backend_pool.pool().take(backend_pool.pool_key(agent_backends.BACKEND_CODEX))
    assert held is not None, "the turn did not leave the app server for the next one"
    assert held.handle.proc is proc
    # Sent once, by the process's own handshake rather than by the turn.
    sent = [json.loads(line) for line in proc.stdin.written]
    assert [message.get("method") for message in sent].count("initialize") == 1
    # And the turn gave its place in the routing tables back on the way out.
    assert held.handle._threads == {}
