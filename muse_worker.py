"""Muse Code worker for BlindPilot.

One Muse turn, driven through Muse's own host protocol (MSP) over the stdio
of ``muse serve``. MSP is JSON-RPC 2.0, one object per line, with server
notifications for everything that streams -- which is the same shape Hermes'
TUI gateway speaks, so this worker reads like that one and the window can
hold either without knowing which it is.

What the protocol gives BlindPilot that a screen-reader front end needs:

    turn/start      a prompt, streamed back as items
    item/*          user and assistant messages, tool calls, reasoning
    turn/steer      guidance into the turn already running
    turn/cancel     stop without ending the session
    session/compact summarise the conversation in place
    approval/*      a tool that needs permission, asked mid-run
    userInput/*     a clarifying question with choices, asked mid-run

Unknown item kinds are rendered generically (MSP requires clients to do so)
rather than dropped: a new Muse that streams a kind this file has never
heard of still says something rather than going quiet.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
Based on the original Claude Code Reader application by doubletaponair:
https://github.com/doubletaponair/claude-code-reader
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import json
import queue
import platform
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional, Sequence

from agent_backends import (
    AskQuestions,
    Question,
    QuestionOption,
    no_window_kwargs,
)
from hermes_backend import STDERR_KEEP_LINES, windows_path_to_wsl
from markdown_rows import complete_sentences as _complete_sentences

from muse_backend import muse_command, muse_session_access_error


# Permission-mode mapping. MSP approval modes are fixed enum values, and the
# window's own mode names are what the user picked from, so the window's
# vocabulary is translated here rather than leaking into the window.
#
# The host seals an approval ceiling from its own startup configuration and
# rejects any session/start or setApprovalMode that exceeds it (measured on
# 1.0.3: "approval mode exceeds or is incomparable with the sealed startup
# mode", for allowAll and onRequest alike, and setApprovalMode cannot lift it
# either). A stock `muse serve` therefore accepts exactly two modes:
# promptUnmatched and denyUnmatched. The window's richer vocabulary is
# expressed on top of those, client-side:
#
#   bypassPermissions  starts promptUnmatched and auto-approves every
#                      approval request as it arrives -- the run never stops
#                      to ask, which is what bypass means here
#   acceptEdits, plan  start promptUnmatched; the host asks about anything
#                      its rules do not cover, and the person answers
#   default, auto      promptUnmatched, the same asking behaviour
#   dontAsk            denyUnmatched, where the host refuses by itself
_MUSE_APPROVAL_MODES = {
    "bypassPermissions": "promptUnmatched",
    "default": "promptUnmatched",
    "auto": "promptUnmatched",
    "acceptEdits": "promptUnmatched",
    "plan": "promptUnmatched",
    "dontAsk": "denyUnmatched",
}
_MUSE_DEFAULT_MODE = "promptUnmatched"


# MSP approval decisions the window's answers map onto.
_MUSE_APPROVE_ONCE = "approved"
_MUSE_APPROVE_SESSION = "approvedForSession"
_MUSE_DENY = "denied"


def _uuid() -> str:
    # MSP calls this a UUIDv7 idempotency handle, and it means it: measured on
    # 1.0.3, session/start answers invalidParams for a v4 with "expected
    # UUIDv7". uuid7() exists from Python 3.14; the fallback builds the same
    # shape by hand -- 48-bit millisecond timestamp, version 7, variant 10,
    # 74 random bits -- which the host accepts (measured).
    uuid7 = getattr(uuid, "uuid7", None)
    if uuid7 is not None:
        return str(uuid7())
    timestamp_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(uuid.uuid4().bytes, "big") & ((1 << 74) - 1)
    value = (timestamp_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= (rand >> 62) << 64
    value |= 0b10 << 62
    value |= rand & ((1 << 62) - 1)
    return str(uuid.UUID(int=value))


class MuseTransport:
    """`muse serve` as a child process, spoken to over its pipes.

    Line-delimited JSON both ways, the same framing Hermes' StdioTransport
    speaks, so the contract written for that transport describes this one too.
    """

    def __init__(self, cwd: str) -> None:
        self._cwd = cwd
        self._proc: Optional[subprocess.Popen] = None
        self._frames: "queue.Queue[dict]" = queue.Queue()
        self._stderr: list[str] = []
        self._closed = False
        self._send_lock = threading.Lock()

    def start(self) -> None:
        """Launch `muse serve`. Raises OSError when it cannot be started."""
        command = muse_command(self._cwd)
        if not command:
            raise OSError("Muse Code is not installed where this process can reach it")
        self._proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [*command, "serve"],
            cwd=None,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            **no_window_kwargs(),
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except ValueError:
                # Not protocol; remembered for the failure message, like stderr.
                with self._send_lock:
                    self._stderr.append(line[:400])
                    del self._stderr[:-STDERR_KEEP_LINES]
                continue
            self._frames.put(frame)
        self._closed = True

    def _read_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            line = line.strip()
            if line:
                with self._send_lock:
                    self._stderr.append(line)
                    del self._stderr[:-STDERR_KEEP_LINES]

    def send(self, message: dict) -> bool:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return False
        try:
            with self._send_lock:
                proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                proc.stdin.flush()
        except (OSError, ValueError):
            return False
        return True

    def receive(self, timeout: float) -> Optional[dict]:
        try:
            return self._frames.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None

    def connected(self) -> bool:
        return not self._closed

    def failure_detail(self) -> str:
        with self._send_lock:
            lines = list(self._stderr)
        detail = "; ".join(lines[-3:])
        # Never empty: after close() this is what the user is told instead of
        # "Muse did not respond in time", and the transport contract requires
        # a failure to say something a person can act on.
        return detail or "Muse Code closed the connection before the turn completed"

    def close(self) -> None:
        proc = self._proc
        self._closed = True
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except (OSError, ValueError):
            pass
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        # Closed pipes and a waited process: same discipline as Hermes' close,
        # because an unclosed pipe is a ResourceWarning that pytest -W error
        # turns into a failed build against whichever test was running.
        self._proc = None


class MuseWorker(threading.Thread):
    """Run one Muse turn, reporting it through BlindPilot's callbacks.

    The signature matches the other backends' workers so the window can hold
    whichever one the user picked without knowing which it is.
    """

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
        resume_only: bool = False,
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
        self._compact = compact
        self._resume_only = resume_only
        self._on_session = on_session
        self._on_started = on_started
        self._on_activity = on_activity
        self._on_complete = on_complete
        self._on_failed = on_failed
        self._on_done = on_done
        self._on_question = on_question

        self._transport: Optional[MuseTransport] = None
        self._cancelled = False
        self._clean_end = False
        self._failed = False
        # bypassPermissions cannot be sent as MSP's allowAll -- the host
        # seals its approval ceiling at startup and refuses anything above it
        # (measured) -- so bypass is honoured by auto-approving each request
        # here. The person is never asked, which is what the mode means.
        self._auto_approve = permission_mode == "bypassPermissions"
        self._accepting_input = threading.Event()
        self._request_id = 100
        # Streaming frames that arrive while a request reply is awaited are
        # parked here for the turn loop to process first, so a turn never
        # loses an item because a handshake was still in flight when it
        # started.
        self._parked: list[dict] = []
        self._parked_lock = threading.Lock()
        # The MSP session this conversation lives in, and the turn the
        # running work belongs to. The turn/start ack is authoritative for
        # the turn id, per the protocol.
        self._live_session = ""
        self._session_log_path = ""
        self._next_access_check = 0.0
        self._turn_id = ""
        self._assistant_parts: list[str] = []
        self._streamed = 0
        self._tool_names: dict[str, str] = {}
        self._tool_output_parts: dict[str, list[str]] = {}

    # -- public surface the window drives ---------------------------------

    def accepting_input(self) -> bool:
        return self._accepting_input.is_set() and not self._cancelled

    def steer(self, text: str) -> bool:
        """Push guidance into the turn that is already running.

        Sent without waiting for the ack: the window calls this on its own
        thread, and an ack is worth nothing next to a frozen window.
        """
        if not self.accepting_input() or not self._live_session or not self._turn_id:
            return False
        return self._fire(
            "turn/steer",
            {
                "commandId": _uuid(),
                "sessionId": self._live_session,
                "expectedTurnId": self._turn_id,
                "input": [{"type": "text", "text": text}],
            },
        )

    def cancel(self) -> None:
        self._cancelled = True
        self._accepting_input.clear()
        if self._live_session:
            # A cancel is asked, not forced: the server answers it, and the
            # turn/completed with terminal "cancelled" ends the loop. If the
            # process has already gone, there is nothing left to ask.
            # turnId names the exact turn; omitting it would target the
            # session's foreground turn, which is the same one here.
            self._fire(
                "turn/cancel",
                {
                    "commandId": _uuid(),
                    "sessionId": self._live_session,
                    "turnId": self._turn_id,
                },
            )

    def run(self) -> None:
        try:
            self._do_run()
        except Exception as exc:  # noqa: BLE001 - the crash IS the report
            if not self._failed and not self._clean_end:
                self._fail(f"Muse turn failed: {exc}")
        finally:
            self._accepting_input.clear()
            transport = self._transport
            if transport is not None:
                transport.close()
            self._on_done()

    # -- protocol plumbing -------------------------------------------------

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _fire(self, method: str, params: Optional[dict] = None) -> bool:
        """One notification-shaped send that does not wait for a reply.

        Steering, cancelling, and answering questions all write from a thread
        that is not the turn loop's; none of them can afford the wait, and
        MSP answers them on the view stream, which the turn loop reads.
        """
        transport = self._transport
        if transport is None or not transport.connected():
            return False
        message: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        return transport.send(message)

    def _request(
        self, method: str, params: Optional[dict] = None, timeout: float = 45.0
    ) -> Optional[dict]:
        """One request, its reply awaited, streaming frames parked."""
        transport = self._transport
        if transport is None or not transport.connected():
            return None
        request_id = self._next_id()
        message: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        if not transport.send(message):
            return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._cancelled:
                return None
            frame = transport.receive(0.25)
            if frame is None:
                if not transport.connected():
                    return None
                continue
            if frame.get("id") == request_id and ("result" in frame or "error" in frame):
                return frame
            with self._parked_lock:
                self._parked.append(frame)
        return None

    def _parked_frames(self) -> list[dict]:
        with self._parked_lock:
            frames = list(self._parked)
            self._parked.clear()
        return frames

    def _notify(self, method: str) -> bool:
        return self._fire(method)

    def _fail(self, message: str) -> None:
        self._failed = True
        self._on_failed(message)

    def _detail(self) -> str:
        transport = self._transport
        return transport.failure_detail() if transport is not None else ""

    # -- the turn -----------------------------------------------------------

    def _do_run(self) -> None:
        transport = MuseTransport(self._cwd)
        try:
            transport.start()
        except OSError as exc:
            self._fail(str(exc))
            return
        self._transport = transport

        if not self._handshake():
            return

        if self._resume_only:
            self._replay()
            self._clean_end = True
            return

        if self._compact:
            if not self._run_compaction():
                return
            self._clean_end = True
            return

        if not self._ensure_session():
            return
        if not self._live_session:
            self._fail("Muse Code started a session without naming it.")
            return

        self._accepting_input.set()
        self._on_started()
        if not self._start_turn():
            return
        self._consume_turn()
        self._clean_end = True

    def _handshake(self) -> bool:
        reply = self._request(
            "initialize",
            # MSP requires clientInfo.name to match ^[a-z0-9_]+$: a mixed-case
            # display name is answered invalidParams and the host never talks
            # to this client.
            {"clientInfo": {"name": "blindpilot", "version": "1"}},
            timeout=60.0,
        )
        if reply is None:
            self._fail(self._detail() or "Muse Code did not answer its initialize request.")
            return False
        if "error" in reply:
            self._fail(self._error_text(reply["error"]))
            return False
        self._notify("initialized")
        return True

    def _ensure_session(self) -> bool:
        """Start a new session or resume the stored one."""
        if self._session_id:
            reply = self._request(
                "session/resume",
                {"commandId": _uuid(), "sessionId": self._session_id},
                timeout=60.0,
            )
            if reply is not None and "result" in reply:
                session = (reply["result"] or {}).get("session") or {}
                self._live_session = str(session.get("sessionId") or self._session_id)
                self._session_log_path = str(session.get("path") or "")
                self._on_session(self._live_session)
                return True
            # The stored conversation no longer exists on this host. The
            # window keeps its session id either way; saying so beats
            # silently starting a different conversation in its place.
            self._fail(
                "Muse could not reopen this conversation. "
                "Start a new one, or pick it from Recent Conversations again."
            )
            return False
        params: dict = {"commandId": _uuid(), "workspaceRoot": self._workspace_root()}
        mode = _MUSE_APPROVAL_MODES.get(self._permission_mode, _MUSE_DEFAULT_MODE)
        params["approvalMode"] = mode
        if self._model:
            params["modelId"] = self._model
        reply = self._request("session/start", params, timeout=60.0)
        if reply is None:
            self._fail(self._detail() or "Muse Code refused to start a session.")
            return False
        if "error" in reply:
            self._fail(self._error_text(reply["error"]))
            return False
        session = (reply.get("result") or {}).get("session") or {}
        self._live_session = str(session.get("sessionId") or "")
        self._session_log_path = str(session.get("path") or "")
        if self._live_session:
            self._on_session(self._live_session)
        return True

    def _workspace_root(self) -> str:
        """The workspace root as the host sees it, always absolute.

        Measured on 1.0.3: session/start answers invalidParams for a relative
        workspaceRoot ("expected an absolute path"), the same rule WSL's own
        --cd has. On Windows the CLI runs inside WSL, where ``D:\\projekty\\x``
        is ``/mnt/d/projekty/x``; on every other platform the path is already
        what the host expects.
        """
        if platform.system() != "Windows":
            return str(Path(self._cwd).resolve())
        return windows_path_to_wsl(str(Path(self._cwd).resolve()))

    def _start_turn(self) -> bool:
        text = (self._prompt or "").strip()
        if not text:
            # An empty prompt would be rejected, and the turn loop would sit
            # waiting for an end that never comes.
            self._fail("Muse was given an empty prompt.")
            return False
        params: dict = {
            "commandId": _uuid(),
            "sessionId": self._live_session,
            "input": [{"type": "text", "text": text}],
        }
        if self._effort:
            params["reasoningEffort"] = self._effort
        reply = self._request("turn/start", params, timeout=60.0)
        if reply is None:
            self._fail(self._detail() or "Muse Code did not accept the prompt.")
            return False
        if "error" in reply:
            self._fail(self._error_text(reply["error"]))
            return False
        result = reply.get("result") or {}
        self._turn_id = str(result.get("turnId") or "")
        if not self._turn_id:
            self._fail("Muse Code started a turn without naming it.")
            return False
        # A "steered" disposition means the input joined a turn that was
        # already running; the transcript is streaming either way.
        return True

    def _error_text(self, error: object) -> str:
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            data = error.get("data")
            if isinstance(data, dict):
                kind = str(data.get("kind") or "").strip()
                if kind and kind not in message:
                    message = f"{message} ({kind})" if message else kind
            return message or json.dumps(error)[:200]
        return str(error)[:200]

    def _consume_turn(self) -> None:
        """Stream the turn until its terminal notification."""
        transport = self._transport
        if transport is None:
            return
        # Annotated because it is first bound by the parked-frames loop (always
        # a dict) and then reassigned from receive(), which answers None.
        frame: Optional[dict]
        while not self._cancelled:
            access_error = self._provider_access_error()
            if access_error:
                self._fail(access_error)
                return
            for frame in self._parked_frames():
                if self._handle_event(frame):
                    return
            frame = transport.receive(0.5)
            if frame is None:
                if not transport.connected():
                    self._fail(
                        self._detail()
                        or "Muse Code closed the connection before the turn finished."
                    )
                    return
                self._release_streamed()
                continue
            if self._handle_event(frame):
                return
        # Cancelled: the cancel request was sent; drain briefly for the
        # terminal notification so the transcript says how it ended.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            frame = transport.receive(0.25)
            if frame is None:
                if not transport.connected():
                    return
                continue
            if self._handle_event(frame):
                return

    def _provider_access_error(self) -> str:
        """Poll the durable log slowly while Muse waits for its provider.

        MSP has no notification for a provider's HTTP refusal while its own
        retry policy is active. The log is only queried once every two seconds,
        and only after this turn has a named session, so ordinary streamed turns
        retain their normal half-second responsiveness.
        """
        if not self._session_log_path:
            return ""
        now = time.monotonic()
        if now < self._next_access_check:
            return ""
        self._next_access_check = now + 2.0
        return muse_session_access_error(self._session_log_path)

    def _handle_event(self, frame: dict) -> bool:
        """One inbound frame. Returns True when the turn has ended."""
        if "id" in frame and ("result" in frame or "error" in frame):
            # A reply to a fired steer/cancel/answer. Its outcome arrives on
            # the view stream, which this loop is already reading.
            return False
        method = str(frame.get("method") or "")
        params = frame.get("params") or {}

        if method == "item/started":
            self._item_started(params.get("item"))
        elif method == "item/delta":
            self._item_delta(params)
        elif method == "item/completed":
            self._item_completed(params.get("item"))
        elif method == "approval/requested":
            self._approval_requested(params)
        elif method == "userInput/requested":
            self._user_input_requested(params)
        elif method == "turn/completed":
            self._turn_completed(params)
            return True
        return False

    # -- items --------------------------------------------------------------

    def _item_started(self, item: object) -> None:
        if not isinstance(item, dict):
            return
        kind = str(item.get("kind") or "")
        item_id = str(item.get("itemId") or "")
        if kind == "toolCall":
            name = str(item.get("tool") or "tool")
            self._tool_names[item_id] = name
            # The command the tool will run, when the tool has one, is the
            # part a listener needs; its verbatim args JSON is not readable
            # by ear.
            subject = ""
            try:
                parsed_args = json.loads(item.get("args") or "{}")
                if isinstance(parsed_args, dict):
                    subject = str(
                        parsed_args.get("command") or parsed_args.get("path") or ""
                    ).strip()
            except ValueError:
                subject = ""
            if not subject:
                subject = _item_text(item).strip()
            self._on_activity("tool", f"{name}: {subject}" if subject else name)
        elif kind not in ("userMessage", "reasoning"):
            # The user's own message is already on the transcript, and
            # reasoning is worth saying only when it has content. Anything
            # else -- including kinds this file has never heard of -- gets
            # a generic line, per the protocol's own requirement: kind name
            # plus fallbackText when the server gives one.
            text = str(item.get("fallbackText") or "").strip() or _item_text(item).strip()
            first = text.splitlines()[0][:120] if text else ""
            self._on_activity("tool", f"{kind}: {first}" if first else kind)

    def _item_delta(self, params: dict) -> None:
        item_id = str(params.get("itemId") or "")
        field = str(params.get("field") or "text")
        delta = str(params.get("delta") or "")
        if not delta:
            return
        if field.startswith("reasoning"):
            self._on_activity("thinking", delta)
            return
        if field.startswith("output") or item_id in self._tool_names:
            self._tool_output_parts.setdefault(item_id, []).append(delta)
            return
        # Assistant answer text, streamed a fragment at a time. Held until it
        # completes a sentence so the screen reader never reads torn words.
        self._assistant_parts.append(delta)
        self._release_streamed()

    def _item_completed(self, item: object) -> None:
        if not isinstance(item, dict):
            return
        kind = str(item.get("kind") or "")
        item_id = str(item.get("itemId") or "")
        if kind == "agentMessage":
            text = _item_text(item)
            if text.strip():
                # The authoritative whole replaces whatever fragments of it
                # were already spoken.
                self._assistant_parts = [text]
                self._streamed = 0
                self._release_all()
        elif kind == "toolCall":
            name = self._tool_names.get(item_id, str(item.get("tool") or "tool"))
            result_text = (
                _item_text(item)
                or str(item.get("visibleOutput") or "")
                or "".join(self._tool_output_parts.get(item_id, []))
            )
            if result_text.strip():
                self._on_activity("result", f"{name}: {result_text.strip()}")
            self._tool_output_parts.pop(item_id, None)
        elif kind == "reasoning":
            text = _item_text(item)
            if text.strip():
                self._on_activity("thinking", text.strip())
        elif kind == "compaction":
            self._on_activity("tool", "Conversation compacted")

    def _turn_completed(self, params: dict) -> None:
        self._release_all()
        error = params.get("error")
        terminal = str(params.get("terminal") or "completed")
        if terminal == "failed":
            message = "Muse turn failed"
            if isinstance(error, dict):
                message = str(error.get("message") or message)
            self._fail(message)
            return
        text = "".join(self._assistant_parts).strip()
        if terminal == "cancelled":
            self._on_complete(text or "Stopped")
            return
        self._on_complete(text or "Finished with nothing to say.")

    # -- mid-run questions --------------------------------------------------

    def _approval_requested(self, params: dict) -> None:
        approval_id = str(params.get("approvalId") or "")
        if not approval_id:
            return
        if self._cancelled:
            # A cancel arrived while the host was composing this request:
            # answering it would restart work the person just stopped.
            self._decide_approval(params, _MUSE_DENY)
            return
        if self._auto_approve:
            self._decide_approval(params, _MUSE_APPROVE_ONCE)
            return
        if self._on_question is None:
            # Nobody is here to answer: deny rather than leave the run wedged
            # on an approval nobody can see.
            self._decide_approval(params, _MUSE_DENY)
            return
        tool = str(params.get("toolName") or "A tool")
        subject = params.get("subject") or {}
        detail = ""
        if isinstance(subject, dict):
            detail = str(
                subject.get("command")
                or subject.get("path")
                or subject.get("target")
                or subject.get("host")
                or ""
            ).strip()
        question = Question(
            question=f"{tool} wants permission" + (f": {detail}" if detail else ""),
            header="Approval",
            options=(
                QuestionOption(label="Allow once", description="Approve this one use"),
                QuestionOption(
                    label="Allow for this conversation",
                    description="Approve for the rest of this session",
                ),
                QuestionOption(label="Deny", description="Refuse this use"),
            ),
            allow_custom=False,
        )
        answers = self._on_question([question])
        if self._cancelled:
            # The person pressed Stop while the dialog was up. That stop wins
            # over whatever the still-open dialog was answered with: approving
            # here would let work continue past the cancel.
            self._decide_approval(params, _MUSE_DENY)
            return
        pick = ((answers or [[]])[0] or ["Deny"])[0]
        if pick.startswith("Allow for"):
            decision = _MUSE_APPROVE_SESSION
        elif pick.startswith("Allow"):
            decision = _MUSE_APPROVE_ONCE
        else:
            decision = _MUSE_DENY
        self._decide_approval(params, decision)

    def _decide_approval(self, params: dict, decision: str) -> None:
        # currentRequirementId is the ApprovalRequirementRef object itself,
        # carried through verbatim: it is the multi-stage race guard, and a
        # decision aimed at one stage must never satisfy another.
        requirement = params.get("currentRequirementId")
        self._fire(
            "approval/decide",
            {
                "approvalId": params.get("approvalId"),
                "sessionId": params.get("sessionId") or self._live_session,
                "commandId": _uuid(),
                "choiceId": self._resolve_choice(params, decision),
                "requirementId": requirement,
            },
        )

    def _resolve_choice(self, params: dict, decision: str) -> str:
        """The server's choiceId whose decision is the one we mean.

        The schema is explicit: choiceId must be "one of the current
        availableChoices", and those ids belong to the server -- a raw
        "approved" sent back as an id is answered -32052. The request names
        each choice with the decision it stands for, so the answer is looked
        up, not assumed. When the intended decision is not on offer, a
        refusal is -- the one answer that cannot push work forward.
        """
        choices = [
            choice for choice in (params.get("availableChoices") or []) if isinstance(choice, dict)
        ]
        for choice in choices:
            if str(choice.get("decision") or "") == decision:
                return str(choice.get("choiceId") or decision)
        for choice in choices:
            if str(choice.get("decision") or "") == "denied":
                return str(choice.get("choiceId") or decision)
        return decision

    def _user_input_requested(self, params: dict) -> None:
        if self._on_question is None:
            self._cancel_input(params)
            return
        questions: list[Question] = []
        for one in params.get("questions") or []:
            if not isinstance(one, dict):
                continue
            options = tuple(
                QuestionOption(
                    label=str(option.get("label") or ""),
                    description=str(option.get("description") or ""),
                )
                for option in (one.get("options") or [])
                if isinstance(option, dict) and str(option.get("label") or "")
            )
            selection = one.get("selection") or {}
            multi = bool(isinstance(selection, dict) and selection.get("mode") == "multiple")
            questions.append(
                Question(
                    question=str(one.get("question") or ""),
                    header=str(one.get("header") or ""),
                    options=options,
                    multi_select=multi,
                    id=str(one.get("id") or ""),
                )
            )
        if not questions:
            self._cancel_input(params)
            return
        answers = self._on_question(questions)
        self._send_answers(params, questions, answers)

    def _send_answers(
        self,
        params: dict,
        questions: Sequence[Question],
        answers: Optional[list[list[str]]],
    ) -> None:
        body_answers = []
        for index, question in enumerate(questions):
            chosen = answers[index] if answers and index < len(answers) else []
            entry: dict = {"questionId": question.id}
            if chosen:
                if question.multi_select:
                    entry["selectedLabels"] = [str(c) for c in chosen]
                else:
                    entry["selectedLabel"] = str(chosen[0])
            body_answers.append(entry)
        self._fire(
            "userInput/answer",
            {
                "userInputId": params.get("userInputId"),
                "sessionId": params.get("sessionId") or self._live_session,
                "commandId": _uuid(),
                "answers": body_answers,
            },
        )

    def _cancel_input(self, params: dict) -> None:
        self._fire(
            "userInput/cancel",
            {
                "userInputId": params.get("userInputId"),
                "sessionId": params.get("sessionId") or self._live_session,
                "commandId": _uuid(),
            },
        )

    # -- compaction ---------------------------------------------------------

    def _run_compaction(self) -> bool:
        if not self._ensure_session():
            return False
        reply = self._request(
            "session/compact",
            {"commandId": _uuid(), "sessionId": self._live_session},
            timeout=60.0,
        )
        if reply is None:
            self._fail(self._detail() or "Muse Code could not compact this conversation.")
            return False
        if "error" in reply:
            self._fail(self._error_text(reply["error"]))
            return False
        # Compaction runs asynchronously: the ack is admission only, and the
        # summary lands as a compaction item on the view stream. Wait a
        # bounded while for it so the row says what happened rather than
        # that it was asked for.
        transport = self._transport
        deadline = time.monotonic() + 30.0
        while transport is not None and time.monotonic() < deadline and not self._cancelled:
            frame = transport.receive(0.5)
            if frame is None:
                if not transport.connected():
                    break
                continue
            method = str(frame.get("method") or "")
            if method == "item/completed":
                item = (frame.get("params") or {}).get("item") or {}
                if str(item.get("kind") or "") == "compaction":
                    self._on_complete("Conversation compacted")
                    return True
            if method == "turn/completed":
                self._on_complete("Conversation compacted")
                return True
        self._on_complete("Compaction accepted; the summary arrives with the next message.")
        return True

    def _replay(self) -> None:
        """Resume-only turns read the stored transcript back and end."""
        reply = self._request(
            "session/resume",
            {"commandId": _uuid(), "sessionId": self._session_id or self._live_session},
            timeout=60.0,
        )
        if reply is None or "result" not in reply:
            self._fail(self._detail() or "Muse could not read this conversation back.")
            return
        session = (reply["result"] or {}).get("session") or {}
        live = str(session.get("sessionId") or "")
        if live and not self._live_session:
            self._live_session = live
            self._on_session(live)
        history = (reply["result"] or {}).get("history") or {}
        items = history.get("items")
        if items is None and isinstance(history.get("snapshot"), dict):
            items = history["snapshot"].get("items")
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "")
            text = _item_text(item)
            if kind == "userMessage" and text.strip():
                self._on_activity("you", text)
            elif kind == "agentMessage" and text.strip():
                self._on_activity("assistant", text)
            elif kind == "toolCall":
                name = str(item.get("tool") or "tool")
                summary = text.strip()
                self._on_activity("tool", f"{name}: {summary}" if summary else name)
        self._on_complete("")

    # -- streaming helpers --------------------------------------------------

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


def _item_text(item: dict) -> str:
    """The display text of one item, whichever fields it carries."""
    for key in ("text", "displayText", "content", "summary", "output", "result"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    parts = item.get("parts")
    if isinstance(parts, list):
        texts = [
            str(part.get("text") or "")
            for part in parts
            if isinstance(part, dict) and part.get("text")
        ]
        if texts:
            return "\n".join(texts)
    return ""
