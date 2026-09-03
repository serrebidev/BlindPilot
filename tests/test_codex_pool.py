"""Codex's app-server, held across turns and shared between tabs."""

from __future__ import annotations

import json

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


def test_an_interrupt_codex_never_confirms_is_reported_unconfirmed():
    """The verify half of "interrupt, verify, kill if unsure"."""
    proc = _FakeProc()
    server = agent_backends.CodexServer(proc)
    assert server.interrupt("thread-1", "turn-1", 0.01) is False


def test_the_verify_budget_is_half_the_teardown_budget():
    """It has to fit inside _CANCEL_JOIN_SECONDS with room for the join."""
    import blindpilot_app

    assert agent_backends._CODEX_INTERRUPT_VERIFY_SECONDS == 1.5
    assert agent_backends._CODEX_INTERRUPT_VERIFY_SECONDS < blindpilot_app._CANCEL_JOIN_SECONDS


def test_the_codex_held_process_satisfies_the_pool_contract():
    def build() -> backend_pool.HeldProcess:
        return backend_pool.HeldProcess(
            agent_backends.CodexServer(_FakeProc()), agent_backends.codex_adapter()
        )

    check_pool_contract(build, "CodexServer")
