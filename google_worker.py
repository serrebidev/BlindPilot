"""Gemini CLI and Antigravity CLI workers for BlindPilot.

One turn is one process, the way Command Code's is: both CLIs answer a single
prompt in their headless mode and exit, and the conversation travels to the
next turn by id.

Gemini CLI (measured at 0.62.0)::

    gemini --output-format stream-json --skip-trust --approval-mode <mode>
           [--model <m>] [--resume <session id>]        <prompt on stdin>

    {"type":"init","session_id":"...","model":"auto"}
    {"type":"message","role":"assistant","content":"Hel","delta":true}
    {"type":"tool_use","tool_name":"read_file","tool_id":"...","parameters":{...}}
    {"type":"tool_result","tool_id":"...","status":"success","output":"..."}
    {"type":"error","severity":"error","message":"..."}
    {"type":"result","status":"success"|"error","error":{...},"stats":{...}}

Antigravity CLI (measured at 1.2.17)::

    agy --input-format stream-json --output-format stream-json
        --disable-slash-commands [--dangerously-skip-permissions | --mode m]
        [--model m] [--effort e] [--conversation <id>]
    stdin: {"event":"user","message":{"content":"<prompt>"}}

    {"event":"init","conversation_id":"...","init":{...}}
    {"event":"step_update","step_update":{"step_index":1,"state":"ACTIVE",
        "step_type":"agent_response","text_delta":"..."}}
    {"event":"result","result":{"conversation_id":"...","status":"SUCCESS",
        "response":"...","error":"..."}}

The prompt travels on stdin for both. agy's plain ``-p "<prompt>"`` waits for a
sign-in that a windowed run cannot give it, where the stream-json input fails
at once with a result naming the problem, so that is the form used.

An event this file does not know is ignored rather than spoken: both CLIs add
bookkeeping events between releases, and the answer and the tool steps are
what a listener needs.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
Based on the original Claude Code Reader application by doubletaponair:
https://github.com/doubletaponair/claude-code-reader
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from typing import Optional

from agent_backends import (
    BACKEND_ANTIGRAVITY,
    BACKEND_GEMINI,
    SUBAGENT_COMPLETED,
    SUBAGENT_RUNNING,
    _tool_use_label,
    _TurnWorker,
    end_process_group,
    find_backend_cli,
    no_window_kwargs,
    own_group_kwargs,
)
from markdown_rows import release_finished, release_remainder

_BYPASS_MODE = "bypassPermissions"
_STDERR_KEEP = 40

# The window's permission vocabulary in Gemini CLI's --approval-mode words.
_GEMINI_APPROVAL = {
    _BYPASS_MODE: "yolo",
    "acceptEdits": "auto_edit",
    "auto": "auto_edit",
    "plan": "plan",
    "default": "default",
    "dontAsk": "default",
}

# And in Antigravity's: bypass is its own flag, the rest a --mode.
_ANTIGRAVITY_MODE = {
    "acceptEdits": "accept-edits",
    "auto": "accept-edits",
    "plan": "plan",
}

# Both CLIs' tool names mapped to Claude Code's, so a step reads "Reading
# a.txt" rather than "view_file". Gemini CLI's come from its bundle at 0.62.0;
# Antigravity's from the tool list its init event reports at 1.2.17.
_CLAUDE_TOOL_NAMES = {
    # Gemini CLI
    "read_file": "Read",
    "read_many_files": "Read",
    "write_file": "Write",
    "replace": "Edit",
    "run_shell_command": "Bash",
    "glob": "Glob",
    "grep_search": "Grep",
    "google_web_search": "WebSearch",
    "web_fetch": "WebFetch",
    "write_todos": "TodoWrite",
    "invoke_agent": "Task",
    # Antigravity CLI
    "view_file": "Read",
    "write_to_file": "Write",
    "replace_file_content": "Edit",
    "multi_replace_file_content": "Edit",
    "sed_file": "Edit",
    "notebook_edit": "NotebookEdit",
    "run_command": "Bash",
    "find_by_name": "Glob",
    "search_web": "WebSearch",
    "read_url_content": "WebFetch",
    "invoke_subagent": "Task",
    "browser_subagent": "Task",
}

# Parameter names, case-insensitively, mapped to the ones the labeller reads.
# Antigravity's tools inherit Windsurf's PascalCase (AbsolutePath, CommandLine).
_PARAM_ALIASES = {
    "file_path": ("file_path", "absolute_path", "absolutepath", "targetfile", "path"),
    "path": ("dir_path", "directorypath", "searchdirectory", "searchpath", "path"),
    "command": ("command", "commandline"),
    "pattern": ("pattern", "query"),
    "url": ("url",),
    "query": ("query",),
    "content": ("content", "codecontent"),
    "old_string": ("old_string", "targetcontent"),
    "new_string": ("new_string", "replacementcontent"),
    "description": ("description", "task", "prompt"),
}


def _labelled_params(params: object) -> dict:
    """The tool input with its keys renamed to the ones the labeller knows."""
    if not isinstance(params, dict):
        return {}
    lowered = {str(key).casefold(): value for key, value in params.items()}
    renamed: dict = {}
    for target, candidates in _PARAM_ALIASES.items():
        for candidate in candidates:
            value = lowered.get(candidate)
            if isinstance(value, str) and value.strip():
                renamed[target] = value
                break
    return renamed


def tool_label(name: str, params: object) -> str:
    """The spoken step for a tool call, phrased the way Claude's are."""
    renamed = _labelled_params(params)
    if name in ("list_directory", "list_dir"):
        folder = os.path.basename(
            str(renamed.get("path") or renamed.get("file_path") or "").rstrip("/\\")
        )
        return f"Listing {folder}" if folder else "Listing a folder"
    return _tool_use_label(_CLAUDE_TOOL_NAMES.get(name, name), renamed)


def _output_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return ""
    return ""


class _GoogleTurnWorker(_TurnWorker):
    """One headless turn of a Google CLI: start, stream, report, end."""

    _install_hint = ""

    def __init__(self, *args, additional_dirs: tuple[str, ...] = (), **kwargs) -> None:
        self._additional_dirs = tuple(additional_dirs or ())
        super().__init__(*args, **kwargs)

    def _setup(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._error_lines: list[str] = []
        self._error_lock = threading.Lock()
        self._stderr_thread: Optional[threading.Thread] = None
        self._parts: list[str] = []
        self._streamed = 0
        self._started_notified = False
        self._session_seen = ""
        self._completed = False

    # -- what the window drives ---------------------------------------------

    def steer(self, text: str) -> bool:
        # One process answers one prompt; a second message waits its turn.
        return False

    def cancel(self) -> None:
        self._cancelled = True
        proc = self._proc
        if proc is not None:
            end_process_group(proc)

    # -- what each CLI supplies ---------------------------------------------

    def _command(self, binary: str) -> list[str]:
        raise NotImplementedError

    def _stdin_text(self) -> str:
        raise NotImplementedError

    def _env(self, binary: str) -> dict[str, str]:
        raise NotImplementedError

    def _handle(self, frame: dict) -> None:
        raise NotImplementedError

    # -- the turn ---------------------------------------------------------------

    def _do_run(self) -> None:
        if self._cancelled:
            return
        binary = find_backend_cli(self._backend)
        if not binary:
            self._fail(self._install_hint)
            return
        try:
            proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                self._command(binary),
                cwd=self._cwd or None,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                env=self._env(binary),
                **own_group_kwargs(),
                **no_window_kwargs(),
            )
        except (OSError, ValueError) as exc:
            self._fail(f"Could not start {self._label()}: {exc}")
            return
        self._proc = proc
        if self._cancelled:
            end_process_group(proc)
            return
        self._stderr_thread = threading.Thread(target=self._read_stderr, args=(proc,), daemon=True)
        self._stderr_thread.start()
        self._send(proc)
        self._read(proc)
        self._finish(proc)

    def _label(self) -> str:
        return "Gemini CLI" if self._backend == BACKEND_GEMINI else "Antigravity CLI"

    def _send(self, proc: subprocess.Popen) -> None:
        stdin = proc.stdin
        if stdin is None:
            return
        try:
            stdin.write(self._stdin_text())
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
                del self._error_lines[:-_STDERR_KEEP]

    def _read(self, proc: subprocess.Popen) -> None:
        stdout = proc.stdout
        if stdout is None:
            return
        for raw in stdout:
            if self._cancelled:
                break
            line = raw.strip()
            if not line.startswith("{"):
                continue
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            if isinstance(frame, dict):
                self._handle(frame)

    def _finish(self, proc: subprocess.Popen) -> None:
        code: Optional[int] = None
        try:
            code = proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            end_process_group(proc)
            try:
                code = proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                code = None
        if self._cancelled:
            if not self._settled.is_set():
                self._release_all()
                self._settled.set()
                self._on_complete("".join(self._parts).strip() or "Stopped")
            return
        if self._completed or self._settled.is_set():
            return
        reader = self._stderr_thread
        self._stderr_thread = None
        if reader is not None:
            reader.join(timeout=5)
        from google_backend import readable_error

        with self._error_lock:
            tail = list(self._error_lines)
        detail = ""
        for line in reversed(tail):
            if "error" in line.casefold():
                detail = readable_error(line)
                break
        message = f"{self._label()} stopped before the turn completed"
        message += f" (exit code {code})." if code else "."
        if detail:
            message = f"{message} {detail}"
        self._fail(message[:600])

    def _teardown(self) -> None:
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

    # -- shared reporting -----------------------------------------------------

    def _remember_session(self, session: str) -> None:
        if session and session != self._session_seen:
            self._session_seen = session
            self._on_session(session)

    def _notify_started(self) -> None:
        if not self._started_notified:
            self._started_notified = True
            self._accepting_input.set()
            self._on_started()

    def _say(self, text: str) -> None:
        self._on_activity("assistant", text)

    def _add_text(self, text: str) -> None:
        if not text:
            return
        self._parts.append(text)
        self._streamed = release_finished("".join(self._parts), self._streamed, self._say)

    def _release_all(self) -> None:
        self._streamed = release_remainder("".join(self._parts), self._streamed, self._say)

    def _end_paragraph(self) -> None:
        """Close the paragraph a message ended on, so the next one starts fresh."""
        self._release_all()
        text = "".join(self._parts)
        if text.strip() and not text.endswith("\n\n"):
            self._parts.append("\n\n")
            self._streamed = len(text) + 2

    def _complete(self, final: str) -> None:
        self._release_all()
        text = final.strip() or "".join(self._parts).strip()
        self._completed = True
        self._settled.set()
        self._on_complete(text or "Finished with nothing to say.")


class GeminiWorker(_GoogleTurnWorker):
    """Run one Gemini CLI turn, reporting it through BlindPilot's callbacks."""

    _backend = BACKEND_GEMINI
    _install_hint = "Gemini CLI is not installed. Run: npm install -g @google/gemini-cli"

    def _setup(self) -> None:
        super()._setup()
        self._tools: dict[str, str] = {}
        self._in_text = False

    def _command(self, binary: str) -> list[str]:
        command = [
            binary,
            "--output-format",
            "stream-json",
            # BlindPilot was pointed at this folder; the folder-trust prompt
            # has nobody to answer it in a windowed run.
            "--skip-trust",
            "--approval-mode",
            _GEMINI_APPROVAL.get(self._permission_mode, "default"),
        ]
        if self._model:
            command += ["--model", self._model]
        if self._session_id:
            command += ["--resume", self._session_id]
        if self._additional_dirs:
            command += ["--include-directories", ",".join(self._additional_dirs)]
        return command

    def _stdin_text(self) -> str:
        # Headless mode starts because stdin is not a terminal; the whole of
        # stdin is the prompt, so a message beginning with "-" is never a flag.
        return self._prompt.rstrip("\n") + "\n"

    def _env(self, binary: str) -> dict[str, str]:
        from google_backend import gemini_configured_auth, turn_env

        # Gemini CLI prefers its own configured sign-in over the environment,
        # so the key is only supplied when its settings choose none.
        return turn_env(binary, use_key=not gemini_configured_auth())

    def _handle(self, frame: dict) -> None:
        kind = str(frame.get("type") or "")
        if kind == "init":
            self._remember_session(str(frame.get("session_id") or ""))
            self._notify_started()
        elif kind == "message":
            if frame.get("role") != "assistant":
                return
            self._notify_started()
            self._in_text = True
            self._add_text(str(frame.get("content") or ""))
        elif kind == "tool_use":
            if self._in_text:
                self._in_text = False
                self._end_paragraph()
            name = str(frame.get("tool_name") or "tool")
            tool_id = str(frame.get("tool_id") or "")
            if tool_id:
                self._tools[tool_id] = name
            params = frame.get("parameters")
            if name == "invoke_agent" and tool_id:
                renamed = _labelled_params(params)
                self._on_subagent(
                    tool_id,
                    str(renamed.get("description") or "agent")[:80],
                    SUBAGENT_RUNNING,
                    "",
                )
            self._on_activity("tool", tool_label(name, params))
        elif kind == "tool_result":
            tool_id = str(frame.get("tool_id") or "")
            name = self._tools.pop(tool_id, "tool")
            if name == "invoke_agent" and tool_id:
                self._on_subagent(tool_id, "", SUBAGENT_COMPLETED, "Finished")
            if frame.get("status") == "error":
                error = frame.get("error")
                message = error.get("message") if isinstance(error, dict) else error
                first = str(message or "").strip().splitlines()
                self._on_activity(
                    "tool", f"Failed: {name}: {first[0][:200]}" if first else f"Failed: {name}"
                )
                return
            output = _output_text(frame.get("output")).strip()
            if output:
                self._on_activity("result", output)
        elif kind == "error":
            from google_backend import readable_error

            message = readable_error(frame.get("message"))
            if message:
                self._on_activity("notice", message)
        elif kind == "result":
            self._result(frame)

    def _result(self, frame: dict) -> None:
        if str(frame.get("status") or "") == "success":
            self._complete("")
            return
        from google_backend import readable_error

        error = frame.get("error")
        message = error.get("message") if isinstance(error, dict) else error
        reason = readable_error(message) or "Gemini CLI reported an error."
        self._release_all()
        self._fail(f"Gemini CLI: {reason}"[:600])


class AntigravityWorker(_GoogleTurnWorker):
    """Run one Antigravity CLI turn, reporting it through BlindPilot's callbacks."""

    _backend = BACKEND_ANTIGRAVITY
    _install_hint = (
        "Antigravity CLI is not installed. Install it from "
        "https://antigravity.google/docs/cli/install/ and try again."
    )

    def _setup(self) -> None:
        super()._setup()
        # The step indexes already announced, so an ACTIVE update that repeats
        # is not read twice.
        self._announced: set[int] = set()
        self._reported: set[int] = set()
        self._text_step: Optional[int] = None

    def _command(self, binary: str) -> list[str]:
        command = [
            binary,
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            # A message that begins with "/" is the user's text, not one of
            # agy's commands; in this mode an unexpanded command ends the run.
            "--disable-slash-commands",
        ]
        if self._permission_mode == _BYPASS_MODE:
            command.append("--dangerously-skip-permissions")
        elif self._permission_mode in _ANTIGRAVITY_MODE:
            command += ["--mode", _ANTIGRAVITY_MODE[self._permission_mode]]
        if self._model:
            command += ["--model", self._model]
        if self._effort:
            command += ["--effort", self._effort]
        if self._session_id:
            command += ["--conversation", self._session_id]
        for directory in self._additional_dirs:
            command += ["--add-dir", directory]
        return command

    def _stdin_text(self) -> str:
        message = {"event": "user", "message": {"content": self._prompt}}
        return json.dumps(message, ensure_ascii=False) + "\n"

    def _env(self, binary: str) -> dict[str, str]:
        from google_backend import antigravity_uses_api_key, turn_env

        return turn_env(binary, use_key=antigravity_uses_api_key())

    def _handle(self, frame: dict) -> None:
        kind = str(frame.get("event") or "")
        if kind == "init":
            self._remember_session(str(frame.get("conversation_id") or ""))
            self._notify_started()
        elif kind == "step_update":
            update = frame.get("step_update")
            if isinstance(update, dict):
                self._step(update)
        elif kind == "result":
            result = frame.get("result")
            if isinstance(result, dict):
                self._result(result)

    def _step(self, update: dict) -> None:
        self._remember_session(str(update.get("conversation_id") or ""))
        self._notify_started()
        step_type = str(update.get("step_type") or "")
        index = update.get("step_index")
        index = index if isinstance(index, int) else -1
        if step_type == "agent_response":
            if self._text_step is not None and self._text_step != index:
                self._end_paragraph()
            self._text_step = index
            self._add_text(str(update.get("text_delta") or ""))
            return
        if step_type == "tool":
            if self._text_step is not None:
                self._end_paragraph()
                self._text_step = None
            info = update.get("tool_info")
            info = info if isinstance(info, dict) else {}
            name = str(info.get("name") or update.get("tool_name") or "tool")
            if index not in self._announced:
                self._announced.add(index)
                self._on_activity("tool", tool_label(name, info.get("parameters")))
            if str(update.get("state") or "") == "DONE" and index not in self._reported:
                self._reported.add(index)
                error = info.get("error")
                if isinstance(error, dict) and error.get("message"):
                    first = str(error["message"]).strip().splitlines()[0][:200]
                    self._on_activity("tool", f"Failed: {name}: {first}")
                else:
                    output = _output_text(info.get("output")).strip()
                    if output:
                        self._on_activity("result", output)
            return
        if step_type == "subagent":
            info = update.get("subagent_info")
            agents = info.get("subagents") if isinstance(info, dict) else None
            done = str(update.get("state") or "") == "DONE"
            for agent in agents if isinstance(agents, list) else []:
                if not isinstance(agent, dict):
                    continue
                agent_id = str(agent.get("conversation_id") or agent.get("role") or "")
                if not agent_id:
                    continue
                name = str(agent.get("role") or agent.get("type_name") or "agent")[:80]
                self._on_subagent(
                    agent_id,
                    name,
                    SUBAGENT_COMPLETED if done else SUBAGENT_RUNNING,
                    "Finished" if done else "",
                )

    def _result(self, result: dict) -> None:
        self._remember_session(str(result.get("conversation_id") or ""))
        status = str(result.get("status") or "").upper()
        if status == "SUCCESS":
            # "response" is this turn's whole answer; it covers a reply that
            # arrived without deltas.
            response = str(result.get("response") or "")
            self._complete("" if "".join(self._parts).strip() else response)
            return
        if status in ("CANCELED", "INTERRUPTED") and self._cancelled:
            return
        from google_backend import readable_error

        reason = (
            readable_error(result.get("error"))
            or f"the turn ended with status {status or 'unknown'}"
        )
        if "authentication" in reason.casefold():
            reason += (
                " Sign in by running agy in a terminal, or choose Sign In in "
                "BlindPilot's setup to use a Gemini API key."
            )
        self._release_all()
        self._fail(f"Antigravity CLI: {reason}"[:600])
