"""The Command Code worker against a scripted event stream.

No Node, no CLI: a fake process hands the worker the exact frames the real one
writes (measured at 1.53.1) and the callbacks are read back.
"""

from __future__ import annotations

import io
import json
import os
import threading

import commandcode_worker
import pytest
from commandcode_worker import CommandcodeWorker, build_command


def _event(event: dict) -> str:
    return json.dumps({"type": "event", "event": event}) + "\n"


def _result(**fields) -> str:
    return json.dumps({"type": "result", **fields}) + "\n"


class _Stdin(io.StringIO):
    """A stdin that keeps what was written even after the worker closes it."""

    def __init__(self):
        super().__init__()
        self.written = ""

    def close(self):
        self.written = self.getvalue()
        super().close()


class _FakeProcess:
    """A process whose pipes are a script of lines and whose exit code is set."""

    def __init__(self, lines=(), stderr=(), returncode=0):
        self.stdin = _Stdin()
        self.stdout = iter(list(lines))
        self.stderr = iter([line + "\n" for line in stderr])
        self._rc = returncode

    def poll(self):
        return self._rc

    def wait(self, timeout=None):
        return self._rc

    def kill(self):
        pass


class _Recorder:
    def __init__(self):
        self.events = []
        self._lock = threading.Lock()

    def callback(self, kind):
        def record(*args):
            with self._lock:
                self.events.append((kind, args))

        return record

    def kinds(self):
        return [kind for kind, _args in self.events]

    def texts(self, kind):
        return [args[0] if args else None for k, args in self.events if k == kind]

    def activity(self, want):
        return [args[1] for k, args in self.events if k == "activity" and args[0] == want]


@pytest.fixture
def worker_env(monkeypatch):
    """Fake discovery and the process so nothing is ever launched."""
    state = {"proc": _FakeProcess()}
    monkeypatch.setattr(commandcode_worker, "find_backend_cli", lambda _backend: "command-code")
    monkeypatch.setattr(commandcode_worker, "subprocess_env", lambda _binary: {})
    monkeypatch.setattr(commandcode_worker.subprocess, "Popen", lambda *a, **k: state["proc"])
    return state


def _run(session_id=None, *, auto_free_fallback=False, model="gpt-5.5", prompt="do the thing"):
    rec = _Recorder()
    worker = CommandcodeWorker(
        prompt,
        session_id,
        "C:\\work",
        "plan",
        model=model,
        effort="xhigh",
        auto_free_fallback=auto_free_fallback,
        on_session=rec.callback("session"),
        on_started=rec.callback("started"),
        on_activity=rec.callback("activity"),
        on_complete=rec.callback("complete"),
        on_failed=rec.callback("failed"),
        on_done=rec.callback("done"),
    )
    worker.start()
    worker.join(20)
    return worker, rec


# --------------------------------------------------------------------------
# The command line
# --------------------------------------------------------------------------


def test_the_permission_vocabulary_is_translated():
    argv = build_command("command-code", "plan")
    assert argv[:2] == ["command-code", "-p"]
    assert "--output-format" in argv and "json" in argv
    assert argv[argv.index("--permission-mode") + 1] == "plan"


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("default", "default"),
        ("acceptEdits", "auto-accept"),
        ("auto", "auto-accept"),
        ("dontAsk", "dont-ask"),
        ("plan", "plan"),
    ],
)
def test_each_mode_maps_to_a_command_code_mode(mode, expected):
    argv = build_command("command-code", mode)
    assert argv[argv.index("--permission-mode") + 1] == expected


def test_bypass_uses_the_launch_only_yolo_flag():
    argv = build_command("command-code", "bypassPermissions")
    assert "--yolo" in argv
    assert "--permission-mode" not in argv


@pytest.mark.parametrize("mode", ["bypassPermissions", "default", "plan"])
def test_the_withheld_bookkeeping_tools_are_asked_back(mode):
    """A headless run hides todo_write and taste from the model.

    Their absence is not a permission question -- the model is told no such
    tool exists -- so no mode, bypass included, lifts it. --tools-enable is.
    """
    argv = build_command("command-code", mode)
    assert argv[argv.index("--tools-enable") + 1] == "todo_write,taste"


def test_the_tools_that_would_answer_for_the_person_stay_withheld():
    argv = build_command("command-code", "bypassPermissions")
    enabled = argv[argv.index("--tools-enable") + 1].split(",")
    assert "ask_user_question" not in enabled
    assert "exit_plan_mode" not in enabled


@pytest.mark.parametrize("mode", ["bypassPermissions", "default", "plan"])
def test_the_turn_budget_is_raised_past_print_modes_own_default(mode):
    """`-p` stops at 100 turns, which is the budget for a script.

    A piece of work spends a turn on every step - read, edit, run the tests,
    read again - and interactive Command Code has no cap at all, so the same
    task that finishes in a terminal stopped here mid-way.
    """
    argv = build_command("command-code", mode)

    assert argv[argv.index("--max-turns") + 1] == str(commandcode_worker.COMMANDCODE_MAX_TURNS)
    assert commandcode_worker.COMMANDCODE_MAX_TURNS > 100


def test_model_effort_and_resume_are_passed_through():
    argv = build_command("command-code", "plan", "gpt-5.5", "xhigh", "abc-123")
    assert argv[argv.index("--model") + 1] == "gpt-5.5"
    assert argv[argv.index("--effort") + 1] == "xhigh"
    assert argv[argv.index("--resume") + 1] == "abc-123"


def test_additional_directories_are_separate_arguments():
    argv = build_command("command-code", "plan", additional_dirs=("/work/with spaces", "/other"))
    assert argv[-4:] == ["--add-dir", "/work/with spaces", "--add-dir", "/other"]


# --------------------------------------------------------------------------
# A turn
# --------------------------------------------------------------------------


def test_a_turn_streams_text_tools_and_the_session(worker_env):
    worker_env["proc"] = _FakeProcess(
        lines=[
            _event({"type": "run_start", "sessionId": "s1"}),
            _event({"type": "turn_start", "turnNumber": 1}),
            _event({"type": "thinking_delta", "delta": "thinking hard"}),
            _event({"type": "thinking_end", "text": "thinking hard"}),
            _event({"type": "text_delta", "delta": "Hello there. "}),
            _event(
                {
                    "type": "tool_queued",
                    "toolCallId": "c1",
                    "toolName": "read_file",
                    "input": {"file_path": "x.py"},
                }
            ),
            _event(
                {
                    "type": "tool_running",
                    "toolCallId": "c1",
                    "toolName": "read_file",
                    "description": None,
                }
            ),
            _event(
                {
                    "type": "tool_completed",
                    "toolCallId": "c1",
                    "toolName": "read_file",
                    "result": [{"type": "text", "text": "print(1)"}],
                }
            ),
            _event({"type": "message_end", "content": [{"type": "text", "text": "Hello there. "}]}),
            _result(subtype="success", sessionId="s1", finalText="Hello there. print(1)"),
        ]
    )

    _worker, rec = _run()

    assert rec.texts("session") == ["s1"]
    assert rec.texts("started") == [None]
    assert "thinking hard" in rec.activity("thinking")
    # Phrased the way Claude's steps are, and the output shown on its own.
    assert "Reading x.py" in rec.activity("tool")
    assert rec.activity("result") == ["print(1)"]
    assert rec.texts("complete") == ["Hello there. print(1)"]
    assert rec.kinds()[-1] == "done"
    assert "failed" not in rec.kinds()


def test_the_prompt_is_written_to_stdin(worker_env):
    proc = _FakeProcess(lines=[_result(subtype="success", sessionId="s1", finalText="ok")])
    worker_env["proc"] = proc

    _run()

    assert proc.stdin.written == "do the thing\n"


def test_an_unknown_event_is_said_generically(worker_env):
    worker_env["proc"] = _FakeProcess(
        lines=[
            _event({"type": "brand_new_event", "description": "something new"}),
            _result(subtype="success", sessionId="s1", finalText="ok"),
        ]
    )

    _worker, rec = _run()

    assert "brand_new_event: something new" in rec.activity("tool")


def test_an_error_result_is_reported(worker_env):
    worker_env["proc"] = _FakeProcess(
        lines=[_result(subtype="error", error={"message": "boom"})], returncode=1
    )

    _worker, rec = _run()

    assert rec.texts("failed") == ["boom"]
    assert "complete" not in rec.kinds()


def test_a_turn_cut_off_at_the_turn_limit_keeps_what_it_produced(worker_env):
    """Print mode hands the partial answer back and exits 8; it did not fail.

    Reporting that as an error threw away everything the turn had finished -
    files written, tests run - and left a failure where the work should be.
    """
    worker_env["proc"] = _FakeProcess(
        lines=[_result(subtype="max_turns", sessionId="s1", finalText="what I got done")],
        returncode=8,
    )

    _worker, rec = _run()

    assert rec.texts("complete") == ["what I got done"]
    assert rec.texts("failed") == []
    # Said as a notice, which is the kind that is spoken whatever the narration
    # mode, because "this is not the whole answer" is BlindPilot's own words.
    notice = rec.activity("notice")
    assert len(notice) == 1
    assert str(commandcode_worker.COMMANDCODE_MAX_TURNS) in notice[0]
    assert "not a finished answer" in notice[0]


def test_a_turn_cut_off_at_the_turn_limit_with_nothing_to_show_still_fails(worker_env):
    """There is no partial answer to keep, so there is nothing to call a turn."""
    worker_env["proc"] = _FakeProcess(
        lines=[_result(subtype="max_turns", sessionId="s1", finalText="")], returncode=8
    )

    _worker, rec = _run()

    assert rec.texts("complete") == []
    assert rec.activity("notice") == []
    failure = " ".join(rec.texts("failed"))
    assert str(commandcode_worker.COMMANDCODE_MAX_TURNS) in failure
    assert "without finishing an answer" in failure


def test_a_partial_answer_streamed_before_the_limit_is_kept_too(worker_env):
    """A release that streams its text and leaves finalText empty still counts."""
    worker_env["proc"] = _FakeProcess(
        lines=[
            _event({"type": "text_delta", "delta": "half an answer"}),
            _result(subtype="max_turns", sessionId="s1", finalText=""),
        ],
        returncode=8,
    )

    _worker, rec = _run()

    assert "half an answer" in "".join(rec.texts("complete"))
    assert rec.texts("failed") == []


def test_a_nonzero_exit_without_a_result_is_explained(worker_env):
    worker_env["proc"] = _FakeProcess(lines=[], returncode=5)

    _worker, rec = _run()

    assert any("rate limited" in text for text in rec.texts("failed"))


@pytest.mark.parametrize("exit_code", [5, 10])
def test_a_limit_error_retries_once_with_a_free_model(worker_env, monkeypatch, exit_code):
    argv_log: list = []
    _two_runs(
        monkeypatch,
        _FakeProcess(
            lines=[_result(subtype="error", error={"message": "limit reached"})],
            returncode=exit_code,
        ),
        _FakeProcess(lines=[_result(subtype="success", sessionId="s1", finalText="free done")]),
        argv_log,
    )
    monkeypatch.setattr(
        commandcode_worker,
        "best_free_model",
        lambda **_kwargs: commandcode_worker.Route("free/code", "", "free"),
    )

    _worker, rec = _run(session_id="keep", auto_free_fallback=True)

    assert len(argv_log) == 2
    assert argv_log[1][argv_log[1].index("--model") + 1] == "free/code"
    assert "--effort" not in argv_log[1]
    assert argv_log[1][argv_log[1].index("--resume") + 1] == "keep"
    assert rec.texts("complete") == ["free done"]
    assert _worker.fallback_model == "free/code"
    assert any("Retrying with free model free/code" in text for text in rec.activity("notice"))
    assert any(
        ("rate limit" if exit_code == 5 else "credit limit") in text
        for text in rec.activity("notice")
    )


@pytest.mark.parametrize("exit_code", [5, 10])
def test_a_limit_error_records_the_usage_cooldown(worker_env, monkeypatch, exit_code):
    argv_log: list = []
    _two_runs(
        monkeypatch,
        _FakeProcess(lines=[], returncode=exit_code),
        _FakeProcess(lines=[_result(subtype="success", sessionId="s1", finalText="done")]),
        argv_log,
    )
    monkeypatch.setattr(
        commandcode_worker,
        "best_free_model",
        lambda **_kwargs: commandcode_worker.Route("free/code", "", "free"),
    )
    recorded: list = []
    monkeypatch.setattr(
        commandcode_worker,
        "record_limit_hit",
        lambda code: recorded.append(code) or True,
    )

    _worker, rec = _run(auto_free_fallback=True)

    assert recorded == [exit_code]


def test_a_limit_error_records_cooldown_without_free_fallback(worker_env, monkeypatch):
    worker_env["proc"] = _FakeProcess(lines=[], returncode=10)
    recorded: list[int] = []
    monkeypatch.setattr(
        commandcode_worker,
        "record_limit_hit",
        lambda code: recorded.append(code) or True,
    )

    _worker, _rec = _run(auto_free_fallback=False)

    assert recorded == [10]


def test_a_free_fallback_never_retries_twice(worker_env, monkeypatch):
    argv_log: list = []
    _two_runs(
        monkeypatch,
        _FakeProcess(lines=[], returncode=5),
        _FakeProcess(lines=[], returncode=5),
        argv_log,
    )
    monkeypatch.setattr(
        commandcode_worker,
        "best_free_model",
        lambda **_kwargs: commandcode_worker.Route("free/code", "", "free"),
    )

    _worker, rec = _run(auto_free_fallback=True)

    assert len(argv_log) == 2
    assert any("free fallback free/code also failed" in text for text in rec.texts("failed"))


def test_a_free_fallback_replaces_the_failed_runs_session(worker_env, monkeypatch):
    argv_log: list = []
    _two_runs(
        monkeypatch,
        _FakeProcess(lines=[_event({"type": "run_start", "sessionId": "failed"})], returncode=5),
        _FakeProcess(lines=[_result(subtype="success", sessionId="saved", finalText="done")]),
        argv_log,
    )
    monkeypatch.setattr(
        commandcode_worker,
        "best_free_model",
        lambda **_kwargs: commandcode_worker.Route("free/code", "", "free"),
    )

    worker, rec = _run(auto_free_fallback=True)

    assert rec.texts("session") == ["failed", "saved"]
    assert worker.session_replaced is True
    assert rec.texts("complete") == ["done"]


def test_a_preferred_free_model_is_not_retried(worker_env, monkeypatch):
    worker_env["proc"] = _FakeProcess(lines=[], returncode=5)
    monkeypatch.setattr(
        commandcode_worker,
        "best_free_model",
        lambda **_kwargs: commandcode_worker.Route("free/code", "", "free"),
    )

    _worker, rec = _run(auto_free_fallback=True, model="free/code")

    assert any("rate limited" in text for text in rec.texts("failed"))


def test_a_limit_without_a_confirmed_free_model_explains_no_retry(worker_env, monkeypatch):
    worker_env["proc"] = _FakeProcess(lines=[], returncode=5)
    monkeypatch.setattr(commandcode_worker, "best_free_model", lambda **_kwargs: None)

    _worker, rec = _run(auto_free_fallback=True)

    assert any("no confirmed free model" in text for text in rec.activity("notice"))
    assert any("rate limited" in text for text in rec.texts("failed"))


def test_a_limited_free_model_tries_the_next_confirmed_free_model(worker_env, monkeypatch):
    argv_log: list = []
    _two_runs(
        monkeypatch,
        _FakeProcess(lines=[], returncode=5),
        _FakeProcess(lines=[_result(subtype="success", sessionId="s1", finalText="done")]),
        argv_log,
    )
    monkeypatch.setattr(
        commandcode_worker,
        "best_free_model",
        lambda **kwargs: commandcode_worker.Route(
            "free/alternate"
            if kwargs.get("exclude_model") == "free/preferred"
            else "free/preferred",
            "",
            "free",
        ),
    )

    _worker, rec = _run(auto_free_fallback=True, model="free/preferred")

    assert len(argv_log) == 2
    assert argv_log[1][argv_log[1].index("--model") + 1] == "free/alternate"
    assert rec.texts("complete") == ["done"]


def test_a_rate_limit_after_partial_output_is_not_retried(worker_env, monkeypatch):
    worker_env["proc"] = _FakeProcess(
        lines=[
            _event({"type": "text_delta", "delta": "half an answer"}),
            _result(subtype="error", error={"message": "limit reached"}),
        ],
        returncode=5,
    )
    monkeypatch.setattr(
        commandcode_worker,
        "best_free_model",
        lambda **_kwargs: pytest.fail("partial output must not be replayed"),
    )

    _worker, rec = _run(auto_free_fallback=True)

    assert any("limit reached" in text for text in rec.texts("failed"))
    assert any("already produced an answer" in text for text in rec.activity("notice"))


@pytest.mark.parametrize(
    "tool_event",
    [
        {"type": "tool_running", "toolCallId": "c1", "toolName": "write_file"},
        {"type": "tool_errored", "toolCallId": "c1", "toolName": "write_file"},
        {"type": "tool_update", "toolCallId": "c1", "toolName": "write_file"},
        {"type": "subagent_start", "toolCallId": "c1", "subagentType": "general"},
    ],
)
def test_a_rate_limit_after_a_started_tool_is_not_retried(worker_env, monkeypatch, tool_event):
    worker_env["proc"] = _FakeProcess(
        lines=[
            _event(tool_event),
        ],
        returncode=5,
    )
    monkeypatch.setattr(
        commandcode_worker,
        "best_free_model",
        lambda **_kwargs: pytest.fail("a started tool must not be replayed"),
    )

    _worker, rec = _run(auto_free_fallback=True)

    assert any("rate limited" in text for text in rec.texts("failed"))
    assert any("already ran a tool" in text for text in rec.activity("notice"))


def test_a_missing_cli_is_reported(monkeypatch):
    monkeypatch.setattr(commandcode_worker, "find_backend_cli", lambda _backend: None)
    rec = _Recorder()
    worker = CommandcodeWorker(
        "hi",
        None,
        "C:\\work",
        "plan",
        on_session=rec.callback("session"),
        on_started=rec.callback("started"),
        on_activity=rec.callback("activity"),
        on_complete=rec.callback("complete"),
        on_failed=rec.callback("failed"),
        on_done=rec.callback("done"),
    )
    worker.start()
    worker.join(20)

    assert any("not installed" in text for text in rec.texts("failed"))


def test_a_turn_provides_what_the_window_drives_it_through():
    # One query per process: nothing can be steered, and saying so is what
    # makes the window start a fresh message rather than fail one.
    worker = CommandcodeWorker(
        "hi",
        None,
        "C:\\work",
        "plan",
        on_session=lambda _s: None,
        on_started=lambda: None,
        on_activity=lambda _k, _t: None,
        on_complete=lambda _t: None,
        on_failed=lambda _m: None,
        on_done=lambda: None,
    )
    assert worker.accepting_input() is False
    assert worker.steer("more") is False


# --------------------------------------------------------------------------
# Cancel
# --------------------------------------------------------------------------


def test_cancel_reports_stopped_without_a_failure(monkeypatch):
    """A cancelled turn is not a failed one: the person asked for it to stop."""
    release = threading.Event()
    started = threading.Event()

    class _Blocking(_FakeProcess):
        def __init__(self):
            super().__init__(returncode=0)
            first = _event({"type": "run_start", "sessionId": "s1"})
            self.stdin = _Stdin()
            self.stdout = self._lines(first)
            self.stderr = iter(())

        def _lines(self, first):
            yield first
            release.wait(10)

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    proc = _Blocking()
    monkeypatch.setattr(commandcode_worker, "find_backend_cli", lambda _backend: "command-code")
    monkeypatch.setattr(commandcode_worker, "subprocess_env", lambda _binary: {})
    monkeypatch.setattr(commandcode_worker.subprocess, "Popen", lambda *a, **k: proc)
    # Real end_process_group would signal a pid the fake does not have; make it
    # the thing that releases the blocked reader instead.
    monkeypatch.setattr(commandcode_worker, "end_process_group", lambda *_a, **_k: release.set())

    rec = _Recorder()

    def on_started():
        started.set()
        rec.callback("started")()

    worker = CommandcodeWorker(
        "count",
        None,
        "C:\\work",
        "plan",
        on_session=rec.callback("session"),
        on_started=on_started,
        on_activity=rec.callback("activity"),
        on_complete=rec.callback("complete"),
        on_failed=rec.callback("failed"),
        on_done=rec.callback("done"),
    )
    worker.start()
    assert started.wait(10)
    worker.cancel()
    worker.join(20)

    assert rec.texts("failed") == []
    assert rec.texts("complete") == ["Stopped"]
    assert rec.kinds()[-1] == "done"


def _worker(rec, **extra):
    return CommandcodeWorker(
        "hi",
        "original",
        ".",
        "default",
        on_session=rec.callback("session"),
        on_started=rec.callback("started"),
        on_activity=rec.callback("activity"),
        on_complete=rec.callback("complete"),
        on_failed=rec.callback("failed"),
        on_done=rec.callback("done"),
        **extra,
    )


def test_cancel_during_process_creation_kills_late_child(worker_env, monkeypatch):
    rec = _Recorder()
    worker = _worker(rec)
    killed = []
    proc = worker_env["proc"]

    def popen(*_args, **_kwargs):
        worker.cancel()
        return proc

    monkeypatch.setattr(commandcode_worker.subprocess, "Popen", popen)
    monkeypatch.setattr(
        commandcode_worker, "end_process_group", lambda process: killed.append(process)
    )
    worker.run()
    assert proc in killed
    assert proc.stdin.written == ""
    assert rec.kinds()[-1] == "done"


def test_compaction_saves_summary_in_new_session_before_switching(worker_env, monkeypatch):
    rec = _Recorder()
    first = _FakeProcess(
        lines=[
            _result(
                subtype="success", sessionId="original", finalText="Objective and unfinished work"
            )
        ]
    )
    second = _FakeProcess(
        lines=[
            _event({"type": "run_start", "sessionId": "compacted"}),
            _result(subtype="success", sessionId="compacted", finalText="Ready"),
        ]
    )
    processes = iter([first, second])
    commands = []

    def popen(argv, **_kwargs):
        commands.append(argv)
        return next(processes)

    monkeypatch.setattr(commandcode_worker.subprocess, "Popen", popen)
    _worker(rec, compact=True).run()
    assert "--resume" in commands[0] and "original" in commands[0]
    assert "--resume" not in commands[1]
    assert "Objective and unfinished work" in second.stdin.written
    assert all(argv[argv.index("--permission-mode") + 1] == "plan" for argv in commands)
    assert rec.texts("session") == ["compacted"]
    assert len(rec.texts("complete")) == 1
    assert rec.kinds().count("done") == 1
    assert "failed" not in rec.kinds()


def test_compaction_announces_free_fallback_and_uses_it_for_both_stages(worker_env, monkeypatch):
    rec = _Recorder()
    processes = iter(
        [
            _FakeProcess(lines=[], returncode=5),
            _FakeProcess(
                lines=[_result(subtype="success", sessionId="original", finalText="Summary")]
            ),
            _FakeProcess(
                lines=[_result(subtype="success", sessionId="compacted", finalText="Ready")]
            ),
        ]
    )
    commands = []

    def popen(argv, **_kwargs):
        commands.append(argv)
        return next(processes)

    monkeypatch.setattr(commandcode_worker.subprocess, "Popen", popen)
    monkeypatch.setattr(
        commandcode_worker,
        "best_free_model",
        lambda **_kwargs: commandcode_worker.Route("free/code", "", "free"),
    )
    worker = _worker(rec, compact=True, model="gpt-5.5", auto_free_fallback=True)
    worker.run()

    assert [argv[argv.index("--model") + 1] for argv in commands] == [
        "gpt-5.5",
        "free/code",
        "free/code",
    ]
    assert rec.activity("notice") == [
        "Command Code rate limit reached. Retrying with free model free/code."
    ]
    assert worker.fallback_model == "free/code"
    assert rec.texts("session") == ["compacted"]


def test_failed_compaction_does_not_switch_from_original(worker_env, monkeypatch):
    rec = _Recorder()
    processes = iter(
        [
            _FakeProcess(
                lines=[_result(subtype="success", sessionId="original", finalText="Summary")]
            ),
            _FakeProcess(
                lines=[
                    _event({"type": "run_start", "sessionId": "incomplete"}),
                    _result(subtype="error", error={"message": "network failed"}),
                ]
            ),
        ]
    )
    monkeypatch.setattr(commandcode_worker.subprocess, "Popen", lambda *_a, **_k: next(processes))
    _worker(rec, compact=True).run()
    assert rec.texts("session") == []
    assert rec.texts("complete") == []
    assert rec.texts("failed") == ["network failed"]
    assert rec.kinds().count("done") == 1


def test_compacting_a_gone_session_fails_instead_of_faking_a_summary(worker_env, monkeypatch):
    # Measured at 1.69.0: the compaction child's --resume dies the same way
    # any resume does. Summarizing the fresh, empty conversation it would
    # otherwise fall back to would save a content-free session and call the
    # compaction a success, so the loss is said plainly instead.
    rec = _Recorder()
    monkeypatch.setattr(
        commandcode_worker.subprocess,
        "Popen",
        lambda *_a, **_k: _FakeProcess(
            lines=[], stderr=['No session "gone" found to resume.'], returncode=1
        ),
    )
    worker = _worker(rec, compact=True)
    worker.run()

    assert rec.texts("session") == []
    assert rec.texts("complete") == []
    assert rec.texts("failed") == [
        "The conversation to compact could not be found - it may have been "
        "cleaned up or was saved in a different folder. Nothing was changed."
    ]
    assert worker.lost_session is True
    assert rec.kinds().count("done") == 1


def test_a_successful_compaction_says_its_session_was_replaced(worker_env, monkeypatch):
    rec = _Recorder()
    processes = iter(
        [
            _FakeProcess(
                lines=[_result(subtype="success", sessionId="original", finalText="Summary")]
            ),
            _FakeProcess(
                lines=[_result(subtype="success", sessionId="compacted", finalText="Ready")]
            ),
        ]
    )
    monkeypatch.setattr(commandcode_worker.subprocess, "Popen", lambda *_a, **_k: next(processes))
    worker = _worker(rec, compact=True)
    worker.run()

    assert rec.texts("session") == ["compacted"]
    assert worker.session_replaced is True


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_a_denied_tool_is_named_rather_than_said_as_an_event_name(worker_env):
    """Before this, a refusal arrived as the bare word "tool_denied"."""
    worker_env["proc"] = _FakeProcess(
        lines=[
            _event({"type": "run_start", "sessionId": "s1"}),
            _event(
                {
                    "type": "tool_queued",
                    "toolCallId": "c1",
                    "toolName": "shell_command",
                    "input": {"command": "rm -rf /"},
                }
            ),
            _event({"type": "tool_denied", "toolCallId": "c1", "toolName": "shell_command"}),
            _result(subtype="success", sessionId="s1", finalText="Could not."),
        ]
    )

    _worker, rec = _run()

    tools = rec.activity("tool")
    assert any("Refused: shell_command: rm -rf /" in line for line in tools)


def test_a_withheld_tool_says_why_no_mode_can_grant_it(worker_env):
    worker_env["proc"] = _FakeProcess(
        lines=[
            _event({"type": "run_start", "sessionId": "s1"}),
            _event({"type": "tool_denied", "toolCallId": "c9", "toolName": "ask_user_question"}),
            _result(subtype="success", sessionId="s1", finalText="Done."),
        ]
    )

    _worker, rec = _run()

    assert any("withholds this tool from a headless run" in t for t in rec.activity("tool"))


def test_a_hook_block_repeats_the_reason_the_hook_gave(worker_env):
    """Command Code's own print-mode gate is such a hook outside bypass."""
    worker_env["proc"] = _FakeProcess(
        lines=[
            _event({"type": "run_start", "sessionId": "s1"}),
            _event(
                {
                    "type": "tool_hook_blocked",
                    "toolCallId": "c1",
                    "toolName": "write_file",
                    "hookOutput": 'Error: Tool "write_file" requires permissions. Use --yolo',
                }
            ),
            _result(subtype="success", sessionId="s1", finalText="Nope."),
        ]
    )

    _worker, rec = _run()

    assert any("Blocked: write_file: Error:" in t for t in rec.activity("tool"))


def test_a_turn_that_permission_denied_stopped_says_so(worker_env):
    """It used to arrive as "Finished with nothing to say.".

    A stop with nothing to hand back is the one case that is still a failure.
    """
    worker_env["proc"] = _FakeProcess(
        lines=[
            _event({"type": "run_start", "sessionId": "s1"}),
            _result(
                subtype="success", sessionId="s1", stopReason="permission_denied", finalText=""
            ),
        ]
    )

    _worker, rec = _run()

    assert rec.texts("complete") == []
    assert any("nobody to give it" in message for message in rec.texts("failed"))


def test_a_permission_stop_keeps_the_answer_it_had_and_names_the_tool(worker_env):
    """Measured against the CLI here: a bypass run whose permissions.ask rule
    matched a call exits 0 and carries the text the turn had produced.

    That text was thrown away and the turn reported as a failure, so the work it
    had finished went with it. The reason the run stopped is on no stream of its
    own either - the event carries the tool name and nothing else, and stderr is
    empty - so the refusal the tool events already spelled out is what names it.
    """
    worker_env["proc"] = _FakeProcess(
        lines=[
            _event({"type": "run_start", "sessionId": "s1"}),
            _event(
                {
                    "type": "tool_queued",
                    "toolCallId": "c1",
                    "toolName": "shell_command",
                    "input": {"command": "echo hi"},
                }
            ),
            _event({"type": "tool_denied", "toolCallId": "c1", "toolName": "shell_command"}),
            _result(
                subtype="success",
                sessionId="s1",
                stopReason="permission_denied",
                finalText="## Running the command",
            ),
        ]
    )

    _worker, rec = _run()

    assert rec.texts("complete") == ["## Running the command"]
    assert rec.texts("failed") == []
    notice = rec.activity("notice")
    assert len(notice) == 1
    assert "Refused: shell_command: echo hi" in notice[0]
    assert "not a finished answer" in notice[0]


def test_a_permission_stop_keeps_an_answer_that_only_arrived_streamed(worker_env):
    """A release that streams its text and leaves finalText empty still counts."""
    worker_env["proc"] = _FakeProcess(
        lines=[
            _event({"type": "text_delta", "delta": "half an answer"}),
            _result(
                subtype="success", sessionId="s1", stopReason="permission_denied", finalText=""
            ),
        ]
    )

    _worker, rec = _run()

    assert "half an answer" in "".join(rec.texts("complete"))
    assert rec.texts("failed") == []


def _bypass_worker(recorder, cwd):
    return CommandcodeWorker(
        "go",
        None,
        cwd,
        "bypassPermissions",
        on_session=recorder.callback("session"),
        on_started=recorder.callback("started"),
        on_activity=recorder.callback("activity"),
        on_complete=recorder.callback("complete"),
        on_failed=recorder.callback("failed"),
        on_done=recorder.callback("done"),
    )


def test_bypass_says_what_the_settings_still_refuse(worker_env, tmp_path, monkeypatch):
    """permissions.disableBypass turns --yolo off with one line on stderr."""
    import commandcode_backend

    project = tmp_path / "project"
    (project / ".commandcode").mkdir(parents=True)
    (project / ".commandcode" / "settings.json").write_text(
        json.dumps({"permissions": {"disableBypass": True, "deny": ["Shell(rm:*)"]}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(commandcode_backend, "commandcode_home", lambda: tmp_path / "home")
    worker_env["proc"] = _FakeProcess(
        lines=[_result(subtype="success", sessionId="s1", finalText="ok")]
    )

    rec = _Recorder()
    worker = _bypass_worker(rec, str(project))
    worker.start()
    worker.join(20)

    notes = rec.activity("tool")
    assert any("permissions.disableBypass" in note for note in notes)
    assert any("1 permissions.deny rule" in note for note in notes)


def test_a_quiet_settings_file_says_nothing(worker_env, tmp_path, monkeypatch):
    import commandcode_backend

    monkeypatch.setattr(commandcode_backend, "commandcode_home", lambda: tmp_path / "home")
    worker_env["proc"] = _FakeProcess(
        lines=[_result(subtype="success", sessionId="s1", finalText="ok")]
    )

    rec = _Recorder()
    worker = _bypass_worker(rec, str(tmp_path))
    worker.start()
    worker.join(20)

    assert rec.activity("tool") == []


def test_tool_steps_read_like_claudes():
    """Command Code shares Claude's input keys, so its steps get Claude's
    phrasing instead of a snake_case name and a whole absolute path
    (inputs as measured at Command Code 1.65.2)."""
    from commandcode_worker import _tool_label

    folder = os.path.join(os.sep, "work", "proj")
    path = os.path.join(folder, "a.txt")
    assert _tool_label("read_file", {"file_path": path}) == "Reading a.txt"
    assert (
        _tool_label("edit_file", {"file_path": path, "old_string": "beta", "new_string": "BETA"})
        == "Editing a.txt, 1 line added, 1 removed"
    )
    written = _tool_label("write_file", {"file_path": path, "content": "one\ntwo\n"})
    assert written == "Writing a.txt, 2 lines"
    assert _tool_label("shell_command", {"command": "echo hi"}) == "Running: echo hi"
    assert _tool_label("grep", {"pattern": "gamma", "path": folder}) == "Searching for gamma"
    assert _tool_label("glob", {"pattern": "*.txt"}) == "Searching for *.txt"
    assert _tool_label("read_directory", {"path": folder}) == "Listing proj"
    assert _tool_label("todo_write", {"todos": []}) == "Updating the task list"
    assert _tool_label("task_list", {}) == "Using task_list"


def test_each_message_is_its_own_paragraph_and_thinking_one_row(worker_env):
    """Shaped like a real 1.65.2 turn: thinking arrives a word at a time, a
    message's heading has no sentence end, and the next message follows a tool
    with no break. That read as a row per word of thinking, and as
    "## Reading a.txt## Editing a.txt" glued together."""
    worker_env["proc"] = _FakeProcess(
        lines=[
            _event({"type": "thinking_start"}),
            _event({"type": "thinking_delta", "delta": "Read"}),
            _event({"type": "thinking_delta", "delta": " it first."}),
            _event({"type": "thinking_end", "text": "Read it first."}),
            _event({"type": "text_delta", "delta": "## Reading"}),
            _event({"type": "text_delta", "delta": " a.txt"}),
            _event({"type": "message_update", "content": [{"type": "text", "text": "## Reading"}]}),
            _event(
                {"type": "message_end", "content": [{"type": "text", "text": "## Reading a.txt"}]}
            ),
            _event(
                {
                    "type": "tool_update",
                    "toolCallId": "c1",
                    "partial": [{"type": "text", "text": "hi"}],
                }
            ),
            _event({"type": "text_delta", "delta": "## Editing a.txt"}),
            _event(
                {"type": "message_end", "content": [{"type": "text", "text": "## Editing a.txt"}]}
            ),
            _event({"type": "message_end", "content": [{"type": "text", "text": "done"}]}),
            _result(subtype="success", sessionId="s1"),
        ]
    )

    _worker, rec = _run()

    assert rec.activity("thinking") == ["Read it first."]
    assert rec.activity("assistant") == ["## Reading a.txt", "## Editing a.txt", "done"]
    assert rec.activity("tool") == []
    assert rec.texts("complete") == ["## Reading a.txt\n\n## Editing a.txt\n\ndone"]


def test_a_stop_at_the_live_edge_waits_for_the_next_delta(worker_env):
    """ "*." then "txt" is one word, not a sentence that ended at the dot."""
    worker_env["proc"] = _FakeProcess(
        lines=[
            _event({"type": "text_delta", "delta": "## Globbing *."}),
            _event({"type": "text_delta", "delta": "txt"}),
            _event({"type": "message_end", "content": []}),
            _result(subtype="success", sessionId="s1", finalText="done"),
        ]
    )

    _worker, rec = _run()

    assert rec.activity("assistant") == ["## Globbing *.txt"]


# --------------------------------------------------------------------------
# A level the model does not take
# --------------------------------------------------------------------------


def _two_runs(monkeypatch, first, second, argv_log):
    """Two processes in order: the refused run, then the bare retry."""

    def _launch(*args, **kwargs):
        argv_log.append(args[0])
        return first if len(argv_log) == 1 else second

    monkeypatch.setattr(commandcode_worker.subprocess, "Popen", _launch)


def test_a_level_the_model_does_not_take_runs_again_bare(worker_env, monkeypatch):
    # Measured at 1.69.0: a level sent to a model that takes none ends the
    # run before it starts -- exit 1, empty stdout, the reason on stderr.
    argv_log: list = []
    _two_runs(
        monkeypatch,
        _FakeProcess(
            lines=[],
            stderr=["Kimi K2.7 Code has no adjustable reasoning effort."],
            returncode=1,
        ),
        _FakeProcess(lines=[_result(subtype="success", sessionId="s1", finalText="done bare")]),
        argv_log,
    )

    _worker, rec = _run()

    assert "--effort" in argv_log[0]
    assert "--effort" not in argv_log[1]
    assert rec.texts("complete") == ["done bare"]
    assert any("no adjustable reasoning effort" in line for line in rec.activity("tool"))


def test_an_unknown_effort_retries_with_the_strongest_supported_level(worker_env, monkeypatch):
    # Measured live: the CLI validates against the model's own levels --
    # kimi-k3 refuses the "xhigh" this worker sends with "Supported: low,
    # high, max". The retry carries the strongest level the CLI named
    # instead of dropping the effort altogether.
    argv_log: list = []
    _two_runs(
        monkeypatch,
        _FakeProcess(
            lines=[],
            stderr=['Unknown effort "xhigh". Supported: low, high, max.'],
            returncode=1,
        ),
        _FakeProcess(lines=[_result(subtype="success", sessionId="s1", finalText="done at max")]),
        argv_log,
    )

    _worker, rec = _run()

    assert argv_log[1][argv_log[1].index("--effort") + 1] == "max"
    assert rec.texts("complete") == ["done at max"]
    assert any("with max instead" in line for line in rec.activity("tool"))


def test_an_unknown_effort_without_a_supported_list_runs_again_bare(worker_env, monkeypatch):
    # A refusal that names no levels leaves nothing to substitute, so the
    # retry is the bare one, as before.
    argv_log: list = []
    _two_runs(
        monkeypatch,
        _FakeProcess(
            lines=[],
            stderr=['Unknown effort "xhigh".'],
            returncode=1,
        ),
        _FakeProcess(lines=[_result(subtype="success", sessionId="s1", finalText="done bare")]),
        argv_log,
    )

    _worker, rec = _run()

    assert "--effort" not in argv_log[1]
    assert rec.texts("complete") == ["done bare"]
    assert any("Unknown effort" in line for line in rec.activity("tool"))


def test_a_turn_that_produced_something_is_never_rerun(worker_env):
    # Only a run that produced nothing qualifies: a mid-turn mention of
    # effort on stderr must not spend the prompt twice.
    worker_env["proc"] = _FakeProcess(
        lines=[
            _event({"type": "run_start", "sessionId": "s1"}),
            _event({"type": "text_delta", "delta": "half an answer. "}),
        ],
        stderr=["Kimi K2.7 Code has no adjustable reasoning effort."],
        returncode=1,
    )

    _worker, rec = _run()

    assert rec.texts("complete") == []
    assert any("exit code 1" in line for line in rec.texts("failed"))


def test_a_failed_retry_fails_like_any_other_run(worker_env, monkeypatch):
    argv_log: list = []
    _two_runs(
        monkeypatch,
        _FakeProcess(
            lines=[],
            stderr=["Kimi K2.7 Code has no adjustable reasoning effort."],
            returncode=1,
        ),
        _FakeProcess(lines=[], stderr=["boom"], returncode=1),
        argv_log,
    )

    _worker, rec = _run()

    assert len(argv_log) == 2
    assert rec.texts("complete") == []
    assert any("boom" in line for line in rec.texts("failed"))


# --------------------------------------------------------------------------
# A session that is no longer there
# --------------------------------------------------------------------------


def test_a_lost_session_runs_again_as_a_fresh_conversation(worker_env, monkeypatch):
    # Measured at 1.69.0: a --resume whose session is gone -- cleaned up, or
    # saved in a different project folder -- ends the run before it starts,
    # with `No session "<id>" found to resume.` on stderr.
    argv_log: list = []
    _two_runs(
        monkeypatch,
        _FakeProcess(lines=[], stderr=['No session "gone" found to resume.'], returncode=1),
        _FakeProcess(lines=[_result(subtype="success", sessionId="s1", finalText="done fresh")]),
        argv_log,
    )

    _worker, rec = _run(session_id="gone")

    assert "--resume" in argv_log[0] and "gone" in argv_log[0]
    assert "--resume" not in argv_log[1]
    assert rec.texts("complete") == ["done fresh"]
    assert rec.texts("session") == ["s1"]
    assert any("fresh conversation" in line for line in rec.activity("tool"))
    # The tab held the dead id; it must adopt the replacement.
    assert _worker.session_replaced is True


def test_a_lost_session_retry_still_gets_the_bare_effort_retry(worker_env, monkeypatch):
    # The CLI resolves --resume before it checks effort (measured at 1.69.0),
    # so the fresh retry runs with the effort still set -- and must still be
    # retried bare when that model refuses the level, instead of the ordering
    # turning one recoverable failure into two hard ones.
    argv_log: list = []
    runs = iter(
        [
            _FakeProcess(lines=[], stderr=['No session "gone" found to resume.'], returncode=1),
            _FakeProcess(
                lines=[],
                stderr=["Kimi K2.7 Code has no adjustable reasoning effort."],
                returncode=1,
            ),
            _FakeProcess(lines=[_result(subtype="success", sessionId="s1", finalText="done")]),
        ]
    )

    def _launch(*args, **kwargs):
        argv_log.append(args[0])
        return next(runs)

    monkeypatch.setattr(commandcode_worker.subprocess, "Popen", _launch)

    rec = _Recorder()
    worker = CommandcodeWorker(
        "do the thing",
        "gone",
        "C:\\work",
        "plan",
        effort="xhigh",
        on_session=rec.callback("session"),
        on_started=rec.callback("started"),
        on_activity=rec.callback("activity"),
        on_complete=rec.callback("complete"),
        on_failed=rec.callback("failed"),
        on_done=rec.callback("done"),
    )
    worker.start()
    worker.join(20)

    assert len(argv_log) == 3
    assert "--resume" in argv_log[0] and "--effort" in argv_log[0]
    assert "--resume" not in argv_log[1] and "--effort" in argv_log[1]
    assert "--resume" not in argv_log[2] and "--effort" not in argv_log[2]
    assert rec.texts("complete") == ["done"]


def test_a_clean_exit_without_a_result_names_the_exit_code(worker_env):
    worker_env["proc"] = _FakeProcess(lines=[], returncode=0)

    _worker, rec = _run()

    assert any("exit code 0" in line for line in rec.texts("failed"))


def test_a_session_lost_after_output_is_not_rerun(worker_env):
    # Only a run that produced nothing qualifies: a mid-turn mention of the
    # session on stderr must not spend the prompt twice.
    worker_env["proc"] = _FakeProcess(
        lines=[
            _event({"type": "run_start", "sessionId": "s1"}),
            _event({"type": "text_delta", "delta": "half an answer. "}),
        ],
        stderr=['No session "gone" found to resume.'],
        returncode=1,
    )

    _worker, rec = _run(session_id="gone")

    assert rec.texts("complete") == []
    assert any("exit code 1" in line for line in rec.texts("failed"))


def test_the_newest_withheld_tool_is_named_as_withheld(worker_env):
    # schedule_wakeup joined the headless denylist after 1.54.0; its refusal
    # says why no mode can grant it like the other nine.
    worker_env["proc"] = _FakeProcess(
        lines=[
            _event({"type": "run_start", "sessionId": "s1"}),
            _event({"type": "tool_denied", "toolCallId": "c9", "toolName": "schedule_wakeup"}),
            _result(subtype="success", sessionId="s1", finalText="Done."),
        ]
    )

    _worker, rec = _run()

    assert any("withholds this tool from a headless run" in t for t in rec.activity("tool"))


# --------------------------------------------------------------------------
# A provider-capacity stop
# --------------------------------------------------------------------------

# Measured from the installed CLI dist: exit 1, raw server text on stderr.
_CAPACITY_TEXT = (
    "Error: 429 The request limited providers for this model and they are "
    "currently at capacity. Add more providers or remove restrictions for "
    "automatic fallback. Providers considered: poolside."
)


def _capacity_worker():
    return CommandcodeWorker(
        "hi",
        None,
        ".",
        "default",
        on_session=lambda _s: None,
        on_started=lambda: None,
        on_activity=lambda _k, _t: None,
        on_complete=lambda _t: None,
        on_failed=lambda _m: None,
        on_done=lambda: None,
    )


@pytest.mark.parametrize(
    "line",
    [
        _CAPACITY_TEXT,
        "error: providers considered: poolside",
        "The request LIMITED PROVIDERS for this model",
        "429 too many requests",
    ],
)
def test_capacity_stop_recognises_the_servers_own_words(line):
    worker = _capacity_worker()
    worker._error_lines = [line]
    assert worker._capacity_stop(1) is True
    # Only a non-zero exit counts, and other exit-1 refusals are not capacity.
    assert worker._capacity_stop(0) is False
    assert worker._capacity_stop(None) is False

    other = _capacity_worker()
    other._error_lines = ['No session "gone" found to resume.']
    assert other._capacity_stop(1) is False


def test_a_capacity_stop_records_the_model_and_retries_on_the_next_paid(worker_env, monkeypatch):
    argv_log: list = []
    _two_runs(
        monkeypatch,
        _FakeProcess(lines=[], stderr=[_CAPACITY_TEXT], returncode=1),
        _FakeProcess(lines=[_result(subtype="success", sessionId="s1", finalText="paid done")]),
        argv_log,
    )
    recorded: list = []
    monkeypatch.setattr(
        commandcode_worker, "record_capacity_hit", lambda model: recorded.append(model) or True
    )
    routed: list = []

    def fake_route(_task, **kwargs):
        routed.append(kwargs)
        return commandcode_worker.Route("paid/next", "high", "standard", cost=0.5, priced=True)

    monkeypatch.setattr(commandcode_worker, "route", fake_route)

    worker, rec = _run(auto_free_fallback=True, model="paid/first")

    assert recorded == ["paid/first"]
    assert routed and routed[0]["exclude_models"] == {"paid/first"}
    # The retry asks without free candidates, so a QUICK task cannot prefer one.
    assert routed[0]["include_free"] is False
    assert routed[0]["context_tokens"] is None
    assert len(argv_log) == 2
    assert argv_log[1][argv_log[1].index("--model") + 1] == "paid/next"
    assert argv_log[1][argv_log[1].index("--effort") + 1] == "high"
    assert rec.texts("complete") == ["paid done"]
    assert worker.fallback_model == "paid/next"
    assert any(
        "at capacity for paid/first. Retrying with paid/next" in text
        for text in rec.activity("notice")
    )


@pytest.mark.parametrize(
    "lines",
    [
        [_event({"type": "text_delta", "delta": "half an answer"})],
        [_event({"type": "tool_running", "toolCallId": "c1", "toolName": "write_file"})],
    ],
)
def test_a_capacity_stop_after_output_or_a_tool_is_not_retried(worker_env, monkeypatch, lines):
    worker_env["proc"] = _FakeProcess(lines=lines, stderr=[_CAPACITY_TEXT], returncode=1)
    recorded: list = []
    monkeypatch.setattr(
        commandcode_worker, "record_capacity_hit", lambda model: recorded.append(model) or True
    )
    monkeypatch.setattr(
        commandcode_worker,
        "route",
        lambda *_a, **_k: pytest.fail("a half-run turn must not replay"),
    )

    _worker, rec = _run(auto_free_fallback=True, model="paid/first")

    # The stop is still recorded: the next turn must not re-pick the model.
    assert recorded == ["paid/first"]
    assert rec.texts("complete") == []
    assert any("at capacity" in text for text in rec.texts("failed"))


def test_a_capacity_stop_falls_back_to_free_when_no_paid_model_remains(worker_env, monkeypatch):
    argv_log: list = []
    _two_runs(
        monkeypatch,
        _FakeProcess(lines=[], stderr=[_CAPACITY_TEXT], returncode=1),
        _FakeProcess(lines=[_result(subtype="success", sessionId="s1", finalText="free done")]),
        argv_log,
    )
    monkeypatch.setattr(commandcode_worker, "record_capacity_hit", lambda _model: True)
    # The router knows the tier but no other paid candidate is eligible.
    monkeypatch.setattr(
        commandcode_worker, "route", lambda _task, **_k: commandcode_worker.Route("", "", "")
    )
    monkeypatch.setattr(
        commandcode_worker,
        "best_free_model",
        lambda **_k: commandcode_worker.Route("free/code", "", "free", cost=0.0, priced=True),
    )

    _worker, rec = _run(auto_free_fallback=True, model="paid/first")

    assert len(argv_log) == 2
    assert argv_log[1][argv_log[1].index("--model") + 1] == "free/code"
    assert "--effort" not in argv_log[1]
    assert rec.texts("complete") == ["free done"]
    assert any(
        "provider capacity reached. Retrying with free model free/code" in text
        for text in rec.activity("notice")
    )


def test_a_capacity_stop_is_recorded_but_not_retried_when_auto_fallback_is_off(
    worker_env, monkeypatch
):
    worker_env["proc"] = _FakeProcess(lines=[], stderr=[_CAPACITY_TEXT], returncode=1)
    recorded: list = []
    monkeypatch.setattr(
        commandcode_worker, "record_capacity_hit", lambda model: recorded.append(model) or True
    )
    monkeypatch.setattr(
        commandcode_worker, "route", lambda *_a, **_k: pytest.fail("no retry without Auto")
    )

    _worker, rec = _run(auto_free_fallback=False, model="paid/first")

    # The stop is recorded like a limit stop is: Auto or not, the next turn
    # must not re-pick a model whose providers are full.
    assert recorded == ["paid/first"]
    assert rec.texts("complete") == []
    assert any("at capacity" in text for text in rec.texts("failed"))


def test_a_limit_that_cannot_be_recorded_says_so(worker_env, monkeypatch):
    worker_env["proc"] = _FakeProcess(lines=[], returncode=10)
    monkeypatch.setattr(commandcode_worker, "record_limit_hit", lambda _code: False)

    _worker, rec = _run(auto_free_fallback=False)

    assert any("could not be recorded" in text for text in rec.activity("notice"))
    assert any("insufficient credits" in text for text in rec.texts("failed"))


# Measured from the installed CLI dist, in the same table shape the router
# parses: a QUICK task's cheapest pick is free, and a paid candidate remains
# beside it.
_QUICK_CATALOG = """| Id (use EXACTLY this) | Name | Context | Efforts | $/1M in/out · cache read | Min plan | Best for |
|---|---|---|---|---|---|---|
| `deepseek/deepseek-v4-flash` | DeepSeek V4 Flash | 1M | off, high, max | $0.15/$0.6 · cache $0.003 | Go and above | fast |
| `deepseek/deepseek-v4.1-flash` | DeepSeek V4.1 Flash | 1M | low, medium | $0.16/$0.58 · cache $0.003 | Go and above | fast |
| `inclusionai/ling-3.1-flash:free` | Ling 3.1 Flash | 262K | low, medium, high | $0/$0 · cache $0 | Go and above | cheap |
"""


def test_a_quick_capacity_retry_prefers_a_paid_candidate_over_free(
    worker_env, monkeypatch, tmp_path
):
    # A QUICK task's normal pick is the free model, so the capacity retry
    # must ask without free candidates or it would "switch" to the model it
    # would have chosen anyway while paid candidates remain.
    import commandcode_router as router

    monkeypatch.setenv("COMMANDCODE_USAGE_JSON", str(tmp_path / "usage.json"))
    catalog = router.parse_prices(_QUICK_CATALOG)
    monkeypatch.setattr(
        commandcode_worker,
        "route",
        lambda task, **kwargs: router.route(task, catalog=catalog, **kwargs),
    )
    argv_log: list = []
    _two_runs(
        monkeypatch,
        _FakeProcess(lines=[], stderr=[_CAPACITY_TEXT], returncode=1),
        _FakeProcess(lines=[_result(subtype="success", sessionId="s1", finalText="paid done")]),
        argv_log,
    )
    monkeypatch.setattr(commandcode_worker, "record_capacity_hit", lambda _model: True)

    _worker, rec = _run(
        auto_free_fallback=True, model="deepseek/deepseek-v4-flash", prompt="fix the typo"
    )

    assert len(argv_log) == 2
    assert argv_log[1][argv_log[1].index("--model") + 1] == "deepseek/deepseek-v4.1-flash"
    assert rec.texts("complete") == ["paid done"]


def test_a_capacity_stop_that_cannot_switch_models_announces_it_once(worker_env, monkeypatch):
    worker_env["proc"] = _FakeProcess(
        lines=[_event({"type": "text_delta", "delta": "half an answer"})],
        stderr=[_CAPACITY_TEXT],
        returncode=1,
    )
    monkeypatch.setattr(commandcode_worker, "record_capacity_hit", lambda _model: True)
    monkeypatch.setattr(
        commandcode_worker,
        "route",
        lambda *_a, **_k: pytest.fail("a half-run turn must not replay"),
    )

    _worker, rec = _run(auto_free_fallback=True, model="paid/first")

    assert rec.activity("notice") == [
        "Command Code providers were at capacity; this turn cannot switch models."
    ]
    assert any("at capacity" in text for text in rec.texts("failed"))


def test_the_turns_context_size_is_exposed_for_pricing_and_the_tab(worker_env):
    # Measured from the installed CLI dist: model_request_end carries the
    # request's usage, and the request's input side is the context the next
    # turn re-reads.
    worker_env["proc"] = _FakeProcess(
        lines=[
            _event({"type": "run_start", "sessionId": "s1"}),
            _event(
                {
                    "type": "model_request_end",
                    "model": "gpt-5.5",
                    "usage": {
                        "inputTokens": 1_000,
                        "outputTokens": 200,
                        "cacheReadTokens": 50_000,
                        "cacheWriteTokens": 300,
                    },
                    "stopReason": "end_turn",
                }
            ),
            _event({"type": "text_delta", "delta": "done. "}),
            _result(subtype="success", sessionId="s1", finalText="done."),
        ]
    )

    worker, _rec = _run()

    assert worker.context_tokens == (51_300, 0)


def test_a_result_that_reports_usage_alone_still_exposes_the_context_size(worker_env):
    # The result's usage totals are one request's usage when the stream
    # carried no model_request_end.
    worker_env["proc"] = _FakeProcess(
        lines=[
            _result(
                subtype="success",
                sessionId="s1",
                finalText="done",
                usage={"inputTokens": 10, "outputTokens": 5, "cacheReadTokens": 40},
            )
        ]
    )

    worker, _rec = _run()

    assert worker.context_tokens == (50, 0)
