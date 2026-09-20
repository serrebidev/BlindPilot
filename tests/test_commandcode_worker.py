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
    """It used to arrive as "Finished with nothing to say."."""
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
