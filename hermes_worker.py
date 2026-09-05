"""The Hermes turn worker: one BlindPilot turn over Hermes' JSON-RPC gateway.

Split from ``hermes_backend.py`` so the transport question (how bytes travel)
stays separate from the conversation question (what a turn looks like).

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
Based on the original Claude Code Reader application by doubletaponair:
https://github.com/doubletaponair/claude-code-reader
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import threading
import time
from typing import Callable, Optional, Sequence

from agent_backends import AskQuestions, Question, QuestionOption, question_summary
from hermes_backend import (
    StdioTransport,
    Transport,
    WebSocketTransport,
    split_model_row,
    hermes_installed,
)
from markdown_rows import complete_sentences as _complete_sentences

# How long a single read may block. Short enough that cancel is felt promptly,
# long enough not to spin: the loop simply re-reads until the turn ends.
_READ_TIMEOUT = 0.5

# A turn that produces nothing at all for this long is treated as lost. Hermes
# emits usage and tool events throughout real work, so silence this long means
# the peer went away without closing the connection - the failure mode of a
# dropped network link, which the local pipe never has.
_IDLE_LIMIT = 900.0

# How often a turn that has gone quiet says so. A long turn is normal work --
# a build, a test run, a model thinking, a rate limit being waited out -- but
# from the outside it is indistinguishable from a hang, and a listener cannot
# glance at a screen to check. So the wait is narrated rather than left silent:
# the turn says how long it has been working and, when it knows, what it is
# waiting for.
_PROGRESS_NOTICE_SECONDS = 60.0


# The clock the turn loop measures quiet against, as a module attribute so a
# test can substitute one that advances without waiting. A wait of minutes has
# to be exercised in milliseconds, and the loop reads real elapsed time rather
# than counting reads -- see _consume_turn for why counting was wrong -- so
# there is nothing left to scale down except the clock itself.
def _now() -> float:
    return time.monotonic()


# How often the connection itself is checked while a turn waits. A held
# connection can die between frames (a server restart, a laptop lid, a network
# drop), and without this the turn would sit out the whole idle limit before
# saying anything -- fifteen minutes of silence that looks exactly like work.
_CONNECTION_CHECK_SECONDS = 15.0

# A turn that has produced NOTHING for this long gets a one-time diagnosis
# instead of only the generic still-working notices. Measured on a live
# gateway: a provider that is rate-limited or out of credits makes Hermes
# grind through backoff and fallbacks it mostly does not narrate, so the
# listener cannot tell a real hang from an account problem -- and the two
# have different remedies. The diagnosis names the likely causes so the
# remedy is the next thing heard.
_SILENCE_DIAGNOSTIC_SECONDS = 120.0
_SILENCE_DIAGNOSTIC_MESSAGE = (
    "Hermes has been silent for 2 minutes. It may be rate-limited or out of "
    "credits, or another Hermes session may be using the same account. "
    "Pick a different model with /model if this continues."
)

# Hermes decorates some status lines for a terminal: "\u26a0\ufe0f Model fallback: ..."
# and "\u26a0 Auxiliary title generation failed: ...". A screen reader reads the
# symbol as "warning sign" before every sentence -- noise on every one of these
# lines -- so the decoration is dropped and the words kept.
_LEADING_SYMBOLS_RE = re.compile(r"^[\W_]+\s*")


def _clean_status_text(text: str) -> str:
    """A status line without the terminal decorations that prefix it."""
    stripped = text.strip()
    return _LEADING_SYMBOLS_RE.sub("", stripped) or stripped


def _how_long(seconds: float) -> str:
    """A wait in words a listener can take in, to the minute."""
    minutes = int(seconds // 60)
    if not minutes:
        return "under a minute"
    return f"{minutes} minute{'' if minutes == 1 else 's'}"


# Hermes advertises its permission surface as slash commands and config rather
# than per-turn flags, so BlindPilot's picker maps onto the closest Hermes
# behaviour: whether this session may act without asking.
_MODE_TO_YOLO = {
    "bypassPermissions": True,
    "auto": True,
    "dontAsk": True,
    "acceptEdits": False,
    "default": False,
    "plan": False,
}

# How large a single attachment may be. Hermes caps a one-shot upload frame
# well above this, but a file this big is not something a person is asking to
# have read aloud: it is a mistake, and the honest answer is to say so before
# spending a minute encoding it.
ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024

# Media types for the file kinds a person actually attaches. Named here because
# ``mimetypes`` consults the Windows registry, and on a Windows Python it did
# not know ``.xlsx``: the same spreadsheet would then be described as a generic
# byte stream on one machine and as a spreadsheet on another.
_ATTACHMENT_MIME_TYPES = {
    ".csv": "text/csv",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".json": "application/json",
    ".log": "text/plain",
    ".md": "text/markdown",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".pdf": "application/pdf",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".rtf": "application/rtf",
    ".txt": "text/plain",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xml": "application/xml",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".zip": "application/zip",
}


def attachment_name(path: str) -> str:
    """The bare filename of an attachment, whichever platform wrote the path.

    Measured on a live gateway: Hermes stores the uploaded file under the
    ``name`` it is given, and a Linux gateway does not read a backslash as a
    separator -- so handing it a Windows path unchanged produces a file called
    ``D:\\projekty\\report.xlsx``, one long name rather than a name in a folder.
    Splitting on both separators here is what keeps the stored file called
    ``report.xlsx`` no matter which side of the WSL boundary the user picked it
    from.
    """
    text = str(path or "").strip().strip('"').rstrip("\\/")
    if not text:
        return ""
    for sep in ("\\", "/"):
        text = text.rsplit(sep, 1)[-1]
    return text


def attachment_data_url(path: str) -> str:
    """Read a file and wrap its bytes as a ``data:`` URL for ``file.attach``.

    The media type is a hint only -- Hermes stores the bytes and lets its file
    tools read them -- but a truthful one costs nothing and makes the frame
    readable in a log.

    The common document types are named here rather than left to
    ``mimetypes``, which reads the Windows registry: measured on a Windows
    Python, ``.xlsx`` came back unknown, so the same file would be described
    differently on two machines for no reason anyone can see.
    """
    with open(path, "rb") as handle:
        payload = handle.read()
    suffix = os.path.splitext(path)[1].lower()
    mime = _ATTACHMENT_MIME_TYPES.get(suffix) or mimetypes.guess_type(path)[0]
    mime = mime or "application/octet-stream"
    return f"data:{mime};base64," + base64.b64encode(payload).decode("ascii")


class AttachmentError(Exception):
    """An attachment could not be sent, with a reason worth reading aloud."""


def _size_text(size: int) -> str:
    """A file size a listener can take in, rather than a count of bytes."""
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} bytes"


def _same_directory(left: str, right: str) -> bool:
    """Whether two directory strings name the same place, as best we can tell.

    Deliberately textual: one of the two comes from ANOTHER machine, so
    ``os.path.samefile`` cannot be asked -- the path may not exist here at all,
    and a Windows client comparing against a Linux server has neither the same
    separator nor the same case rules. Slashes are unified and a trailing one
    dropped so ``/srv/app`` and ``/srv/app/`` do not read as a relocation, and
    the comparison is case-insensitive because a Windows path that came back
    unchanged may differ only in case.
    """

    def flatten(value: str) -> str:
        text = (value or "").strip().replace("\\", "/").rstrip("/")
        return text.casefold()

    return flatten(left) == flatten(right)


def check_attachment(path: str) -> int:
    """Size of an attachment, or ``AttachmentError`` saying why it cannot go.

    Refusing early, by name and reason, is the difference between a message a
    screen reader user can act on and a turn that simply answers about a file
    the model never received.
    """
    name = attachment_name(path) or path
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise AttachmentError(f"{name} could not be read: {exc.strerror or exc}") from exc
    if size == 0:
        raise AttachmentError(f"{name} is empty")
    if size > ATTACHMENT_MAX_BYTES:
        mb = ATTACHMENT_MAX_BYTES // (1024 * 1024)
        raise AttachmentError(
            f"{name} is too large to send ({size // (1024 * 1024)} MB; the limit is {mb} MB)"
        )
    return size


def _first_text(*candidates: object) -> str:
    """The first candidate that is a non-empty string."""
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return ""


def _clarify_questions(payload: dict) -> list[Question]:
    """Hermes' clarify payload, in either shape, as questions the window asks.

    Hermes emits two different payloads on the one event. A single question
    carries ``question`` and ``choices`` at the top level, and is answered by
    request id alone. A batch carries ``questions``, each entry with its own
    ``qid``, and is answered with one reply per qid. Reading only the first
    shape is what left a real question showing as the fallback wording, with
    the choices Hermes offered never reaching the person deciding.
    """
    batch = payload.get("questions")
    if isinstance(batch, list) and batch:
        found = []
        for entry in batch:
            if not isinstance(entry, dict):
                continue
            question = _clarify_question(entry, str(entry.get("qid") or ""))
            if question is not None:
                found.append(question)
        if found:
            return found
    single = _clarify_question(payload, "")
    return [single] if single is not None else []


def _clarify_question(entry: dict, qid: str) -> Optional[Question]:
    """One clarify entry as a question, or None when it carries no text."""
    asked = _first_text(entry.get("question"), entry.get("prompt"), entry.get("message"))
    if not asked:
        return None
    choices = entry.get("choices")
    options = tuple(
        QuestionOption(str(choice).strip())
        for choice in (choices if isinstance(choices, list) else [])
        if str(choice).strip()
    )
    return Question(
        question=asked,
        options=options,
        # Hermes only honours multi-select where it offered choices to select
        # between, so neither does this -- a free-text question marked
        # multi-select would offer a checkbox list with nothing in it.
        multi_select=bool(entry.get("multi_select")) and bool(options),
        id=qid,
    )


def _clarify_answer(question: Question, chosen: Sequence[str]) -> str:
    """One answer in the form Hermes' clarify tool parses.

    Multi-select goes as a JSON array. The tool accepts a JSON array or a
    comma-separated string, and only the array survives an answer that itself
    contains a comma -- which the free-text row makes possible for any
    question, not just the ones whose own choices contain one.
    """
    picked = [str(value) for value in chosen if str(value).strip()]
    if not picked:
        return ""
    return json.dumps(picked) if question.multi_select else picked[0]


# Hermes' ``thinking.delta`` carries its terminal spinner - a kawaii face and a
# random verb ("(⌐■_■) contemplating...") drawn from agent/display.py - not the
# model's reasoning. It exists to show a sighted user that work is happening.
# Spoken aloud it is pure noise, and the real reasoning arrives separately in
# ``reasoning.available``, so the spinner is dropped rather than read out.
_SPINNER_VERBS = (
    "pondering",
    "contemplating",
    "musing",
    "cogitating",
    "ruminating",
    "deliberating",
    "mulling",
    "reflecting",
    "processing",
    "reasoning",
    "analyzing",
    "computing",
    "synthesizing",
    "formulating",
    "brainstorming",
)


def _is_spinner_text(text: str) -> bool:
    """Whether a thinking delta is Hermes' spinner rather than real reasoning.

    Matched on shape, not on an exact list of faces: a skin can replace the
    faces, but the trailing "verb..." is what the spinner always looks like,
    and real reasoning is prose that does not end that way.
    """
    stripped = text.strip()
    if not stripped:
        return True
    if not stripped.endswith("..."):
        return False
    tail = stripped[:-3].strip().rsplit(" ", 1)[-1].lower()
    return tail in _SPINNER_VERBS


# How much of a tool result becomes a row. A screen reader reads a row line by
# line, so an unbounded result is not merely untidy - it buries the answer.
_RESULT_MAX_LINES = 40
_RESULT_MAX_CHARS = 4000

# Keys Hermes' own tools use for the part of a result a person wants to hear,
# in the order they are worth trying.
_RESULT_TEXT_KEYS = ("output", "content", "text", "summary", "stdout", "message", "result")


def _describe_tool_result(result: object) -> str:
    """Render a tool result as something worth reading aloud.

    Most Hermes tools answer with a decoded object rather than a string, so
    the interesting part has to be picked out: the output of a command, the
    text of a file, the error that explains a failure. Dumping the raw JSON
    would technically be complete and practically unusable.
    """
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, dict):
        parts: list[str] = []
        for key in _RESULT_TEXT_KEYS:
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
                break
        # An error and a non-zero exit are the whole point of the row when
        # they are present, so they are reported even alongside output.
        error = result.get("error")
        if isinstance(error, str) and error.strip():
            parts.append(f"error: {error.strip()}")
        exit_code = result.get("exit_code")
        if isinstance(exit_code, int) and exit_code != 0:
            parts.append(f"exit code {exit_code}")
        if parts:
            return "\n".join(parts)
        # Nothing recognisable: say what came back rather than staying silent,
        # because a tool that ran and reported nothing reads as a hang.
        keys = ", ".join(sorted(str(k) for k in result)[:8])
        return f"finished ({keys})" if keys else "finished"
    if isinstance(result, list):
        return f"{len(result)} {'item' if len(result) == 1 else 'items'}"
    if result is None:
        return ""
    return str(result).strip()


def _message_text(message: dict) -> str:
    """The display text of one transcript message, whichever shape it arrived in."""
    text = message.get("text")
    if isinstance(text, str) and text.strip():
        return text
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    return ""


def _replay_rows(messages: list) -> list[tuple[str, str]]:
    """Turn a resume transcript into (kind, text) rows the window understands.

    The window's own rows are built from these: a user message becomes its
    "You:" row, an assistant message goes through the same Markdown segmenter
    a live answer does, and a tool call becomes the step line it would have
    announced live. Empty rows are dropped rather than spoken as gaps.
    """
    rows: list[tuple[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        if role == "user":
            text = _message_text(message)
            if text.strip():
                rows.append(("you", text))
            continue
        if role == "assistant":
            text = _message_text(message)
            if text.strip():
                rows.append(("assistant", text))
            continue
        if role == "tool":
            name = str(message.get("name") or "tool")
            context = str(message.get("context") or "").strip()
            rows.append(("tool", f"{name}: {context}" if context else name))
    return rows


class HeldConnection:
    """One connection kept across the turns of a single conversation.

    A worker is created per turn, and until now so was its connection: every
    message paid for a login, a handshake, and a session resume, and left the
    server reaping the abandoned session moments later. The cost is small
    (measured at about a tenth of a second) but the reaping is noise in the
    server's log and the reconnect is work for nothing.

    So the connection outlives the worker and belongs to the conversation. It is
    handed to each turn in turn, and only dropped when the conversation is
    closed, when the user cancels, or when it is found dead -- in which case the
    next turn opens a fresh one, exactly as before.

    One turn runs at a time in a tab, which is what makes a single shared
    connection safe here: the window refuses a second send while a worker is
    alive.
    """

    def __init__(self) -> None:
        self._transport: Optional[Transport] = None
        self._live_session = ""
        # The picker row the live session was last known to run, so the next
        # turn only asks for a switch when the pick has changed. Empty means
        # unknown, which the next turn treats as changed.
        self.model = ""

    def take(self) -> tuple[Optional[Transport], str]:
        """The connection to reuse and the live session id it was bound to.

        Returns ``(None, "")`` when there is nothing usable, which is the
        signal to connect from scratch.
        """
        transport = self._transport
        if transport is None:
            return None, ""
        if not transport.connected():
            self.drop()
            return None, ""
        return transport, self._live_session

    def keep(self, transport: Optional[Transport], live_session: str, model: str = "") -> None:
        self._transport = transport
        self._live_session = live_session
        self.model = model

    def drop(self) -> None:
        transport = self._transport
        self._transport = None
        self._live_session = ""
        self.model = ""
        if transport is not None:
            transport.close()


class HermesWorker(threading.Thread):
    """Run one Hermes turn, reporting it through BlindPilot's callbacks.

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
        remote_url: str = "",
        remote_token: str = "",
        remote_credential: str = "token",
        remote_username: str = "",
        held: Optional[HeldConnection] = None,
        attachments: Optional[Sequence[str]] = None,
        resume_only: bool = False,
        session_title: str = "",
        on_session: Callable[[str], None],
        on_started: Callable[[], None],
        on_activity: Callable[[str, str], None],
        on_complete: Callable[[str], None],
        on_failed: Callable[[str], None],
        on_done: Callable[[], None],
        # Answers Hermes' clarify, sudo and secret requests, each of which
        # blocks the agent until a reply arrives.
        on_question: Optional[AskQuestions] = None,
    ) -> None:
        super().__init__(daemon=True)
        self._prompt = prompt
        self._session_id = session_id
        self._cwd = cwd
        self._permission_mode = permission_mode
        self._model = model
        # A per-session override on session.create. It changes this
        # conversation only, never the profile's own setting.
        self._effort = effort
        # A name typed in the New Session dialog, sent on session.create only.
        # Empty means "let Hermes name it from the first message".
        self._session_title = (session_title or "").strip()
        self._compact = compact
        self._remote_url = remote_url
        self._remote_token = remote_token
        # Which credential name the remote server expects: its session token,
        # or a ticket minted after a password login.
        self._remote_credential = remote_credential
        # Only used when the credential is a password: Hermes asks for a
        # username at its login, and mints the WebSocket ticket from that.
        self._remote_username = remote_username
        # Where the connection lives between turns. Absent (the default) the
        # worker owns its own, so a caller that has not opted in keeps the old
        # connect-per-turn behaviour and nothing about it changes.
        self._held = held
        # Resume-only turns replay a stored conversation instead of adding to
        # it: the worker asks Hermes for the transcript (and, when the
        # conversation is live, the tail of its running turn) and ends without
        # sending anything. The window uses this to open a past conversation
        # -- including one that is running right now on the gateway.
        self._resume_only = resume_only
        # Files the user attached to this message. They live on the machine
        # BlindPilot runs on, which is not necessarily the machine Hermes runs
        # on, so their bytes are uploaded before the prompt goes out.
        self._attachments = [str(p) for p in (attachments or []) if str(p).strip()]
        # Whether this turn inherited a live connection rather than opening one.
        self._reused = False
        self._on_session = on_session
        self._on_started = on_started
        # Counted, not just called: the turn loop needs to know whether a frame
        # produced anything a listener would hear, so that housekeeping traffic
        # cannot pass for progress. Wrapping the callbacks keeps that count in
        # ONE place -- a new call site added later is counted automatically,
        # where a hand-maintained flag would quietly stop being accurate.
        self._rows_emitted = 0

        def counted_activity(kind: str, text: str) -> None:
            self._rows_emitted += 1
            on_activity(kind, text)

        def counted_complete(text: str) -> None:
            self._rows_emitted += 1
            on_complete(text)

        self._on_activity = counted_activity
        self._on_complete = counted_complete
        self._on_failed = on_failed
        self._on_done = on_done
        # Called by the clarify and secret handlers; None means answer empty.
        self._on_question = on_question
        self._transport: Optional[Transport] = None
        self._cancelled = False
        # Set when the turn's end arrives while a reply is still awaited, so
        # the caller waiting on that reply does not report a second ending.
        self._ended = False
        # Set only by a turn that ended the way it should. Anything else, the
        # idle limit, an error event, a refused prompt, may leave the server
        # still working, and its late frames must not reach the next worker.
        self._clean_end = False
        # The picker row the live session is known to run; see HeldConnection.
        self._session_model = ""
        self._accepting_input = threading.Event()
        self._request_id = 100
        self._gateway_session = session_id or ""
        # The per-process session id Hermes addresses turns with. Distinct from
        # the stored id above, which is what survives a restart.
        self._live_session = ""
        self._answer_parts: list[str] = []
        # How much of the answer has already been handed to the window. Hermes
        # streams the answer in fragments of a few characters, and a fragment is
        # usually half a word: released as it arrives, a screen reader reads
        # torn words. So a fragment is held until it completes a sentence, and
        # only whole sentences are released while the turn is still running.
        self._streamed = 0
        self._thinking_parts: list[str] = []
        # Hermes sends the turn's finished reasoning as one replacement event;
        # it wins over the streamed fragments when both arrive.
        self._reasoning = ""
        # Hermes reports a tool's name at start and again at completion; the
        # name is kept so a result row can say which tool produced it.
        self._tool_names: dict[str, str] = {}
        # The last step Hermes reported, so a turn that goes quiet can say what
        # it is quiet ON. "Still working on terminal" tells a listener the run
        # is alive and where it is; "still working" only tells them the first.
        self._last_step = ""
        # Whether the silence diagnosis has been spoken. One per turn: once the
        # listener has been told what a quiet turn is probably waiting on,
        # repeating the same sentence every minute is noise, not news.
        self._silence_diagnosed = False

    # -- public surface the window drives ---------------------------------

    def accepting_input(self) -> bool:
        return self._accepting_input.is_set() and not self._cancelled

    def steer(self, text: str) -> bool:
        """Push guidance into the turn that is already running."""
        if not self.accepting_input() or not (self._live_session or self._gateway_session):
            return False
        # The live session id is the one Hermes answers to: steering and
        # interrupting by the stored id always came back "session not found"
        # (measured on a live gateway), so a steer in remote mode silently did
        # nothing. Fall back to the stored id only while no live id is known,
        # which is the local stdio path before its first reply.
        target = self._live_session or self._gateway_session
        return self._request("session.steer", {"session_id": target, "text": text})

    def cancel(self) -> None:
        self._cancelled = True
        self._accepting_input.clear()
        target = self._live_session or self._gateway_session
        if target:
            # Ask Hermes to stop the turn before dropping the connection, so a
            # remote Hermes is not left working on an answer nobody will read.
            # Addressed by the live id -- see steer() for why the stored one
            # cannot be used.
            self._request("session.interrupt", {"session_id": target})
        # A cancelled turn leaves the connection mid-conversation: the interrupt
        # is answered by frames this worker will not read. Reusing it would hand
        # those to the next turn, so the connection goes with the cancellation.
        if self._held is not None:
            self._held.drop()
        transport = self._transport
        # Closing a local gateway can wait two seconds on the process. While
        # the worker thread is running that wait is its job; run() closes on
        # its way out as soon as it sees the cancellation.
        if transport is not None and not self.is_alive():
            transport.close()

    def run(self) -> None:
        try:
            self._do_run()
        finally:
            self._accepting_input.clear()
            transport = self._transport
            # Hand the connection back for the next turn instead of closing it,
            # unless the turn was cancelled, ended abnormally, or the
            # connection did not survive.
            keep = (
                self._held is not None
                and not self._cancelled
                and self._clean_end
                and transport is not None
                and transport.connected()
                and bool(self._live_session)
            )
            if keep:
                self._held.keep(transport, self._live_session, self._session_model)  # type: ignore[union-attr]
                if self._cancelled:
                    # cancel() ran between the check above and the hand-over
                    # and left the close to this thread.
                    self._held.drop()  # type: ignore[union-attr]
            elif transport is not None:
                transport.close()
                if self._held is not None:
                    self._held.keep(None, "")
            self._on_done()

    # -- protocol plumbing -------------------------------------------------

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _send(self, method: str, params: dict) -> Optional[int]:
        """Send one request. Returns the id to await, or None when it could not go."""
        transport = self._transport
        if transport is None:
            return None
        request_id = self._next_id()
        frame = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        return request_id if transport.send(frame) else None

    def _request(self, method: str, params: dict) -> bool:
        return self._send(method, params) is not None

    def _await_frame(self, wanted: Callable[[dict], bool], timeout: float) -> Optional[dict]:
        """Wait for a frame ``wanted`` accepts, handling the events around it.

        Timed against the clock rather than by counting empty reads, so a peer
        that streams frames but never answers still runs out of time. Ends
        early when the connection is gone or the turn ends first.
        """
        deadline = _now() + timeout
        while _now() < deadline and not self._cancelled:
            transport = self._transport
            if transport is None:
                return None
            frame = transport.receive(_READ_TIMEOUT)
            if frame is None:
                if not transport.connected():
                    return None
                continue
            if wanted(frame):
                return frame
            if self._handle_event(frame) is True:
                # The turn ended with a reply still owed on this connection.
                # The caller must not report a second ending, and the
                # connection is not handed on, because that reply lands on
                # whoever reads it next.
                self._ended = True
                self._clean_end = False
                return None
        return None

    def _await_response(self, request_id: int, timeout: float) -> Optional[dict]:
        return self._await_frame(lambda frame: frame.get("id") == request_id, timeout)

    def _call(
        self, method: str, params: dict, timeout: float, no_reply: str, refused: str
    ) -> Optional[dict]:
        """Send one request and wait for its result.

        Returns the reply's ``result`` (a dict, possibly empty), or None after
        reporting the failure. Nothing is reported when the turn was cancelled
        or ended on its own while the reply was outstanding.
        """
        transport = self._transport
        if transport is None:
            return None
        request_id = self._send(method, params)
        reply = self._await_response(request_id, timeout) if request_id is not None else None
        if reply is None:
            if not self._cancelled and not self._ended:
                self._on_failed(transport.failure_detail() or no_reply)
            return None
        error = reply.get("error")
        if error:
            self._on_failed(self._error_text(error, refused))
            return None
        result = reply.get("result")
        return result if isinstance(result, dict) else {}

    def _open_transport(self) -> bool:
        """Connect, local or remote. Reports its own failure and returns False.

        A connection held from an earlier turn is reused when it is still
        healthy, which also carries its live session id: that turn already
        claimed the conversation, so this one has nothing to create or resume.
        """
        if self._held is not None:
            transport, live_session = self._held.take()
            if transport is not None:
                self._transport = transport
                self._live_session = live_session
                self._session_model = self._held.model
                self._reused = True
                return True
        if self._remote_url:
            transport = WebSocketTransport(
                self._remote_url,
                self._remote_token,
                self._remote_credential,
                self._remote_username,
            )
        else:
            if not hermes_installed():
                self._on_failed(
                    "Hermes Agent is not installed. See https://hermes-agent.nousresearch.com/docs"
                )
                return False
            transport = StdioTransport(self._cwd)
        try:
            transport.start()
        except OSError as exc:
            self._on_failed(str(exc))
            return False
        self._transport = transport
        return True

    def _do_run(self) -> None:
        if self._compact and not self._session_id:
            self._on_failed("There is no Hermes conversation to compact yet")
            return
        if not self._open_transport():
            return

        # A reused connection is past all of this: the gateway announced itself
        # on the turn that opened it, and that turn already holds the session.
        if not self._reused:
            # Hermes announces itself before accepting work; waiting for that is
            # how a client knows the far end is a Hermes gateway at all, which on
            # the remote path is the difference between "wrong address" and "slow".
            if self._wait_for_ready() is False:
                return
            if self._resume_only:
                self._run_replay()
                return
            if not self._ensure_session():
                return
        else:
            # The model and reasoning level ride on session.create, so a
            # conversation already under way would keep whatever it started
            # with -- picking a new one mid-conversation would announce a
            # change that never happened. session.resume takes neither, so the
            # change is applied to the live session the way Hermes' own hosts
            # do it: through its slash commands.
            self._apply_live_selection()
        if self._compact:
            self._run_compaction()
            return
        self._run_turn()

    def _apply_live_selection(self) -> None:
        """Move a reused session onto the currently picked model.

        The reasoning level is deliberately NOT sent here. Hermes takes it on
        session.create only: session.resume has no such parameter, and its
        ``/reasoning`` slash command runs in a worker process of its own whose
        result is never mirrored onto the gateway's live agent -- measured, and
        worth stating because that command answers with a tick either way. So
        sending it would report a change that did not happen. A new level
        therefore applies from the next conversation, which is what the window
        says when the level is picked.

        Best-effort by design: a refused switch must not lose the message the
        user typed. The reply is reported as activity so a wrong model name is
        heard rather than silently ignored.
        """
        if not self._model or self._model == self._session_model:
            return
        provider, model = split_model_row(self._model)
        command = f"/model {model}"
        if provider:
            command += f" --provider {provider}"
        # --session: this conversation's choice, not a new profile default.
        command += " --session"
        request_id = self._send(
            "slash.exec", {"session_id": self._live_session, "command": command}
        )
        if request_id is None:
            return
        reply = self._await_response(request_id, 60.0)
        if reply is None:
            return
        error = reply.get("error")
        if isinstance(error, dict):
            self._on_activity(
                "tool",
                f"Hermes refused {command}: {error.get('message') or 'no reason given'}",
            )
            return
        self._session_model = self._model

    def _wait_for_ready(self) -> bool:
        ready = self._await_frame(lambda frame: self._event_type(frame) == "gateway.ready", 60.0)
        if ready is not None:
            return True
        if not self._cancelled and not self._ended:
            detail = self._transport.failure_detail() if self._transport else ""
            self._on_failed(detail or "Hermes did not become ready in time")
        return False

    def _run_replay(self) -> None:
        """Reopen a stored conversation and hand its transcript to the window.

        One request does both jobs: ``session.resume`` with the transcript not
        omitted returns the whole visible history, and -- when that
        conversation is live in the gateway process -- attaches to it, reusing
        the SAME live session instead of building a parallel one. That is what
        makes "go back to the conversation that is running right now" real:
        the reply's ``running`` flag says the turn is still going, and this
        worker then consumes its events exactly like a turn of its own, until
        the completion event arrives.

        The price of attaching is ownership of the stream: the gateway keeps
        one transport per session, so whoever resumed last receives the events.
        The window says so before this worker is started.
        """
        target = self._gateway_session
        if not target:
            self._on_failed("There is no conversation id to reopen")
            return
        result = self._call(
            "session.resume",
            {"session_id": target, "omit_messages": False},
            120.0,
            "Hermes did not answer the resume request",
            "Hermes could not reopen that conversation",
        )
        if result is None:
            return
        self._live_session = str(result.get("session_id") or "")
        # ``session_id`` is the per-process handle and dies with the gateway.
        # The durable id comes back as ``session_key`` or ``resumed``, since
        # resume has no ``stored_session_id`` field.
        stored = str(
            result.get("session_key")
            or result.get("resumed")
            or result.get("stored_session_id")
            or target
        )
        if not self._live_session:
            self._on_failed("Hermes did not return a session id")
            return
        self._on_session(stored)
        self._accepting_input.set()
        self._on_started()
        for kind, text in _replay_rows(result.get("messages") or []):
            self._on_activity(kind, text)
        if bool(result.get("running")):
            # The conversation's turn is still going on the gateway. Eating its
            # events here is what "attaching" means; the same idle and
            # connection checks as a live turn guard the wait.
            self._consume_turn()
            return
        # A finished conversation replays silently: nothing is "completed" into
        # the transcript, because there is no new answer -- the window marks
        # the end itself from on_done.
        self._clean_end = True
        self._on_complete("")

    def _ensure_session(self) -> bool:
        """Create a conversation, or reopen the one we were given."""
        if self._gateway_session:
            # Reopening a stored conversation. Hermes resolves the id through
            # its compression chain, so a conversation that was compacted since
            # it was last seen still lands on the turns written afterwards.
            # ``omit_messages`` keeps it from replaying the whole transcript:
            # BlindPilot already rebuilt those rows from history.
            method = "session.resume"
            params: dict = {"session_id": self._gateway_session, "omit_messages": True}
        else:
            method = "session.create"
            params = {"cwd": self._cwd, **self._session_params()}
        result = self._call(
            method,
            params,
            120.0,
            "Hermes did not answer the session request",
            "Hermes could not start a session",
        )
        if result is None:
            return False
        # Two ids come back: one for this gateway process and one that survives
        # restarts. The turn is addressed with the first; the second is what
        # BlindPilot stores so the conversation can be reopened later.
        self._live_session = str(result.get("session_id") or "")
        stored = str(result.get("stored_session_id") or "")
        if not self._live_session:
            self._on_failed("Hermes did not return a session id")
            return False
        self._on_session(stored or self._live_session)
        # session.create carried the pick, so the session runs it. A resumed
        # session keeps whatever it had, which is not known here.
        self._session_model = "" if self._gateway_session else self._model
        # A remote Hermes validates the folder against its own filesystem and,
        # finding nothing, silently uses its own directory. session.create
        # reports the resolved directory in ``info``, so say so here.
        landed = str(((result.get("info") or {}).get("cwd")) or "")
        if landed and self._cwd and not _same_directory(landed, self._cwd):
            self._on_activity(
                "note",
                f"Hermes could not use {self._cwd}, so this conversation is running in {landed}.",
            )
        return True

    def _session_params(self) -> dict:
        params: dict = {}
        if self._model:
            # Sent as two fields. Hermes reads a "provider:model" prefix only
            # for providers it ships with, so a user-defined one would be
            # taken as part of the model name.
            provider, model = split_model_row(self._model)
            params["model"] = model
            if provider:
                params["provider"] = provider
        if self._effort:
            # Hermes validates this itself and ignores a level it does not
            # know, so a stale saved value cannot break a turn.
            params["reasoning_effort"] = self._effort
        if self._session_title:
            # Hermes keeps a user-given title over its own automatic one.
            # Omitted when empty, which is how a conversation gets that
            # automatic name instead.
            params["title"] = self._session_title
        yolo = _MODE_TO_YOLO.get(self._permission_mode)
        if yolo is not None:
            params["yolo"] = yolo
        return params

    def _run_turn(self) -> None:
        command = self._as_slash_command()
        if command is not None:
            self._run_slash_command(command)
            return
        text = self._prompt
        if self._attachments:
            uploaded = self._upload_attachments()
            if uploaded is None:
                return
            text = self._prompt_with_attachments(uploaded)
        accepted = self._call(
            "prompt.submit",
            {"session_id": self._live_session, "text": text},
            120.0,
            "Hermes did not accept the prompt",
            "Hermes could not start the turn",
        )
        if accepted is None:
            return
        self._accepting_input.set()
        self._on_started()
        self._consume_turn()

    def _as_slash_command(self) -> Optional[str]:
        """The prompt, if Hermes would recognise it as one of its commands.

        Hermes is ASKED rather than matched against a list held here. It has
        about 120 built-in commands plus whatever skills, bundles and plugins
        are installed, and both halves move: a list compiled into BlindPilot
        would be wrong the first time a skill was installed, and would answer
        "/whatever" by sending those characters to the model as a message --
        which is what used to happen to every Hermes command that BlindPilot
        did not implement itself.

        Anything Hermes does not recognise stays an ordinary message, so a
        sentence that happens to open with a slash is not swallowed. Same rule
        the opencode adapter follows, for the same reason.
        """
        text = self._prompt.strip()
        if not text.startswith("/") or self._attachments:
            return None
        # Split on any whitespace: a command can be followed by its arguments
        # on the same line or on the next one.
        words = text[1:].split(None, 1)
        name = words[0] if words else ""
        if not name:
            return None
        request_id = self._send("complete.slash", {"text": "/" + name})
        reply = self._await_response(request_id, 30.0) if request_id is not None else None
        if reply is None or isinstance(reply.get("error"), dict):
            # No answer is not a licence to guess. Sending it as a message is
            # the older behaviour and the safer of the two wrong answers: the
            # model reads it, rather than the turn dying on a command that may
            # not exist.
            return None
        items = (reply.get("result") or {}).get("items")
        known = {
            str(item.get("text") or "").strip().lstrip("/").lower()
            for item in (items if isinstance(items, list) else [])
            if isinstance(item, dict)
        }
        return text if name.lower() in known else None

    def _run_slash_command(self, text: str) -> None:
        """Run one of Hermes' own commands and read its output back.

        ``slash.exec`` answers with the command's output rather than starting a
        turn, so there is no stream to consume and no completion event coming:
        the reply IS the end of this turn.
        """
        name = text.split()[0]
        self._on_activity("tool", f"Running Hermes command {name}")
        # The parameter is "command"; a frame carrying "text" instead is
        # answered with "empty command" (error 4004). The wait is generous
        # because /update downloads a release, /init scans a repository, and
        # /skills can reach the network. None of them stream progress.
        result = self._call(
            "slash.exec",
            {"session_id": self._live_session, "command": text},
            600.0,
            f"Hermes did not answer {name}",
            f"Hermes could not run {name}",
        )
        if result is None:
            return
        output = str(result.get("output") or "").strip()
        # A command that did its work silently still has to end out loud: a
        # turn that finishes with nothing said is indistinguishable from one
        # that failed.
        self._clean_end = True
        self._on_complete(output or f"{name} finished with no output.")

    def _upload_attachments(self) -> Optional[list[tuple[str, str]]]:
        """Send each attached file's bytes to Hermes, before the prompt goes out.

        Returns pairs of (name as the user knows it, path Hermes stored it at),
        or ``None`` when the turn has already been reported as failed.

        The bytes travel even when the path would resolve on the gateway. Two
        machines that both mount a drive at the same place -- an everyday
        Windows-and-WSL pair, or two hosts sharing a folder layout -- would
        otherwise let a path point at a DIFFERENT file with the same name, and
        the answer would be about a file the user never attached. Uploading is
        the only way the file being discussed is the file that was picked.
        """
        sent: list[tuple[str, str]] = []
        for path in self._attachments:
            display = attachment_name(path) or path
            try:
                size = check_attachment(path)
                data_url = attachment_data_url(path)
            except AttachmentError as exc:
                self._on_failed(str(exc))
                return None
            except OSError as exc:
                self._on_failed(f"{display} could not be read: {exc.strerror or exc}")
                return None
            self._on_activity("tool", f"Sending {display} ({_size_text(size)}) to Hermes")
            # The name is given separately on purpose: a Linux gateway handed
            # "D:\\dir\\report.xlsx" stores one file with that whole string as
            # its name.
            result = self._call(
                "file.attach",
                {"session_id": self._live_session, "name": display, "data_url": data_url},
                300.0,
                f"Hermes did not confirm receiving {display}",
                f"Hermes could not accept {display}",
            )
            if result is None:
                return None
            stored = str(result.get("path") or "")
            if not stored:
                self._on_failed(f"Hermes accepted {display} but did not say where it put it")
                return None
            self._on_activity("result", f"{display} received by Hermes")
            sent.append((display, stored))
        return sent

    def _prompt_with_attachments(self, uploaded: list[tuple[str, str]]) -> str:
        """The prompt, plus where Hermes can find each file that came with it.

        Measured, and the reason this is a path rather than an ``@file:`` ref:
        Hermes stages uploads in its own ``attachments`` directory, which is
        outside the conversation's workspace, and its ``@`` expansion refuses
        anything outside that workspace ("path is outside the allowed
        workspace"). The ref would be answered with a warning and no content.
        A plain path is read by the agent's own file tools, which is what the
        probe saw happen.
        """
        lines = [f"{name}: {stored}" for name, stored in uploaded]
        listing = "\n".join(lines)
        noun = "file" if len(lines) == 1 else "files"
        note = (
            f"Attached {noun} (uploaded to this machine, please read {'it' if len(lines) == 1 else 'them'}):\n"
            + listing
        )
        return f"{self._prompt}\n\n{note}" if self._prompt else note

    def _run_compaction(self) -> None:
        """Compact in place. Hermes answers when the summary is written."""
        result = self._call(
            "session.compress",
            {"session_id": self._live_session},
            600.0,
            "Hermes did not finish compacting the conversation",
            "Hermes could not compact",
        )
        if result is None:
            return
        # Compaction produces no answer of its own, so say what happened rather
        # than finishing in silence - a silent end reads as a failed turn.
        self._clean_end = True
        self._on_complete("Conversation compacted.")

    def _consume_turn(self) -> None:
        """Read events until the answer is complete, failed, or interrupted.

        A quiet stretch is narrated rather than sat out. Hermes can spend
        minutes on a single step -- a build, a test run, a rate limit being
        waited out -- and the connection can also die between frames. Both look
        identical from here: no frames. So the wait is timed, said out loud
        while it lasts, and the connection is checked as it goes, which is what
        turns "did anything happen?" into an answer the listener is given
        without having to ask.

        "Quiet" means NOTHING WORTH SAYING ARRIVED, not "no bytes arrived". That
        distinction is the whole of a measured defect: a turn stuck retrying a
        rejected model produced a frame every few seconds -- ``sessions.changed``
        housekeeping and ``thinking.delta`` frames whose text was empty -- and
        every one of them reset this timer, so nothing was ever announced. The
        turn sat there for five minutes with a screen reader saying nothing at
        all, which is indistinguishable from a hang and is the worst thing this
        loop can do. Frames that produce no row therefore leave the clock
        running.
        """
        # Timed against the clock, not by counting empty reads. A trickle of
        # content-free frames returns immediately every time, so a counter of
        # empty reads never advances while the listener hears nothing.
        last_row = _now()
        next_notice = _PROGRESS_NOTICE_SECONDS
        next_check = _CONNECTION_CHECK_SECONDS
        while not self._cancelled:
            transport = self._transport
            if transport is None:
                return
            frame = transport.receive(_READ_TIMEOUT)
            if frame is not None:
                before = self._rows_emitted
                if self._handle_event(frame) is True:
                    return
                if self._rows_emitted != before:
                    last_row = _now()
                    next_notice = _PROGRESS_NOTICE_SECONDS
                    next_check = _CONNECTION_CHECK_SECONDS
                    continue
            quiet = _now() - last_row
            if quiet >= next_check:
                next_check = quiet + _CONNECTION_CHECK_SECONDS
                # A connection that has gone away is reported now, with the
                # reason, instead of after the full idle limit.
                if not transport.connected():
                    self._on_failed(transport.failure_detail())
                    return
            # Absolute silence gets a diagnosis, once, instead of only the
            # generic notices. Measured: the most common reason a live Hermes
            # produces nothing is a rate-limited or credit-exhausted provider,
            # which it grinds through with backoff and fallbacks the gateway
            # mostly does not narrate -- indistinguishable from a hang until
            # the listener is told what it probably is.
            if quiet >= _SILENCE_DIAGNOSTIC_SECONDS and not self._silence_diagnosed:
                self._silence_diagnosed = True
                self._on_activity("tool", _SILENCE_DIAGNOSTIC_MESSAGE)
            if quiet >= next_notice:
                next_notice = quiet + _PROGRESS_NOTICE_SECONDS
                self._announce_still_working(quiet)
            if quiet >= _IDLE_LIMIT:
                # The connection was checked above and is open. Nothing closed;
                # the turn simply produced nothing for the whole limit.
                self._on_failed(
                    f"Hermes has been silent for {_how_long(_IDLE_LIMIT)}, "
                    "so this turn was given up."
                )
                return

    def _announce_still_working(self, waited: float) -> None:
        """Say that the turn is still going, and what it was last doing.

        Named after what it answers: the listener's question is not "how many
        seconds" but "is this still alive". The last step Hermes reported is
        included when there is one, because "still working on terminal" is worth
        far more than "still working".
        """
        how_long = _how_long(waited)
        if self._last_step:
            self._on_activity("tool", f"Still working, {how_long} on {self._last_step}")
        else:
            self._on_activity("tool", f"Still working, {how_long} so far")

    # -- events into accessible rows --------------------------------------

    @staticmethod
    def _event_type(frame: dict) -> str:
        params = frame.get("params")
        if isinstance(params, dict):
            return str(params.get("type") or "")
        return ""

    def _handle_event(self, frame: dict) -> Optional[bool]:
        """Turn one frame into rows. ``True`` means the turn is over.

        Anything unrecognised is ignored on purpose: Hermes gains events over
        time, and a front end that fails on an unknown one would break at the
        next Hermes release.
        """
        params = frame.get("params")
        if not isinstance(params, dict):
            return None
        event = str(params.get("type") or "")
        payload = params.get("payload")
        payload = payload if isinstance(payload, dict) else {}

        if event == "message.delta":
            text = str(payload.get("text") or "")
            if text:
                self._answer_parts.append(text)
                self._release_finished_sentences()
            return None

        if event == "thinking.delta":
            text = str(payload.get("text") or "")
            # Only real reasoning is kept; the spinner is Hermes drawing a
            # progress indicator for a terminal nobody is looking at here.
            if text and not _is_spinner_text(text):
                self._thinking_parts.append(text)
            return None

        if event == "reasoning.available":
            # Where Hermes puts the model's actual reasoning for the turn. It
            # replaces whatever was streamed, rather than adding to it, so the
            # row is the reasoning once and not the reasoning twice.
            text = str(payload.get("text") or "").strip()
            if text:
                self._reasoning = text
            return None

        if event == "status.update":
            # Hermes' own account of what it is doing between answers: which
            # process it started, that it is summarising the conversation to
            # free up context, and so on. Ignoring it is what left a long turn
            # with nothing to say for itself, so it becomes a row like any other
            # step -- and it is remembered, so a turn that then goes quiet can
            # say what it went quiet on. The \u26a0\ufe0f / \u26a0 prefixes Hermes draws
            # for a terminal are dropped: a screen reader reads them as
            # "warning sign", which precedes nearly every one of these lines.
            text = _first_text(payload.get("text"), payload.get("kind"))
            if text:
                kind = str(payload.get("kind") or "")
                label = "Summarising the conversation" if kind == "compacting" else text
                label = _clean_status_text(label)
                self._last_step = label
                self._on_activity("tool", label)
            return None

        if event == "notification.show":
            # The gateway's own account of why nothing has happened yet: the
            # agent is still building (tool discovery, model setup) and the
            # message will be sent as soon as it is ready. Dropping it left a
            # slow start sounding exactly like a hang, so it becomes a row like
            # any other step -- and it is remembered, so the still-working
            # notice can say what the wait is FOR.
            text = _first_text(payload.get("text"), payload.get("message"))
            if text:
                label = _clean_status_text(text)
                self._last_step = label
                self._on_activity("tool", label)
            return None

        if event == "tool.start":
            self._tool_start(payload)
            return None

        if event == "tool.complete":
            self._tool_complete(payload)
            return None

        if event == "approval.request":
            # Approved automatically in the yolo modes and denied in the rest,
            # with a row saying so either way. Nothing is shown to decide on.
            self._answer_approval(payload)
            return None

        if event == "clarify.request":
            self._answer_clarify(payload)
            return None

        if event in ("sudo.request", "secret.request"):
            self._answer_secret(event, payload)
            return None

        if event == "message.complete":
            return self._turn_complete(payload)

        if event == "error":
            message = _first_text(payload.get("message"), payload.get("error"))
            if message:
                self._on_failed(message)
                return True
            return None

        return None

    def _release_finished_sentences(self, final: bool = False) -> None:
        """Hand the window every sentence the answer has finished so far.

        The listener hears the answer while the model is still writing it,
        instead of waiting for the whole turn. Only finished sentences go out:
        the live edge of the stream is a half-written word, and half a word read
        aloud is what makes a run sound broken. At the end of the turn nothing
        more is coming, so ``final`` releases whatever is left as it stands.

        Always emits. When live rows are switched off in Options the window
        drops them; that setting is not consulted here.
        """
        pending = "".join(self._answer_parts)[self._streamed :]
        if not pending:
            return
        ready = pending if final else _complete_sentences(pending)
        if not ready:
            return
        self._streamed += len(ready)
        spoken = ready.strip()
        if spoken:
            self._on_activity("assistant", spoken)

    def _tool_start(self, payload: dict) -> None:
        name = str(payload.get("name") or "tool")
        tool_id = str(payload.get("tool_id") or "")
        if tool_id:
            self._tool_names[tool_id] = name
        # Remembered for the progress notice: a long wait is nearly always a
        # long-running tool, and naming it is what makes the notice useful.
        self._last_step = name
        context = _first_text(payload.get("context"), payload.get("args_text"))
        self._on_activity("tool", f"{name}: {context}" if context else name)

    def _tool_complete(self, payload: dict) -> None:
        tool_id = str(payload.get("tool_id") or "")
        name = str(payload.get("name") or self._tool_names.pop(tool_id, "") or "tool")
        # Prefer Hermes' own one-line summary when it sends one: it is written
        # for a human. Otherwise fall back to the result itself, which arrives
        # as a decoded object for most tools rather than a string.
        detail = _first_text(
            payload.get("summary"),
            payload.get("inline_diff"),
            payload.get("result_text"),
        )
        if not detail:
            detail = _describe_tool_result(payload.get("result"))
        if not detail:
            return
        # A tool result can be thousands of lines, and a screen reader has to
        # walk every one of them. The row says how much was left out rather
        # than pretending the whole result is there.
        lines = detail.splitlines()
        if len(lines) > _RESULT_MAX_LINES:
            omitted = len(lines) - _RESULT_MAX_LINES
            detail = "\n".join(lines[:_RESULT_MAX_LINES])
            detail += f"\n[{omitted} more lines not shown]"
        elif len(detail) > _RESULT_MAX_CHARS:
            detail = detail[:_RESULT_MAX_CHARS] + " […truncated]"
        self._on_activity("result", f"{name}: {detail}")

    def _answer_clarify(self, payload: dict) -> None:
        """Put Hermes' question in front of the user, and answer it.

        Hermes waits on the answer with no deadline at all when its
        ``clarify_timeout`` is zero, so every request with an id is answered,
        even one whose question could not be read.
        """
        request_id = str(payload.get("request_id") or "")
        if not request_id:
            return
        questions = _clarify_questions(payload)
        if not questions:
            self._on_activity(
                "tool", "Hermes asked a question that could not be read; it was answered empty."
            )
            self._request("clarify.respond", {"request_id": request_id, "answer": ""})
            return
        answers = self._on_question(questions) if self._on_question else None
        self._on_activity("tool", question_summary(questions, answers))
        self._send_clarify_answers(request_id, questions, answers)

    def _send_clarify_answers(
        self,
        request_id: str,
        questions: Sequence[Question],
        answers: Optional[list[list[str]]],
    ) -> None:
        """One ``clarify.respond`` per question -- answered or not.

        A question the user skipped is still answered, with an empty string.
        Hermes releases a batch only once EVERY question id has been locked,
        so staying silent about one leaves the turn hanging exactly as it did
        before; and an empty answer is already how Hermes records "the user
        said nothing" -- its own tool result spells it ``"user_response": ""``.
        """
        for index, question in enumerate(questions):
            chosen = answers[index] if answers is not None and index < len(answers) else []
            params = {"request_id": request_id, "answer": _clarify_answer(question, chosen)}
            if question.id:
                # Batch shape: Hermes keys the answers by the question's own id
                # and ignores a reply that does not carry one.
                params["question_id"] = question.id
            self._request("clarify.respond", params)

    def _answer_secret(self, event: str, payload: dict) -> None:
        """Answer a password or secret request rather than stall on it.

        Asked with ``secret`` set, so the transcript records THAT it was
        answered without echoing the value: these rows are read aloud, copied
        and saved. A request the user declines is still answered, with an
        empty string, because Hermes blocks the turn until something arrives.
        """
        request_id = str(payload.get("request_id") or "")
        if not request_id:
            return
        sudo = event == "sudo.request"
        asked = _first_text(
            payload.get("question"), payload.get("prompt"), payload.get("message")
        ) or ("Hermes needs the administrator password" if sudo else "Hermes needs a secret value")
        question = Question(question=asked, secret=True)
        answers = self._on_question([question]) if self._on_question else None
        self._on_activity("tool", question_summary([question], answers))
        given = answers[0][0] if answers and answers[0] else ""
        self._request(
            "sudo.respond" if sudo else "secret.respond",
            {"request_id": request_id, "password" if sudo else "value": given},
        )

    def _answer_approval(self, payload: dict) -> None:
        request_id = payload.get("request_id")
        allowed = _MODE_TO_YOLO.get(self._permission_mode, False)
        command = _first_text(payload.get("command"), payload.get("summary"))
        if allowed:
            self._on_activity("tool", f"Approved automatically: {command or 'a command'}")
            decision = "approve"
        else:
            self._on_activity(
                "tool",
                f"Hermes needs approval for: {command or 'a command'}. "
                "Switch the permission mode to allow it.",
            )
            decision = "deny"
        if request_id is not None:
            self._request("approval.respond", {"request_id": request_id, "decision": decision})

    def _turn_complete(self, payload: dict) -> Optional[bool]:
        self._accepting_input.clear()
        status = str(payload.get("status") or "complete")
        # Nothing more is coming, so the tail that never finished a sentence is
        # released as it stands. Without this the last clause of every answer
        # would only reach the listener through the final text, arriving after
        # everything else it was already read.
        self._release_finished_sentences(final=True)
        # The replacement event wins; the streamed fragments are the fallback
        # for a provider that never sends one.
        thinking = self._reasoning or "".join(self._thinking_parts).strip()
        answer = _first_text(payload.get("text")) or "".join(self._answer_parts)
        if thinking and thinking.strip() != answer.strip():
            # Reasoning is its own row so it can be skipped or read on demand,
            # the same way the other backends present it. When a provider
            # reports the answer as its reasoning - some do, for a one-word
            # reply - saying it twice would just make the reader repeat itself.
            self._on_activity("thinking", thinking)
        self._thinking_parts.clear()
        self._reasoning = ""
        if status in ("interrupted", "cancelled"):
            if not self._cancelled:
                self._on_failed("Hermes stopped before finishing the answer")
            return True
        if status in ("error", "failed"):
            self._on_failed(
                _first_text(payload.get("error"), payload.get("text")) or "Hermes turn failed"
            )
            return True
        self._clean_end = True
        self._on_complete(answer.strip())
        return True

    @staticmethod
    def _error_text(error: object, fallback: str) -> str:
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
        return fallback
