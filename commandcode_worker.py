"""Command Code worker for BlindPilot.

One Command Code turn, driven through its non-interactive mode:

    command-code -p --output-format json [--resume <id>] <prompt on stdin>

``-p`` writes one event object per line -- ``run_start``, ``text_delta``,
``tool_running``, ``tool_completed`` and friends -- and ends with a single
``result`` line carrying the session id and the final text. A turn is one
process rather than a held session, because ``-p`` answers a single query and
exits; the conversation is carried to the next turn by ``--resume``, whose id
comes from the turn that just ran.

The prompt goes in over stdin rather than as an argument: the documented form
is a piped query, and it avoids every quoting question an argument would
raise (a prompt beginning with ``-`` would otherwise be read as a flag).

The event names below are the ones measured at Command Code 1.53.1. An event
this file has never heard of is rendered generically rather than dropped, so a
newer release still says something.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
Based on the original Claude Code Reader application by doubletaponair:
https://github.com/doubletaponair/claude-code-reader
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import json
import subprocess
import threading
from typing import Callable, Optional

from agent_backends import (
    BACKEND_COMMANDCODE,
    AskQuestions,
    end_process_group,
    find_backend_cli,
    no_window_kwargs,
    own_group_kwargs,
    subprocess_env,
)
from hermes_backend import STDERR_KEEP_LINES
from markdown_rows import complete_sentences as _complete_sentences

# The window's permission vocabulary translated to Command Code's. "bypass" has
# no --permission-mode value at all -- it is the launch-only --yolo flag -- so
# it is handled separately below.
_PERMISSION_MODES = {
    "default": "default",
    "acceptEdits": "auto-accept",
    "plan": "plan",
    "auto": "auto-accept",
    "dontAsk": "dont-ask",
}
_BYPASS_MODE = "bypassPermissions"

# Command Code's documented print-mode exit codes, said as something a listener
# can act on rather than a number.
_EXIT_MESSAGES = {
    3: "Command Code is not signed in. Run 'cmd login' in a terminal, then try again.",
    4: "Command Code refused a tool by its permission rules.",
    5: "Command Code is rate limited. Wait a moment, then try again.",
    6: "Command Code could not reach the network.",
    7: "Command Code's server returned an error.",
    8: "Command Code reached its maximum number of turns before finishing.",
    9: "Command Code produced no response.",
    10: "Command Code has insufficient credits for this request.",
    130: "Command Code was interrupted.",
}


def build_command(
    binary: str,
    permission_mode: str,
    model: str = "",
    effort: str = "",
    session_id: Optional[str] = None,
) -> list[str]:
    """The argv for one headless turn. The prompt itself travels on stdin."""
    command = [
        binary,
        "-p",
        "--output-format",
        "json",
        # An automated run: do not stop for taste onboarding, and do not let a
        # background update replace the executable mid-conversation.
        "--skip-onboarding",
        "--no-auto-update",
        # BlindPilot drives a project it was pointed at; the initial trust
        # prompt has nobody to answer it in a windowed run.
        "-t",
    ]
    if permission_mode == _BYPASS_MODE:
        command.append("--yolo")
    else:
        command += ["--permission-mode", _PERMISSION_MODES.get(permission_mode, "default")]
    if model:
        command += ["--model", model]
    if effort:
        command += ["--effort", effort]
    if session_id:
        command += ["--resume", session_id]
    return command


class CommandcodeWorker(threading.Thread):
    """Run one Command Code turn, reporting it through BlindPilot's callbacks."""

    def __init__(
        self,
        prompt: str,
        session_id: Optional[str],
        cwd: str,
        permission_mode: str,
        *,
        model: str = "",
        effort: str = "",
        compact: bool = False,
        on_session: Callable[[str], None],
        on_started: Callable[[], None],
        on_activity: Callable[[str, str], None],
        on_complete: Callable[[str], None],
        on_failed: Callable[[str], None],
        on_done: Callable[[], None],
        on_question: Optional[AskQuestions] = None,
    ) -> None:
        super().__init__(daemon=True)
        self._prompt = prompt
        self._session_id = session_id
        self._cwd = cwd
        self._permission_mode = permission_mode
        self._model = model
        self._effort = effort
        # Accepted for the shared worker signature. Compaction is a command
        # the interactive CLI runs; a headless prompt of "/compact" is treated
        # as text (measured), so this backend does not offer it and this flag
        # is never set by the window.
        self._compact = compact
        self._on_session = on_session
        self._on_started = on_started
        self._on_activity = on_activity
        self._on_complete = on_complete
        self._on_failed = on_failed
        self._on_done = on_done
        self._on_question = on_question

        self._proc: Optional[subprocess.Popen] = None
        self._cancelled = False
        self._failed = False
        self._clean_end = False
        self._completed = False
        self._started_notified = False

        # Named for what it holds, not `_stderr`: threading.Thread keeps its
        # own attribute under that name.
        self._error_lines: list[str] = []
        self._error_lock = threading.Lock()

        self._assistant_parts: list[str] = []
        self._streamed = 0
        self._session_seen = ""
        self._tool_names: dict[str, str] = {}
        self._tool_subjects: dict[str, str] = {}

    # -- public surface the window drives ---------------------------------

    def accepting_input(self) -> bool:
        """Whether a message would join the running turn. It never can: one
        ``-p`` process answers one query, so a second message waits its turn."""
        return False

    def steer(self, text: str) -> bool:
        return False

    def cancel(self) -> None:
        """Stop the turn by ending its process tree.

        A headless turn is a single process with no cancel request to send, so
        the whole tree goes: the launched ``command-code`` shim (npm's is a
        batch/Node launcher) has a Node child, and killing only the launcher
        would leave the child running.
        """
        self._cancelled = True
        proc = self._proc
        if proc is not None:
            end_process_group(proc)

    # -- the turn ----------------------------------------------------------

    def run(self) -> None:
        try:
            self._do_run()
        except Exception as exc:  # noqa: BLE001 - the crash IS the report
            if not self._failed and not self._clean_end:
                self._fail(f"Command Code turn failed: {exc}")
        finally:
            self._close_process()
            self._on_done()

    def _do_run(self) -> None:
        binary = find_backend_cli(BACKEND_COMMANDCODE)
        if not binary:
            self._fail("Command Code is not installed. Run: npm install -g command-code")
            return
        command = build_command(
            binary, self._permission_mode, self._model, self._effort, self._session_id
        )
        try:
            proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                command,
                cwd=self._cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                env=subprocess_env(binary),
                **own_group_kwargs(),
                **no_window_kwargs(),
            )
        except (OSError, ValueError) as exc:
            self._fail(f"Could not start Command Code: {exc}")
            return
        self._proc = proc
        threading.Thread(target=self._read_stderr, args=(proc,), daemon=True).start()
        self._send_prompt(proc)
        self._read_stdout(proc)
        self._finish(proc)
        self._clean_end = True

    def _send_prompt(self, proc: subprocess.Popen) -> None:
        """Write the prompt and close the pipe, so the CLI stops reading stdin."""
        stdin = proc.stdin
        if stdin is None:
            return
        try:
            stdin.write(self._prompt.rstrip("\n") + "\n")
            stdin.flush()
        except (OSError, ValueError):
            pass
        finally:
            try:
                stdin.close()
            except (OSError, ValueError):
                pass

    def _read_stderr(self, proc: subprocess.Popen) -> None:
        stderr = proc.stderr
        if stderr is None:
            return
        for line in stderr:
            text = line.strip()
            if not text:
                continue
            with self._error_lock:
                self._error_lines.append(text)
                del self._error_lines[:-STDERR_KEEP_LINES]

    def _error_tail(self) -> str:
        with self._error_lock:
            return "\n".join(self._error_lines[-6:]).strip()

    def _read_stdout(self, proc: subprocess.Popen) -> None:
        stdout = proc.stdout
        if stdout is None:
            return
        for raw in stdout:
            if self._cancelled:
                break
            line = raw.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except ValueError:
                # Not protocol. It is not a reason to stop reading a stream
                # that is otherwise well-formed.
                continue
            if isinstance(frame, dict):
                self._handle_frame(frame)

    def _handle_frame(self, frame: dict) -> None:
        kind = str(frame.get("type") or "")
        if kind == "result":
            self._handle_result(frame)
            return
        if kind != "event":
            return
        event = frame.get("event")
        if isinstance(event, dict):
            self._handle_event(event)

    def _handle_event(self, event: dict) -> None:
        etype = str(event.get("type") or "")
        if etype == "run_start":
            session = str(event.get("sessionId") or "")
            if session:
                self._remember_session(session)
            self._notify_started()
        elif etype == "text_delta":
            delta = str(event.get("delta") or "")
            if delta:
                # Assistant answer text, held until it completes a sentence so
                # the screen reader never reads a torn word.
                self._assistant_parts.append(delta)
                self._release_streamed()
        elif etype == "thinking_delta":
            delta = str(event.get("delta") or "")
            if delta:
                self._on_activity("thinking", delta)
        elif etype == "tool_queued":
            call_id = str(event.get("toolCallId") or "")
            if call_id:
                self._tool_names[call_id] = str(event.get("toolName") or "tool")
                self._tool_subjects[call_id] = _tool_subject(event.get("input"))
        elif etype == "tool_running":
            self._tool_running(event)
        elif etype == "tool_completed":
            self._tool_completed(event)
        elif etype in ("message_end", "message_update"):
            self._fallback_message_text(event)
        elif etype in (
            "turn_start",
            "message_start",
            "model_request_start",
            "model_request_end",
            "model_trace",
            "thinking_start",
            "thinking_end",
            "turn_end",
            "run_end",
        ):
            # Bookkeeping and lifecycle frames: nothing to say about them that
            # the tool and text events have not already said.
            return
        else:
            self._generic_event(etype, event)

    def _remember_session(self, session: str) -> None:
        if session and session != self._session_seen:
            self._session_seen = session
            self._on_session(session)

    def _notify_started(self) -> None:
        if not self._started_notified:
            self._started_notified = True
            self._on_started()

    def _tool_running(self, event: dict) -> None:
        call_id = str(event.get("toolCallId") or "")
        name = str(event.get("toolName") or self._tool_names.get(call_id, "tool"))
        # The description is often null; the queued frame's input is the part
        # worth hearing either way.
        subject = str(event.get("description") or "").strip() or self._tool_subjects.get(
            call_id, ""
        )
        self._on_activity("tool", f"{name}: {subject}" if subject else name)

    def _tool_completed(self, event: dict) -> None:
        call_id = str(event.get("toolCallId") or "")
        name = str(event.get("toolName") or self._tool_names.get(call_id, "tool"))
        result = _result_text(event.get("result"))
        if result.strip():
            self._on_activity("result", f"{name}: {result.strip()}")

    def _fallback_message_text(self, event: dict) -> None:
        """Use a whole-message text if no streaming deltas arrived.

        Some models or releases answer in one content block rather than a
        stream of deltas; without this, such a turn would end with nothing
        having been said until the final result line.
        """
        if self._assistant_parts:
            return
        text = _content_text(event.get("content"))
        if text.strip():
            self._assistant_parts = [text]
            self._release_all()

    def _generic_event(self, etype: str, event: dict) -> None:
        """Say something for an event kind this file has never heard of.

        A newer Command Code that streams a kind we do not know about should
        still be visible in the transcript rather than silently swallowed.
        """
        detail = (
            str(event.get("description") or "")
            or _content_text(event.get("content"))
            or str(event.get("text") or "")
            or str(event.get("delta") or "")
        ).strip()
        first = detail.splitlines()[0][:120] if detail else ""
        self._on_activity("tool", f"{etype}: {first}" if first else etype)

    def _handle_result(self, frame: dict) -> None:
        subtype = str(frame.get("subtype") or "")
        session = str(frame.get("sessionId") or "")
        if session:
            self._remember_session(session)
        self._release_all()
        if subtype == "error":
            self._fail(self._error_text(frame) or "Command Code reported an error.")
            return
        if subtype == "max_turns":
            self._fail("Command Code reached its maximum number of turns before finishing.")
            return
        final = str(frame.get("finalText") or "").strip()
        text = final or "".join(self._assistant_parts).strip()
        self._completed = True
        self._on_complete(text or "Finished with nothing to say.")

    def _error_text(self, frame: dict) -> str:
        error = frame.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or "").strip()
        return str(error or "").strip()

    def _finish(self, proc: subprocess.Popen) -> None:
        """Report the outcome once the stream has ended."""
        code: Optional[int] = None
        try:
            code = proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            # A pipe that never closes must not hold the turn open.
            end_process_group(proc)
            try:
                code = proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                code = None
        if self._cancelled:
            if not self._failed:
                self._release_all()
                text = "".join(self._assistant_parts).strip()
                self._on_complete(text or "Stopped")
            return
        if self._completed or self._failed:
            return
        message = _EXIT_MESSAGES.get(code)
        if message is None:
            message = "Command Code stopped before the turn completed"
            message += f" (exit code {code})." if code else "."
        detail = self._error_tail()
        if detail:
            message = f"{message} {detail}"
        self._fail(message[:600])

    def _close_process(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            end_process_group(proc)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            close = getattr(stream, "close", None)
            if close is None:
                continue
            try:
                close()
            except (OSError, ValueError):
                pass
        try:
            proc.wait(timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        self._proc = None

    def _fail(self, message: str) -> None:
        if self._failed:
            return
        self._failed = True
        self._on_failed(message)

    # -- streaming helpers -------------------------------------------------

    def _release_streamed(self) -> None:
        text = "".join(self._assistant_parts)
        if len(text) <= self._streamed:
            return
        spoken = _complete_sentences(text[self._streamed :])
        if not spoken:
            return
        self._streamed += len(spoken)
        self._on_activity("assistant", spoken)

    def _release_all(self) -> None:
        text = "".join(self._assistant_parts)
        if len(text) > self._streamed:
            self._on_activity("assistant", text[self._streamed :])
        self._streamed = len(text)


def _content_text(content: object) -> str:
    """The text blocks of a message content array, joined."""
    if not isinstance(content, list):
        return ""
    return "".join(
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _result_text(result: object) -> str:
    """The text of a tool result, which Command Code sends as content blocks."""
    if isinstance(result, list):
        return _content_text(result)
    if isinstance(result, str):
        return result
    return ""


def _tool_subject(payload: object) -> str:
    """The readable part of a tool's input: a command, path or query."""
    if not isinstance(payload, dict):
        return ""
    for key in ("command", "file_path", "path", "pattern", "query", "url", "prompt", "description"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
