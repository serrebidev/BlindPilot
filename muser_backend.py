"""Muser backend for BlindPilot.

Muser (``npm install -g muser-cli``, run as ``muser``) is a terminal coding
agent for muse.ai: Muse itself runs the tools, on this machine, in the
folder the conversation is in. That is what separates it from BlindPilot's
muse.ai backend, whose agent runs on Meta's own machine and can only chat:
a Muser turn is a real agent-mode turn, with files read and edited here.

A turn is one process, like Copilot's (measured at muser-cli 0.3.6)::

    muser exec --json [--session <id>] --mode <mode> -     <prompt on stdin>

The prompt travels on stdin (``-`` asks for it there), so a long or
multi-line prompt never has to survive being quoted onto a ``muser.cmd``
command line. stdout is JSON Lines: a ``start`` line naming the session,
one ``tool`` line per tool call and one ``muse_step`` line per Muse-side
step as the turn runs, and a final ``result`` line whose ``reply`` is the
whole answer -- nothing streams, so the answer arrives at once at the end.
A conversation continues by passing its session id back with ``--session``.

The modes are ``default``, ``auto-edit``, ``plan`` (read-only) and
``bypass``. In an exec run nothing can be answered mid-turn: the approver
declines whatever the mode does not pre-authorize, and Muse is told why,
so BlindPilot's permission modes map onto Muser's (see ``_MODES``).

Sign-in is ``muser login``, one onboarding flow that does both missing
steps: the muse.ai web sign-in (it opens an Edge window) and registering
this PC as a Muse device. ``muser doctor`` reports both halves, and an
exec run refuses to start when either is missing, so the sign-in check
requires both. Muser keeps its session index and transcripts under
``%LOCALAPPDATA%\\muser`` (see ``session_history``), its settings in
``~/.muser``, and picks its own model -- there is no model or effort
picker, as with the muse.ai backend.

Muser is Windows only: the npm package declares ``os: win32`` and
Node 22 or later.

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Optional

import agent_backends
from agent_backends import (
    BACKEND_MUSER,
    _TurnWorker,
    end_process_group,
    find_backend_cli,
    no_window_kwargs,
    own_group_kwargs,
    subprocess_env,
)
from markdown_rows import SentenceStreamer

_STDERR_KEEP = 40

# BlindPilot's permission modes in Muser's. In an exec run an approval
# nobody pre-authorized is declined, so Default and Don't Ask land in the
# same place. Auto promises that routine work does not stop; Muser has no
# reviewer mode of its own (no assisted approval), so bypass is the only
# mode that keeps that promise.
_MODES = {
    "default": "default",
    "dontAsk": "default",
    "acceptEdits": "auto-edit",
    "plan": "plan",
    "auto": "bypass",
    "bypassPermissions": "bypass",
}


def muser_mode(permission_mode: str) -> str:
    return _MODES.get(permission_mode, "default")


def muser_data_dir() -> Path:
    """Where Muser keeps its device store: sessions, transcripts, sign-in."""
    root = os.environ.get("LOCALAPPDATA", "").strip()
    return Path(root) / "muser" if root else Path.home() / "AppData" / "Local" / "muser"


def muser_home() -> Path:
    return Path.home() / ".muser"


# --------------------------------------------------------------------------
# Settings and sign-in
# --------------------------------------------------------------------------

_DOCTOR_LINE = re.compile(r"^([^:]+?)\s*:\s*(.*)$")


def muser_doctor(timeout: int = 20) -> dict[str, str]:
    """``muser doctor`` as a name-to-value dict; empty when Muser is absent."""
    binary = find_backend_cli(BACKEND_MUSER)
    if not binary:
        return {}
    code, text = agent_backends._probe_backend(binary, ["doctor"], timeout)
    if code != 0:
        return {}
    found: dict[str, str] = {}
    for line in text.splitlines():
        match = _DOCTOR_LINE.match(line.strip())
        if match:
            found[match.group(1).strip().casefold()] = match.group(2).strip()
    return found


def muser_auth_ok(timeout: int = 20) -> bool:
    """Whether a Muser turn can start without asking anybody.

    Both halves of the sign-in have to be in place: the muse.ai web login
    and this PC registered as a Muse device. Exec refuses to run when
    either is missing, so either one alone reads as signed out.
    """
    doctor = muser_doctor(timeout)
    return doctor.get("web login", "").startswith("ready") and doctor.get(
        "pc device", ""
    ).startswith("authenticated")


def _weekly_usage_line(timeout: int = 20) -> str:
    binary = find_backend_cli(BACKEND_MUSER)
    if not binary:
        return ""
    code, text = agent_backends._probe_backend(binary, ["usage"], timeout)
    if code != 0:
        return ""
    for line in text.splitlines():
        if "weekly usage" in line:
            return line.strip()
    return ""


def muser_account_lines() -> list[str]:
    doctor = muser_doctor()
    if not doctor:
        return ["Signed in: no"]
    if not (
        doctor.get("web login", "").startswith("ready")
        and doctor.get("pc device", "").startswith("authenticated")
    ):
        return ["Signed in: no"]
    lines = ["Signed in: yes"]
    gateway = doctor.get("gateway", "")
    if gateway:
        lines.append(f"Gateway: {gateway}")
    usage = _weekly_usage_line()
    if usage:
        lines.append(usage)
    return lines


def readable_error(text: object) -> str:
    """Muser's own sentence, plus what to do about a sign-in failure."""
    first = str(text or "").strip().splitlines()
    if not first:
        return ""
    message = first[0].removeprefix("Error:").strip()
    lowered = message.casefold()
    if (
        "login" in lowered
        or "logged" in lowered
        or "authenticat" in lowered
        or "ログイン" in message
    ):
        message += ". Sign in: choose Model, Manage Backends, pick Muser, and choose Sign In."
    return message[:600]


# --------------------------------------------------------------------------
# The turn
# --------------------------------------------------------------------------


class MuserWorker(_TurnWorker):
    """Run one Muser exec turn, reporting it through BlindPilot's callbacks."""

    _backend = BACKEND_MUSER

    def _setup(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._error_lines: list[str] = []
        self._error_lock = threading.Lock()
        self._stderr_thread: Optional[threading.Thread] = None
        self._parts: list[str] = []
        self._streamer = SentenceStreamer(lambda text: self._on_activity("assistant", text))
        # Not `_started`: threading.Thread owns that name (its start() calls
        # .is_set() on it), and shadowing it stops the worker ever starting.
        self._announced = False
        self._completed = False
        self._errors: list[str] = []
        # A new conversation's id is Muser's to choose; it arrives on the
        # start line. A resumed one is the id BlindPilot was handed.
        self._conversation = self._session_id or ""

    def steer(self, text: str) -> bool:
        # One process answers one prompt; a second message waits its turn.
        return False

    def cancel(self) -> None:
        self._cancelled = True
        proc = self._proc
        if proc is not None:
            end_process_group(proc)

    def _command(self, binary: str) -> list[str]:
        command = [binary, "exec", "--json", "--mode", muser_mode(self._permission_mode)]
        if self._conversation:
            command += ["--session", self._conversation]
        # "-" is exec's word for "the prompt is on stdin".
        command.append("-")
        return command

    def _do_run(self) -> None:
        if self._cancelled:
            return
        binary = find_backend_cli(BACKEND_MUSER)
        if not binary:
            self._fail("Muser is not installed. Run: npm install -g muser-cli")
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
            self._fail(f"Could not start Muser: {exc}")
            return
        self._proc = proc
        if self._cancelled:
            end_process_group(proc)
            return
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
        if not self._announced:
            self._announced = True
            self._accepting_input.set()
            self._on_started()

    def _note_session(self, frame: dict) -> None:
        session = str(frame.get("session") or "")
        if session and session != self._conversation:
            self._conversation = session
            self._on_session(session)

    def _handle_frame(self, frame: dict) -> None:
        kind = str(frame.get("type") or "")
        if kind == "start":
            self._notify_started()
            self._note_session(frame)
        elif kind == "tool":
            self._notify_started()
            self._tool(frame)
        elif kind == "muse_step":
            self._notify_started()
            title = str(frame.get("title") or "").strip()
            if title:
                self._on_activity("notice", title)
        elif kind == "result":
            self._note_session(frame)
            self._result(frame)

    def _tool(self, frame: dict) -> None:
        command = str(frame.get("command") or "tool")
        summary = str(frame.get("summary") or "").strip()
        label = f"{command} {summary}".strip()
        if frame.get("ok") is False:
            if frame.get("declined"):
                self._on_activity("tool", f"Declined (needs approval in this mode): {label}")
                return
            error = str(frame.get("error") or "").strip().splitlines()
            self._on_activity(
                "tool", f"Failed: {label}: {error[0][:200]}" if error else f"Failed: {label}"
            )
            return
        self._on_activity("tool", label)

    def _result(self, frame: dict) -> None:
        if frame.get("ok") is not True:
            message = readable_error(frame.get("error"))
            if message:
                self._errors.append(message)
            return
        self._notify_started()
        reply = str(frame.get("reply") or "").strip()
        if reply:
            self._parts.append(reply)
            self._streamer.feed(reply)
        sources = frame.get("sources")
        links = [
            f"- {entry.get('label') or entry.get('uri')}: {entry.get('uri')}"
            if entry.get("label") and entry.get("uri") != entry.get("label")
            else f"- {entry.get('uri')}"
            for entry in (sources if isinstance(sources, list) else [])
            if isinstance(entry, dict) and entry.get("uri")
        ]
        if links:
            joined = "".join(self._parts)
            if joined.strip() and not joined.endswith("\n\n"):
                self._parts.append("\n\n")
            self._parts.append("Sources:\n" + "\n".join(links))
        self._report_tasks()
        self._complete()

    def _report_tasks(self) -> None:
        """The turn's final task list, from the transcript Muser just wrote.

        Exec's JSON carries no task list of its own, but the transcript
        record for the turn does -- the same ``todos`` a reopened
        conversation replays (see session_history.restore_tasks).
        """
        report_tasks = getattr(self, "on_tasks", None)
        if not report_tasks or not self._conversation:
            return
        path = muser_data_dir() / "transcripts" / f"{self._conversation}.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in reversed(lines):
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            todos = record.get("todos")
            if isinstance(todos, list) and todos:
                report_tasks("muser", todos)
            return

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
        detail = self._errors[-1] if self._errors else ""
        if not detail:
            detail = next((readable_error(line) for line in tail if readable_error(line)), "")
        message = "Muser stopped before the turn completed"
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
