"""One Claude Code process kept between turns: who hears what it says."""

from __future__ import annotations

import io
import json
import queue
import time

import claude_session as cs


class _FeedStdout:
    """A stdout the test writes to while the reader runs."""

    def __init__(self):
        self._lines: queue.Queue = queue.Queue()

    def feed(self, event):
        self._lines.put(json.dumps(event) + "\n")

    def feed_raw(self, text):
        self._lines.put(text)

    def close(self):
        self._lines.put(None)

    def __iter__(self):
        return self

    def __next__(self):
        line = self._lines.get()
        if line is None:
            raise StopIteration
        return line


class _Stdin:
    def __init__(self):
        self.written: list[str] = []
        self.closed = False

    def write(self, data):
        if self.closed:
            raise ValueError("closed")
        self.written.append(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True

    def payloads(self):
        return [json.loads(line) for line in self.written]


class _Proc:
    def __init__(self, stdout=None, stderr=None):
        self.stdin = _Stdin()
        self.stdout = stdout if stdout is not None else _FeedStdout()
        self.stderr = stderr if stderr is not None else io.StringIO("")
        self.returncode = None
        self.pid = 4242

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = 1
        self.stdout.close()

    def terminate(self):
        self.kill()


def _settle(predicate, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


WANTS = cs.Wants(cwd="C:/work", permission_mode="default")


def test_the_command_line_is_the_one_a_turn_used_to_build():
    wants = cs.Wants(
        cwd="C:/work", permission_mode="plan", model="opus", effort="high", session_id="s1"
    )
    assert cs.build_command("claude", wants, "stdio") == [
        "claude",
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-prompt-tool",
        "stdio",
        "--permission-mode",
        "plan",
        "--model",
        "opus",
        "--effort",
        "high",
        "--resume",
        "s1",
    ]
    bare = cs.build_command("claude", cs.Wants(cwd="C:/work", permission_mode=""), "")
    assert "--permission-mode" not in bare and "--resume" not in bare


def test_an_attached_turn_receives_events_in_order_and_the_result_closes_nothing():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    events = session.attach()
    proc.stdout.feed({"type": "assistant", "n": 1})
    proc.stdout.feed({"type": "result"})
    assert events.get(timeout=1) == {"type": "assistant", "n": 1}
    assert events.get(timeout=1) == {"type": "result"}
    session.detach()
    assert not proc.stdin.closed, "the result ended the turn, not the process"
    assert session.alive()


def test_the_init_event_names_the_session():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    events = session.attach()
    proc.stdout.feed({"type": "system", "subtype": "init", "session_id": "abc"})
    events.get(timeout=1)
    assert session.session_id == "abc"


def test_events_with_no_turn_attached_wake_the_idle_sink_once_and_wait_for_the_next_turn():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    woken = []
    session.set_idle_sink(lambda: woken.append(1))
    proc.stdout.feed({"type": "assistant", "n": 1})
    proc.stdout.feed({"type": "assistant", "n": 2})
    assert _settle(lambda: len(woken) == 1)
    time.sleep(0.05)
    assert woken == [1], "the sink was told once, not once per event"
    events = session.attach()
    assert events.get(timeout=1)["n"] == 1
    assert events.get(timeout=1)["n"] == 2
    session.detach()
    proc.stdout.feed({"type": "assistant", "n": 3})
    assert _settle(lambda: len(woken) == 2), "a later idle event wakes the sink again"


def test_a_sink_set_after_events_arrived_is_woken_at_once():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    proc.stdout.feed({"type": "assistant"})
    assert _settle(lambda: session._pending)
    woken = []
    session.set_idle_sink(lambda: woken.append(1))
    assert woken == [1]


def test_only_one_turn_can_be_attached():
    session = cs.ClaudeSession(_Proc(), WANTS)
    session.attach()
    try:
        session.attach()
    except RuntimeError:
        pass
    else:
        raise AssertionError("two turns read the same stream")
    assert session.busy()
    session.detach()
    assert not session.busy()


def test_a_process_that_ends_hands_eof_to_the_turn_and_not_to_the_idle_sink():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    events = session.attach()
    proc.returncode = 1
    proc.stdout.close()
    assert events.get(timeout=1) is cs.EOF
    session.detach()
    woken = []
    session.set_idle_sink(lambda: woken.append(1))
    time.sleep(0.05)
    assert woken == [], "a dead process is the pool's business, not a late turn"
    assert not session.alive()
    assert session.returncode() == 1


def test_a_turn_attaching_after_the_end_learns_of_it():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    proc.returncode = 0
    proc.stdout.close()
    assert _settle(lambda: session._ended.is_set())
    assert session.attach().get(timeout=1) is cs.EOF


def test_a_malformed_line_is_skipped_not_fatal():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    events = session.attach()
    proc.stdout.feed_raw("not json\n")
    proc.stdout.feed({"type": "result"})
    assert events.get(timeout=1) == {"type": "result"}


def test_user_messages_are_written_as_the_cli_expects():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    assert session.send_user("hello")
    assert proc.stdin.payloads() == [
        {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        }
    ]
    session.stop()
    assert not session.send_user("again"), "a stopped session refuses writes"


def test_stderr_is_drained_and_a_turn_reads_only_its_own_lines():
    stderr = _FeedStdout()
    proc = _Proc(stderr=stderr)
    session = cs.ClaudeSession(proc, WANTS)
    stderr.feed_raw("old news\n")
    assert _settle(lambda: session.stderr_since(0) == "old news")
    mark = session.stderr_mark()
    stderr.feed_raw("this turn's complaint\n")
    assert _settle(lambda: "complaint" in session.stderr_since(mark))
    assert "old news" not in session.stderr_since(mark)


def test_every_event_touches_the_clock():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    touched = []
    session.on_event = lambda: touched.append(1)
    session.attach()
    proc.stdout.feed({"type": "assistant"})
    assert _settle(lambda: touched == [1])


def test_stop_ends_the_process_group_once(monkeypatch):
    ended = []
    monkeypatch.setattr(cs, "end_process_group", lambda proc, timeout=0.0: ended.append(proc))
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    session.stop()
    session.stop()
    assert ended == [proc]
    assert proc.stdin.closed
    assert not session.alive()


def test_start_runs_the_command_in_the_working_directory(monkeypatch):
    seen = {}

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setattr(cs, "_popen", fake_popen)
    monkeypatch.setattr(cs, "subprocess_env", lambda binary: {"PATH": "x"})
    session = cs.ClaudeSession.start("claude", WANTS, "stdio", {"creationflags": 7})
    assert seen["cmd"][0] == "claude" and "--permission-prompt-tool" in seen["cmd"]
    assert seen["kwargs"]["cwd"] == "C:/work"
    assert seen["kwargs"]["env"] == {"PATH": "x"}
    assert seen["kwargs"]["creationflags"] == 7
    assert seen["kwargs"]["errors"] == "replace"
    assert session.alive()


def test_an_idle_sink_that_raises_is_logged_not_propagated(caplog):
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    proc.stdout.feed({"type": "assistant"})
    assert _settle(lambda: session._pending)
    with caplog.at_level("ERROR", logger="blindpilot.claude"):
        session.set_idle_sink(lambda: (_ for _ in ()).throw(RuntimeError("sink broke")))
    assert "the Claude session's idle sink raised" in caplog.text
    assert any(r.levelname == "ERROR" for r in caplog.records if r.name == "blindpilot.claude")
    events = session.attach()
    assert events.get(timeout=1) == {"type": "assistant"}
