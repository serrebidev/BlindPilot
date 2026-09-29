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
    turn/interrupt  stop without ending the session
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

import base64
import json
import platform
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional, Sequence

from agent_backends import (
    SUBAGENT_COMPLETED,
    SUBAGENT_FAILED,
    SUBAGENT_RUNNING,
    AskQuestions,
    Question,
    QuestionOption,
    SubagentReport,
    ignore_subagents,
    subagent_line,
    subagent_status,
)
from hermes_backend import JsonRpcCalls, StdioTransport, windows_path_to_wsl
from markdown_rows import release_finished, release_remainder

from muse_backend import (
    muse_command,
    muse_image_part,
    muse_session_access_error,
    muse_session_log_text,
    muse_skills,
    note_muse_usage,
    parse_muse_transcript,
)


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


# MSP approval decisions. The person picks from the server's own choices;
# these name the two the worker picks by itself (bypass, and refusals).
_MUSE_APPROVE_ONCE = "approved"
_MUSE_REFUSALS = ("denied", "abort")

# How much of a truncated tool output is fetched back. The view budget is
# ~64KB (measured: a 268KB shell output arrived as 65534 chars with
# truncated set); past a megabyte the transcript row is the runaway one.
_MAX_OUTPUT_FETCH_BYTES = 1024 * 1024

# How many view pages a resume-only replay reads before stopping. Two hundred
# events a page, twenty pages: past that the transcript is a runaway one.
_REPLAY_MAX_PAGES = 20


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


def _muse_transport(cwd: str, *, unsandboxed: bool = False) -> StdioTransport:
    """`muse serve` as a child process, spoken to over its pipes.

    MSP is line-delimited JSON-RPC, which is the framing Hermes' gateway
    speaks down the same kind of pipe, so the connection is Hermes'
    StdioTransport with Muse's own command line rather than a second copy of
    it. Raises OSError when Muse is not installed where this process can
    reach it, which is what the caller reports.
    """
    command = muse_command(cwd)
    if not command:
        raise OSError("Muse Code is not installed where this process can reach it")
    # The shell sandbox is fixed when the host starts and cannot be changed
    # over the wire. Left on, a bypass turn's shell has no LAN, no Windows
    # interop from WSL, a read-only home and a /tmp that is gone next turn:
    # "adb connect" answered "Network is unreachable" and powershell.exe
    # failed on UtilBindVsockAnyPort. These two flags are what Muse's own
    # --yolo turns off, which is what bypass means.
    flags = ["--disable-sandbox", "--trust-workspace"] if unsandboxed else []
    return StdioTransport(cwd, argv=[*command, "serve", *flags], peer="Muse Code")


def _json_object(raw: object) -> dict:
    """A tool's args or output, which Muse sends as JSON text, as a dict."""
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw or ""))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


class MuseWorker(JsonRpcCalls, threading.Thread):
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
        attachments: Optional[Sequence[str]] = None,
        on_session: Callable[[str], None],
        on_started: Callable[[], None],
        on_activity: Callable[[str, str], None],
        on_complete: Callable[[str], None],
        on_failed: Callable[[str], None],
        on_done: Callable[[], None],
        on_question: Optional[AskQuestions] = None,
        on_subagent: Optional[SubagentReport] = None,
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
        self._attachments = list(attachments or [])
        self._on_session = on_session
        self._on_started = on_started
        self._on_activity = on_activity
        self._on_complete = on_complete
        self._on_failed = on_failed
        self._on_done = on_done
        self._on_question = on_question
        self._on_subagent = on_subagent or ignore_subagents

        self._transport: Optional[StdioTransport] = None
        self._cancelled = False
        self._clean_end = False
        self._failed = False
        # bypassPermissions cannot be sent as MSP's allowAll -- the host
        # seals its approval ceiling at startup and refuses anything above it
        # (measured) -- so bypass is honoured by auto-approving each request
        # here. The person is never asked, which is what the mode means.
        self._auto_approve = permission_mode == "bypassPermissions"
        self._accepting_input = threading.Event()
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
        # Answer text seen so far per agentMessage item.
        self._answer_items: dict[str, str] = {}
        # Approval stages already answered, and each approval's tool name
        # (approval/updated does not repeat it). A prompt can arrive three
        # ways at once -- the server request, its notification twin, and the
        # pending list after a resume -- so both sets below are the dedup.
        self._answered_stages: set[str] = set()
        self._seen_user_inputs: set[str] = set()
        self._approval_tools: dict[str, str] = {}
        # Native subagents and workflow runs this turn has seen, by id, with
        # where each stands, so Stop can end the ones still running: a
        # turn/interrupt ends the parent turn, not the children it spawned.
        self._subagents: dict[str, str] = {}
        self._subagent_control: dict[str, str] = {}
        self._workflows: dict[str, str] = {}
        # Fired commands awaiting their reply: id -> (method, params, may retry).
        self._fired: dict[object, tuple[str, Optional[dict], bool]] = {}

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
            # A stop is asked, not forced: the server answers it, and the
            # turn/completed with terminal "cancelled" ends the loop. If the
            # process has already gone, there is nothing left to ask.
            # turn/interrupt is the "user pressed stop" gesture on the
            # runtime's priority lane; turn/cancel is the plain lane. Both
            # end the turn as cancelled (measured 1.4.0, mid-stream and
            # before the first token alike); the priority lane is what a
            # stop button is for. No retract is paired with it: none was
            # ever observed to come back as turn/retracted, so there is no
            # prompt-restoring signal to wait for. turnId names the exact
            # turn; omitting it would target the session's foreground turn,
            # which is the same one here.
            self._stop_children()
            self._fire(
                "turn/interrupt",
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

    def _fire(self, method: str, params: Optional[dict] = None, retry: bool = True) -> bool:
        """One request sent without waiting for its reply.

        Steering, cancelling, and answering questions all write from a thread
        that is not the turn loop's; none of them can afford the wait, and
        MSP answers them on the view stream, which the turn loop reads. They
        still carry an id: measured on 1.3.0, an id-less turn/interrupt or
        approval/decide is dropped without a word and the turn hangs.

        Each one is remembered until answered so an internal failure can be
        resent once; see _fired_reply.
        """
        transport = self._transport
        if transport is None or not transport.connected():
            return False
        request_id = self._next_id()
        self._fired[request_id] = (method, params, retry)
        return transport.send(self._rpc_frame(method, params, request_id))

    def _fired_reply(self, frame: dict) -> None:
        """A reply to a fired command: resend an internal failure once.

        Measured on 1.3.0 under load: approval/decide answered "approval
        ledger durability fence ... left records unflushed (internal)" while
        the decision had in fact been saved and the tool ran. Every fired
        command carries a commandId, and a resend with the same one is
        answered with the first result (measured), so one retry is safe and
        turns the false "refused" into the real outcome. Anything else, or a
        second failure, is said rather than lost.
        """
        method, params, retry = self._fired.pop(frame.get("id"), ("", None, False))
        error = frame.get("error")
        if error is None:
            return
        kind = error.get("data", {}).get("kind") if isinstance(error, dict) else None
        if retry and kind == "internal" and self._fire(method, params, retry=False):
            return
        self._on_activity("tool", f"Muse Code refused: {self._error_text(error)}")

    def _request(
        self, method: str, params: Optional[dict] = None, timeout: float = 45.0
    ) -> Optional[dict]:
        """One request, its reply awaited, streaming frames parked."""
        transport = self._transport
        if transport is None or not transport.connected():
            return None
        request_id = self._next_id()
        if not transport.send(self._rpc_frame(method, params, request_id)):
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

    def _fail(self, message: str) -> None:
        self._failed = True
        self._on_failed(message)

    def _detail(self) -> str:
        transport = self._transport
        return transport.failure_detail() if transport is not None else ""

    # -- the turn -----------------------------------------------------------

    def _do_run(self) -> None:
        try:
            transport = _muse_transport(self._cwd, unsandboxed=self._auto_approve)
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
        self._stash_usage()
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
        # The one true notification: given an id, the host answers it "Not
        # initialized" and refuses everything after (measured 1.3.0).
        if self._transport is not None:
            self._transport.send(self._rpc_frame("initialized", None))
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
                if self._model and self._model != session.get("modelId"):
                    # session/start is the only other place a model is sent;
                    # without this a picker change on a reopened
                    # conversation was silently ignored.
                    self._set_model()
                self._sync_approval_mode(session)
                self._drain_pending()
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

    def _set_model(self) -> None:
        """Switch a resumed session to the picked model; a refusal is said, not fatal."""
        reply = self._request(
            "session/setModel",
            {
                "commandId": _uuid(),
                "sessionId": self._live_session,
                "model": {"modelId": self._model},
            },
        )
        if reply is None or "error" in reply:
            reason = self._error_text(reply["error"]) if reply else "no answer"
            self._on_activity("tool", f"Muse Code kept its model ({reason})")

    def _sync_approval_mode(self, session: dict) -> None:
        """Bring a resumed session onto the window's permission mode.

        The mode is otherwise only sent at session/start, so a mode picked
        mid-conversation never reached the host: the next turn ran under the
        old one. A refusal is said rather than fatal -- bypass still
        auto-answers client-side, and anything else keeps asking as before.
        """
        live = session.get("approvalMode")
        current = live.get("mode") if isinstance(live, dict) else ""
        desired = _MUSE_APPROVAL_MODES.get(self._permission_mode, _MUSE_DEFAULT_MODE)
        if not current or current == desired:
            return
        reply = self._request(
            "session/setApprovalMode",
            {
                "commandId": _uuid(),
                "sessionId": self._live_session,
                "mode": desired,
            },
        )
        if reply is not None and "error" not in reply:
            return
        reason = self._error_text(reply["error"]) if reply else "no answer"
        self._on_activity("tool", f"Muse Code kept its approval mode ({reason})")

    def _drain_pending(self) -> None:
        """Answer whatever the conversation was parked on when it was reopened.

        A resume re-issues the unsettled prompts, but only to a host that is
        still subscribed when they fire; the pending list is the pull dual
        that cannot be missed, and it names approvals and questions alike.
        """
        reply = self._request(
            "approval/listPending", {"sessionId": self._live_session}, timeout=20.0
        )
        if reply is None or "result" not in reply:
            return
        result = reply["result"] or {}
        pending = result.get("approvals") or []
        for params in pending:
            if isinstance(params, dict):
                self._approval_requested(params)
        pending = result.get("userInputs") or []
        for params in pending:
            if isinstance(params, dict):
                self._user_input_requested(params)

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
        parts, display = self._turn_parts(text)
        if not parts:
            # An empty prompt would be rejected, and the turn loop would sit
            # waiting for an end that never comes.
            self._fail("Muse was given an empty prompt.")
            return False
        params: dict = {
            "commandId": _uuid(),
            "sessionId": self._live_session,
            "input": parts,
        }
        if display:
            params["displayText"] = display
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

    def _turn_parts(self, text: str) -> tuple[list[dict], str]:
        """The turn/start input parts, plus the transcript display text.

        Images travel as base64 parts the model actually sees; anything else
        attached is named in the text by its path, translated for the WSL
        distribution Muse runs in on Windows. A leading /skill the CLI knows
        becomes a skill part -- the wire twin of what the TUI accepts after
        the shortcut token -- with the typed line kept as the display text so
        the transcript still shows what was asked.
        """
        images: list[dict] = []
        named: list[str] = []
        for path in self._attachments:
            part = muse_image_part(path)
            if part is not None:
                images.append(part)
            elif str(path or "").strip():
                named.append(_host_path(str(path).strip()))
        if text.startswith("/"):
            invocation = _skill_invocation(text, self._cwd)
            if invocation is not None:
                selector, arguments = invocation
                skill: dict = {"type": "skill", "selector": selector}
                if arguments:
                    skill["arguments"] = arguments
                return [skill, *images], text
        if named:
            listing = "\n".join(named)
            text = (
                f"{text}\n\nAttached files (please read them):\n{listing}"
                if text
                else f"Attached files (please read them):\n{listing}"
            )
        parts: list[dict] = [{"type": "text", "text": text}] if text else []
        parts.extend(images)
        display = ""
        if self._attachments:
            names = ", ".join(
                _file_name(path) for path in self._attachments if str(path or "").strip()
            )
            suffix = f"[Attached: {names}]" if names else "[Attached files]"
            original = (self._prompt or "").strip()
            display = f"{original}\n{suffix}" if original else suffix
        return parts, display

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
            # the view stream, which this loop is already reading; a refusal
            # does not, so it is handled here rather than lost.
            self._fired_reply(frame)
            return False
        method = str(frame.get("method") or "")
        params = frame.get("params") or {}
        if "id" in frame and method:
            self._receipt(frame["id"], method)

        if method == "item/started":
            self._item_started(params.get("item"))
        elif method == "item/delta":
            self._item_delta(params)
        elif method == "item/completed":
            self._item_completed(params.get("item"))
        elif method == "item/updated":
            self._item_updated(params.get("item"))
        elif method in ("approval/requested", "approval/updated", "approval/request"):
            # The request and its notification twin carry the same full
            # params; the answered-stages set keeps the twin from asking
            # twice, and the request twin is the one a resume re-issues.
            self._approval_requested(params)
        elif method in ("userInput/requested", "userInput/request"):
            self._user_input_requested(params)
        elif method == "turn/completed":
            self._turn_completed(params)
            return True
        elif method == "turn/retryScheduled":
            self._on_activity("tool", "Muse Code is retrying the turn")
        elif method == "turn/retracted":
            self._on_activity("tool", "The turn was retracted before anything was sent")
        elif method == "turn/unqueued":
            self._on_activity("tool", "The queued turn was removed")
        elif method == "session/approvalModeChanged":
            mode = str(params.get("mode") or "")
            self._on_activity(
                "tool", f"Approval mode is now {mode}" if mode else "Approval mode changed"
            )
        elif method == "usage/changed":
            note_muse_usage(params.get("usage"))
        # Turn, session, skill, and view bookkeeping (turn/started, token and
        # context usage, name and branch changes, todo lists, gaps) carries
        # no words for the transcript and is ignored on purpose.
        return False

    def _stash_usage(self) -> None:
        """Remember what this host observed of the subscription, for /status.

        Asked on the live connection before it closes: a fresh host observes
        nothing, which is why the status report cannot ask for itself.
        """
        if self._cancelled or self._failed:
            return
        reply = self._request("usage/read", None, timeout=10.0)
        if reply is not None and "result" in reply:
            note_muse_usage((reply["result"] or {}).get("usage"))

    def _receipt(self, request_id: object, method: str) -> None:
        """Answer a request the host sent us, as MSP requires of a client.

        approval/request and userInput/request are "must-answer": the reply
        is an empty presentation receipt, and the decision itself still
        travels as approval/decide or userInput/answer, driven by the
        matching .../requested notification. Anything else is not ours.
        """
        transport = self._transport
        if transport is None:
            return
        reply: dict = {"jsonrpc": "2.0", "id": request_id}
        if method in ("approval/request", "userInput/request"):
            reply["result"] = {}
        else:
            reply["error"] = {"code": -32601, "message": f"{method} is not supported"}
        transport.send(reply)

    # -- items --------------------------------------------------------------

    def _item_started(self, item: object) -> None:
        if not isinstance(item, dict):
            return
        self._track_children(item)
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
                    subject = _args_subject(parsed_args)
            except ValueError:
                subject = ""
            if not subject:
                subject = _item_text(item).strip()
            self._on_activity("tool", f"{name}: {subject}" if subject else name)
        elif kind not in ("userMessage", "reasoning", "agentMessage", "reminderChild"):
            # The user's own message is already on the transcript, reasoning
            # and the answer are said as their text arrives, and a
            # reminderChild is Muse's internal housekeeping (measured 1.3.0:
            # two per turn, "Reminder child session"). Anything
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
        # A reasoning item streams its summary as "summary.0", "summary.1"...
        # (measured 1.3.0); taken for answer text, it was spoken and saved as
        # the reply.
        if field.startswith(("reasoning", "summary")):
            self._on_activity("thinking", delta)
            return
        if field.startswith("output") or item_id in self._tool_names:
            self._tool_output_parts.setdefault(item_id, []).append(delta)
            return
        # Assistant answer text, streamed a fragment at a time. Held until it
        # completes a sentence so the screen reader never reads torn words.
        self._answer_part(item_id, delta)
        self._release_streamed()

    def _answer_part(self, item_id: str, text: str) -> None:
        """Add answer text, a blank line apart from an earlier message's."""
        if item_id not in self._answer_items:
            self._answer_items[item_id] = ""
            if self._assistant_parts:
                self._assistant_parts.append("\n\n")
        self._answer_items[item_id] += text
        self._assistant_parts.append(text)

    def _item_completed(self, item: object) -> None:
        if not isinstance(item, dict):
            return
        self._track_children(item)
        kind = str(item.get("kind") or "")
        item_id = str(item.get("itemId") or "")
        if kind == "agentMessage":
            text = _item_text(item)
            streamed = self._answer_items.get(item_id, "")
            # The whole message adds only what its deltas did not already
            # carry: resetting to it re-spoke every streamed sentence, and
            # dropped any earlier message of the same turn.
            if text.startswith(streamed) and text[len(streamed) :].strip():
                self._answer_part(item_id, text[len(streamed) :])
            if item.get("truncated"):
                self._recover_truncated_answer()
            self._release_all()
        elif kind == "toolCall":
            name = self._tool_names.get(item_id, str(item.get("tool") or "tool"))
            result_text = (
                _item_text(item)
                or str(item.get("visibleOutput") or "")
                or "".join(self._tool_output_parts.get(item_id, []))
            )
            full = self._full_output(item)
            if full:
                result_text = full
            # write_todos answers bookkeeping JSON; its row already lists the todos.
            if result_text.strip() and name != "write_todos":
                self._on_activity("result", f"{name}: {result_text.strip()}")
            self._tool_output_parts.pop(item_id, None)
        elif kind == "reasoning":
            text = _item_text(item)
            if text.strip():
                self._on_activity("thinking", text.strip())
        elif kind == "compaction":
            self._on_activity("tool", "Conversation compacted")
        elif kind == "userShell":
            text = _item_text(item).strip() or str(item.get("visibleOutput") or "").strip()
            full = self._full_output(item)
            if full:
                text = full.strip()
            first = text.splitlines()[0][:200] if text else ""
            if first:
                self._on_activity("tool", f"userShell: {first}")
            else:
                self._on_activity("tool", str(item.get("status") or "userShell"))
        elif kind in ("subagent", "workflow"):
            # A delegated run finished: its first line is the outcome, and a
            # bare kind is still better than a turn that went quiet.
            text = _item_text(item).strip()
            first = text.splitlines()[0][:200] if text else ""
            status = str(item.get("status") or "").strip()
            if first:
                self._on_activity("tool", f"{kind}: {first}")
            elif status:
                self._on_activity("tool", f"{kind}: {status}")
            else:
                self._on_activity("tool", kind)

    def _full_output(self, item: dict) -> str:
        """The complete result of a truncated tool call, or "" to keep the text.

        Only attempted when the item says it was truncated and names servable
        bytes; anything else -- and any fetch that fails -- keeps its visible
        text instead of stalling the turn on a second request.
        """
        if not item.get("truncated"):
            return ""
        ref = item.get("outputRef")
        if not isinstance(ref, dict) or ref.get("availability") != "available":
            return ""
        ref_id = str(ref.get("id") or "")
        item_id = str(item.get("itemId") or "")
        if not ref_id or not item_id or not self._live_session:
            return ""
        reply = self._request(
            "item/readOutput",
            {
                "sessionId": self._live_session,
                "itemId": item_id,
                "outputRef": ref_id,
                "lengthBytes": _MAX_OUTPUT_FETCH_BYTES,
            },
            timeout=30.0,
        )
        if reply is None or "result" not in reply:
            return ""
        result = reply["result"] or {}
        content = result.get("content")
        if not isinstance(content, str) or not content:
            return ""
        if result.get("encoding") == "base64":
            # Binary media arrives base64; text media arrives utf8. The enum
            # is closed, so anything else is a future value to keep verbatim.
            try:
                text = base64.b64decode(content).decode("utf-8", "replace")
            except ValueError:
                return ""
        else:
            text = content
        if not result.get("eof", True):
            served = result.get("byteLen")
            total = ref.get("byteLen")
            if isinstance(served, int) and isinstance(total, int):
                text += f"\n[showing the first {served} of {total} bytes]"
            else:
                text += "\n[the rest of the output was not fetched]"
        return text

    def _recover_truncated_answer(self) -> None:
        """Append what the view budget clipped off the answer, from the log.

        A truncated agentMessage has no item/readOutput twin: the durable
        session log is the only place the full text survives. Read only when
        the flag says the streamed text is short, and merged only when the
        log extends it -- never duplicated, never guessed.
        """
        if not self._session_log_path:
            return
        turns = parse_muse_transcript(muse_session_log_text(self._session_log_path))
        if not turns:
            return
        full = turns[-1][1]
        current = "".join(self._assistant_parts)
        if full.startswith(current) and full[len(current) :].strip():
            self._assistant_parts.append(full[len(current) :])

    def _item_updated(self, item: object) -> None:
        """A non-terminal change to an open item: backgrounding, subagent news.

        Deltas cannot express these, so the host re-emits the whole item;
        only the changes worth interrupting the transcript for are said.
        """
        if not isinstance(item, dict):
            return
        self._track_children(item)
        kind = str(item.get("kind") or "")
        if kind == "toolCall" and item.get("background"):
            name = str(item.get("tool") or "tool")
            self._on_activity("tool", f"{name}: running in the background")
        elif kind == "subagent":
            status = str(item.get("controlStatus") or item.get("status") or "").strip()
            if status:
                self._on_activity("tool", f"Subagent {status}")

    def _track_children(self, item: dict) -> None:
        """Report a native subagent or a workflow's children to the window.

        A `subagent` item is re-emitted whole on every change, so each copy is
        compared with the last: the objective is said once, a control-state
        change once, and the result when the item ends. A `workflow` item
        folds all its children into one `children` list, each keyed by its
        (childId, attempt) pair.
        """
        kind = str(item.get("kind") or "")
        if kind == "toolCall" and str(item.get("tool") or "").startswith("subagent_"):
            self._subagent_tool(item)
        elif kind == "subagent":
            agent_id = str(item.get("subagentId") or item.get("itemId") or "")
            if not agent_id:
                return
            status = subagent_status(item.get("status"))
            known = agent_id in self._subagents
            if not known or status:
                self._subagents[agent_id] = status or SUBAGENT_RUNNING
            name = subagent_line(item.get("role") or item.get("objective") or "Subagent", 80)
            lines: list[str] = []
            if not known:
                objective = subagent_line(item.get("objective"))
                lines.append(f"Task: {objective}" if objective else "Started")
            control = str(item.get("controlStatus") or "")
            if control and control != self._subagent_control.get(agent_id):
                self._subagent_control[agent_id] = control
                if known:
                    lines.append(f"State: {control}")
            if status and status != SUBAGENT_RUNNING:
                result = item.get("result")
                summary = ""
                if isinstance(result, dict):
                    summary = subagent_line(result.get("summary") or result.get("text"))
                reason = subagent_line(item.get("failureReason"))
                lines.append(summary or reason or f"Finished: {status}")
            if not lines:
                lines.append("")
            # The name goes with the first line and the state with the last,
            # so an agent that finished says so with its result.
            last = len(lines) - 1
            for index, line in enumerate(lines):
                self._on_subagent(
                    agent_id, name if index == 0 else "", status if index == last else "", line
                )
        elif kind == "workflow":
            run_id = str(item.get("workflowRunId") or item.get("itemId") or "")
            children = item.get("children")
            if not run_id or not isinstance(children, list):
                return
            running = False
            for child in children:
                if not isinstance(child, dict):
                    continue
                child_id = str(child.get("childId") or "")
                if not child_id:
                    continue
                agent_id = f"{run_id}:{child_id}"
                status = subagent_status(child.get("terminal") or child.get("status"))
                running = running or status in ("", SUBAGENT_RUNNING)
                phase = subagent_line(child.get("phase"))
                previous = self._subagents.get(agent_id)
                current = status or previous or SUBAGENT_RUNNING
                if previous == current and not phase:
                    continue
                self._subagents[agent_id] = current
                name = subagent_line(child.get("label") or child_id, 80)
                line = phase if phase and previous is not None else ""
                if previous is None:
                    line = f"Started{f': {phase}' if phase else ''}"
                elif current != previous and current != SUBAGENT_RUNNING:
                    line = f"Finished: {current}"
                self._on_subagent(agent_id, name, current, line)
            self._workflows[run_id] = SUBAGENT_RUNNING if running else "done"

    def _subagent_tool(self, item: dict) -> None:
        """Follow a native subagent through the parent's own subagent_* tools.

        Measured on 1.4.1: no `subagent` item reached the parent's stream. The
        model drives its children with tools instead -- subagent_spawn, whose
        output names the new child's `subagent_id` and whose args carry its
        objective and role, then subagent_wait, whose output says whether its
        result is ready and summarises it. Only a finished call carries an
        output, so the child is known from the spawn's completion on.
        """
        if str(item.get("status") or "") == "inProgress":
            return
        tool = str(item.get("tool") or "")
        args = _json_object(item.get("args"))
        output = _json_object(item.get("visibleOutput") or item.get("output"))
        agent_id = str(output.get("subagent_id") or args.get("subagent_id") or "")
        if not agent_id:
            return
        state = str(output.get("status") or "")
        if tool == "subagent_spawn":
            if agent_id in self._subagents:
                return
            if state in ("rejected", "failed", "error"):
                self._subagents[agent_id] = SUBAGENT_FAILED
                self._on_subagent(
                    agent_id, "Subagent", SUBAGENT_FAILED, f"Could not start: {state}"
                )
                return
            self._subagents[agent_id] = SUBAGENT_RUNNING
            name = subagent_line(args.get("role") or args.get("objective") or "Subagent", 80)
            objective = subagent_line(args.get("objective"))
            self._on_subagent(
                agent_id, name, SUBAGENT_RUNNING, f"Task: {objective}" if objective else "Started"
            )
            return
        summary = subagent_line(output.get("summary"))
        if not summary and not state:
            # A call that ended without an answer -- a wait cut short by Stop
            # -- says nothing about the child.
            return
        # "ready" is the wait's word for a child whose result is in.
        status = SUBAGENT_COMPLETED if state == "ready" else subagent_status(state)
        if status and status != SUBAGENT_RUNNING:
            self._subagents[agent_id] = status
        verb = tool[len("subagent_") :].replace("_", " ")
        line = summary or (f"{verb}: {state}" if state else verb)
        self._on_subagent(agent_id, "", status if status != SUBAGENT_RUNNING else "", line)

    def _stop_children(self) -> None:
        """Stop every native subagent and workflow run still going.

        Stopping the parent turn does not stop what it spawned: the host
        keeps a child running until its owner closes it (subagent/stop) or
        the run is cancelled (workflow/cancel). Both are fired, not awaited,
        like the interrupt that follows them.
        """
        for agent_id, status in list(self._subagents.items()):
            if status != SUBAGENT_RUNNING or ":" in agent_id:
                continue
            self._fire(
                "subagent/stop",
                {
                    "commandId": _uuid(),
                    "sessionId": self._live_session,
                    "subagentId": agent_id,
                    "reason": "Stopped from BlindPilot",
                },
            )
        for run_id, status in list(self._workflows.items()):
            if status != SUBAGENT_RUNNING:
                continue
            self._fire(
                "workflow/cancel",
                {"commandId": _uuid(), "sessionId": self._live_session, "workflowRunId": run_id},
            )

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
        # A piped command is approved one stage at a time: the decide for
        # stage 0 is answered "terminal": false and stage 1 arrives as
        # approval/updated with a new currentRequirementId (measured 1.3.0).
        # Each stage is answered once; other updates repeat a stage. Scoped to
        # the approval because the request twin, its notification, and the
        # pending list all carry the same stage for the same approval.
        stage = json.dumps(params.get("currentRequirementId"), sort_keys=True)
        key = f"{approval_id}:{stage}"
        if key in self._answered_stages:
            return
        self._answered_stages.add(key)
        origin = params.get("subagentOrigin")
        if isinstance(origin, dict) and origin.get("subagentId"):
            # A child's approval, projected onto this session so it can be
            # answered here. Its log says what it asked for.
            tool = str(params.get("toolName") or self._approval_tools.get(approval_id) or "a tool")
            self._on_subagent(str(origin["subagentId"]), "", "", f"Asked permission for {tool}")
        if params.get("toolName"):
            self._approval_tools[approval_id] = str(params["toolName"])
        if self._cancelled:
            # A cancel arrived while the host was composing this request:
            # answering it would restart work the person just stopped.
            self._decide_approval(params, _refusal(params))
            return
        if self._auto_approve:
            self._decide_approval(
                params, _choice(params, (_MUSE_APPROVE_ONCE,)) or _refusal(params)
            )
            return
        choices = _choices(params)
        if self._on_question is None or not choices:
            # Nobody is here to answer: deny rather than leave the run wedged
            # on an approval nobody can see.
            self._decide_approval(params, _refusal(params))
            return
        tool = self._approval_tools.get(approval_id, "A tool")
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
        # The server's own choices, in its words. Measured on 1.3.0, a shell
        # approval offers allow once, always allow in this workspace, and
        # reject -- no session scope and no "denied" -- so a fixed menu
        # mapped onto decisions sent choice ids the host does not have.
        question = Question(
            question=f"{tool} wants permission" + (f": {detail}" if detail else ""),
            header="Approval",
            options=tuple(
                QuestionOption(label=_label(choice), description=str(choice.get("scope") or ""))
                for choice in choices
            ),
            allow_custom=False,
        )
        answers = self._on_question([question])
        if self._cancelled:
            # The person pressed Stop while the dialog was up. That stop wins
            # over whatever the still-open dialog was answered with: approving
            # here would let work continue past the cancel.
            self._decide_approval(params, _refusal(params))
            return
        pick = ((answers or [[]])[0] or [""])[0]
        chosen = next((c for c in choices if _label(c) == pick), None)
        self._decide_approval(params, str(chosen["choiceId"]) if chosen else _refusal(params))

    def _decide_approval(self, params: dict, choice_id: str) -> None:
        # currentRequirementId is the ApprovalRequirementRef object itself,
        # carried through verbatim: it is the multi-stage race guard, and a
        # decision aimed at one stage must never satisfy another.
        self._fire(
            "approval/decide",
            {
                "approvalId": params.get("approvalId"),
                "sessionId": params.get("sessionId") or self._live_session,
                "commandId": _uuid(),
                "choiceId": choice_id,
                "requirementId": params.get("currentRequirementId"),
            },
        )

    def _user_input_requested(self, params: dict) -> None:
        prompt_id = str(params.get("userInputId") or "")
        if prompt_id:
            if prompt_id in self._seen_user_inputs:
                return
            self._seen_user_inputs.add(prompt_id)
        elif not params.get("questions"):
            # A prompt with neither an id nor questions is a frame the host
            # never meant as one; cancelling it would answer a prompt nobody
            # asked with an id nobody holds.
            return
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
        if self._cancelled:
            # Stop was pressed while the dialog was up; answering would let
            # the work go on past it, the same rule approvals follow.
            self._cancel_input(params)
            return
        self._send_answers(params, questions, answers)

    def _send_answers(
        self,
        params: dict,
        questions: Sequence[Question],
        answers: Optional[list[list[str]]],
    ) -> None:
        # MSP wants exactly one of selectedLabel, selectedLabels or freeText
        # per question, labels from the offered options only, and answers
        # anything else -32057 while the prompt stays open -- so a closed
        # dialog, or a question left blank, used to wedge the turn, and a
        # typed "Other" answer was sent as a label the host has never heard of.
        body_answers = []
        for index, question in enumerate(questions):
            chosen = [str(c) for c in (answers[index] if answers and index < len(answers) else [])]
            chosen = [c for c in chosen if c.strip()]
            if not chosen:
                self._cancel_input(params)
                return
            labels = {option.label for option in question.options}
            picked = [c for c in chosen if c in labels]
            typed = "\n".join(c for c in chosen if c not in labels)[:500]
            entry: dict = {"questionId": question.id}
            if picked and question.multi_select:
                entry["selectedLabels"] = picked
            elif picked:
                entry["selectedLabel"] = picked[0]
            else:
                entry["freeText"] = typed
                typed = ""
            if typed:
                entry["note"] = typed
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
        if isinstance(items, list):
            for item in items:
                self._replay_item(item)
        else:
            # A long or compacted conversation is served as a snapshot or not
            # at all; the view is paged instead, which is what the protocol
            # tells a client to do when inline history is not what it got.
            self._page_replay()
        self._on_complete("")

    def _replay_item(self, item: object) -> None:
        if not isinstance(item, dict):
            return
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

    def _page_replay(self) -> None:
        """Read the transcript back a page at a time, oldest first."""
        cursor: Optional[str] = None
        for _ in range(_REPLAY_MAX_PAGES):
            params: dict = {"sessionId": self._live_session, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            reply = self._request("view/page", params, timeout=30.0)
            if reply is None or "result" not in reply:
                return
            result = reply["result"] or {}
            for event in result.get("events") or []:
                if not isinstance(event, dict):
                    continue
                if event.get("method") == "item/completed":
                    item = (event.get("params") or {}).get("item")
                    self._replay_item(item)
            cursor = result.get("nextCursor")
            if not cursor:
                return

    # -- streaming helpers --------------------------------------------------

    def _emit_answer(self, text: str) -> None:
        self._on_activity("assistant", text)

    def _release_streamed(self) -> None:
        text = "".join(self._assistant_parts)
        self._streamed = release_finished(text, self._streamed, self._emit_answer)

    def _release_all(self) -> None:
        text = "".join(self._assistant_parts)
        self._streamed = release_remainder(text, self._streamed, self._emit_answer)


def _skill_invocation(text: str, cwd: str) -> Optional[tuple[str, str]]:
    """(selector, arguments) when the prompt calls a Muse skill, else None.

    Only the first token counts, and only when the CLI lists it: a path that
    happens to start with a slash is text, not a call.
    """
    first, _, rest = text.strip().partition(" ")
    token = first[1:]
    if not token:
        return None
    selectors = {name for name, _description in muse_skills(cwd)}
    if token not in selectors:
        return None
    return token, rest.strip()


def _host_path(path: str) -> str:
    """An attached file's path as the Muse host sees it.

    On Windows the host runs inside WSL, where a Windows path names nothing;
    everywhere else the path is already what the host expects.
    """
    if platform.system() != "Windows":
        return path
    return windows_path_to_wsl(path)


def _file_name(path: str) -> str:
    """The bare filename, whichever platform wrote the path."""
    text = str(path or "").strip().strip('"').rstrip("\\/")
    for sep in ("\\", "/"):
        text = text.rsplit(sep, 1)[-1]
    return text or str(path or "").strip()


def _choices(params: dict) -> list[dict]:
    """The approval's availableChoices; choiceId must be one of these (-32052)."""
    return [
        choice
        for choice in (params.get("availableChoices") or [])
        if isinstance(choice, dict) and choice.get("choiceId")
    ]


def _label(choice: dict) -> str:
    return str(choice.get("label") or choice["choiceId"])


def _choice(params: dict, decisions: Sequence[str]) -> str:
    """The server's choiceId for the first of ``decisions`` it offers, or ""."""
    for decision in decisions:
        for choice in _choices(params):
            if str(choice.get("decision") or "") == decision:
                return str(choice["choiceId"])
    return ""


def _refusal(params: dict) -> str:
    """The choice that cannot push work forward: deny, else abort."""
    return _choice(params, _MUSE_REFUSALS) or "denied"


def _args_subject(args: dict) -> str:
    """What a tool call is about, from its args (measured 1.3.0: search sends
    ``pattern``, write_todos a ``todos`` list of {text, status})."""
    for key in ("command", "path", "pattern", "query", "url"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    todos = args.get("todos")
    if isinstance(todos, list):
        return "; ".join(
            str(todo.get("text") or "").strip()
            for todo in todos
            if isinstance(todo, dict) and str(todo.get("text") or "").strip()
        )
    return ""


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
