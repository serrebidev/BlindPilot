"""One Claude Code process kept between turns: who hears what it says."""

from __future__ import annotations

import io
import json
import queue
import threading
import time

import backend_pool
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


class _Panel:
    """Stands in for a SessionPanel: identity-hashed and weak-referenceable.

    Plain `object()` cannot sit in `backend_pool`'s per-panel WeakKeyDictionary
    (it has no `__weakref__` slot), the same reason test_backend_pool.py keeps
    this stand-in rather than using one directly.
    """


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


def _answer_controls(proc, subtype="success"):
    """A CLI that answers every control request it is sent, on a thread."""

    def worker():
        seen = 0
        while True:
            time.sleep(0.005)
            payloads = proc.stdin.payloads()
            for payload in payloads[seen:]:
                if payload.get("type") == "control_request":
                    proc.stdout.feed(
                        {
                            "type": "control_response",
                            "response": {"subtype": subtype, "request_id": payload["request_id"]},
                        }
                    )
            seen = len(payloads)
            if proc.returncode is not None:
                return

    threading.Thread(target=worker, daemon=True).start()


def test_an_interrupt_the_cli_confirms_is_true_and_leaves_the_process_alive():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    _answer_controls(proc)
    assert session.interrupt(timeout=2.0)
    sent = [p for p in proc.stdin.payloads() if p["type"] == "control_request"]
    assert sent[0]["request"] == {"subtype": "interrupt"}
    assert session.alive()


def test_an_interrupt_nobody_answers_is_false():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    assert not session.interrupt(timeout=0.1)


def test_an_interrupt_the_cli_refuses_is_false():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    _answer_controls(proc, subtype="error")
    assert not session.interrupt(timeout=2.0)


def test_a_model_change_goes_down_the_stream_and_is_remembered():
    proc = _Proc()
    session = cs.ClaudeSession(proc, cs.Wants(cwd="C:/work", permission_mode="default", model="a"))
    _answer_controls(proc)
    assert session.set_model("b")
    sent = [p for p in proc.stdin.payloads() if p["type"] == "control_request"]
    assert sent[-1]["request"] == {"subtype": "set_model", "model": "b"}
    assert session.wants.model == "b"
    assert session.set_model("b"), "the model it already runs costs no request"
    assert len([p for p in proc.stdin.payloads() if p["type"] == "control_request"]) == 1


def test_a_permission_mode_change_goes_down_the_stream():
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    _answer_controls(proc)
    assert session.set_permission_mode("plan")
    sent = [p for p in proc.stdin.payloads() if p["type"] == "control_request"]
    assert sent[-1]["request"] == {"subtype": "set_permission_mode", "mode": "plan"}
    assert session.wants.permission_mode == "plan"


def test_the_default_model_cannot_be_asked_for_by_name():
    proc = _Proc()
    session = cs.ClaudeSession(proc, cs.Wants(cwd="C:/work", permission_mode="default", model="a"))
    _answer_controls(proc)
    assert not session.set_model(""), (
        "there is no request for 'the default', so the process restarts"
    )


def test_can_serve_needs_the_same_directory_effort_and_session():
    proc = _Proc()
    session = cs.ClaudeSession(proc, cs.Wants(cwd="C:/work", permission_mode="default"))
    session.session_id = "s1"
    same = cs.Wants(cwd="C:/work", permission_mode="plan", model="b", session_id="s1")
    assert session.can_serve(same), "model and mode differ but both travel down the stream"
    assert not session.can_serve(replace_wants(same, cwd="D:/other"))
    assert not session.can_serve(replace_wants(same, effort="high"))
    assert not session.can_serve(replace_wants(same, session_id="s2"))
    assert not session.can_serve(replace_wants(same, session_id=None))
    proc.returncode = 0
    assert not session.can_serve(same), "a dead process serves nobody"


def test_can_serve_refuses_a_session_whose_stream_has_already_ended():
    """A stream that ended is a process on its way out, code or no code.

    `poll()` has nothing to report between the CLI closing stdout and the
    process being reaped, so `alive()` alone said yes to a session that could
    never answer another turn.
    """
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    session.session_id = "s1"
    wants = replace_wants(WANTS, session_id="s1")
    assert session.can_serve(wants)
    proc.stdout.close()
    assert _settle(lambda: session._ended.is_set())
    assert session.alive(), "the fake has not been reaped, which is the case under test"
    assert not session.can_serve(wants)


def replace_wants(wants, **changes):
    from dataclasses import replace

    return replace(wants, **changes)


def test_adopt_applies_model_then_mode_and_fails_on_a_refusal():
    proc = _Proc()
    session = cs.ClaudeSession(proc, cs.Wants(cwd="C:/work", permission_mode="default", model="a"))
    _answer_controls(proc)
    assert session.adopt(cs.Wants(cwd="C:/work", permission_mode="plan", model="b"))
    assert session.wants.model == "b" and session.wants.permission_mode == "plan"
    refusing = _Proc()
    session2 = cs.ClaudeSession(refusing, cs.Wants(cwd="C:/work", permission_mode="default"))
    _answer_controls(refusing, subtype="error")
    assert not session2.adopt(cs.Wants(cwd="C:/work", permission_mode="plan"))


def _fresh_pool(monkeypatch, request):
    """A pool of this test's own, emptied when the test ends.

    conftest's sweep empties `backend_pool`'s singleton, and this one is not
    it, so without this every process a take_or_start test starts keeps its
    reader thread for the rest of the run.
    """
    pool = backend_pool.BackendPool()
    monkeypatch.setattr(backend_pool, "pool", lambda: pool)
    request.addfinalizer(pool.drop_all)
    return pool


def _starting(monkeypatch, procs):
    """Each start takes the next fake process from `procs`."""
    made = []

    def fake_popen(cmd, **kwargs):
        proc = procs.pop(0)
        proc.cmd = cmd
        made.append(proc)
        return proc

    monkeypatch.setattr(cs, "_popen", fake_popen)
    monkeypatch.setattr(cs, "subprocess_env", lambda binary: {})
    return made


def test_the_adapter_reports_the_session_alive_busy_and_stops_it(monkeypatch):
    monkeypatch.setattr(cs, "end_process_group", lambda proc, timeout=0.0: proc.kill())
    proc = _Proc()
    session = cs.ClaudeSession(proc, WANTS)
    adapter = cs.claude_adapter()
    assert adapter.alive(session)
    assert not adapter.busy(session)
    session.attach()
    assert adapter.busy(session)
    session.detach()
    adapter.stop(session)
    assert not adapter.alive(session)


def test_the_first_turn_starts_a_process_and_keeps_it(monkeypatch, request):
    pool = _fresh_pool(monkeypatch, request)
    made = _starting(monkeypatch, [_Proc()])
    panel = _Panel()
    session = cs.take_or_start(panel, WANTS, "claude", "stdio")
    assert len(made) == 1
    assert pool.held_count() == 1
    assert session.on_event is not None, "events touch the pool's idle clock"
    assert "--permission-prompt-tool" in made[0].cmd


def test_the_next_turn_reuses_the_process_when_the_conversation_matches(monkeypatch, request):
    _fresh_pool(monkeypatch, request)
    made = _starting(monkeypatch, [_Proc(), _Proc()])
    panel = _Panel()
    first = cs.take_or_start(panel, WANTS, "claude")
    first.session_id = "s1"
    again = cs.take_or_start(panel, replace_wants(WANTS, session_id="s1"), "claude")
    assert again is first
    assert len(made) == 1


def test_a_different_effort_or_directory_or_conversation_starts_a_new_process(monkeypatch, request):
    _fresh_pool(monkeypatch, request)
    made = _starting(monkeypatch, [_Proc(), _Proc(), _Proc()])
    monkeypatch.setattr(cs, "end_process_group", lambda proc, timeout=0.0: proc.kill())
    panel = _Panel()
    first = cs.take_or_start(panel, WANTS, "claude")
    first.session_id = "s1"
    second = cs.take_or_start(panel, replace_wants(WANTS, session_id="s1", effort="high"), "claude")
    assert second is not first and not first.alive(), "the old process was stopped, not leaked"
    second.session_id = "s1"
    third = cs.take_or_start(panel, replace_wants(WANTS, session_id="s2", effort="high"), "claude")
    assert third is not second
    assert len(made) == 3


def test_a_model_change_is_sent_down_the_stream_to_the_held_process(monkeypatch, request):
    _fresh_pool(monkeypatch, request)
    procs = [_Proc()]
    made = _starting(monkeypatch, list(procs))
    panel = _Panel()
    first = cs.take_or_start(panel, WANTS, "claude")
    first.session_id = "s1"
    _answer_controls(procs[0])
    again = cs.take_or_start(panel, replace_wants(WANTS, session_id="s1", model="b"), "claude")
    assert again is first and first.wants.model == "b"
    assert len(made) == 1


def test_a_model_change_the_cli_does_not_answer_restarts_the_process(monkeypatch, request):
    _fresh_pool(monkeypatch, request)
    made = _starting(monkeypatch, [_Proc(), _Proc()])
    monkeypatch.setattr(cs, "end_process_group", lambda proc, timeout=0.0: proc.kill())
    monkeypatch.setattr(cs, "_CONTROL_SECONDS", 0.2)
    panel = _Panel()
    first = cs.take_or_start(panel, WANTS, "claude")
    first.session_id = "s1"
    # Nobody answers the set_model request, so the change cannot be trusted
    # to have landed and the process is replaced with the model on its command line.
    # The wait is the patched value.
    second = cs.take_or_start(panel, replace_wants(WANTS, session_id="s1", model="c"), "claude")
    assert second is not first and not first.alive()
    assert len(made) == 2 and "--model" in made[1].cmd


def test_two_tabs_get_two_processes(monkeypatch, request):
    _fresh_pool(monkeypatch, request)
    made = _starting(monkeypatch, [_Proc(), _Proc()])
    one = cs.take_or_start(_Panel(), WANTS, "claude")
    two = cs.take_or_start(_Panel(), WANTS, "claude")
    assert one is not two and len(made) == 2


def test_the_idle_sink_is_registered_on_take(monkeypatch, request):
    _fresh_pool(monkeypatch, request)
    _starting(monkeypatch, [_Proc()])
    woken = []
    session = cs.take_or_start(_Panel(), WANTS, "claude", idle_sink=lambda: woken.append(1))
    session._proc.stdout.feed({"type": "assistant"})
    assert _settle(lambda: woken == [1])


def test_one_tabs_slow_model_change_does_not_block_another_tab(monkeypatch, request):
    _fresh_pool(monkeypatch, request)
    made = _starting(monkeypatch, [_Proc(), _Proc(), _Proc()])
    monkeypatch.setattr(cs, "end_process_group", lambda proc, timeout=0.0: proc.kill())
    monkeypatch.setattr(cs, "_CONTROL_SECONDS", 0.2)
    panel_a = _Panel()
    panel_b = _Panel()
    first = cs.take_or_start(panel_a, WANTS, "claude")
    first.session_id = "s1"
    # Nobody answers control requests on this process, so the model change
    # below waits out the CLI's silence before it gives up and replaces it.
    # The wait is the patched value.

    result = {}

    def change_a_model():
        result["a"] = cs.take_or_start(
            panel_a, replace_wants(WANTS, session_id="s1", model="c"), "claude"
        )

    thread = threading.Thread(target=change_a_model)
    thread.start()
    time.sleep(0.05)  # let the thread take panel A's lock and start waiting

    start = time.monotonic()
    second = cs.take_or_start(panel_b, WANTS, "claude")
    elapsed = time.monotonic() - start
    assert elapsed < 0.3, "tab B waited on tab A's slow model change"

    thread.join()
    replaced_a = result["a"]
    assert len(made) == 3
    assert replaced_a.alive() and second.alive()


def test_a_late_turn_with_nothing_held_gets_nothing_and_starts_no_process(monkeypatch, request):
    """A late turn reads the process it was woken for, or reads nothing.

    Starting one here would block for ever: a fresh process has no turn
    running and nothing to say, and the late turn would wait on its silence.
    """
    pool = _fresh_pool(monkeypatch, request)
    made = _starting(monkeypatch, [_Proc()])
    assert cs.take_or_start(_Panel(), WANTS, "claude", late=True) is None
    assert made == [], "a late turn started a process of its own"
    assert pool.held_count() == 0


def test_a_late_turn_takes_the_held_process_exactly_as_it_is(monkeypatch, request):
    """Never replace or adopt for a late turn.

    Its events are already in the held process. Stopping that process to
    apply a model change would throw them away and leave the late turn
    waiting on a replacement that has nothing to say.
    """
    _fresh_pool(monkeypatch, request)
    made = _starting(monkeypatch, [_Proc(), _Proc()])
    panel = _Panel()
    first = cs.take_or_start(panel, WANTS, "claude")
    first.session_id = "s1"
    wanted = replace_wants(WANTS, session_id="s1", model="b", effort="high")
    again = cs.take_or_start(panel, wanted, "claude", late=True)
    assert again is first
    assert len(made) == 1, "the late turn replaced the process it was woken for"
    assert first.alive() and first.wants.model == ""
    assert [p for p in made[0].stdin.payloads() if p["type"] == "control_request"] == []
