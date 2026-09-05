"""Send must adopt a startup in progress, never race it with another CLI."""

import threading

import agent_backends as ab
import pytest


def test_posix_terminal_drains_before_worker_adopts_it(monkeypatch):
    pexpect = pytest.importorskip("pexpect")
    produced = threading.Event()
    ended = threading.Event()

    class Child:
        reads = 0

        def read_nonblocking(self, size, timeout):
            self.reads += 1
            if self.reads == 1:
                return "startup frame"
            produced.set()
            raise pexpect.EOF("closed")

    child = Child()
    monkeypatch.setattr(ab.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(pexpect, "spawn", lambda *args, **kwargs: child)
    terminal, read = ab._spawn_freebuff_pty(["freebuff"], ".", ended)
    assert produced.wait(3), "Startup output was left unread until adoption"
    assert ended.wait(3)
    assert terminal is child
    assert read(0) == "startup frame"
    assert read(0) == ""


def test_send_waits_for_starting_terminal(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    claimed = threading.Event()
    terminal = object()
    results = []
    monkeypatch.setattr(ab, "_freebuff_prewarm", None)
    monkeypatch.setattr(ab, "find_backend_cli", lambda _: "freebuff")
    monkeypatch.setattr(ab, "set_freebuff_model", lambda _: None)
    monkeypatch.setattr(ab, "_freebuff_chat_dirs", lambda _: {"old-chat": 1})

    def spawn(*args):
        entered.set()
        assert release.wait(3)
        return terminal, lambda _: ""

    monkeypatch.setattr(ab, "_spawn_freebuff_pty", spawn)
    ab.prewarm_freebuff(".", None, "model")
    assert entered.wait(3)

    def take():
        results.append(ab._take_freebuff_prewarm(".", None, "model"))
        claimed.set()

    thread = threading.Thread(target=take)
    thread.start()
    try:
        assert not claimed.wait(0.1)
    finally:
        release.set()
        thread.join(3)
    assert claimed.is_set()
    assert results[0]["pty"] is terminal
    assert results[0]["before"] == {"old-chat": 1}
    assert ab._freebuff_prewarm is None


def test_send_invalidates_delayed_start(monkeypatch):
    # Hold the background thread before it starts, just as the one-second
    # delay after a completed turn does. Send claims an empty slot first.
    pending = []

    class DeferredThread:
        def __init__(self, target, **kwargs):
            pending.append(target)

        def start(self):
            pass

    monkeypatch.setattr(ab, "_freebuff_prewarm", None)
    monkeypatch.setattr(ab, "find_backend_cli", lambda _: "freebuff")
    monkeypatch.setattr(ab.threading, "Thread", DeferredThread)
    spawned = []
    monkeypatch.setattr(ab, "_spawn_freebuff_pty", lambda *args: spawned.append(args))
    ab.prewarm_freebuff(".", None, "model")
    assert ab._take_freebuff_prewarm(".", None, "model") is None
    pending[0]()
    assert spawned == []
    assert ab._freebuff_prewarm is None
