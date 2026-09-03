"""Codex's app-server, held across turns and shared between tabs."""

from __future__ import annotations

import json
import threading
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

    # Not the whole 0.3 to the microsecond: Windows' clock granularity means a
    # wait of exactly the budget can be measured a hair short of it. The
    # distinction being drawn is against an implementation that waits not at all.
    assert waited >= 0.25, f"it gave up after {waited:.3f}s without waiting to be sure"
    sent = [json.loads(line) for line in proc.stdin.written]
    assert any(message.get("method") == "turn/interrupt" for message in sent)


def test_nothing_said_the_instant_a_thread_starts_can_fall_between_the_two():
    """The gap between thread/start answering and its turn subscribing.

    There is none: the reply names the conversation, and the one reader binds
    it to the asking turn's queue before it hands that reply on. Everything
    arrives on one stdout read by one thread, so a notification for the new
    conversation can only be a later message - by which time the binding is
    already there. Here the notification is the very next line.
    """
    reply = {"id": 11, "result": {"thread": {"id": "thread-1"}}}
    straight_after = {
        "method": "item/started",
        "params": {"threadId": "thread-1", "turnId": "turn-1", "item": {}},
    }
    proc = _FakeProc([json.dumps(reply), json.dumps(straight_after)])
    server = agent_backends.CodexServer(proc)
    inbox = server.inbox()
    server.expect_thread(11, inbox)
    server.start_readers()

    assert inbox.get(timeout=5) == reply
    assert inbox.get(timeout=5) == straight_after


def test_what_a_finished_turn_says_on_its_way_out_is_not_kept_for_the_next_one():
    """Stop is answered a moment later, and by then nobody is listening.

    Codex goes on producing for a few hundred milliseconds after an interrupt
    is sent. Keeping those messages for whoever reads the conversation next
    hands the following turn the previous turn's ending to act on.
    """
    proc = _FakeProc()
    server = agent_backends.CodexServer(proc)
    first = server.inbox()
    server.attach("thread-1", first)
    server.detach("thread-1", first)

    server._route({"method": "item/completed", "params": {"threadId": "thread-1", "item": {}}})

    assert server._threads == {}, "a conversation nobody is reading was kept anyway"
    second = server.inbox()
    server.attach("thread-1", second)
    assert second.empty(), "the next turn was handed the last turn's leftovers"


def _bare_worker(session_id=None, prompt="hello"):
    """A worker with none of its callbacks doing anything, for unit work."""
    return agent_backends.CodexWorker(
        prompt,
        session_id,
        ".",
        "default",
        on_session=lambda _s: None,
        on_started=lambda: None,
        on_activity=lambda _k, _v: None,
        on_complete=lambda _v: None,
        on_failed=lambda _v: None,
        on_done=lambda: None,
    )


def _cancelled_before_it_read_its_reply(server):
    """A turn bound to a conversation it never learned the id of.

    The reader binds the thread from the `thread/start` reply. A turn stopped
    between that routing and its own reading of the reply has a binding on the
    server and an empty `_thread_id`, so it cannot detach by id.
    """
    worker = _bare_worker()
    inbox = server.inbox()
    server.expect_thread(11, inbox)
    server._route({"id": 11, "result": {"thread": {"id": "thread-new"}}})
    assert server._threads == {"thread-new": inbox}, "the reply did not bind the conversation"
    worker._server = server
    worker._inbox = inbox
    assert worker._thread_id == "", "this turn is supposed never to have learned its id"
    return worker


def test_a_conversation_bound_by_a_reply_nobody_read_is_not_left_behind():
    """Stop between the reply being routed and the turn reading it.

    No race is needed: the binding happens on the reader thread, the reading
    on the turn's. Detaching by an id the turn never learned would leave the
    conversation registered for the life of the process -- which both makes
    every unnamed message look ambiguous and stops the server ever being seen
    as free again.
    """
    server = agent_backends.CodexServer(_FakeProc())
    worker = _cancelled_before_it_read_its_reply(server)

    worker._release()

    assert server._threads == {}, "a conversation the turn never named was left bound"


def test_only_the_tabs_actually_reading_count_towards_ambiguity():
    """The unnamed-message fallback must not be confused by a stale entry.

    `configWarning` carries no threadId. With one conversation being read there
    is no doubt who should hear it, and one orphan left behind by a cancelled
    turn must not make it look as though there were two.
    """
    warning = {"method": "configWarning", "params": {"summary": "check your config"}}
    server = agent_backends.CodexServer(_FakeProc())
    _cancelled_before_it_read_its_reply(server)._release()
    live = server.inbox()
    server.attach("thread-2", live)

    server._route(warning)

    assert live.get(timeout=5) == warning


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


class _ProcWithLeftovers(_FakeProc):
    """The app server as it is a moment after Stop, when the next prompt lands.

    Turn 1 was interrupted. Codex honours that a few hundred milliseconds
    later, by which time the person has typed again and turn 2 is resuming the
    same conversation. So turn 1's trailing output arrives around turn 2's
    thread/resume: some of it before the resume is answered, some after.
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

    @staticmethod
    def _leftovers_of_turn_one() -> list[str]:
        return [
            json.dumps(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "item": {"id": "m0", "type": "agentMessage", "text": "half a thought"},
                    },
                }
            ),
            json.dumps(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turn": {"id": "turn-1", "status": "interrupted"},
                    },
                }
            ),
        ]

    def _script(self):
        resume = self._asked("thread/resume")
        # Turn 1's ending, arriving while nobody is reading the conversation.
        yield from self._leftovers_of_turn_one()
        yield json.dumps({"id": resume["id"], "result": {"thread": {"id": "thread-1"}}})
        # And the rest of it, now that turn 2 is reading.
        yield from self._leftovers_of_turn_one()
        turn = self._asked("turn/start")
        yield json.dumps({"id": turn["id"], "result": {"turn": {"id": "turn-2"}}})
        yield json.dumps(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-2",
                    "itemId": "m1",
                    "delta": "the answer",
                },
            }
        )
        yield json.dumps(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "turn-2", "status": "completed"},
                },
            }
        )


def _turn(proc, monkeypatch, session_id=None, prompt="hello"):
    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _b: "codex")
    monkeypatch.setattr(agent_backends, "_codex_app_server_binary", lambda b: b)
    monkeypatch.setattr(agent_backends, "subprocess_env", lambda _b: {})
    monkeypatch.setattr(agent_backends.subprocess, "Popen", lambda *_a, **_k: proc)
    completed: list[str] = []
    failures: list[str] = []
    activity: list[tuple] = []
    worker = agent_backends.CodexWorker(
        prompt,
        session_id,
        ".",
        "default",
        on_session=lambda _s: None,
        on_started=lambda: None,
        on_activity=lambda kind, value: activity.append((kind, value)),
        on_complete=completed.append,
        on_failed=failures.append,
        on_done=lambda: None,
    )
    worker.run()
    return worker, completed, failures, activity


def test_a_turn_is_not_failed_by_what_the_turn_before_it_said_on_its_way_out(monkeypatch):
    """The bug this guards: every Stop poisoned the next message in that tab.

    Stop detached the conversation, Codex's trailing `turn/completed` for the
    interrupted turn was kept for whoever read it next, and turn 2 acted on it
    - failing instantly with "Codex turn was interrupted" before it had said
    anything at all. Retrying worked, so it read as a random glitch; for a
    person who cannot see a spinner that is the worst shape a failure can take.
    """
    proc = _ProcWithLeftovers()

    _worker, completed, failures, activity = _turn(proc, monkeypatch, session_id="thread-1")

    assert failures == [], f"the leftovers of the interrupted turn failed this one: {failures}"
    assert completed == ["the answer"]
    assert ("assistant", "half a thought") not in activity, "turn 1's output was spoken in turn 2"


def test_two_first_turns_at_once_start_one_server_and_neither_is_killed(monkeypatch):
    """Two tabs sending their first Codex message together.

    Both used to find the pool empty, both launched a server, and the second
    one handed back displaced - and stopped - the first, killing a live turn
    with "Codex app server closed before the turn completed".
    """
    started: list[agent_backends.CodexServer] = []
    barrier = threading.Barrier(2)

    def slow_start() -> agent_backends.CodexServer:
        server = agent_backends.CodexServer(_FakeProc())
        time.sleep(0.05)
        started.append(server)
        return server

    monkeypatch.setattr(agent_backends, "_start_codex_server", slow_start)
    borrowed: list = []

    def borrow() -> None:
        worker = agent_backends.CodexWorker(
            "hello",
            None,
            ".",
            "default",
            on_session=lambda _s: None,
            on_started=lambda: None,
            on_activity=lambda _k, _v: None,
            on_complete=lambda _v: None,
            on_failed=lambda _v: None,
            on_done=lambda: None,
        )
        barrier.wait(timeout=10)
        borrowed.append(worker._borrow_server())

    threads = [threading.Thread(target=borrow, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()

    assert len(started) == 1, f"{len(started)} app servers were started for two first turns"
    assert borrowed[0] is borrowed[1], "the two tabs were given different servers"
    assert not started[0].proc.killed, "the one live server was stopped mid-turn"


def test_a_server_that_cannot_start_a_conversation_does_not_serve_the_next_turn(monkeypatch):
    """A broken app server must not fail every prompt for the next quarter hour.

    The per-turn process this replaces got a fresh start each time; a held one
    that answers `thread/start` with an error has to be let go the same way.
    """

    class _RefusesToStart(_FakeProc):
        def __init__(self) -> None:
            super().__init__()
            self.stdout = self._script()

        def _script(self):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                for line in list(self.stdin.written):
                    message = json.loads(line)
                    if message.get("method") == "thread/start":
                        yield json.dumps(
                            {"id": message["id"], "error": {"message": "no conversation for you"}}
                        )
                        return
                time.sleep(0.005)

    proc = _RefusesToStart()
    _worker, _completed, failures, _activity = _turn(proc, monkeypatch)

    assert failures == ["no conversation for you"]
    key = backend_pool.pool_key(agent_backends.BACKEND_CODEX)
    assert backend_pool.pool().take(key) is None, "the broken server was left for the next turn"


def test_a_conversation_being_read_is_never_dropped_by_another_tabs_failure(monkeypatch):
    """One tab's bad session id must not end another tab's live turn."""
    server = agent_backends.CodexServer(_FakeProc())
    key = backend_pool.pool_key(agent_backends.BACKEND_CODEX)
    backend_pool.pool().keep(key, backend_pool.HeldProcess(server, agent_backends.codex_adapter()))
    monkeypatch.setattr(agent_backends, "_start_codex_server", lambda: server)
    mid_turn = _bare_worker()
    assert mid_turn._borrow_server() is not None
    server.attach("someone-elses-thread", mid_turn._inbox)

    giving_up = _bare_worker(session_id="no-such-thread")
    assert giving_up._borrow_server() is not None
    giving_up._discard_server()

    assert backend_pool.pool().take(key) is not None, "a tab mid-turn had its server taken away"


def test_the_watch_on_a_turn_goes_when_the_last_waiter_lets_go():
    """One event per interrupted turn, kept for ever, is a leak in a process
    that now outlives thousands of turns."""
    server = agent_backends.CodexServer(_FakeProc())

    server.watch_turn("turn-1")
    server.watch_turn("turn-1")
    server.forget_turn("turn-1")
    assert server._turns, "the second waiter was left with nothing to wait on"
    server.forget_turn("turn-1")
    assert server._turns == {}

    # And an interrupt, which is the caller with no turn of its own to tidy up.
    server.interrupt("thread-1", "turn-2", 0.01)
    assert server._turns == {}, "an interrupt left its watch behind"


def test_a_turn_gives_its_place_back_and_leaves_nothing_behind(monkeypatch):
    proc = _ScriptedProc()
    _worker, completed, failures, _activity = _turn(proc, monkeypatch)

    assert not failures and completed == ["hello"]
    key = backend_pool.pool_key(agent_backends.BACKEND_CODEX)
    held = backend_pool.pool().take(key)
    assert held is not None
    assert held.handle._threads == {}, "the conversation was left registered"
    assert held.handle._turns == {}, "the turn's completion watch was left behind"
    assert held.handle._waiting == {}, "a reply the turn asked for is still expected"
    assert held.handle._thread_replies == set()


def test_a_server_a_turn_is_still_waiting_on_is_not_taken_away(monkeypatch):
    """Tab A has sent thread/start and is waiting; tab B fails and gives up.

    Tab A holds the process without being bound to any conversation yet -- MCP
    servers can take hundreds of milliseconds to come up before the reply
    arrives -- so counting bound conversations says nobody is there. Counting
    borrowers says otherwise, which is the difference between tab A finishing
    its turn and hearing "Codex app server closed before the turn completed".
    """
    server = agent_backends.CodexServer(_FakeProc())
    key = backend_pool.pool_key(agent_backends.BACKEND_CODEX)
    backend_pool.pool().keep(key, backend_pool.HeldProcess(server, agent_backends.codex_adapter()))
    monkeypatch.setattr(agent_backends, "_start_codex_server", lambda: server)

    waiting = _bare_worker()
    assert waiting._borrow_server() is not None
    assert server._threads == {}, "tab A is supposed to be bound to nothing yet"

    giving_up = _bare_worker(session_id="no-such-thread")
    assert giving_up._borrow_server() is not None
    giving_up._discard_server()

    assert backend_pool.pool().take(key) is not None, (
        "a turn waiting on thread/start had its app server stopped underneath it"
    )
    assert not server.proc.killed


def test_the_last_turn_holding_a_broken_server_is_the_one_that_drops_it(monkeypatch):
    """With nobody else holding it, a server that cannot start a conversation goes."""
    server = agent_backends.CodexServer(_FakeProc())
    key = backend_pool.pool_key(agent_backends.BACKEND_CODEX)
    backend_pool.pool().keep(key, backend_pool.HeldProcess(server, agent_backends.codex_adapter()))
    monkeypatch.setattr(agent_backends, "_start_codex_server", lambda: server)

    worker = _bare_worker(session_id="no-such-thread")
    assert worker._borrow_server() is not None
    worker._discard_server()

    assert backend_pool.pool().take(key) is None, "the broken server was left for the next turn"


def test_a_turn_gives_its_borrow_back_when_it_ends():
    server = agent_backends.CodexServer(_FakeProc())
    worker = _bare_worker()
    worker._server = server
    worker._inbox = server.inbox()
    server.borrow()
    worker._borrowed = True

    worker._release()
    worker._release()

    assert server.borrower_count() == 0, "the turn is over but still counted as holding it"


def test_a_reply_of_the_wrong_shape_does_not_end_every_tabs_turn():
    """The reader serves everyone, so nothing it parses may take it down.

    Before the thread binding moved into the reader, this same malformed reply
    was parsed inside one worker and failed one turn. On the reader thread it
    would be read as the stream breaking and end all of them at once.
    """
    nonsense = {"id": 11, "result": {"thread": "not an object"}}
    healthy = {"method": "item/started", "params": {"threadId": "thread-1", "item": {}}}
    proc = _FakeProc([json.dumps(nonsense), json.dumps(healthy)])
    server = agent_backends.CodexServer(proc)
    mine = server.inbox()
    server.expect_thread(11, mine)
    other = server.inbox()
    server.attach("thread-1", other)
    server.start_readers()

    assert mine.get(timeout=5) == nonsense
    assert other.get(timeout=5) == healthy, "one malformed reply closed the whole server"
    assert server.read_error() == ""


def test_a_question_from_a_turn_that_is_over_is_declined_rather_than_ignored():
    """Codex holds an unanswered request id open, so silence is not refusal."""
    sent: list[dict] = []
    worker = _bare_worker()
    worker._turn_id = "turn-2"
    worker._send = lambda message: sent.append(message) or True

    worker._decline_server_request(
        {
            "method": "item/commandExecution/requestApproval",
            "id": 77,
            "params": {"threadId": "thread-1", "turnId": "turn-1"},
        }
    )
    worker._decline_server_request(
        {
            "method": "item/tool/requestUserInput",
            "id": 78,
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "questions": [{"id": "pick", "question": "Which one?", "options": []}],
            },
        }
    )

    assert sent == [
        {"id": 77, "result": {"decision": "decline"}},
        {"id": 78, "result": {"answers": {"pick": {"answers": []}}}},
    ]


def test_a_turn_knows_its_own_whichever_message_names_it_first():
    """Compaction never learns its turn id from a reply.

    `thread/compact/start` answers empty and the turn announces itself, so the
    guard must not depend on `turn/started` arriving before the turn's items.
    Anything else silently drops the compaction's content and its completion,
    and the turn hangs until the reaper takes the process.
    """
    worker = _bare_worker(prompt="/compact")
    worker._turn_asked = True

    item = {"threadId": "thread-1", "turnId": "turn-c", "item": {}}
    assert worker._is_this_turn("item/started", item) is True
    assert agent_backends.CodexWorker._turn_named(item) == "turn-c"
    # And the announcement that follows it is recognised as the same turn.
    worker._turn_id = "turn-c"
    started = {"threadId": "thread-1", "turn": {"id": "turn-c"}}
    assert worker._is_this_turn("turn/started", started) is True


def test_nothing_naming_a_turn_is_this_turn_before_this_turn_is_asked_for():
    """The other half: what makes the leftovers of a stopped turn recognisable."""
    worker = _bare_worker()

    leftover = {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "interrupted"}}
    assert worker._is_this_turn("turn/completed", leftover) is False
    assert worker._is_this_turn("turn/started", {"turn": {"id": "turn-1"}}) is False
    # Nothing naming a turn at all is nobody's leftovers.
    assert worker._is_this_turn("configWarning", {"summary": "check your config"}) is True

    # Once a turn has been asked for, an ending is still not adoptable: a turn
    # cannot finish before the beginning this one never saw.
    worker._turn_asked = True
    assert worker._is_this_turn("turn/completed", leftover) is False
    assert worker._is_this_turn("item/started", {"turnId": "turn-9", "item": {}}) is True


def test_a_compaction_whose_items_arrive_before_its_announcement_still_finishes(monkeypatch):
    """The ordering N5 warned about, driven end to end."""

    class _CompactsOutOfOrder(_FakeProc):
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
            resume = self._asked("thread/resume")
            yield json.dumps({"id": resume["id"], "result": {"thread": {"id": "thread-1"}}})
            compact = self._asked("thread/compact/start")
            yield json.dumps({"id": compact["id"], "result": {}})
            # An item before the announcement, which is the order under test.
            # It has to be one that says something, or the test cannot tell
            # whether it was acted on or quietly dropped.
            yield json.dumps(
                {
                    "method": "item/started",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-c",
                        "item": {
                            "type": "commandExecution",
                            "id": "c1",
                            "command": ["git", "log"],
                        },
                    },
                }
            )
            yield json.dumps(
                {
                    "method": "turn/started",
                    "params": {"threadId": "thread-1", "turn": {"id": "turn-c"}},
                }
            )
            yield json.dumps(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turn": {"id": "turn-c", "status": "completed"},
                    },
                }
            )

    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _b: "codex")
    monkeypatch.setattr(agent_backends, "_codex_app_server_binary", lambda b: b)
    monkeypatch.setattr(agent_backends, "subprocess_env", lambda _b: {})
    monkeypatch.setattr(agent_backends.subprocess, "Popen", lambda *_a, **_k: _CompactsOutOfOrder())
    completed: list[str] = []
    failures: list[str] = []
    activity: list[tuple] = []
    worker = agent_backends.CodexWorker(
        "/compact",
        "thread-1",
        ".",
        "default",
        compact=True,
        on_session=lambda _s: None,
        on_started=lambda: None,
        on_activity=lambda kind, value: activity.append((kind, value)),
        on_complete=completed.append,
        on_failed=failures.append,
        on_done=lambda: None,
    )

    worker.run()

    assert not failures, failures
    assert completed == ["Conversation compacted."]
    assert ("tool", "Running: git log") in activity, (
        "what the compaction did before it announced itself was dropped"
    )


class _StragglesPastTheMark(_FakeProc):
    """The turn before this one goes on talking after this one has asked.

    Some of the interrupted turn's output queues ahead of the mark, which is
    the ordinary case, and the rest arrives after it -- late enough that where
    it sits can no longer tell whose it is.
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

    @staticmethod
    def _said_by_turn_one(text: str) -> str:
        return json.dumps(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "item": {"id": "m0", "type": "agentMessage", "text": text},
                },
            }
        )

    def _script(self):
        resume = self._asked("thread/resume")
        yield json.dumps({"id": resume["id"], "result": {"thread": {"id": "thread-1"}}})
        # Ahead of the mark: named here, and so recognised from here on.
        yield self._said_by_turn_one("half a thought")
        turn = self._asked("turn/start")
        # Behind the mark, and before this turn knows its own id: position
        # cannot tell whose this is, only the name can.
        yield self._said_by_turn_one("and the rest of it")
        yield json.dumps({"id": turn["id"], "result": {"turn": {"id": "turn-2"}}})
        yield json.dumps(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-2",
                    "itemId": "m1",
                    "delta": "the answer",
                },
            }
        )
        yield json.dumps(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "turn-2", "status": "completed"},
                },
            }
        )


def test_the_stopped_turns_words_are_never_spliced_into_the_next_turns_answer(monkeypatch):
    """A straggler past the mark is still the stopped turn's, not this one's.

    `_item_completed` on an agentMessage both speaks the text and adds it to
    the answer, so admitting one of these is the agent saying words it never
    said - to people who read entirely by ear, with nothing on screen to
    contradict it.
    """
    proc = _StragglesPastTheMark()

    _worker, completed, failures, activity = _turn(proc, monkeypatch, session_id="thread-1")

    assert failures == [], failures
    assert completed == ["the answer"], "the stopped turn's words got into the answer"
    spoken = [value for kind, value in activity if kind == "assistant"]
    assert "half a thought" not in spoken
    assert "and the rest of it" not in spoken, "a straggler past the mark was spoken as this turn"


def test_a_turn_already_known_to_be_somebody_elses_stays_that_way():
    """The name is remembered, so lateness stops being a way in."""
    worker = _bare_worker()
    leftover = {"threadId": "thread-1", "turnId": "turn-1", "item": {}}

    # Before the mark, position alone rejects it - and the name is noted.
    assert worker._is_this_turn("item/completed", leftover) is False
    worker._stale_turns.add(agent_backends.CodexWorker._turn_named(leftover))

    # After the mark, position would have let the rest of it through.
    worker._turn_asked = True
    assert worker._is_this_turn("item/completed", leftover) is False
    assert worker._is_this_turn("item/started", {"turnId": "turn-1", "item": {}}) is False
    # A turn nobody has seen before is still this turn's to adopt.
    assert worker._is_this_turn("item/started", {"turnId": "turn-9", "item": {}}) is True


def test_a_server_is_not_dropped_while_another_tab_is_registering_its_borrow(monkeypatch):
    """Between one tab's take and its borrow, another tab must not let go.

    `take` leaves the entry in the pool, so a tab that has been handed the
    server but has not yet been counted is invisible to a count taken outside
    the lock - and a failing tab would drop the process it is a few
    instructions from using.
    """
    server = agent_backends.CodexServer(_FakeProc())
    key = backend_pool.pool_key(agent_backends.BACKEND_CODEX)
    backend_pool.pool().keep(key, backend_pool.HeldProcess(server, agent_backends.codex_adapter()))
    monkeypatch.setattr(agent_backends, "_start_codex_server", lambda: server)

    holding = _bare_worker()
    assert holding._borrow_server() is not None

    handed_over = threading.Event()
    carry_on = threading.Event()
    registered = server.inbox

    def between_the_take_and_the_borrow():
        handed_over.set()
        carry_on.wait(10)
        return registered()

    monkeypatch.setattr(server, "inbox", between_the_take_and_the_borrow)
    arriving = _bare_worker()
    borrowing = threading.Thread(target=arriving._borrow_server, daemon=True)
    borrowing.start()
    assert handed_over.wait(10), "the arriving tab never reached the window under test"

    giving_up = threading.Thread(target=holding._discard_server, daemon=True)
    giving_up.start()
    # Under the fix this is still waiting on the same lock the borrow is
    # registered under; without it, it has already counted one and dropped.
    giving_up.join(timeout=0.2)
    carry_on.set()
    borrowing.join(timeout=10)
    giving_up.join(timeout=10)
    assert not borrowing.is_alive() and not giving_up.is_alive()

    assert backend_pool.pool().take(key) is not None, (
        "the server was dropped from under a tab that had just been handed it"
    )
    assert not server.proc.killed


def test_a_message_that_cannot_be_routed_is_written_down(caplog):
    """Dropping it beats closing the server, but silence is not the trade.

    The turn it was for now waits on a reply that will not come, and the only
    other sign of that is a turn ending at the reaper a quarter of an hour
    later. The record says method and id, never what the message was about.
    """
    proc = _FakeProc([json.dumps({"id": 11, "result": {"thread": "not an object"}})])
    server = agent_backends.CodexServer(proc)
    mine = server.inbox()
    server.expect_thread(11, mine)

    def explode(_message):
        raise TypeError("something nobody anticipated")

    server._route = explode
    with caplog.at_level("WARNING", logger="blindpilot.codex"):
        server.start_readers()
        for reader in server._readers:
            reader.join(timeout=5)

    assert any("could not be routed" in record.getMessage() for record in caplog.records), (
        f"nothing was written down: {[r.getMessage() for r in caplog.records]}"
    )
    written = " ".join(record.getMessage() for record in caplog.records)
    assert "id=11" in written
    assert "not an object" not in written, "the message content was written to the log"
