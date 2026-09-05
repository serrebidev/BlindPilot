"""Hermes backend regression tests.

These lock down the behaviours that were wrong in the first working version
and were only found by talking to a real Hermes: the spinner arriving as
reasoning, a tool result that is an object rather than a string, and a status
check whose exit code says nothing. Each of those produced a turn that looked
successful while losing something a screen-reader user needs.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import agent_backends
import hermes_backend
import hermes_worker
from agent_backends import (
    BACKEND_CLAUDE,
    BACKEND_HERMES,
    BACKENDS,
    backend_label,
    compaction_request,
    normalize_backend,
    worker_class,
)
from hermes_worker import HermesWorker


def _callbacks() -> dict:
    return {
        "on_session": lambda _value: None,
        "on_started": lambda: None,
        "on_activity": lambda _kind, _value: None,
        "on_complete": lambda _value: None,
        "on_failed": lambda _value: None,
        "on_done": lambda: None,
    }


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    """A finished child process with the given output."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _reset_wsl_cache(monkeypatch) -> None:
    """Clear the once-per-run WSL probe so a test can drive it itself."""
    monkeypatch.setattr(hermes_backend, "_WSL_HERMES", None, raising=False)
    monkeypatch.setattr(hermes_backend, "_WSL_CHECKED", False, raising=False)
    monkeypatch.setattr(hermes_backend, "_WSL_PYTHON", None, raising=False)
    monkeypatch.setattr(hermes_backend, "_WSL_PYTHON_CHECKED", False, raising=False)


class _NullThread:
    """Stands in for the reader threads a transport starts."""

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return None


def _worker(**overrides) -> HermesWorker:
    callbacks = _callbacks()
    callbacks.update(overrides)
    return HermesWorker("test", None, ".", "default", **callbacks)


class _FakeTransport:
    """A transport that replays scripted frames, so no Hermes is needed.

    Its stream ENDS when the script runs out, the way a real pipe or socket
    does, and ``connected()`` then answers False — see
    tests/transport_contract.py for why a fake that stays "connected but
    silent, for ever" hid two cross-platform defects in this file's own tests.
    """

    def __init__(self, frames: list[dict]) -> None:
        self.frames = list(frames)
        self.sent: list[dict] = []
        self.closed = False
        # Held connections ask whether a transport can carry another turn. A
        # scripted one can until its frames run out or it is closed.
        self.alive = True

    def send(self, message: dict) -> bool:
        if not self.connected():
            # A real transport cannot write to a peer that has gone: both
            # StdioTransport and WebSocketTransport answer False here.
            return False
        self.sent.append(message)
        return True

    def receive(self, timeout: float) -> dict | None:  # noqa: ARG002 - interface
        if self.frames:
            return self.frames.pop(0)
        self.alive = False
        return None

    def close(self) -> None:
        self.closed = True

    def connected(self) -> bool:
        return self.alive and not self.closed

    def failure_detail(self) -> str:
        return "fake transport ended"


def _event(kind: str, payload: dict | None = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {"type": kind, "session_id": "s1", "payload": payload or {}},
    }


# -- registration ----------------------------------------------------------


def test_hermes_is_registered_and_named():
    assert normalize_backend("Hermes") == BACKEND_HERMES
    assert normalize_backend("hermes-agent") == BACKEND_HERMES
    assert normalize_backend("NOUS") == BACKEND_HERMES
    assert backend_label(BACKEND_HERMES) == "Hermes"
    assert BACKEND_HERMES in agent_backends.BACKEND_IDS


def test_worker_class_selects_the_hermes_adapter():
    class Claude:
        pass

    assert worker_class(BACKEND_HERMES, Claude) is HermesWorker
    # The other backends must keep resolving as before.
    assert worker_class(BACKEND_CLAUDE, Claude) is Claude


def test_declared_capabilities_match_what_the_worker_implements():
    info = BACKENDS[BACKEND_HERMES]
    assert info.supports_steering is True
    assert info.supports_compaction is True
    assert info.supports_permissions is True
    # Hermes takes a reasoning level as a per-session override on
    # session.create. This asserted False for a while, freezing a wrong belief
    # about the protocol: the picker hid the control and the setup wizard told
    # the user Hermes "does not expose a reasoning effort level".
    assert info.supports_effort is True
    # A backend that says it compacts must have a request to compact with.
    assert compaction_request(BACKEND_HERMES) is not None


# -- reasoning versus the spinner -----------------------------------------


def test_the_terminal_spinner_never_becomes_a_reasoning_row():
    """Hermes streams a kawaii spinner on the reasoning channel.

    It is a progress indicator for a terminal. Read aloud it is noise, so it
    must not reach the transcript.
    """
    rows = []
    worker = _worker(on_activity=lambda kind, value: rows.append((kind, value)))
    for text in ("(⌐■_■) contemplating...", "٩(๑❛ᴗ❛๑)۶ musing...", "ಠ_ಠ deliberating..."):
        worker._handle_event(_event("thinking.delta", {"text": text}))
    worker._handle_event(_event("message.complete", {"text": "Done.", "status": "complete"}))

    assert [kind for kind, _ in rows] == []


def test_real_reasoning_becomes_its_own_row():
    rows = []
    worker = _worker(on_activity=lambda kind, value: rows.append((kind, value)))
    worker._handle_event(
        _event("reasoning.available", {"text": "The file is missing, so I will create it."})
    )
    worker._handle_event(_event("message.complete", {"text": "Created.", "status": "complete"}))

    assert rows == [("thinking", "The file is missing, so I will create it.")]


def test_reasoning_identical_to_the_answer_is_not_read_twice():
    """Some providers report a one-word answer as its own reasoning."""
    rows = []
    completed = []
    worker = _worker(
        on_activity=lambda kind, value: rows.append((kind, value)),
        on_complete=completed.append,
    )
    worker._handle_event(_event("reasoning.available", {"text": "PONG"}))
    worker._handle_event(_event("message.complete", {"text": "PONG", "status": "complete"}))

    assert rows == []
    assert completed == ["PONG"]


# -- tool rows -------------------------------------------------------------


def test_a_tool_result_object_still_produces_a_result_row():
    """Hermes sends most results decoded, not as a string.

    The first version only read strings, so tools ran and reported nothing -
    a silent turn that looked like a success.
    """
    rows = []
    worker = _worker(on_activity=lambda kind, value: rows.append((kind, value)))
    worker._handle_event(
        _event("tool.start", {"tool_id": "t1", "name": "terminal", "context": "echo hi"})
    )
    worker._handle_event(
        _event(
            "tool.complete",
            {
                "tool_id": "t1",
                "name": "terminal",
                "result": {"output": "hi", "exit_code": 0, "error": None},
            },
        )
    )

    assert rows == [("tool", "terminal: echo hi"), ("result", "terminal: hi")]


def test_a_failing_command_reports_its_error_and_exit_code():
    rows = []
    worker = _worker(on_activity=lambda kind, value: rows.append((kind, value)))
    worker._handle_event(
        _event(
            "tool.complete",
            {
                "tool_id": "t2",
                "name": "terminal",
                "result": {"output": "", "exit_code": 127, "error": "not found"},
            },
        )
    )

    assert len(rows) == 1
    kind, text = rows[0]
    assert kind == "result"
    assert "not found" in text
    assert "127" in text


def test_hermes_own_summary_is_preferred_over_the_raw_result():
    rows = []
    worker = _worker(on_activity=lambda kind, value: rows.append((kind, value)))
    worker._handle_event(
        _event(
            "tool.complete",
            {
                "tool_id": "t3",
                "name": "web_search",
                "summary": "Did 3 searches in 2.1s",
                "result": {"data": {"web": [1, 2, 3]}},
            },
        )
    )

    assert rows == [("result", "web_search: Did 3 searches in 2.1s")]


def test_a_huge_tool_result_is_bounded_and_says_so():
    """A screen reader walks a row line by line, so results are capped."""
    rows = []
    worker = _worker(on_activity=lambda kind, value: rows.append((kind, value)))
    output = "\n".join(f"line {n}" for n in range(500))
    worker._handle_event(
        _event("tool.complete", {"tool_id": "t4", "name": "terminal", "result": {"output": output}})
    )

    _kind, text = rows[0]
    assert len(text.splitlines()) <= hermes_worker._RESULT_MAX_LINES + 2
    assert "more lines not shown" in text


def test_an_unrecognised_result_shape_still_says_the_tool_finished():
    """Silence reads as a hang, so an unknown shape is still reported."""
    rows = []
    worker = _worker(on_activity=lambda kind, value: rows.append((kind, value)))
    worker._handle_event(
        _event("tool.complete", {"tool_id": "t5", "name": "mystery", "result": {"odd": 1}})
    )

    assert rows and rows[0][0] == "result"
    assert "finished" in rows[0][1]


# -- streaming and turn completion ---------------------------------------


def test_streamed_deltas_become_the_completed_answer():
    completed = []
    worker = _worker(on_complete=completed.append)
    for chunk in ("Blind", "Pilot", " ready."):
        worker._handle_event(_event("message.delta", {"text": chunk}))
    worker._handle_event(_event("message.complete", {"status": "complete"}))

    assert completed == ["BlindPilot ready."]


def test_an_interrupted_turn_is_reported_once_and_not_as_an_answer():
    failed = []
    completed = []
    worker = _worker(on_failed=failed.append, on_complete=completed.append)
    worker._handle_event(_event("message.complete", {"status": "interrupted"}))

    assert completed == []
    assert len(failed) == 1


def test_a_user_cancelled_turn_is_not_reported_as_a_failure():
    """Stopping a turn on purpose is not an error worth announcing."""
    failed = []
    worker = _worker(on_failed=failed.append)
    worker._cancelled = True
    worker._handle_event(_event("message.complete", {"status": "interrupted"}))

    assert failed == []


def test_unknown_events_are_ignored_rather_than_breaking_the_turn():
    """Hermes gains events over releases; an unknown one must not be fatal."""
    rows = []
    failed = []
    worker = _worker(
        on_activity=lambda kind, value: rows.append((kind, value)),
        on_failed=failed.append,
    )
    assert worker._handle_event(_event("some.future.event", {"whatever": True})) is None
    assert worker._handle_event({"jsonrpc": "2.0", "result": {}, "id": 1}) is None
    assert rows == []
    assert failed == []


def test_the_noisy_session_list_event_produces_no_rows():
    """``sessions.changed`` arrives dozens of times with an empty payload.

    Acting on each one would rebuild the conversation list constantly, which
    on Windows clears the selection and interrupts whatever is being read.
    """
    rows = []
    worker = _worker(on_activity=lambda kind, value: rows.append((kind, value)))
    for _ in range(30):
        worker._handle_event(_event("sessions.changed", {}))

    assert rows == []


# -- permissions and approvals -------------------------------------------


def test_permission_modes_map_onto_whether_hermes_may_act_unattended():
    worker = _worker()
    worker._permission_mode = "bypassPermissions"
    assert worker._session_params()["yolo"] is True
    worker._permission_mode = "default"
    assert worker._session_params()["yolo"] is False
    worker._permission_mode = "plan"
    assert worker._session_params()["yolo"] is False


def test_an_approval_is_answered_so_the_turn_cannot_hang():
    """An unanswered approval leaves Hermes waiting forever."""
    for mode, expected in (("auto", "approve"), ("default", "deny")):
        rows = []
        # `rows=rows` binds this iteration's list. Without it the closure reads
        # whatever `rows` names when it is finally called, which in a loop is
        # the LAST iteration's list — so an assertion about the first would be
        # checking the wrong object. ruff's B023 catches it; the older version
        # pinned here did not have the rule switched on.
        worker = _worker(on_activity=lambda kind, value, rows=rows: rows.append((kind, value)))
        worker._permission_mode = mode
        transport = _FakeTransport([])
        worker._transport = transport
        worker._handle_event(
            _event("approval.request", {"request_id": "r1", "command": "rm -rf /tmp/x"})
        )

        answers = [m for m in transport.sent if m.get("method") == "approval.respond"]
        assert len(answers) == 1
        assert answers[0]["params"]["decision"] == expected
        # Either way the user is told what happened.
        assert rows and rows[0][0] == "tool"


def test_a_denied_approval_explains_how_to_allow_it():
    rows = []
    worker = _worker(on_activity=lambda kind, value: rows.append((kind, value)))
    worker._permission_mode = "default"
    worker._transport = _FakeTransport([])
    worker._handle_event(_event("approval.request", {"request_id": "r2", "command": "git push"}))

    assert "permission mode" in rows[0][1]


# -- steering and cancelling ---------------------------------------------


def test_steering_is_refused_before_the_turn_is_live():
    worker = _worker()
    worker._transport = _FakeTransport([])
    worker._gateway_session = "s1"
    assert worker.steer("go left") is False

    worker._accepting_input.set()
    worker._live_session = "s1"
    assert worker.steer("go left") is True
    steers = [m for m in worker._transport.sent if m.get("method") == "session.steer"]
    assert steers and steers[0]["params"]["text"] == "go left"


def test_cancel_asks_hermes_to_stop_before_dropping_the_connection():
    """A remote Hermes would otherwise keep working on a dead answer."""
    worker = _worker()
    transport = _FakeTransport([])
    worker._transport = transport
    worker._gateway_session = "s1"
    worker.cancel()

    assert [m["method"] for m in transport.sent] == ["session.interrupt"]
    assert transport.closed is True


def test_cancel_leaves_the_close_to_a_running_worker_thread():
    """Closing a local gateway can wait two seconds; cancel() runs on the GUI thread."""
    released = threading.Event()
    closed_on: list[str] = []

    class _Recording(_FakeTransport):
        def close(self) -> None:
            closed_on.append(threading.current_thread().name)
            super().close()

    transport = _Recording([])
    worker = _worker()
    worker._transport = transport
    worker._live_session = "live-1"
    worker._gateway_session = "live-1"
    worker._do_run = lambda: released.wait(5)
    worker.start()

    worker.cancel()
    assert transport.closed is False
    released.set()
    worker.join(5)

    assert transport.closed is True
    assert closed_on == [worker.name]


# -- compaction -----------------------------------------------------------


def test_compacting_without_a_conversation_is_refused_clearly():
    failed = []
    callbacks = _callbacks()
    callbacks["on_failed"] = failed.append
    worker = HermesWorker("", None, ".", "default", compact=True, **callbacks)
    worker._do_run()

    assert failed and "compact" in failed[0].lower()


def test_compaction_uses_hermes_own_request_and_says_what_happened():
    completed = []
    callbacks = _callbacks()
    callbacks["on_complete"] = completed.append
    worker = HermesWorker("", "stored-1", ".", "default", compact=True, **callbacks)
    transport = _FakeTransport([{"jsonrpc": "2.0", "id": 101, "result": {}}])
    worker._transport = transport
    worker._live_session = "live-1"
    worker._request_id = 100
    worker._run_compaction()

    assert [m["method"] for m in transport.sent] == ["session.compress"]
    # A compaction turn has no answer text, so silence would read as failure.
    assert completed == ["Conversation compacted."]


# -- resuming -------------------------------------------------------------


def test_resuming_asks_hermes_to_reopen_the_stored_conversation():
    callbacks = _callbacks()
    sessions = []
    callbacks["on_session"] = sessions.append
    worker = HermesWorker("hi", "stored-7", ".", "default", **callbacks)
    transport = _FakeTransport([{"jsonrpc": "2.0", "id": 101, "result": {"session_id": "live-7"}}])
    worker._transport = transport
    worker._request_id = 100

    assert worker._ensure_session() is True
    request = transport.sent[0]
    assert request["method"] == "session.resume"
    assert request["params"]["session_id"] == "stored-7"
    # The transcript is already on screen; replaying it would duplicate rows.
    assert request["params"]["omit_messages"] is True


def test_a_new_conversation_reports_the_id_that_survives_a_restart():
    """Two ids come back; only the stored one can reopen the conversation."""
    sessions = []
    callbacks = _callbacks()
    callbacks["on_session"] = sessions.append
    worker = HermesWorker("hi", None, "/tmp", "default", **callbacks)
    transport = _FakeTransport(
        [
            {
                "jsonrpc": "2.0",
                "id": 101,
                "result": {"session_id": "live-1", "stored_session_id": "20260816_1200_abc"},
            }
        ]
    )
    worker._transport = transport
    worker._request_id = 100

    assert worker._ensure_session() is True
    assert sessions == ["20260816_1200_abc"]
    assert worker._live_session == "live-1"


def test_a_session_error_is_surfaced_with_hermes_own_message():
    failed = []
    callbacks = _callbacks()
    callbacks["on_failed"] = failed.append
    worker = HermesWorker("hi", None, "/tmp", "default", **callbacks)
    worker._transport = _FakeTransport(
        [{"jsonrpc": "2.0", "id": 101, "error": {"code": 4001, "message": "session not found"}}]
    )
    worker._request_id = 100

    assert worker._ensure_session() is False
    assert failed == ["session not found"]


# -- Hermes inside WSL ----------------------------------------------------
#
# A Windows desktop with Hermes installed in WSL. Every one of these covers a
# fault found by running on Windows, not by reading the code.


def test_a_windows_desktop_finds_hermes_installed_in_wsl(monkeypatch):
    """Nothing in Windows' PATH points at a Hermes living in WSL.

    Before this, a Windows machine with a perfectly good Hermes reported that
    Hermes was not installed.
    """
    monkeypatch.setattr(hermes_backend.platform, "system", lambda: "Windows")
    monkeypatch.setattr(hermes_backend.shutil, "which", lambda name: r"C:\Windows\wsl.exe")
    monkeypatch.setattr(hermes_backend, "hermes_python", lambda: None)
    _reset_wsl_cache(monkeypatch)

    calls = []

    def _fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return _completed("/home/u/.hermes/hermes-agent/venv/bin/python3")

    monkeypatch.setattr(hermes_backend.subprocess, "run", _fake_run)

    assert hermes_backend.wsl_hermes_python() == "/home/u/.hermes/hermes-agent/venv/bin/python3"
    assert hermes_backend.hermes_installed() is True
    # The probe runs inside WSL, so its home is expanded there rather than
    # guessed from Windows.
    probe = " ".join(calls[0][0])
    assert "HERMES_HOME" in probe and "wsl.exe" in probe


def test_the_wsl_probe_is_only_run_once(monkeypatch):
    """Every backend check would otherwise start a WSL process."""
    monkeypatch.setattr(hermes_backend.platform, "system", lambda: "Windows")
    monkeypatch.setattr(hermes_backend.shutil, "which", lambda name: r"C:\Windows\wsl.exe")
    _reset_wsl_cache(monkeypatch)
    runs = []

    def _fake_run(command, **kwargs):
        runs.append(command)
        return _completed("/opt/hermes/venv/bin/python3")

    monkeypatch.setattr(hermes_backend.subprocess, "run", _fake_run)

    hermes_backend.wsl_hermes_python()
    hermes_backend.wsl_hermes_python()
    hermes_backend.wsl_hermes_available()

    assert len(runs) == 1


def test_hermes_output_is_read_as_utf8_whatever_the_code_page(monkeypatch):
    """Windows decodes a child's output with the legacy code page by default.

    Hermes' own banner contains bytes outside it, so the check died with
    UnicodeDecodeError and reported an unconfigured Hermes.
    """
    kwargs = hermes_backend._text_output_kwargs()

    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"

    # And every probe has to use it, or the crash comes back in one of them.
    monkeypatch.setattr(hermes_backend.platform, "system", lambda: "Windows")
    monkeypatch.setattr(hermes_backend.shutil, "which", lambda name: r"C:\Windows\wsl.exe")
    monkeypatch.setattr(hermes_backend, "find_hermes_cli", lambda: None)
    _reset_wsl_cache(monkeypatch)
    seen = []

    def _fake_run(command, **kw):
        seen.append(kw)
        return _completed("/opt/hermes/venv/bin/python3\nModel: some-model\n")

    monkeypatch.setattr(hermes_backend.subprocess, "run", _fake_run)
    hermes_backend.wsl_hermes_python()
    hermes_backend.hermes_auth_ok()

    assert seen, "expected the probes to run"
    for kw in seen:
        assert kw.get("encoding") == "utf-8", kw


def test_a_windows_folder_becomes_the_path_wsl_understands():
    """The folder picker hands over a Windows path; Hermes needs a WSL one."""
    convert = hermes_backend.windows_path_to_wsl

    assert convert(r"D:\projekty\blindpilot") == "/mnt/d/projekty/blindpilot"
    assert convert(r"C:\Users\someone") == "/mnt/c/Users/someone"
    # A drive on its own is still a directory.
    assert convert("D:") == "/mnt/d"
    # Repeated and trailing separators must not leave empty components: the
    # first version produced "/mnt/d/projekty//blindpilot".
    assert convert("D:\\projekty\\\\blindpilot\\") == "/mnt/d/projekty/blindpilot"
    assert "//" not in convert("D:\\\\a\\\\\\\\b\\\\")
    # Anything already POSIX is left alone.
    assert convert("/home/ubuntu") == "/home/ubuntu"
    assert convert("") == ""


def test_a_wsl_path_becomes_the_windows_form_again():
    """Hermes records the directory as it saw it; reopening needs it back.

    Without this a resumed conversation quietly reopened in whatever folder
    happened to be current, because Windows cannot stat "/mnt/d/work".
    """
    convert = hermes_backend.wsl_path_to_windows

    assert convert("/mnt/d/projekty/blindpilot") == "D:\\projekty\\blindpilot"
    assert convert("/mnt/c/Users/someone") == "C:\\Users\\someone"
    assert convert("/mnt/d") == "D:\\"
    assert convert("/mnt/d/") == "D:\\"
    # Inside the distribution's own filesystem there is no drive-letter form,
    # so it comes back unchanged for the caller to reject.
    assert convert("/home/ubuntu/projects") == "/home/ubuntu/projects"
    assert convert("") == ""
    # And a round trip has to land where it started.
    assert convert(hermes_backend.windows_path_to_wsl(r"D:\a\b")) == "D:\\a\\b"


def test_the_gateway_in_wsl_is_started_as_a_module_in_the_chosen_folder(monkeypatch):
    """Hermes has no CLI subcommand for this protocol - measured, not assumed.

    The working directory goes to WSL rather than to Popen, which only
    understands Windows paths.
    """
    monkeypatch.setattr(hermes_backend.platform, "system", lambda: "Windows")
    monkeypatch.setattr(hermes_backend.shutil, "which", lambda name: r"C:\Windows\wsl.exe")
    monkeypatch.setattr(hermes_backend, "hermes_python", lambda: None)
    monkeypatch.setattr(hermes_backend, "hermes_source_root", lambda: None)
    monkeypatch.setattr(hermes_backend, "wsl_hermes_python", lambda: "/opt/h/venv/bin/python3")
    started = {}

    class _FakeProc:
        stdout = None
        stderr = None
        stdin = None

        def poll(self):
            return None

    def _fake_popen(command, **kwargs):
        started["command"] = command
        started["cwd"] = kwargs.get("cwd")
        return _FakeProc()

    monkeypatch.setattr(hermes_backend.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(hermes_backend.threading, "Thread", _NullThread)

    transport = hermes_backend.StdioTransport(r"D:\work\project")
    transport.start()

    command = started["command"]
    assert command[0].endswith("wsl.exe")
    assert "--cd" in command
    assert command[command.index("--cd") + 1] == "/mnt/d/work/project"
    assert command[-2:] == ["-m", hermes_backend.HERMES_GATEWAY_MODULE]
    # Popen must not be handed the Windows path as a working directory.
    assert started["cwd"] is None


def test_a_local_hermes_is_preferred_over_one_in_wsl(monkeypatch):
    """Where Hermes is installed on Windows itself, that is the one to run."""
    monkeypatch.setattr(
        hermes_backend, "hermes_python", lambda: r"C:\hermes\venv\Scripts\python.exe"
    )
    monkeypatch.setattr(hermes_backend, "hermes_source_root", lambda: Path(r"C:\hermes"))

    def _must_not_run():  # pragma: no cover - asserts it is not consulted
        raise AssertionError("WSL must not be probed when Hermes is local")

    monkeypatch.setattr(hermes_backend, "wsl_hermes_python", _must_not_run)
    started = {}

    class _FakeProc:
        stdout = None
        stderr = None
        stdin = None

        def poll(self):
            return None

    monkeypatch.setattr(
        hermes_backend.subprocess,
        "Popen",
        lambda command, **kwargs: (
            started.update(command=command, cwd=kwargs.get("cwd")),
            _FakeProc(),
        )[1],
    )
    monkeypatch.setattr(hermes_backend.threading, "Thread", _NullThread)

    transport = hermes_backend.StdioTransport(r"D:\work")
    transport.start()

    assert started["command"][0].endswith("python.exe")
    assert started["cwd"] == r"D:\work"


def test_wsl_is_not_consulted_on_other_systems(monkeypatch):
    """A Linux or macOS machine has no wsl.exe and must not look for one."""
    monkeypatch.setattr(hermes_backend.platform, "system", lambda: "Linux")
    _reset_wsl_cache(monkeypatch)

    assert hermes_backend.wsl_exe() is None
    assert hermes_backend.wsl_hermes_python() is None
    assert hermes_backend.wsl_hermes_available() is False


# -- discovery ------------------------------------------------------------


def test_the_model_catalog_qualifies_every_model_with_its_provider():
    """Hermes groups models under providers and repeats names across them.

    The row is NOT joined with a colon, even though that is the form Hermes'
    own /model command documents: it only reads a colon prefix as a provider
    when the left side is a provider it ships with, so a user-defined entry
    came back as part of the model name. The two halves are split apart again
    before the turn is sent (see test_hermes_model_selection.py).
    """
    sep = hermes_backend.MODEL_ROW_SEPARATOR
    models, current = hermes_backend._model_rows(
        {
            "providers": [
                {"slug": "openai", "models": ["gpt-5", "gpt-5-mini"], "is_current": False},
                {"slug": "anthropic", "models": ["gpt-5"], "is_current": True},
            ]
        }
    )

    assert models == [
        f"openai{sep}gpt-5",
        f"openai{sep}gpt-5-mini",
        f"anthropic{sep}gpt-5",
    ]
    assert current == f"anthropic{sep}gpt-5"


def test_providers_without_credentials_are_left_out_of_the_picker():
    """Offering one would fail at the first turn, after the user chose it."""
    models, _current = hermes_backend._model_rows(
        {
            "providers": [
                {"slug": "ready", "models": ["a"], "authenticated": True},
                {"slug": "nope", "models": ["b"], "authenticated": False},
            ]
        }
    )

    assert models == [f"ready{hermes_backend.MODEL_ROW_SEPARATOR}a"]


def test_a_malformed_catalog_produces_no_rows_rather_than_raising():
    assert hermes_backend._model_rows({}) == ([], "")
    assert hermes_backend._model_rows({"providers": "nonsense"}) == ([], "")
    assert hermes_backend._model_rows({"providers": [None, {"models": ["x"]}]}) == ([], "")


def test_the_picker_reports_a_missing_hermes_instead_of_waiting(monkeypatch):
    monkeypatch.setattr(hermes_backend, "hermes_installed", lambda: False)
    models, efforts, current, effort, error = hermes_backend.hermes_model_options()

    assert (models, efforts, current, effort) == ([], [], "", "")
    assert "not found" in error


def test_hermes_is_only_usable_when_its_python_environment_is_present(monkeypatch):
    """A launcher alone cannot run the gateway, which is a module in the venv.

    On Windows there is a second place to look -- a Hermes inside WSL -- so
    both have to come up empty for Hermes to count as unusable.
    """
    monkeypatch.setattr(hermes_backend, "hermes_python", lambda: None)
    monkeypatch.setattr(hermes_backend, "wsl_hermes_available", lambda: False)
    assert hermes_backend.hermes_installed() is False
    monkeypatch.setattr(hermes_backend, "hermes_python", lambda: "/somewhere/python")
    assert hermes_backend.hermes_installed() is True


def test_a_configured_model_counts_as_authenticated(monkeypatch):
    """``hermes status`` exits 0 even when nothing is set up.

    So the check has to read the output. Trusting the exit code reported an
    unconfigured Hermes as ready, and the failure only showed up mid-turn.
    """
    monkeypatch.setattr(hermes_backend, "find_hermes_cli", lambda: "/bin/hermes")

    def _status(output: str, code: int = 0):
        def _run(*_args, **_kwargs):
            return type("Proc", (), {"returncode": code, "stdout": output, "stderr": ""})()

        return _run

    monkeypatch.setattr(hermes_backend.subprocess, "run", _status("  Model:  claude-sonnet-4\n"))
    assert hermes_backend.hermes_auth_ok() is True

    monkeypatch.setattr(hermes_backend.subprocess, "run", _status("  Model:  (not set)\n"))
    assert hermes_backend.hermes_auth_ok() is False

    monkeypatch.setattr(hermes_backend.subprocess, "run", _status("no model line here\n"))
    assert hermes_backend.hermes_auth_ok() is False


def test_a_missing_hermes_is_reported_rather_than_crashing(monkeypatch):
    failed = []
    callbacks = _callbacks()
    callbacks["on_failed"] = failed.append
    monkeypatch.setattr(hermes_worker, "hermes_installed", lambda: False)
    worker = HermesWorker("hi", None, ".", "default", **callbacks)

    assert worker._open_transport() is False
    assert failed and "not installed" in failed[0]


def test_a_password_buys_a_ticket_because_tickets_expire_in_thirty_seconds(monkeypatch):
    """Any non-loopback bind makes Hermes require a real login - measured.

    Its WebSocket upgrade then accepts only a single-use ticket with a 30s life,
    so a ticket cannot be pasted into a settings field: it expires while it is
    being typed. The password is stored and the ticket minted per connection.
    """
    minted = []
    monkeypatch.setattr(
        hermes_backend,
        "mint_ws_ticket",
        lambda url, user, secret: minted.append((url, user, secret)) or "fresh-ticket",
    )
    opened = {}

    class _FakeSocket:
        def recv(self):
            # The transport reads continuously to answer the server's keepalive
            # pings, so the fake has to be readable. Reporting the connection as
            # finished ends that thread instead of spinning in the test.
            raise OSError("closed")

        def close(self):
            return None

    def _fake_create(url, timeout=None, **kwargs):
        opened["url"] = url
        opened["kwargs"] = kwargs
        return _FakeSocket()

    monkeypatch.setitem(
        sys.modules, "websocket", types.SimpleNamespace(create_connection=_fake_create)
    )

    transport = hermes_backend.WebSocketTransport(
        "ws://box:9119/api/ws", "secret-pass", "password", "someone"
    )
    transport.start()

    assert minted == [("ws://box:9119/api/ws", "someone", "secret-pass")]
    # The minted ticket has to go out as ?ticket=. Sending it as ?token= is how
    # a gated Hermes rejected every connection: the legacy token parameter is
    # unconditionally refused once the auth gate engages.
    assert opened["url"] == "ws://box:9119/api/ws?ticket=fresh-ticket"
    assert "secret-pass" not in opened["url"]


def test_the_settings_credentials_are_not_the_wire_credentials():
    """Two different lists, and confusing them broke every gated connection.

    The settings can hold a password; the WebSocket upgrade never accepts one.
    Validating the query parameter against the settings list silently turned a
    ticket into ?token=.
    """
    assert "password" in hermes_backend.REMOTE_CREDENTIALS
    assert "password" not in hermes_backend.WS_QUERY_CREDENTIALS
    assert "ticket" in hermes_backend.WS_QUERY_CREDENTIALS

    url = hermes_backend._authenticated_ws_url("ws://box/api/ws", "value", "password")
    assert "password=" not in url
    assert url.endswith("token=value")


def test_a_login_that_is_refused_says_so_without_repeating_the_password(monkeypatch):
    """The message is read aloud, so it must diagnose without disclosing."""
    import email.message
    import urllib.error
    import urllib.request

    class _Opener:
        def open(self, request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 401, "Unauthorized", email.message.Message(), None
            )

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: _Opener())

    try:
        hermes_backend.mint_ws_ticket("ws://box:9119/api/ws", "someone", "hunter2")
    except OSError as exc:
        message = str(exc)
    else:  # pragma: no cover
        raise AssertionError("a refused login has to raise")

    assert "rejected that username and password" in message
    assert "hunter2" not in message


# -- remote mode ----------------------------------------------------------


def test_the_gateway_url_is_built_from_a_host_the_user_types():
    """Asking for a full ws:// URL with the right path invites getting it wrong."""
    assert hermes_backend.remote_ws_url("garfield") == "ws://garfield:9119/api/ws"
    assert hermes_backend.remote_ws_url("100.64.0.5", 9223) == "ws://100.64.0.5:9223/api/ws"
    # A port typed into the host field wins over the separate field.
    assert hermes_backend.remote_ws_url("garfield:9300", 9119) == "ws://garfield:9300/api/ws"
    # And a pasted URL is tolerated rather than mangled.
    assert hermes_backend.remote_ws_url("ws://box:9119/api/ws") == "ws://box:9119/api/ws"
    assert hermes_backend.remote_ws_url("https://box") == "wss://box:9119/api/ws"


def test_the_credential_travels_in_the_query_string_not_a_header():
    """Hermes' WebSocket upgrade reads its credential from the URL.

    A header alone is answered with 403, which was the first version's bug.
    """
    url = hermes_backend._authenticated_ws_url("ws://box:9119/api/ws", "s3cret", "token")
    assert url == "ws://box:9119/api/ws?token=s3cret"

    # A server reachable from outside this machine wants a minted ticket.
    ticket = hermes_backend._authenticated_ws_url("ws://box:9119/api/ws", "abc", "ticket")
    assert ticket == "ws://box:9119/api/ws?ticket=abc"

    # An unknown credential name falls back rather than sending nonsense.
    odd = hermes_backend._authenticated_ws_url("ws://box:9119/api/ws", "abc", "nonsense")
    assert odd == "ws://box:9119/api/ws?token=abc"

    # A credential with URL-significant characters must survive intact.
    quoted = hermes_backend._authenticated_ws_url("ws://box/api/ws", "a b&c=d", "token")
    assert "a%20b%26c%3Dd" in quoted


def test_no_credential_means_no_query_string():
    assert hermes_backend._authenticated_ws_url("ws://box/api/ws", "", "token") == (
        "ws://box/api/ws"
    )


def test_the_address_shown_to_the_user_never_carries_the_key():
    """A token read aloud by a screen reader is a token read out to the room."""
    assert hermes_backend._redacted_ws_url("ws://box/api/ws?token=s3cret") == "ws://box/api/ws"


def test_connection_failures_say_what_to_do_about_them():
    """ "Could not connect" is useless to someone who cannot glance at a log."""
    url = "ws://box:9119/api/ws"

    rejected = hermes_backend._remote_failure_message(url, OSError("Handshake status 403"))
    assert "key" in rejected.lower()

    refused = hermes_backend._remote_failure_message(url, OSError("[Errno 111] Connection refused"))
    assert "hermes serve" in refused

    timeout = hermes_backend._remote_failure_message(url, OSError("timed out"))
    assert "hermes serve" in timeout and "reachable" in timeout

    unknown = hermes_backend._remote_failure_message(url, OSError("getaddrinfo failed"))
    assert "host name" in unknown

    # Every message names the address, and none of them leaks a credential.
    for message in (rejected, refused, timeout, unknown):
        assert url in message


def test_a_missing_websocket_library_is_reported_as_a_fixable_thing(monkeypatch):
    """The local backend needs no such library, so this must not be fatal.

    It is an optional dependency; the message has to name the package.
    """
    import builtins

    real_import = builtins.__import__

    def _no_websocket(name, *args, **kwargs):
        if name == "websocket":
            raise ImportError("no module named websocket")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_websocket)
    transport = hermes_backend.WebSocketTransport("ws://box/api/ws", "t")
    try:
        transport.start()
    except OSError as exc:
        assert "websocket-client" in str(exc)
    else:  # pragma: no cover - the import must fail here
        raise AssertionError("expected the missing library to be reported")


def test_a_remote_url_selects_the_network_transport(monkeypatch):
    """The remote path is the whole point of using this protocol."""
    created = {}

    class _Fake:
        def __init__(self, url, token="", credential="token", username=""):
            created["url"] = url
            created["token"] = token
            created["credential"] = credential
            created["username"] = username

        def start(self):
            return None

    monkeypatch.setattr(hermes_worker, "WebSocketTransport", _Fake)
    callbacks = _callbacks()
    worker = HermesWorker(
        "hi",
        None,
        ".",
        "default",
        remote_url="ws://192.168.1.10:9119/api/ws",
        remote_token="secret",
        **callbacks,
    )

    assert worker._open_transport() is True
    assert created == {
        "url": "ws://192.168.1.10:9119/api/ws",
        "token": "secret",
        "credential": "token",
        "username": "",
    }


def test_an_unreachable_remote_hermes_says_where_it_tried(monkeypatch):
    """ "Could not connect" without an address is useless to a blind user."""

    class _Fake:
        def __init__(self, url, token="", credential="token", username=""):
            self._url = url

        def start(self):
            raise OSError(f"Could not reach Hermes at {self._url}: refused")

    monkeypatch.setattr(hermes_worker, "WebSocketTransport", _Fake)
    failed = []
    callbacks = _callbacks()
    callbacks["on_failed"] = failed.append
    worker = HermesWorker(
        "hi", None, ".", "default", remote_url="ws://nope:9119/api/ws", **callbacks
    )

    assert worker._open_transport() is False
    assert "ws://nope:9119/api/ws" in failed[0]


# -- transport framing ----------------------------------------------------


def test_frames_are_newline_delimited_json(tmp_path):
    """Hermes' gateway reads one JSON object per line.

    Verified against the real gateway; this keeps a future refactor from
    quietly switching to Content-Length framing, which it does not speak.
    """
    transport = hermes_backend.StdioTransport(str(tmp_path))
    written: list[str] = []

    class _Stdin:
        def write(self, data):
            written.append(data)

        def flush(self):
            return None

    transport._proc = type("Proc", (), {"stdin": _Stdin(), "poll": lambda self: None})()
    assert transport.send({"method": "ping", "params": {}}) is True
    assert written[0].endswith("\n")
    assert json.loads(written[0]) == {"method": "ping", "params": {}}


def test_stderr_noise_is_never_treated_as_protocol(tmp_path):
    """Hermes warns about SQLite and MCP servers on stderr while working fine.

    Those lines only ever explain an exit that already happened.
    """
    transport = hermes_backend.StdioTransport(str(tmp_path))
    transport._stderr.extend(["state.db: linked SQLite is vulnerable", "browser tools missing"])
    detail = transport.failure_detail()

    assert "SQLite" in detail
    # And with nothing to report, the message still says something usable.
    empty = hermes_backend.StdioTransport(str(tmp_path))
    assert empty.failure_detail()


def test_the_reader_thread_hands_frames_over_in_order(tmp_path):
    transport = hermes_backend.StdioTransport(str(tmp_path))
    with transport._frames_ready:
        transport._frames.extend([{"n": 1}, {"n": 2}])
        transport._frames_ready.notify_all()

    assert transport.receive(0.1) == {"n": 1}
    assert transport.receive(0.1) == {"n": 2}
    assert transport.receive(0.01) is None


def test_the_worker_never_blocks_forever_on_a_silent_peer():
    """A dropped network link goes quiet without closing, so reads time out."""
    worker = _worker()
    worker._transport = _FakeTransport([])
    worker._request_id = 100
    done = threading.Event()

    def _run():
        worker._await_response(999, 0.2)
        done.set()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    assert done.wait(5) is True


# -- streaming the answer as it is written --------------------------------


def test_a_finished_sentence_is_released_while_the_turn_is_still_running():
    """The listener hears the answer as it is written, not after it ends.

    Hermes streams an answer in fragments of a few characters. Delivered as they
    arrive they would read as torn words, so a fragment is held until it
    finishes a sentence -- but once it does, it goes out immediately rather than
    waiting for the turn.
    """
    rows = []
    worker = _worker(on_activity=lambda kind, text: rows.append((kind, text)))

    worker._handle_event(_event("message.delta", {"text": "The first"}))
    worker._handle_event(_event("message.delta", {"text": " sentence"}))
    # Nothing yet: no sentence has finished, and half a sentence read aloud is
    # what makes a run sound broken.
    assert rows == []

    worker._handle_event(_event("message.delta", {"text": " is done. And the"}))
    assert rows == [("assistant", "The first sentence is done.")]

    # The unfinished tail is still held back.
    assert [text for _kind, text in rows] == ["The first sentence is done."]


def test_the_last_clause_is_released_when_the_turn_ends():
    """An answer whose final words never finish a sentence is not swallowed."""
    rows = []
    completed = []
    worker = _worker(
        on_activity=lambda kind, text: rows.append((kind, text)),
        on_complete=completed.append,
    )
    worker._handle_event(_event("message.delta", {"text": "Done. No full stop here"}))
    worker._handle_event(_event("message.complete", {"status": "complete"}))

    assert ("assistant", "Done.") in rows
    assert ("assistant", "No full stop here") in rows
    # The final text is still the whole answer, so nothing depends on the rows.
    assert completed == ["Done. No full stop here"]


def test_no_part_of_the_answer_is_streamed_twice():
    """Every character reaches the listener exactly once.

    The window guards against reading a response twice, but the worker must not
    rely on that: re-releasing text already sent is what would make a screen
    reader repeat clauses mid-answer.
    """
    rows = []
    worker = _worker(on_activity=lambda kind, text: rows.append((kind, text)))
    for chunk in ("One. ", "Two. ", "Three. ", "Four"):
        worker._handle_event(_event("message.delta", {"text": chunk}))
    worker._handle_event(_event("message.complete", {"status": "complete"}))

    streamed = [text for kind, text in rows if kind == "assistant"]
    assert " ".join(streamed) == "One. Two. Three. Four"


# -- one connection per conversation -------------------------------------


def test_a_held_connection_is_reused_instead_of_logging_in_again():
    """The second turn of a conversation does not reconnect.

    Reconnecting per turn costs a login, a handshake and a resume, and leaves
    the server reaping the abandoned session moments later.
    """
    from hermes_worker import HeldConnection

    held = HeldConnection()
    transport = _FakeTransport([])
    held.keep(transport, "live-1")

    worker = _worker()
    worker._held = held
    assert worker._open_transport() is True

    assert worker._transport is transport
    assert worker._live_session == "live-1"
    # Reused, so this turn has no session to create or resume.
    assert worker._reused is True
    assert transport.sent == []


def test_a_dead_held_connection_is_replaced_rather_than_reused():
    """A server restart between turns must not break the next message."""
    from hermes_worker import HeldConnection

    held = HeldConnection()
    dead = _FakeTransport([])
    dead.alive = False
    held.keep(dead, "live-1")

    assert held.take() == (None, "")
    # And the caller is told to start from scratch, not handed the corpse.
    worker = _worker()
    worker._held = held
    worker._remote_url = ""
    assert worker._reused is False


def test_a_finished_turn_hands_its_connection_to_the_next_one():
    from hermes_worker import HeldConnection

    held = HeldConnection()
    worker = _worker()
    worker._held = held
    transport = _FakeTransport([])
    worker._live_session = "live-1"
    # The turn itself is not under test here, only what happens to the
    # connection once it ends, so the turn is replaced by the state it leaves.
    worker._clean_end = True
    worker._do_run = lambda: setattr(worker, "_transport", transport)

    worker.run()

    assert transport.closed is False
    assert held.take() == (transport, "live-1")


def test_a_cancelled_turn_does_not_leave_its_connection_behind():
    """The frames answering an interrupt would otherwise reach the next turn."""
    from hermes_worker import HeldConnection

    held = HeldConnection()
    worker = _worker()
    worker._held = held
    transport = _FakeTransport([])
    worker._transport = transport
    worker._live_session = "live-1"
    worker._gateway_session = "live-1"

    worker.cancel()

    assert transport.closed is True
    assert held.take() == (None, "")


def test_a_turn_that_never_opened_a_session_keeps_nothing():
    """A failed connection must not be stored as if it were usable."""
    from hermes_worker import HeldConnection

    held = HeldConnection()
    worker = _worker()
    worker._held = held
    transport = _FakeTransport([])
    # Connected, but no session was ever claimed on it.
    worker._do_run = lambda: setattr(worker, "_transport", transport)

    worker.run()

    assert transport.closed is True
    assert held.take() == (None, "")


def test_without_a_held_connection_nothing_changes():
    """The old behaviour is intact for any caller that has not opted in."""
    worker = _worker()
    transport = _FakeTransport([])
    worker._live_session = "live-1"
    worker._do_run = lambda: setattr(worker, "_transport", transport)

    worker.run()

    assert worker._held is None
    assert transport.closed is True


def test_cancelling_releases_the_connection_and_does_not_hand_it_on():
    """Two things at once, because the mutation test caught them separately.

    An interrupt is answered by frames this worker will not read, so the
    connection must be closed AND the held slot emptied.

    The slot is inspected directly rather than through ``take()``: a closed
    transport reports itself unusable, so ``take()`` returns nothing either way
    and cannot tell a released slot from one still holding a corpse. That is
    the difference a cancelled turn depends on -- a connection left in the slot
    is one the next turn tries to adopt.
    """
    from hermes_worker import HeldConnection

    held = HeldConnection()
    transport = _FakeTransport([])
    held.keep(transport, "live-1")

    worker = _worker()
    worker._held = held
    worker._transport = transport
    worker._live_session = "live-1"
    worker._gateway_session = "live-1"

    worker.cancel()

    assert transport.closed is True
    # The property: nothing is left in the slot at all.
    assert held._transport is None
    assert held._live_session == ""

    # And the turn that ends after the cancellation must not put it back.
    worker.run()
    assert held._transport is None


def test_a_reused_connection_does_not_create_a_second_session():
    """The turn that opened the connection already holds the conversation.

    Creating or resuming again would start a second conversation on a
    connection already bound to one, so the answer would land somewhere the
    user is not reading.
    """
    from hermes_worker import HeldConnection

    held = HeldConnection()
    transport = _FakeTransport([_event("message.complete", {"status": "complete", "text": "hi"})])
    held.keep(transport, "live-1")

    worker = _worker()
    worker._held = held
    worker._do_run()

    methods = [message.get("method") for message in transport.sent]
    assert "session.create" not in methods
    assert "session.resume" not in methods
    # It went straight to the work.
    assert "prompt.submit" in methods


# -- the reply wait, the turn's end, and the held connection --------------


def test_a_turn_that_ends_before_the_reply_reports_one_terminal_callback():
    """A turn whose end arrives before the awaited reply used to end twice.

    The ending event went to _handle_event, whose "turn over" answer was
    dropped, so the worker reported the completion and then a failure when
    the reply never came. Exactly one terminal callback is the property.
    """
    from hermes_worker import HeldConnection

    events: list[tuple[str, str]] = []
    held = HeldConnection()
    transport = _FakeTransport([_event("message.complete", {"status": "complete", "text": "hi"})])
    held.keep(transport, "live-1")
    worker = _worker(
        on_complete=lambda text: events.append(("complete", text)),
        on_failed=lambda text: events.append(("failed", text)),
    )
    worker._held = held

    worker._do_run()

    assert events == [("complete", "hi")]


def test_a_reply_wait_runs_out_by_the_clock_not_by_empty_reads(monkeypatch):
    """A peer that streams frames but never answers must still time out."""
    clock = {"t": 0.0}
    monkeypatch.setattr(hermes_worker, "_now", lambda: clock["t"])

    class _Streaming(_FakeTransport):
        def __init__(self) -> None:
            super().__init__([])
            self.reads = 0

        def receive(self, timeout: float) -> dict | None:  # noqa: ARG002 - interface
            self.reads += 1
            clock["t"] += 0.1
            if self.reads > 50:
                # A bound so a failing run ends instead of hanging the suite.
                self.alive = False
                return None
            return _event("message.delta", {"text": "word "})

    worker = _worker()
    transport = _Streaming()
    worker._transport = transport

    assert worker._await_response(999, 0.2) is None
    assert transport.reads < 10, f"{transport.reads} reads for a 0.2 s wait"


def test_an_error_event_drops_the_held_connection():
    """The server-side turn may still be running after an error event.

    Its late frames would land in the next worker, so the connection is only
    handed on when the turn ended cleanly.
    """
    from hermes_worker import HeldConnection

    held = HeldConnection()
    transport = _FakeTransport(
        [
            {"jsonrpc": "2.0", "id": 101, "result": {}},
            _event("error", {"message": "provider exploded"}),
        ]
    )
    held.keep(transport, "live-1")
    failed: list[str] = []
    worker = _worker(on_failed=failed.append)
    worker._held = held

    worker.run()

    assert failed == ["provider exploded"]
    assert held.take() == (None, "")
    assert transport.closed is True


def test_a_refused_prompt_drops_the_held_connection():
    from hermes_worker import HeldConnection

    held = HeldConnection()
    transport = _FakeTransport([{"jsonrpc": "2.0", "id": 101, "error": {"message": "busy"}}])
    held.keep(transport, "live-1")
    worker = _worker()
    worker._held = held

    worker.run()

    assert held.take() == (None, "")


def test_a_closed_stdio_transport_still_waits_on_receive(tmp_path):
    """A caller polling a dead pipe must not spin at full speed.

    receive used to skip its wait once the stream had closed, so every loop
    that did not check connected() on each None ran flat out.
    """
    transport = hermes_backend.StdioTransport(str(tmp_path))
    with transport._frames_ready:
        transport._closed = True
    started = time.monotonic()
    for _ in range(10):
        assert transport.receive(0.05) is None
    assert time.monotonic() - started >= 0.4


def test_a_bracketed_ipv6_host_keeps_its_one_port():
    assert hermes_backend.remote_ws_url("[::1]:9300") == "ws://[::1]:9300/api/ws"
    assert hermes_backend.remote_ws_url("[::1]") == "ws://[::1]:9119/api/ws"


def test_a_secure_remote_connection_uses_the_packaged_trust_store(monkeypatch):
    """The frozen macOS build ships an empty OpenSSL store, so wss failed there."""
    from certificates import certificate_context

    opened = {}

    class _FakeSocket:
        def recv(self):
            raise OSError("closed")

        def close(self):
            return None

    def _fake_create(url, timeout=None, **kwargs):
        opened["kwargs"] = kwargs
        return _FakeSocket()

    monkeypatch.setitem(
        sys.modules, "websocket", types.SimpleNamespace(create_connection=_fake_create)
    )
    transport = hermes_backend.WebSocketTransport("wss://box:9119/api/ws", "k", "token", "")
    transport.start()
    transport.close()

    assert opened["kwargs"]["sslopt"]["context"] is certificate_context()


def test_the_password_login_uses_the_packaged_trust_store(monkeypatch):
    import urllib.error
    import urllib.request

    from certificates import certificate_context

    handlers: list = []

    class _Opener:
        def open(self, request, timeout=None):
            raise urllib.error.URLError("no network in this test")

    def _build(*given):
        handlers.extend(given)
        return _Opener()

    monkeypatch.setattr(urllib.request, "build_opener", _build)
    raised = False
    try:
        hermes_backend.mint_ws_ticket("wss://box:9119/api/ws", "someone", "pw")
    except OSError:
        raised = True

    assert raised
    contexts = [h._context for h in handlers if isinstance(h, urllib.request.HTTPSHandler)]
    assert contexts == [certificate_context()]


def test_the_wsl_history_probes_stay_off_the_screen(monkeypatch):
    """Every WSL child of the windowed build needs CREATE_NO_WINDOW."""
    _reset_wsl_cache(monkeypatch)
    monkeypatch.setattr(hermes_backend, "_WSL_STATE_DB", None, raising=False)
    monkeypatch.setattr(hermes_backend, "_WSL_STATE_DB_CHECKED", False, raising=False)
    monkeypatch.setattr(hermes_backend.platform, "system", lambda: "Windows")
    monkeypatch.setattr(hermes_backend, "wsl_exe", lambda: "wsl.exe")
    seen: list[dict] = []

    def _run(command, **kwargs):
        seen.append(kwargs)
        return _completed("/home/u/.hermes/state.db" if "state.db" in command[-1] else "[]")

    monkeypatch.setattr(hermes_backend.subprocess, "run", _run)

    assert hermes_backend.wsl_sqlite_query("select 1") == []
    assert len(seen) == 2
    assert all(kwargs.get("creationflags") == 0x08000000 for kwargs in seen)
