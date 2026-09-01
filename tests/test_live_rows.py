"""Unit tests for the live-activity rows: your own message, thinking, tool
steps, and tool results.

Run from the project root:

    python -m pytest tests/ -q
    # or, with no pytest installed:
    python tests/test_live_rows.py
"""

from __future__ import annotations

from collections import deque
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_reader import _result_label, _tool_result_text, _tool_use_label  # noqa: E402
from markdown_rows import Row, reassemble, reassemble_all  # noqa: E402


def test_live_narration_is_enabled_for_a_fresh_configuration(monkeypatch):
    import claude_reader

    monkeypatch.setattr(claude_reader, "_load_config", lambda: {})
    settings = claude_reader._Settings()

    assert settings.live_rows is True
    assert settings.speak_live is True


def test_completed_answer_is_narrated_when_no_assistant_activity_was_spoken(
    monkeypatch,
):
    import claude_reader

    spoken = []
    panel = type(
        "PanelStub",
        (),
        {
            "_assistant_narrated_this_turn": False,
            "_session_backend": claude_reader.BACKEND_FREEBUFF,
            "_say": lambda self, text: spoken.append(text) or True,
        },
    )()
    monkeypatch.setattr(claude_reader.SETTINGS, "speak_live", True)

    claude_reader.SessionPanel._narrate_completed_response(panel, "Finished cleanly.")

    assert spoken == ["FreeBuff. Finished cleanly."]
    assert panel._assistant_narrated_this_turn is True


# ----- Tool step narration -----
def test_read_says_the_file_name_only():
    label = _tool_use_label("Read", {"file_path": "/home/me/project/claude_reader.py"})
    assert label == "Reading claude_reader.py"


def test_bash_says_the_command():
    assert _tool_use_label("Bash", {"command": "pytest -q"}) == "Running: pytest -q"


def test_search_says_the_pattern():
    assert _tool_use_label("Glob", {"pattern": "*.py"}) == "Searching for *.py"


def test_unknown_tool_falls_back_to_its_name():
    assert _tool_use_label("Frobnicate", {}) == "Using Frobnicate"


def test_missing_input_never_produces_a_dangling_label():
    # A tool call with no usable parameters still reads as a whole sentence.
    for name in ("Read", "Write", "Edit", "Bash", "Grep", "WebFetch"):
        label = _tool_use_label(name, {})
        assert label and not label.endswith(" ")


def test_multiline_command_is_flattened_for_speech():
    label = _tool_use_label("Bash", {"command": "git add -A\ngit commit -m x"})
    assert "\n" not in label


# ----- Tool results -----
def test_result_text_from_plain_string():
    assert _tool_result_text("  hello\n") == "hello"


def test_result_text_from_typed_blocks_keeps_text_and_notes_images():
    content = [
        {"type": "text", "text": "line one"},
        {"type": "image", "source": {}},
    ]
    assert _tool_result_text(content) == "line one\n[image]"


def test_result_text_ignores_unexpected_shapes():
    assert _tool_result_text(None) == ""
    assert _tool_result_text([{"type": "text"}, "junk"]) == ""


def test_result_label_previews_the_first_line_and_truncates():
    text = "first line\nsecond line"
    assert _result_label(text) == "Result: first line"
    long = "x" * 300
    assert len(_result_label(long)) == len("Result: ") + 100


# ----- Copy whole response -----
def _live_rows():
    return [
        Row(kind="you", label="You: do it", payload="do it", response_number=1),
        Row(kind="header", label="Response 1", payload="Done.", response_number=1),
        Row(kind="thinking", label="Thinking: hmm", payload="hmm", response_number=1),
        Row(kind="tool", label="Reading a.py", payload="Reading a.py", response_number=1),
        Row(kind="result", label="Result: x", payload="x = 1", response_number=1),
        Row(kind="prose", label="Done.", payload="Done.", response_number=1),
    ]


def test_reassemble_copies_every_row_of_the_response_in_list_order():
    assert reassemble(_live_rows(), 1) == (
        "You: do it\n\nThinking: hmm\n\nReading a.py\n\nResult: x = 1\n\nDone."
    )


def test_reassemble_falls_back_to_the_header_when_there_are_no_other_rows():
    # Mid-stream a response can be nothing but its header.
    rows = [Row(kind="header", label="Response 1", payload="Full answer.", response_number=1)]
    assert reassemble(rows, 1) == "Full answer."


def test_reassemble_ignores_other_responses():
    rows = _live_rows() + [
        Row(kind="header", label="Response 2", payload="Second.", response_number=2),
        Row(kind="prose", label="Second.", payload="Second.", response_number=2),
    ]
    assert reassemble(rows, 2) == "Second."


def test_reassemble_all_covers_the_whole_list_with_response_markers():
    rows = _live_rows() + [
        Row(kind="header", label="Response 2", payload="Second.", response_number=2),
        Row(
            kind="code",
            label="Code, Python, 1 line",
            payload="x = 1",
            response_number=2,
            language="Python",
            lang_token="python",
        ),
    ]
    assert reassemble_all(rows) == (
        "You: do it\n\n"
        "Response 1\n\n"
        "Thinking: hmm\n\n"
        "Reading a.py\n\n"
        "Result: x = 1\n\n"
        "Done.\n\n"
        "Response 2\n\n"
        "```python\nx = 1\n```"
    )


# ----- Stream wiring: which events become live activity -----
class _FakeStdin:
    """Captures what the worker writes into the process."""

    def __init__(self):
        self.written: list[str] = []
        self.closed = False

    def write(self, data):
        if self.closed:
            raise ValueError("write to closed pipe")
        self.written.append(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _FakeProc:
    """Stands in for the Claude Code subprocess, replaying canned stdout."""

    def __init__(self, lines):
        import io

        self.stdin = _FakeStdin()
        self.stdout = iter(lines)
        self.stderr = io.StringIO("")
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0


def _run_worker(events, on_activity=None):
    """Drive ClaudeWorker over `events` and collect its activity callbacks."""
    import json
    import subprocess

    import claude_reader

    lines = [json.dumps(e) + "\n" for e in events]
    activity: list[tuple[str, str]] = []
    completed: list[str] = []
    procs: list[_FakeProc] = []

    def fake_popen(*_a, **_k):
        proc = _FakeProc(lines)
        procs.append(proc)
        return proc

    def record(kind, text):
        activity.append((kind, text))
        if on_activity is not None:
            on_activity(procs[0] if procs else None, kind, text)

    real_popen, real_find = subprocess.Popen, claude_reader._find_claude
    subprocess.Popen = fake_popen  # type: ignore[assignment]
    claude_reader._find_claude = lambda: "claude"  # type: ignore[assignment]
    try:
        worker = claude_reader.ClaudeWorker(
            "hi",
            None,
            os.getcwd(),
            "default",
            on_session=lambda _sid: None,
            on_started=lambda: None,
            on_activity=record,
            on_complete=completed.append,
            on_failed=lambda msg: completed.append("FAILED: " + msg),
            on_done=lambda: None,
        )
        worker.run()
    finally:
        subprocess.Popen = real_popen  # type: ignore[assignment]
        claude_reader._find_claude = real_find  # type: ignore[assignment]
    return activity, completed, procs[0]


def test_stream_emits_thinking_tool_result_and_text_in_order():
    activity, completed, proc = _run_worker(
        [
            {"type": "system", "subtype": "init", "session_id": "abc"},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "I should look at the file."},
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {"file_path": "a.py"},
                        },
                    ]
                },
            },
            {
                "type": "user",
                "message": {"content": [{"type": "tool_result", "content": "x = 1"}]},
            },
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "It sets x."}]},
            },
            {"type": "result", "subtype": "success"},
        ]
    )
    assert activity == [
        ("thinking", "I should look at the file."),
        ("tool", "Reading a.py"),
        ("result", "x = 1"),
        ("assistant", "It sets x."),
    ]
    # Thinking and tool chatter stay out of the final answer text.
    assert completed == ["It sets x."]
    # The prompt went in over stdin as a stream-json user message...
    import json as _json

    first = _json.loads(proc.stdin.written[0])
    assert first["type"] == "user"
    assert first["message"]["content"][0]["text"] == "hi"
    # ...and stdin was closed once the turn's result arrived, so the CLI can exit.
    assert proc.stdin.closed


def test_redacted_thinking_still_announces_something():
    activity, _, _ = _run_worker(
        [
            {
                "type": "assistant",
                "message": {"content": [{"type": "redacted_thinking", "data": "..."}]},
            },
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "ok"}]},
            },
            {"type": "result", "subtype": "success"},
        ]
    )
    assert activity[0] == ("thinking", "[redacted thinking]")


# ----- Steering a run that is already going -----
def test_steer_writes_a_second_message_into_the_running_process():
    import json as _json

    sent = {}
    running = {}

    def steer_when_the_tool_runs(kind, _text):
        # Mid-run, exactly when the user would hear "Reading a.py" and type.
        if kind == "tool" and "steered" not in sent:
            sent["steered"] = running["worker"].steer("actually, stop")

    events = [
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "a.py"}}]
            },
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "stopped"}]},
        },
        {"type": "result", "subtype": "success"},
    ]

    import claude_reader
    import subprocess

    lines = [_json.dumps(e) + "\n" for e in events]
    procs = []

    def fake_popen(*_a, **_k):
        proc = _FakeProc(lines)
        procs.append(proc)
        return proc

    real_popen, real_find = subprocess.Popen, claude_reader._find_claude
    subprocess.Popen = fake_popen  # type: ignore[assignment]
    claude_reader._find_claude = lambda: "claude"  # type: ignore[assignment]
    try:
        worker = claude_reader.ClaudeWorker(
            "do a thing",
            None,
            os.getcwd(),
            "default",
            on_session=lambda _sid: None,
            on_started=lambda: None,
            on_activity=steer_when_the_tool_runs,
            on_complete=lambda _t: None,
            on_failed=lambda _m: None,
            on_done=lambda: None,
        )
        running["worker"] = worker
        worker.run()
    finally:
        subprocess.Popen = real_popen  # type: ignore[assignment]
        claude_reader._find_claude = real_find  # type: ignore[assignment]

    assert sent.get("steered") is True
    written = [_json.loads(w) for w in procs[0].stdin.written]
    assert [m["message"]["content"][0]["text"] for m in written] == [
        "do a thing",
        "actually, stop",
    ]


def test_steer_is_refused_once_the_run_is_over():
    import claude_reader

    worker = claude_reader.ClaudeWorker(
        "hi",
        None,
        os.getcwd(),
        "default",
        on_session=lambda _s: None,
        on_started=lambda: None,
        on_activity=lambda _k, _t: None,
        on_complete=lambda _t: None,
        on_failed=lambda _m: None,
        on_done=lambda: None,
    )
    # Never started, so there is nothing listening — must refuse, not raise.
    assert worker.steer("too late") is False


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except Exception:
            failed += 1
            print(f"FAIL: {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


# ----- Stopping a run -----
class _Button:
    """Minimal stand-in for the wx buttons the lifecycle handlers touch."""

    def __init__(self) -> None:
        self.enabled = True

    def Enable(self) -> None:
        self.enabled = True

    def Disable(self) -> None:
        self.enabled = False


class _Earcons:
    def __init__(self) -> None:
        self.stopped = 0

    def stop_progress(self) -> None:
        self.stopped += 1

    def play_received(self) -> None:
        pass


def _stub_panel(app, **overrides):
    """A SessionPanel stand-in carrying only the state these handlers use."""
    panel = type("PanelStub", (), {})()
    panel._earcons = _Earcons()
    panel._turns = []
    panel._rows = []
    panel._response_count = 0
    panel._stream_response = None
    panel._streamed_assistant = ""
    panel._stopping = False
    panel._assistant_narrated_this_turn = True
    panel._session_backend = app.BACKEND_FREEBUFF
    panel.announced = []
    panel.status = []
    # The real `_announce` speaks and mirrors to the status bar; a stub that
    # only recorded one of the two would hide which of them a caller used.
    panel._announce = lambda text: (panel.announced.append(text), panel.status.append(text))
    panel._set_status = lambda text: panel.status.append(text)
    panel._refresh_list = lambda: None
    panel._say = lambda _text: False
    # Whether a turn is still going is asked through the real method, so a stub
    # cannot quietly disagree with the window about it.
    panel._worker = None
    panel._run_in_progress = lambda: app.SessionPanel._run_in_progress(panel)
    panel.send_btn = _Button()
    panel.steer_btn = _Button()
    panel.stop_btn = _Button()
    panel._finish_stopped_turn = lambda: app.SessionPanel._finish_stopped_turn(panel)
    for name, value in overrides.items():
        setattr(panel, name, value)
    return panel


def test_stopping_keeps_the_partial_answer_and_is_not_reported_as_an_error():
    import blindpilot_app as app

    panel = _stub_panel(app)
    panel._stopping = True
    panel._turns = [app.Turn(prompt="Do the work")]
    panel._streamed_assistant = "Got partway."
    panel._stream_response = 1
    panel._rows = [app.Row(kind="header", label="Response 1", payload="", response_number=1)]

    # The cancelled backend reports its own interruption; the user asked for it.
    app.SessionPanel._on_failed(panel, "FreeBuff reported that the response was interrupted")
    assert panel.announced == []
    assert panel._turns == [app.Turn(prompt="Do the work")]

    app.SessionPanel._on_worker_finished(panel)

    assert panel.announced == ["Stopped"]
    assert panel._turns[0].response == "Got partway."
    assert panel._rows[0].payload == "Got partway."
    assert panel._stream_response is None
    assert panel._stopping is False
    assert panel.send_btn.enabled is True
    assert panel.steer_btn.enabled is False
    assert panel.stop_btn.enabled is False


def test_stop_without_a_running_task_says_so_and_does_nothing():
    import blindpilot_app as app

    panel = _stub_panel(app, _worker=None)

    app.SessionPanel._on_stop(panel)

    assert panel.announced == ["Error: Nothing is running to stop"]
    assert panel.status == ["Error: Nothing is running to stop"]
    assert panel._stopping is False


def test_finished_answer_that_never_streamed_is_still_added_to_the_list():
    """Streaming is best effort, so the final text is what the list must show."""
    import blindpilot_app as app

    panel = _stub_panel(app)
    panel._turns = [app.Turn(prompt="Say banana")]
    panel._stream_response = 1
    panel._response_count = 1
    panel._streamed_assistant = ""
    panel._rows = [app.Row(kind="header", label="Response 1", payload="", response_number=1)]
    panel._narrate_completed_response = lambda _text: None

    app.SessionPanel._on_response_complete(panel, "banana")

    assert [row.label for row in panel._rows[1:]] == ["FreeBuff: banana"]
    assert panel._rows[0].payload == "banana"


def test_answer_already_streamed_is_not_added_to_the_list_twice():
    import blindpilot_app as app

    panel = _stub_panel(app)
    panel._turns = [app.Turn(prompt="Say banana")]
    panel._stream_response = 1
    panel._response_count = 1
    # Codex joins its final text differently from the pieces it streamed, so
    # only the characters can decide whether it is the same answer.
    panel._streamed_assistant = "One.\n\nTwo."
    panel._rows = [app.Row(kind="header", label="Response 1", payload="", response_number=1)]
    panel._narrate_completed_response = lambda _text: None

    app.SessionPanel._on_response_complete(panel, "One.Two.")

    assert len(panel._rows) == 1


def test_reasoning_is_left_out_of_the_activity_by_default(monkeypatch):
    """Reasoning is the backend talking to itself, and it is not the answer."""
    import blindpilot_app as app

    panel = _stub_panel(app)
    panel._stream_response = 1
    panel._response_count = 1
    panel._begin_stream_response = lambda: 1
    monkeypatch.setattr(app.SETTINGS, "live_rows", True)
    monkeypatch.setattr(app.SETTINGS, "show_thinking", False)

    app.SessionPanel._on_activity(panel, "thinking", "Considering the options")

    assert panel._rows == []
    assert panel.status == []

    monkeypatch.setattr(app.SETTINGS, "show_thinking", True)
    app.SessionPanel._on_activity(panel, "thinking", "Considering the options")

    assert [row.kind for row in panel._rows] == ["thinking"]


def test_down_on_last_response_row_stays_in_responses():
    """The prompt is reachable from responses by Tab, never by Down."""
    import blindpilot_app as app

    class Event:
        skipped = False

        @staticmethod
        def GetKeyCode():
            return app.wx.WXK_DOWN

        @staticmethod
        def CmdDown():
            return False

        def Skip(self):
            self.skipped = True

    class Prompt:
        focused = False

        def SetFocus(self):
            self.focused = True

    panel = type("PanelStub", (), {})()
    panel.prompt = Prompt()
    panel._selected_row = lambda: 2
    panel._row_count = lambda: 3
    event = Event()

    app.SessionPanel._on_list_key(panel, event)

    assert panel.prompt.focused is False
    assert event.skipped is False


def test_worker_activity_uses_bounded_gui_batches(monkeypatch):
    """A chatty long job posts one drain at a time and redraws once per batch."""
    import blindpilot_app as app

    scheduled = []
    monkeypatch.setattr(app.wx, "CallAfter", lambda callback: scheduled.append(callback))

    panel = type("PanelStub", (), {})()
    panel._worker_event_lock = threading.Lock()
    panel._worker_events = deque()
    panel._worker_events_scheduled = False
    panel.processed = []
    panel.refreshes = 0
    panel._on_activity = lambda kind, text, refresh=True: panel.processed.append(
        (kind, text, refresh)
    )
    panel._refresh_list = lambda: setattr(panel, "refreshes", panel.refreshes + 1)
    panel._drain_worker_events = lambda: app.SessionPanel._drain_worker_events(panel)

    total = app._WORKER_EVENT_BATCH_SIZE + 5
    for number in range(total):
        app.SessionPanel._queue_worker_event(panel, "activity", "tool", str(number))

    assert len(scheduled) == 1
    scheduled.pop(0)()
    assert len(panel.processed) == app._WORKER_EVENT_BATCH_SIZE
    assert panel.refreshes == 1
    assert len(scheduled) == 1

    scheduled.pop(0)()
    assert [text for _kind, text, _refresh in panel.processed] == [
        str(number) for number in range(total)
    ]
    assert all(refresh is False for _kind, _text, refresh in panel.processed)
    assert panel.refreshes == 2
    assert panel._worker_events_scheduled is False


def test_stream_refresh_preserves_the_selected_response_row(monkeypatch):
    """New streamed rows must not interrupt NVDA reading an existing row."""
    import blindpilot_app as app

    class Responses:
        def __init__(self):
            self.selection = 1
            self.labels = []

        def GetSelection(self):
            return self.selection

        def Set(self, labels):
            self.labels = list(labels)
            self.selection = app.wx.NOT_FOUND

        def SetSelection(self, index):
            self.selection = index

    old_rows = [
        Row(kind="prose", label="First", payload="First", response_number=1),
        Row(kind="prose", label="Reading this", payload="Reading this", response_number=1),
    ]
    panel = type("PanelStub", (), {})()
    panel._rows = old_rows + [
        Row(kind="tool", label="New output", payload="New output", response_number=1)
    ]
    panel._displayed = list(old_rows)
    panel._search_term = ""
    panel.responses = Responses()
    panel._selected_row = lambda: app.SessionPanel._selected_row(panel)
    panel._select_row = lambda index: app.SessionPanel._select_row(panel, index)
    panel._row_count = lambda: len(panel._displayed)
    monkeypatch.setattr(app.SETTINGS, "text_view", False)

    app.SessionPanel._refresh_list(panel)

    assert panel.responses.labels == ["First", "Reading this", "New output"]
    assert panel.responses.selection == 1


# ----- Compaction and starting over -----
def _compaction_panel(backend, session_id):
    """A panel stub with just the state compaction looks at."""
    import claude_reader

    calls = {"announced": [], "sent": [], "prompt": ""}
    panel = type(
        "PanelStub",
        (),
        {
            "_worker": None,
            # The real one: compaction refuses while a turn is still being
            # applied, and a stub must not be able to disagree about when
            # that is.
            "_run_in_progress": claude_reader.SessionPanel._run_in_progress,
            "_session_id": session_id,
            "_session_backend": backend,
            "selected_backend": lambda self: backend,
            "_announce": lambda self, text: calls["announced"].append(text),
            "_on_send": lambda self, worker_extra=None: calls["sent"].append(worker_extra),
            "prompt": type(
                "PromptStub", (), {"SetValue": lambda self, text: calls.update(prompt=text)}
            )(),
        },
    )()
    return claude_reader.SessionPanel.compact_conversation, panel, calls


def test_compacting_claude_sends_it_as_an_ordinary_message():
    compact, panel, calls = _compaction_panel("claude", "session-1")

    compact(panel)

    assert calls["prompt"] == "/compact"
    # Claude Code needs no extra worker arguments — the message is the command.
    assert calls["sent"] == [{}]


def test_compacting_codex_tells_its_worker_rather_than_typing_a_command():
    compact, panel, calls = _compaction_panel("codex", "thread-1")

    compact(panel)

    assert calls["sent"] == [{"compact": True}]


def test_freebuff_says_plainly_that_it_cannot_compact():
    """Silence here would read as a broken command rather than a missing one."""
    compact, panel, calls = _compaction_panel("freebuff", "chat-1")

    compact(panel)

    assert calls["sent"] == []
    assert calls["announced"] == [
        "Error: FreeBuff cannot compact a conversation. Start a new conversation instead"
    ]


def test_there_is_nothing_to_compact_before_the_first_message():
    compact, panel, calls = _compaction_panel("claude", None)

    compact(panel)

    assert calls["sent"] == []
    assert calls["announced"] == ["Error: There is no conversation to compact yet"]


# ----- AskUserQuestion -----
#
# Claude Code asks a multiple-choice question by asking permission to run its
# AskUserQuestion tool, and takes the answers back as part of that tool's own
# input. These check both halves: that the prompt tool is switched on at all
# (without it the tool is not even offered in headless mode), and that what the
# person chose reaches the CLI in the shape the tool reads.


def _run_worker_with_questions(events, answer, mode="bypassPermissions"):
    """Drive ClaudeWorker over `events`, answering any question it asks."""
    import json
    import subprocess

    import claude_reader

    lines = [json.dumps(event) + "\n" for event in events]
    asked: list[tuple] = []
    activity: list[tuple[str, str]] = []
    procs: list[_FakeProc] = []
    commands: list[list[str]] = []

    def fake_popen(cmd, *_a, **_k):
        commands.append(list(cmd))
        proc = _FakeProc(lines)
        procs.append(proc)
        return proc

    def on_question(questions):
        asked.append(tuple(questions))
        return answer

    real_popen, real_find = subprocess.Popen, claude_reader._find_claude
    subprocess.Popen = fake_popen  # type: ignore[assignment]
    claude_reader._find_claude = lambda: "claude"  # type: ignore[assignment]
    try:
        worker = claude_reader.ClaudeWorker(
            "hi",
            None,
            os.getcwd(),
            mode,
            on_session=lambda _sid: None,
            on_started=lambda: None,
            on_activity=lambda kind, text: activity.append((kind, text)),
            on_complete=lambda _text: None,
            on_failed=lambda _msg: None,
            on_done=lambda: None,
            on_question=on_question,
        )
        worker.run()
    finally:
        subprocess.Popen = real_popen  # type: ignore[assignment]
        claude_reader._find_claude = real_find  # type: ignore[assignment]
    written = [json.loads(line) for line in procs[0].stdin.written]
    return asked, written, activity, commands[0]


ASK_REQUEST = {
    "type": "control_request",
    "request_id": "req-1",
    "request": {
        "subtype": "can_use_tool",
        "tool_name": "AskUserQuestion",
        "input": {
            "questions": [
                {
                    "question": "Tabs or spaces?",
                    "header": "Indent",
                    "multiSelect": False,
                    "options": [
                        {"label": "Tabs", "description": "Tab characters."},
                        {"label": "Spaces", "description": "Space characters."},
                    ],
                }
            ]
        },
        "tool_use_id": "toolu_1",
        "requires_user_interaction": True,
    },
}


def test_headless_claude_is_told_it_can_show_a_permission_prompt():
    _asked, _written, _activity, command = _run_worker_with_questions([], None)

    # Without this the CLI leaves AskUserQuestion out of the tool set entirely,
    # so Claude has no way to ask anything.
    assert "--permission-prompt-tool" in command
    assert command[command.index("--permission-prompt-tool") + 1] == "stdio"


def test_askuserquestion_answers_go_back_as_the_tools_own_input():
    asked, written, activity, _command = _run_worker_with_questions([ASK_REQUEST], [["Spaces"]])

    (questions,) = asked
    assert [question.question for question in questions] == ["Tabs or spaces?"]
    assert [option.label for option in questions[0].options] == ["Tabs", "Spaces"]

    reply = written[-1]
    assert reply["type"] == "control_response"
    assert reply["response"]["subtype"] == "success"
    assert reply["response"]["request_id"] == "req-1"
    assert reply["response"]["response"]["behavior"] == "allow"
    # Claude Code keys the answers by each question's own text, and reads them
    # off the input it was allowed to run with.
    assert reply["response"]["response"]["updatedInput"]["answers"] == {"Tabs or spaces?": "Spaces"}
    # The transcript keeps the question as well as the answer: read back later,
    # a bare "Spaces" says nothing about what it decided.
    assert ("tool", 'You answered "Tabs or spaces?" with "Spaces".') in activity


def test_several_answers_to_one_question_are_joined_the_way_the_tool_reads_them():
    import json

    multi = json.loads(json.dumps(ASK_REQUEST))
    multi["request"]["input"]["questions"][0]["multiSelect"] = True
    _asked, written, _activity, _command = _run_worker_with_questions([multi], [["Tabs", "Spaces"]])

    assert written[-1]["response"]["response"]["updatedInput"]["answers"] == {
        "Tabs or spaces?": "Tabs, Spaces"
    }


def test_a_question_nobody_answered_is_denied_rather_than_left_hanging():
    _asked, written, _activity, _command = _run_worker_with_questions([ASK_REQUEST], None)

    # An unanswered control request holds the turn open for good, which sounds
    # exactly like a model that has stopped thinking.
    assert written[-1]["response"]["response"]["behavior"] == "deny"


def test_every_other_tool_still_answers_to_the_permission_mode():
    request = {
        "type": "control_request",
        "request_id": "req-2",
        "request": {
            "subtype": "can_use_tool",
            "tool_name": "Bash",
            "input": {"command": "rm -rf /"},
            "tool_use_id": "toolu_2",
        },
    }
    asked, written, _activity, _command = _run_worker_with_questions([request], [["yes"]])

    # The prompt tool must not turn BlindPilot into an approval dialog: the
    # permission mode decided this before, and it still does.
    assert asked == []
    assert written[-1]["response"]["response"]["behavior"] == "deny"
