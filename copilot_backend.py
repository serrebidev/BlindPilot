"""GitHub Copilot CLI backend for BlindPilot.

GitHub Copilot CLI (``npm install -g @github/copilot``, or ``winget install
GitHub.Copilot``, run as ``copilot``) answers one prompt headlessly and exits,
so a turn is one process, like Gemini CLI's (measured at 1.0.104)::

    copilot --output-format json --no-ask-user --no-auto-update --no-color
            --session-id <uuid> --allow-all-tools [--allow-all | --mode plan |
            --assisted-approval] [--model m] [--reasoning-effort e]
            [--add-dir d]...                         <prompt on stdin>

``--session-id`` names a new conversation or resumes an old one, so
BlindPilot picks the id of a new conversation itself and every later turn
passes the same one back. The prompt travels on stdin, which Copilot reads as
the prompt when it is piped: a long or multi-line prompt never has to survive
being quoted onto a ``copilot.cmd`` command line.

stdout is JSONL, one event per line, ``{"type", "data", "id", "parentId",
"timestamp"}``, with ``"ephemeral": true`` on the ones Copilot does not save.
The event names and fields are the Copilot SDK's (``@github/copilot-sdk``,
``generated/session-events.d.ts``): ``assistant.message_delta`` streams the
answer (``deltaContent``), ``assistant.message`` repeats it whole with its
``toolRequests``, ``tool.execution_start`` / ``tool.execution_complete`` are
the steps, ``subagent.*`` the agents it hands work to, ``session.error`` a
failure, and a final ``result`` ends the run. Events from a subagent carry
``parentToolCallId`` or ``agentId`` and are left out of the answer.

Copilot saves every conversation as
``~/.copilot/session-state/<id>/events.jsonl`` (``COPILOT_HOME`` moves it),
in the same envelope, which is what Recent Conversations reads.

Sign-in is ``copilot login``, GitHub's device flow, in a terminal the person
can read the one-time code from. Copilot also signs in from ``gh``'s own
sign-in, or from ``COPILOT_GITHUB_TOKEN``, ``GH_TOKEN`` or ``GITHUB_TOKEN``,
so any of those counts as signed in.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
Based on the original Claude Code Reader application by doubletaponair:
https://github.com/doubletaponair/claude-code-reader
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Optional

import agent_backends
from agent_backends import (
    BACKEND_COPILOT,
    SUBAGENT_COMPLETED,
    SUBAGENT_FAILED,
    SUBAGENT_RUNNING,
    _tool_use_label,
    _TurnWorker,
    end_process_group,
    find_backend_cli,
    no_window_kwargs,
    own_group_kwargs,
    subprocess_env,
)
from markdown_rows import SentenceStreamer

# Copilot's --reasoning-effort values, from `copilot --help` at 1.0.104.
COPILOT_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

_BYPASS_MODE = "bypassPermissions"
_STDERR_KEEP = 40

# --------------------------------------------------------------------------
# Settings and sign-in
# --------------------------------------------------------------------------


def copilot_home() -> Path:
    override = os.environ.get("COPILOT_HOME", "").strip()
    return Path(override) if override else Path.home() / ".copilot"


def _read_commented_json(path: Path) -> dict:
    """Copilot's config.json opens with whole-line ``//`` comments."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))
    try:
        payload = json.loads(body) if body.strip() else {}
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def copilot_account() -> str:
    """The GitHub login ``copilot login`` last signed in as, or ""."""
    user = _read_commented_json(copilot_home() / "config.json").get("lastLoggedInUser")
    login = user.get("login") if isinstance(user, dict) else None
    return login.strip() if isinstance(login, str) else ""


def copilot_configured_model() -> str:
    model = _read_commented_json(copilot_home() / "settings.json").get("model")
    return model.strip() if isinstance(model, str) else ""


def _token_variable() -> str:
    for name in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(name, "").strip():
            return name
    return ""


def _gh_signed_in(timeout: int) -> bool:
    gh = shutil.which("gh")
    if not gh:
        return False
    code, _text = agent_backends._probe_backend(gh, ["auth", "status"], timeout)
    return code == 0


def copilot_auth_ok(timeout: int = 20) -> bool:
    """Whether a Copilot turn can sign in without asking anybody.

    Copilot has no status command. Its own sign-in is recorded in config.json
    (the token itself is in the system credential store); failing that it uses
    a token in the environment or the GitHub CLI's sign-in. Whether the
    account has a Copilot plan is only known from the first turn.
    """
    return bool(copilot_account() or _token_variable() or _gh_signed_in(timeout))


def copilot_account_lines() -> list[str]:
    account = copilot_account()
    if account:
        return ["Signed in: yes", f"Account: {account}"]
    variable = _token_variable()
    if variable:
        return ["Signed in: yes", f"Sign-in method: the {variable} environment variable"]
    if _gh_signed_in(20):
        return ["Signed in: yes", "Sign-in method: the GitHub CLI (gh) sign-in"]
    return ["Signed in: no"]


def copilot_model_options() -> tuple[list[str], list[str], str, str, str]:
    """The picker's answer: (models, efforts, current model, current effort, error).

    Copilot has no model-list command. "auto" lets Copilot choose per request,
    and a model id typed into the picker is passed straight through.
    """
    if find_backend_cli(BACKEND_COPILOT) is None:
        return [], [], "", "", "GitHub Copilot CLI was not found."
    models = ["auto"]
    current = copilot_configured_model() or "auto"
    if current not in models:
        models.append(current)
    return models, list(COPILOT_EFFORTS), current, "", ""


_REQUEST_ID = re.compile(r"\s*\(Request ID:[^)]*\)")


def readable_error(text: object) -> str:
    """Copilot's own sentence, without the request id, plus what to do about it."""
    first = str(text or "").strip().splitlines()
    if not first:
        return ""
    message = _REQUEST_ID.sub("", first[0].removeprefix("Error:").strip())
    if "access denied by policy" in message.casefold():
        message += (
            ". This GitHub account has no Copilot plan that allows Copilot CLI, or an "
            "organization turned it off. Check https://github.com/settings/copilot "
            "(Copilot Free is enough)."
        )
    elif "not logged in" in message.casefold() or "authenticat" in message.casefold():
        message += (
            ". Sign in: choose Model, Manage Backends, pick GitHub Copilot CLI, and choose Sign In."
        )
    return message[:600]


# --------------------------------------------------------------------------
# Tool steps
# --------------------------------------------------------------------------

# Copilot's tool names in Claude Code's, so a step reads "Reading a.txt".
# Read from saved Copilot sessions.
_CLAUDE_TOOL_NAMES = {
    "view": "Read",
    "edit": "Edit",
    "create": "Write",
    "powershell": "PowerShell",
    "bash": "Bash",
    "grep": "Grep",
    "glob": "Glob",
    "web_fetch": "WebFetch",
    "web_search": "WebSearch",
    "task": "Task",
}
_PARAM_NAMES = {
    "path": "file_path",
    "old_str": "old_string",
    "new_str": "new_string",
    "file_text": "content",
    "prompt": "description",
}


def tool_label(name: str, params: object) -> str:
    if not isinstance(params, dict):
        params = {}
    if name == "report_intent":
        return str(params.get("intent") or "")
    renamed = {_PARAM_NAMES.get(key, key): value for key, value in params.items()}
    return _tool_use_label(_CLAUDE_TOOL_NAMES.get(name, name), renamed)


def _result_text(result: object) -> str:
    if isinstance(result, dict):
        result = result.get("content")
    return result.strip() if isinstance(result, str) else ""


# --------------------------------------------------------------------------
# The turn
# --------------------------------------------------------------------------


class CopilotWorker(_TurnWorker):
    """Run one GitHub Copilot CLI turn, reporting it through BlindPilot's callbacks."""

    _backend = BACKEND_COPILOT

    def __init__(self, *args, additional_dirs: tuple[str, ...] = (), **kwargs) -> None:
        self._additional_dirs = tuple(additional_dirs or ())
        super().__init__(*args, **kwargs)

    def _setup(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._error_lines: list[str] = []
        self._error_lock = threading.Lock()
        self._stderr_thread: Optional[threading.Thread] = None
        self._parts: list[str] = []
        self._streamer = SentenceStreamer(lambda text: self._on_activity("assistant", text))
        self._started = False
        self._completed = False
        self._message_id = ""
        self._streamed: set[str] = set()
        self._tools: dict[str, str] = {}
        self._errors: list[str] = []
        # Copilot is told the id of a new conversation, so the window knows
        # it before Copilot has said anything.
        self._conversation = self._session_id or str(uuid.uuid4())

    def steer(self, text: str) -> bool:
        # One process answers one prompt; a second message waits its turn.
        return False

    def cancel(self) -> None:
        self._cancelled = True
        proc = self._proc
        if proc is not None:
            end_process_group(proc)

    def _command(self, binary: str) -> list[str]:
        command = [
            binary,
            "--output-format",
            "json",
            # Nobody can answer a question in the middle of a headless turn.
            "--no-ask-user",
            # BlindPilot updates Copilot itself, in its logged npm installer.
            "--no-auto-update",
            "--no-color",
            "--session-id",
            self._conversation,
            # Prompt mode refuses to run without it.
            "--allow-all-tools",
        ]
        if self._permission_mode == _BYPASS_MODE:
            command.append("--allow-all")
        elif self._permission_mode == "plan":
            command += ["--mode", "plan"]
        elif self._permission_mode == "auto":
            # Copilot's own reviewer judges each risky step, as Codex's
            # automatic approval review does in Auto.
            command.append("--assisted-approval")
        if self._model:
            command += ["--model", self._model]
        if self._effort:
            command += ["--reasoning-effort", self._effort]
        for directory in self._additional_dirs:
            command += ["--add-dir", directory]
        return command

    def _do_run(self) -> None:
        if self._cancelled:
            return
        binary = find_backend_cli(BACKEND_COPILOT)
        if not binary:
            self._fail("GitHub Copilot CLI is not installed. Run: npm install -g @github/copilot")
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
                env=subprocess_env(binary),
                **own_group_kwargs(),
                **no_window_kwargs(),
            )
        except (OSError, ValueError) as exc:
            self._fail(f"Could not start GitHub Copilot CLI: {exc}")
            return
        self._proc = proc
        if self._cancelled:
            end_process_group(proc)
            return
        self._on_session(self._conversation)
        self._stderr_thread = threading.Thread(target=self._read_stderr, args=(proc,), daemon=True)
        self._stderr_thread.start()
        stdin = proc.stdin
        if stdin is not None:
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
        stdout = proc.stdout
        for raw in stdout if stdout is not None else ():
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
                self._handle_frame(frame)
        self._finish(proc)

    def _read_stderr(self, proc: subprocess.Popen) -> None:
        for line in proc.stderr if proc.stderr is not None else ():
            text = line.strip()
            if text:
                with self._error_lock:
                    self._error_lines.append(text)
                    del self._error_lines[:-_STDERR_KEEP]

    # -- events ---------------------------------------------------------------

    def _notify_started(self) -> None:
        if not self._started:
            self._started = True
            self._accepting_input.set()
            self._on_started()

    def _add_text(self, message_id: str, text: str) -> None:
        if not text:
            return
        if message_id != self._message_id:
            # A new message after a tool step starts a paragraph of its own.
            self._message_id = message_id
            joined = "".join(self._parts)
            if joined.strip() and not joined.endswith("\n\n"):
                self._streamer.flush()
                self._parts.append("\n\n")
        self._parts.append(text)
        self._streamer.feed(text)

    def _handle_frame(self, frame: dict) -> None:
        kind = str(frame.get("type") or "")
        data = frame.get("data")
        data = data if isinstance(data, dict) else {}
        if frame.get("agentId") or data.get("parentToolCallId"):
            # A subagent's own traffic; its result arrives as the task tool's.
            return
        if kind in ("session.start", "user.message", "assistant.turn_start"):
            self._notify_started()
        elif kind == "assistant.message_delta":
            self._notify_started()
            message_id = str(data.get("messageId") or "")
            self._streamed.add(message_id)
            self._add_text(message_id, str(data.get("deltaContent") or ""))
        elif kind == "assistant.message":
            message_id = str(data.get("messageId") or "")
            if message_id not in self._streamed:
                self._add_text(message_id, str(data.get("content") or ""))
        elif kind == "tool.execution_start":
            self._tool_started(data)
        elif kind == "tool.execution_complete":
            self._tool_finished(data)
        elif kind in ("subagent.started", "subagent.completed", "subagent.failed"):
            agent_id = str(data.get("toolCallId") or "")
            if agent_id:
                status = {
                    "subagent.started": SUBAGENT_RUNNING,
                    "subagent.completed": SUBAGENT_COMPLETED,
                    "subagent.failed": SUBAGENT_FAILED,
                }[kind]
                name = str(data.get("agentDisplayName") or data.get("agentName") or "agent")
                line = "" if status == SUBAGENT_RUNNING else "Finished"
                if status == SUBAGENT_FAILED:
                    line = str(data.get("error") or "Failed")
                self._on_subagent(agent_id, name[:80], status, line)
        elif kind == "session.error":
            message = readable_error(data.get("message"))
            if message:
                self._errors.append(message)
                self._on_activity("notice", message)
        elif kind == "result":
            self._result(frame, data)

    def _tool_started(self, data: dict) -> None:
        name = str(data.get("toolName") or "tool")
        call_id = str(data.get("toolCallId") or "")
        if call_id:
            self._tools[call_id] = name
        params = data.get("arguments")
        if name == "update_todo" and isinstance(params, dict):
            report_tasks = getattr(self, "on_tasks", None)
            if report_tasks:
                report_tasks("copilot", params.get("todos"))
            return
        label = tool_label(name, params)
        if label:
            self._on_activity("tool", label)

    def _tool_finished(self, data: dict) -> None:
        name = self._tools.pop(str(data.get("toolCallId") or ""), "tool")
        if name in ("report_intent", "update_todo"):
            return
        if data.get("success") is False:
            error = data.get("error")
            message = error.get("message") if isinstance(error, dict) else error
            first = str(message or "").strip().splitlines()
            self._on_activity(
                "tool", f"Failed: {name}: {first[0][:200]}" if first else f"Failed: {name}"
            )
            return
        output = _result_text(data.get("result"))
        if output:
            self._on_activity("result", output)

    def _result(self, frame: dict, data: dict) -> None:
        code = frame.get("exitCode", data.get("exitCode", 0))
        if code in (0, None) and not self._errors:
            self._complete()
        # Anything else is reported from the exit, with stderr's account.

    def _complete(self) -> None:
        if self._completed:
            return
        self._streamer.flush()
        self._completed = True
        self._settled.set()
        self._on_complete("".join(self._parts).strip() or "Finished with nothing to say.")

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
        reader = self._stderr_thread
        self._stderr_thread = None
        if reader is not None:
            reader.join(timeout=5)
        if self._cancelled:
            if not self._settled.is_set():
                self._streamer.flush()
                self._settled.set()
                self._on_complete("".join(self._parts).strip() or "Stopped")
            return
        if self._completed or self._settled.is_set():
            return
        if code == 0 and not self._errors and "".join(self._parts).strip():
            self._complete()
            return
        with self._error_lock:
            tail = list(self._error_lines)
        detail = next(
            (readable_error(line) for line in tail if line.startswith("Error:")),
            self._errors[-1] if self._errors else "",
        )
        message = "GitHub Copilot CLI stopped before the turn completed"
        message += f" (exit code {code})." if code else "."
        self._streamer.flush()
        self._fail(f"{message} {detail}".strip()[:700])

    def _teardown(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            end_process_group(proc)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass
        try:
            proc.wait(timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        self._proc = None
