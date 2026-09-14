"""The Command Code worker against a scripted event stream.

No Node, no CLI: a fake process hands the worker the exact frames the real one
writes (measured at 1.53.1) and the callbacks are read back.
"""

from __future__ import annotations

import io
import json
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


def _run(session_id=None):
    rec = _Recorder()
    worker = CommandcodeWorker(
        "do the thing",
        session_id,
        "C:\\work",
        "plan",
        model="gpt-5.5",
        effort="high",
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


def test_model_effort_and_resume_are_passed_through():
    argv = build_command("command-code", "plan", "gpt-5.5", "high", "abc-123")
    assert argv[argv.index("--model") + 1] == "gpt-5.5"
    assert argv[argv.index("--effort") + 1] == "high"
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
    assert "read_file: x.py" in rec.activity("tool")
    assert any("read_file" in row and "print(1)" in row for row in rec.activity("result"))
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


def test_max_turns_is_explained_rather_than_numbered(worker_env):
    worker_env["proc"] = _FakeProcess(
        lines=[_result(subtype="max_turns", sessionId="s1", finalText="partial")], returncode=8
    )

    _worker, rec = _run()

    assert any("maximum number of turns" in text for text in rec.texts("failed"))


def test_a_nonzero_exit_without_a_result_is_explained(worker_env):
    worker_env["proc"] = _FakeProcess(lines=[], returncode=5)

    _worker, rec = _run()

    assert any("rate limited" in text for text in rec.texts("failed"))


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
