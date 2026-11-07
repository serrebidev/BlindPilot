"""The Claude turn worker: one BlindPilot turn over the tab's Claude Code process.

A turn no longer owns a process. ``claude_session`` keeps one per tab between
turns; this worker attaches to it, sends the prompt, reads the stream to this
turn's result and detaches. Everything the CLI says on that stream becomes a
callback here -- live activity, questions, subagent reports, the answer -- and
the window marshals each of them back to its own thread.

Split out of ``blindpilot_app.py`` (issue #33) so the window holds the worker
rather than defines it. The protocol, the spoken text and the order of the
callbacks are unchanged by the move.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
Based on the original Claude Code Reader application by doubletaponair:
https://github.com/doubletaponair/claude-code-reader
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import functools
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from agent_backends import (
    BACKEND_CLAUDE,
    SUBAGENT_COMPLETED,
    SUBAGENT_FAILED,
    SUBAGENT_RUNNING,
    SubagentReport,
    _tool_use_label,
    claude_limit_warning,
    ignore_subagents,
    subagent_line,
    subagent_status,
)
import backend_pool
import claude_session
from claude_session import _find_claude
import diagnostics
from markdown_rows import SentenceStreamer
from process_utils import no_window_kwargs
from questions import AskQuestions, Question, _questions, question_summary
import remote_sessions
from task_list import CLAUDE_TASK_TOOLS


# The tools Claude Code starts an agent with: Agent now, Task before it.
_CLAUDE_AGENT_TOOLS = ("Agent", "Task")


def _tool_result_text(content: object) -> str:
    """Plain text of a tool's result, whatever shape the CLI delivers it in.

    ``tool_result`` content is sometimes a bare string (most tools) and sometimes
    a list of typed blocks (``{"type": "text", "text": …}`` plus images). We keep
    the text and note any image so the actual *output* of the tool can be shown.
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif block.get("type") == "image":
                parts.append("[image]")
        return "\n".join(p for p in parts if p).strip()
    return ""


# How a refused tool call reads, as against one that simply failed. Claude Code
# flags both with ``is_error`` on the tool result and says nothing else about
# which it was, so the wording is what tells them apart: a permission rule, a
# disabled tool and a PreToolUse hook all refuse, and all three refuse in
# bypassPermissions exactly as they do in any other mode (measured against
# Claude Code on 2026-09-15). A tool that looked for a file and did not find one
# is a different thing and is not called a refusal.
_CLAUDE_REFUSAL_MARKERS = (
    "permission to use",
    "has been denied",
    "permission denied",
    "is disabled for this session",
    "no such tool available",
    "hook error",
    "blocked by",
    "requested permissions",
    "haven't granted it yet",
)

# Said once a turn, when a bypass turn has a tool refused anyway. Somebody who
# chose "bypass permissions" has every reason to read a refusal as a bug.
_CLAUDE_BYPASS_GAP_NOTE = (
    "Bypass does not cover this. Claude Code still applies a permissions.deny rule, a "
    "disabled tool and a PreToolUse hook in bypass, the same way it does in every other mode."
)


def _looks_like_refusal(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in _CLAUDE_REFUSAL_MARKERS)


AUTH_ERROR_MARKERS = (
    "not logged in",
    "not authenticated",
    "please log in",
    "please login",
    "run /login",
    "run `claude /login`",
    "invalid api key",
    "unauthorized",
    "401",
    "no credentials",
    "missing credentials",
    "authentication required",
    "auth required",
    "oauth token",
)
AUTH_HINT = "Not signed in. Run `claude auth login` in a terminal, then try again."


def _looks_like_auth_error(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in AUTH_ERROR_MARKERS)


# The tool Claude Code stops a turn to ask a multiple-choice question with.
ASK_USER_QUESTION_TOOL = "AskUserQuestion"

# What to pass to `--permission-prompt-tool`. "stdio" means "this host answers
# permission prompts on the JSON stream it is already reading", which is what
# makes AskUserQuestion available at all in headless mode. Cleared for the rest
# of the session if the installed Claude Code turns out not to know the flag,
# so an older CLI keeps working exactly as it did — without questions.
_CLAUDE_PERMISSION_PROMPT_TOOL = "stdio"


def _claude_questions(raw: list) -> tuple[Question, ...]:
    """Read AskUserQuestion's input into BlindPilot's own question shape.

    Claude Code always offers an "Other" answer of its own, whatever the
    question says, so `allow_custom` is not read from the payload.
    """
    return _questions(raw, multi_key="multiSelect")


# How long a stopped turn waits for the CLI's result before the tab's process
# is dropped. A confirmed interrupt is answered with the interrupted turn's
# result straight away, so a process that stays silent this long is one that
# cannot be trusted to carry the next turn.
_CANCEL_DRAIN_SECONDS = 5.0

# Put on a stopped turn's queue by cancel(), so a reader parked on a queue with
# no timeout wakes up and asks again with the drain's clock running. It carries
# nothing and means nothing else.
_CANCEL_WAKE = {"type": "cancel_wake"}


class ClaudeWorker(threading.Thread):
    """Runs one Claude turn on the tab's held process and reports it back.

    All callbacks are invoked from this worker thread; the caller is
    responsible for marshalling them back to the GUI thread (wx.CallAfter).
    """

    # Stop may wait for the CLI to confirm the interrupt and then for the
    # interrupted turn's result, so the panel must wait longer than both
    # before it says the stop did not land.
    stop_seconds = 12.0
    # Background agents keep working after the turn that started them
    # ends, and report their end in whichever turn reads it.
    subagents_outlive_turn = True

    def __init__(
        self,
        prompt: Optional[str],
        session_id: Optional[str],
        cwd: str,
        permission_mode: str,
        *,
        model: str = "",
        effort: str = "",
        on_session: Callable[[str], None],
        on_started: Callable[[], None],
        on_activity: Callable[[str, str], None],
        on_complete: Callable[[str], None],
        on_failed: Callable[[str], None],
        on_done: Callable[[], None],
        on_question: Optional[AskQuestions] = None,
        on_subagent: Optional[SubagentReport] = None,
        held_for: object = None,
        on_unsolicited: Optional[Callable[[], None]] = None,
        on_permission: Optional[Callable[[str, dict, list], dict]] = None,
        remote_control: Optional[dict] = None,
    ):
        super().__init__(daemon=True)
        # {"enabled", "name", "reattach"}: Remote Control as the tab wants it.
        self._remote_control = remote_control
        # Asked about a tool the permission mode leaves to a person; returns
        # the control response's own body (allow or deny).
        self._on_permission = on_permission
        self._prompt = prompt
        self._session_id = session_id
        self._cwd = cwd
        self._permission_mode = permission_mode
        self._model = model
        self._effort = effort
        self._on_session = on_session
        self._on_started = on_started
        self._on_activity = on_activity
        self._on_complete = on_complete
        self._on_failed = on_failed
        self._on_done = on_done
        self._on_question = on_question
        self._on_subagent = on_subagent or ignore_subagents
        # Which tab's held process this turn borrows, and who to tell when the
        # CLI speaks with no turn attached.
        self._held_for = held_for
        self._on_unsolicited = on_unsolicited
        self._session: Optional[claude_session.ClaudeSession] = None
        # The queue this turn is reading, kept so Stop can wake a reader that
        # is parked on it.
        self._events: Optional[queue.Queue] = None
        self._cancelled = False
        # When the drain that follows Stop runs out. Set by `cancel`, so the
        # budget is counted from Stop rather than from the last event.
        self._cancel_deadline: Optional[float] = None
        # Set once the session is up and the opening prompt has gone in, cleared
        # when the turn ends. Guards `steer()` against writing to a session that
        # is not there yet (or is already gone).
        self._accepting_input = threading.Event()
        # A failure is reported once, so a crash late in the turn cannot talk
        # over the explanation the turn already gave.
        self._failed = False

    def _fail(self, message: str) -> None:
        """Report why the turn ended, once."""
        if self._failed:
            return
        self._failed = True
        self._on_failed(message)

    def _ending_note(self, rc: object, detail: str) -> str:
        """How the run ended, with its exit code when there is one.

        A process whose stream has ended has not always been reaped, so there
        is not always a code to give. "Claude Code exited with code None" read
        as a bug in BlindPilot; saying there was no code says what happened.
        """
        if rc is None:
            return f"Claude Code exited without a code{detail}"
        return f"Claude Code exited with code {rc}{detail}"

    @staticmethod
    def _count(value: object) -> int:
        """A count, or zero. `True` is an `int` in Python and is not a count:
        `started_in_background: true` would otherwise mean one agent forever."""
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    @staticmethod
    def _diagnostic_path() -> Path:
        """Where a turn that ended badly leaves its account of itself."""
        return diagnostics.log_path()

    def _log_unfinished_turn(self, rc: object, complete: bool, stderr_text: str) -> None:
        """Record a turn the CLI did not finish.

        The window is gone by the time anyone looks, and an exit code says
        nothing about what the run was doing. This is what is left behind.
        """
        diagnostics.log_unfinished_turn(
            "claude",
            exit_code=rc,
            completed=complete,
            session_id=self._session_id or "(new)",
            permission_mode=self._permission_mode,
            model=self._model or "(default)",
            cancelled=self._cancelled,
            detail=stderr_text or "(nothing on stderr)",
        )

    def accepting_input(self) -> bool:
        """Whether the active Claude turn can accept a steering message."""
        return self._accepting_input.is_set() and not self._cancelled

    def _write_json(self, payload: dict) -> bool:
        session = self._session
        return session is not None and session.write_json(payload)

    def steer(self, text: str) -> bool:
        """Send a follow-up message into the turn that is already running.

        Returns False if the run is no longer listening, so the caller can put
        the text back in the prompt box rather than silently dropping it.
        """
        if not self.accepting_input():
            return False
        return self._session is not None and self._session.send_user(text)

    def cancel(self) -> None:
        """Stop the turn, and only the turn when the CLI lets us.

        An interrupt the CLI confirms ends this turn with a result and leaves
        the process, and every agent it holds, running. One it does not
        confirm means the process cannot be trusted with the next turn, so
        it is dropped from the pool, which stops it.
        """
        # Before `_cancelled`, because that is what the reader watches: it
        # must never find the turn cancelled with no deadline to drain to.
        self._cancel_deadline = time.monotonic() + _CANCEL_DRAIN_SECONDS
        self._accepting_input.clear()
        self._cancelled = True
        session = self._session
        if session is None:
            return
        session.stop_agents()
        if not session.interrupt(claude_session._INTERRUPT_SECONDS):
            self._drop_process()
        # The reader may have been parked on the queue with no timeout since
        # before Stop was pressed, and the drain's clock only starts when it
        # asks for the next event. This is what makes it ask.
        events = self._events
        if events is not None:
            events.put(_CANCEL_WAKE)

    def _track_subagents(self, event: dict, session: claude_session.ClaudeSession) -> None:
        """Tell the window about the agents this turn's process is running.

        Claude Code reports each agent through three system events keyed by
        task id -- task_started, task_progress, task_notification -- and marks
        every message an agent sends with the id of the Agent tool call that
        started it (`parent_tool_use_id`). Background shell tasks share the
        system events; they are not agents, and are left out by `task_type`.
        """
        report = self._on_subagent
        etype = event.get("type")
        if etype == "system":
            subtype = event.get("subtype")
            task_id = str(event.get("task_id") or "")
            if not task_id:
                return
            if subtype == "task_started":
                if event.get("task_type") not in (None, "local_agent", "remote_agent"):
                    return
                agent_id = str(event.get("tool_use_id") or task_id)
                session.agent_tasks[task_id] = agent_id
                session.agent_calls.add(agent_id)
                background = bool(event.get("is_backgrounded"))
                if background:
                    session.background_agents.add(agent_id)
                report(
                    agent_id,
                    subagent_line(event.get("description"), 80),
                    SUBAGENT_RUNNING,
                    "Started in the background" if background else "Started",
                )
            elif subtype == "task_progress":
                agent_id = session.agent_tasks.get(task_id, "")
                if not agent_id:
                    return
                summary = subagent_line(event.get("summary"))
                tool = str(event.get("last_tool_name") or "")
                report(agent_id, "", "", summary or (f"Using {tool}" if tool else ""))
            elif subtype == "task_notification":
                agent_id = session.agent_tasks.pop(task_id, "")
                if not agent_id:
                    return
                status = subagent_status(event.get("status")) or SUBAGENT_COMPLETED
                report(
                    agent_id,
                    "",
                    status,
                    subagent_line(event.get("summary")) or f"Finished: {status}",
                )
            return
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        parent = str(event.get("parent_tool_use_id") or "")
        if etype == "assistant":
            for block in blocks:
                btype = block.get("type")
                raw_input = block.get("input")
                params = raw_input if isinstance(raw_input, dict) else {}
                name = str(block.get("name") or "tool")
                if btype == "tool_use" and name in _CLAUDE_AGENT_TOOLS:
                    call_id = str(block.get("id") or "")
                    if not call_id:
                        continue
                    session.agent_calls.add(call_id)
                    title = params.get("description") or params.get("subagent_type") or "Subagent"
                    prompt = subagent_line(params.get("prompt"))
                    report(
                        call_id,
                        subagent_line(title, 80),
                        SUBAGENT_RUNNING,
                        f"Task: {prompt}" if prompt else "",
                    )
                elif parent and btype == "text":
                    report(parent, "", "", subagent_line(block.get("text")))
                elif parent and btype == "tool_use":
                    report(parent, "", "", _tool_use_label(name, params))
        elif etype == "user":
            for block in blocks:
                if block.get("type") != "tool_result":
                    continue
                result = _tool_result_text(block.get("content"))
                call_id = str(block.get("tool_use_id") or "")
                if parent:
                    if result:
                        report(parent, "", "", f"Result: {subagent_line(result, 200)}")
                    continue
                if call_id not in session.agent_calls:
                    continue
                if call_id in session.background_agents or result.lstrip().casefold().startswith(
                    "async agent"
                ):
                    # The launch of a background agent, not its answer.
                    session.background_agents.add(call_id)
                    continue
                status = SUBAGENT_FAILED if block.get("is_error") else SUBAGENT_COMPLETED
                report(call_id, "", status, subagent_line(result) or f"Finished: {status}")

    def _drop_process(self) -> None:
        """Stop the process this turn borrowed and take it out of the pool.

        The pool holds it under the tab's key, but a process already dropped
        by somebody else is still this turn's to stop, so both are done.
        """
        backend_pool.pool().drop(backend_pool.pool_key(BACKEND_CLAUDE, self._held_for))
        session = self._session
        if session is not None:
            session.stop()

    def run(self) -> None:
        try:
            self._do_run()
        except Exception as exc:
            # Anything thrown here used to end the turn without a word, leaving
            # the exit code as the whole explanation. Say what actually
            # happened instead.
            self._fail(f"BlindPilot stopped reading Claude Code: {exc}")
        finally:
            self._on_done()

    @staticmethod
    def _retry_without_prompt_tool(stderr_text: str) -> bool:
        """Whether this failure was `--permission-prompt-tool` and is now off."""
        global _CLAUDE_PERMISSION_PROMPT_TOOL
        if not _CLAUDE_PERMISSION_PROMPT_TOOL:
            return False
        if "permission-prompt-tool" not in stderr_text:
            return False
        _CLAUDE_PERMISSION_PROMPT_TOOL = ""
        return True

    @staticmethod
    def _retry_without_partial_messages(stderr_text: str) -> bool:
        """Whether this failure was `--include-partial-messages`, now off."""
        if not claude_session.include_partial_messages:
            return False
        if "include-partial-messages" not in stderr_text:
            return False
        claude_session.include_partial_messages = False
        return True

    def _handle_control_request(self, event: dict) -> None:
        """Answer one control request from the CLI.

        Only `can_use_tool` reaches us, because that is the only kind
        `--permission-prompt-tool stdio` turns on. AskUserQuestion arrives that
        way too: the CLI asks permission to run it and takes the answers back in
        the tool's own input, so the dialog is opened here and what the person
        chose is written into `answers` before the tool is allowed to run.

        Every request has to be answered. One left hanging holds the turn open
        for good, which sounds exactly like a model that has stopped thinking.
        """
        request = event.get("request") or {}
        request_id = event.get("request_id")
        if not isinstance(request, dict) or not isinstance(request_id, str):
            return
        if request.get("subtype") != "can_use_tool":
            # Nothing else is switched on for this session. Say so rather than
            # stay silent, so an unexpected request cannot stall the turn.
            self._write_json(
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "error",
                        "request_id": request_id,
                        "error": "BlindPilot does not handle this request",
                    },
                }
            )
            return

        tool = str(request.get("tool_name") or "")
        payload = request.get("input")
        payload = payload if isinstance(payload, dict) else {}
        if tool == ASK_USER_QUESTION_TOOL and self._on_question is not None:
            self._answer_ask_user_question(request_id, payload)
            return
        # Any other tool is one the permission mode leaves to a person: put it
        # in front of them, as the interactive CLI would. With nobody to ask,
        # it is denied, which is what headless Claude Code did on its own.
        if self._on_permission is not None:
            suggestions = request.get("permission_suggestions")
            suggestions = suggestions if isinstance(suggestions, list) else []
            reason = str(request.get("decision_reason") or "")
            answer = self._on_permission(tool, {**payload, "_reason": reason}, suggestions)
            if answer.get("behavior") == "allow":
                answer = {**answer, "updatedInput": payload}
            self._write_json(
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "request_id": request_id,
                        "response": answer,
                    },
                }
            )
            return
        self._deny(request_id, tool)

    def _deny(self, request_id: str, tool: str = "") -> None:
        """Refuse one tool call, in the words the permission mode gives it."""
        self._write_json(
            {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": request_id,
                    "response": {
                        "behavior": "deny",
                        "message": (
                            f"{tool or 'That tool'} needs approval, which the "
                            f"{self._permission_mode or 'current'} permission mode does not give."
                        ),
                    },
                },
            }
        )

    def _answer_ask_user_question(self, request_id: str, payload: dict) -> None:
        """Put AskUserQuestion in front of the person and send back their answers.

        Claude Code takes the answers as part of the tool's input: a map from
        each question's own text to the chosen labels, joined by commas when the
        question allowed more than one. Allowing the call with that map filled in
        is what makes the tool report the answers to the model.
        """
        raw = payload.get("questions")
        questions = _claude_questions(raw if isinstance(raw, list) else [])
        answers = self._on_question(questions) if (questions and self._on_question) else None
        self._on_activity("notice", question_summary(questions, answers))
        if answers is None:
            self._write_json(
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "request_id": request_id,
                        "response": {
                            "behavior": "deny",
                            "message": "The user closed the question without answering it.",
                        },
                    },
                }
            )
            return
        updated = dict(payload)
        updated["answers"] = {
            question.question: ", ".join(answers[index]) if index < len(answers) else ""
            for index, question in enumerate(questions)
        }
        self._write_json(
            {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": request_id,
                    "response": {"behavior": "allow", "updatedInput": updated},
                },
            }
        )

    def _do_run(self) -> None:
        binary = _find_claude()
        if binary is None:
            self._on_failed("Claude Code not installed. Install from claude.com/claude-code")
            return

        wants = claude_session.Wants(
            cwd=self._cwd,
            permission_mode=self._permission_mode,
            model=self._model,
            effort=self._effort,
            session_id=self._session_id,
        )
        session = self._take(wants, binary)
        if session is None:
            return
        if session is self._session:
            # This turn is being retried after that same process died, and
            # its exit has not reached the pool yet. Reading its stream again
            # only reaches the end of it again.
            self._drop_process()
            session = self._take(wants, binary)
            if session is None:
                return
        self._session = session
        events = session.attach()
        self._events = events
        try:
            mark = session.stderr_mark()
            self._read_turn(session, events, mark)
        finally:
            session.detach()
            self._accepting_input.clear()

    def _take(
        self, wants: "claude_session.Wants", binary: str
    ) -> Optional["claude_session.ClaudeSession"]:
        """The tab's process for this turn, or None once it has been accounted for."""
        try:
            session = claude_session.take_or_start(
                self._held_for,
                wants,
                binary,
                _CLAUDE_PERMISSION_PROMPT_TOOL,
                idle_sink=self._on_unsolicited,
                popen_kwargs=no_window_kwargs(),
                # A turn with no prompt is a late one: it reads the process it
                # was woken for or it reads nothing. A replacement has no turn
                # running and would never send the result it waits for.
                late=self._prompt is None,
            )
        except Exception as exc:
            # OSError is the launch failing. Anything else is the pool or the
            # session objecting, and a turn that ends without a word over it
            # leaves Send disabled with nothing said.
            self._fail(f"Failed to launch Claude Code: {exc}")
            return None
        if session is None:
            # Late, and the process that woke this tab is already gone. There
            # is nothing to read and nothing went wrong, so the turn ends the
            # way any turn that reached its end in silence ends.
            self._on_activity("notice", "Claude Code finished the turn without saying anything.")
        return session

    def _apply_remote_control(self, session: claude_session.ClaudeSession) -> None:
        """Turn Remote Control on or off for this process, when the tab changed it.

        Reported through ``on_remote`` (set by the tab): Claude Code's answer,
        with ``bridge_session_id`` and ``session_url``, or ``{"error": ...}``.
        """
        wanted = self._remote_control
        if not wanted or bool(wanted.get("enabled")) == session.remote_control_on:
            return
        enabled = bool(wanted.get("enabled"))
        response = session.send_control(
            "remote_control",
            timeout=15,
            **remote_sessions.remote_control_fields(
                enabled, str(wanted.get("name") or ""), str(wanted.get("reattach") or "")
            ),
        )
        report = getattr(self, "on_remote", None)
        if response is None or response.get("subtype") == "error":
            error = "Claude Code did not answer" if response is None else response.get("error")
            if report:
                report({"error": str(error or "it was refused")})
            return
        session.remote_control_on = enabled
        body = response.get("response")
        if report and enabled:
            report(body if isinstance(body, dict) else {})

    def _read_turn(
        self, session: claude_session.ClaudeSession, events: "queue.Queue", mark: int
    ) -> None:
        if self._cancelled:
            # Stop landed before there was a process to stop. Sending the
            # prompt now would start the very turn that was called off.
            return
        self._apply_remote_control(session)
        if self._prompt is not None and not session.send_user(self._prompt):
            self._fail("Could not send the prompt to Claude Code")
            return
        self._accepting_input.set()

        text_parts: list[str] = []
        first_assistant_seen = False
        complete = False
        died = False
        # tool_use id -> name. A tool_result carries the id and not the name,
        # and a refusal that cannot say which tool was refused is not much of a
        # report.
        tool_names: dict[str, str] = {}
        # tool_use id -> (name, input) for the task-list tools, applied only
        # once their result says they worked.
        task_calls: dict[str, tuple[str, dict]] = {}
        gap_note_said = False
        # Partial-message stream, by speaker (the parent tool call for a
        # subagent): the sentences a text block has already said live, so
        # its whole-message form says only what is left. The CLI sends that
        # block's assistant event right after its last delta, one block per
        # event, so the stream's block index (which counts thinking and tool
        # blocks) is not needed and would not match the event's own.
        partial_live: dict[str, SentenceStreamer] = {}

        while True:
            timeout: Optional[float] = None
            if self._cancelled:
                # After Stop, the CLI's result for the interrupted turn is due
                # at once; a process that keeps it is not trusted with the
                # next. The budget runs from Stop, so a CLI that keeps talking
                # cannot put the deadline back for ever.
                deadline = self._cancel_deadline
                timeout = _CANCEL_DRAIN_SECONDS if deadline is None else deadline - time.monotonic()
                if timeout <= 0:
                    self._drop_process()
                    return
            try:
                event = events.get(timeout=timeout)
            except queue.Empty:
                self._drop_process()
                return
            if event is _CANCEL_WAKE:
                # Stop put this here to be woken by. Ask again, now with the
                # drain's clock running.
                continue
            if event is claude_session.EOF:
                died = True
                break
            # Before the stopped-turn branch: an agent's end reported during
            # the drain is exactly what Stop is waiting to hear.
            self._track_subagents(event, session)
            if self._cancelled:
                if event.get("type") == "control_request":
                    # A request left unanswered holds the CLI for ever, and a
                    # stopped turn is still the only one listening.
                    request_id = event.get("request_id")
                    if isinstance(request_id, str):
                        self._deny(request_id)
                    continue
                # Read to the result so it is not left for a late turn to find.
                if event.get("type") == "result":
                    if not event.get("is_error") and self._count(event.get("queued_turn_count")):
                        # Somebody else's result, as on the live path. Ours is
                        # the one that leaves nothing queued behind it.
                        continue
                    return
                continue

            etype = event.get("type")

            if etype == "control_request":
                # Answered on this thread, so a question blocks reading the
                # stream for as long as the dialog is open — which is what a
                # turn waiting on an answer is supposed to do.
                self._handle_control_request(event)

            elif etype == "system" and event.get("subtype") == "init":
                sid = event.get("session_id")
                if sid:
                    self._on_session(sid)

            elif etype == "stream_event":
                # Token-level partials (--include-partial-messages): the same
                # stream a terminal renders as it arrives. Text goes out as
                # finished sentences; thinking partials wait for the complete
                # block in the assistant message, as before.
                inner = event.get("event")
                if isinstance(inner, dict):
                    if inner.get("type") == "message_start" and not first_assistant_seen:
                        first_assistant_seen = True
                        self._on_started()
                    elif inner.get("type") == "content_block_delta":
                        delta = inner.get("delta")
                        if isinstance(delta, dict) and delta.get("type") == "text_delta":
                            piece = str(delta.get("text") or "")
                            if piece:
                                if not first_assistant_seen:
                                    first_assistant_seen = True
                                    self._on_started()
                                kind = (
                                    "subagent" if event.get("parent_tool_use_id") else "assistant"
                                )
                                key = str(event.get("parent_tool_use_id") or "")
                                live = partial_live.get(key)
                                if live is None:
                                    live = partial_live[key] = SentenceStreamer(
                                        functools.partial(self._on_activity, kind)
                                    )
                                live.feed(piece)

            elif etype == "rate_limit_event":
                warning = claude_limit_warning(event.get("rate_limit_info"))
                if warning:
                    self._on_activity("notice", warning)

            elif etype == "assistant":
                if not first_assistant_seen:
                    first_assistant_seen = True
                    self._on_started()
                # Work done by a subagent carries the id of the tool call that
                # started it. It is shown live like everything else, but it is
                # somebody else's running commentary rather than the answer to
                # this turn — five agents' worth of it would otherwise be
                # collected up and read out as the reply.
                from_subagent = bool(event.get("parent_tool_use_id"))
                message = event.get("message") or {}
                if not from_subagent:
                    # Everything the model was sent for this reply, which is
                    # how full the conversation's context now is.
                    usage = message.get("usage")
                    if isinstance(usage, dict):
                        used = sum(
                            int(usage.get(key) or 0)
                            for key in (
                                "input_tokens",
                                "cache_creation_input_tokens",
                                "cache_read_input_tokens",
                                "output_tokens",
                            )
                        )
                        if used:
                            window = getattr(self, "context_tokens", (0, 0))[1]
                            self.context_tokens = (used, window)
                            # The window is this model's, not a subagent's.
                            self._context_model = str(message.get("model") or "")
                for block in message.get("content") or []:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        # Claude's own words, as it writes them — streamed live to
                        # the list so the user reads the narration as it happens.
                        text = (block.get("text") or "").strip()
                        if text:
                            if not from_subagent:
                                text_parts.append(text)
                            kind = "subagent" if from_subagent else "assistant"
                            live = partial_live.pop(
                                str(event.get("parent_tool_use_id") or ""), None
                            )
                            if live is not None:
                                # Already said live as partials: only the
                                # unfinished tail is still unheard.
                                live.flush()
                            else:
                                # Somebody else's running commentary, not this
                                # turn's reply. Shown as a row either way; in
                                # Keep up it is what a fan-out would drown in.
                                self._on_activity(kind, text)
                    elif btype == "thinking":
                        # Extended-thinking blocks: Claude reasoning about what to
                        # do next. Surfaced live so the user hears the plan while
                        # the work happens, but kept out of `text_parts` — it is
                        # not part of the answer.
                        thought = (block.get("thinking") or "").strip()
                        if thought:
                            self._on_activity("thinking", thought)
                    elif btype == "redacted_thinking":
                        self._on_activity("thinking", "[redacted thinking]")
                    elif btype == "tool_use":
                        # The live "what is it doing" signal: announced when the
                        # tool is called, with its result following separately.
                        params = block.get("input")
                        name = str(block.get("name") or "tool")
                        call_id = block.get("id")
                        if isinstance(call_id, str):
                            tool_names[call_id] = name
                            if name in CLAUDE_TASK_TOOLS and isinstance(params, dict):
                                task_calls[call_id] = (name, params)
                        self._on_activity(
                            "tool",
                            _tool_use_label(
                                name,
                                params if isinstance(params, dict) else {},
                            ),
                        )

            elif etype == "user":
                # Tool results come back as user-role messages. Surface the actual
                # output (file contents, command output, …) as its own live row.
                message = event.get("message") or {}
                for block in message.get("content") or []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_result":
                        result = _tool_result_text(block.get("content"))
                        task_call = task_calls.pop(str(block.get("tool_use_id") or ""), None)
                        report_tasks = getattr(self, "on_tasks", None)
                        if task_call and report_tasks and not block.get("is_error"):
                            report_tasks("claude", task_call[0], task_call[1], result)
                        if not result:
                            continue
                        if not block.get("is_error"):
                            self._on_activity("result", result)
                            continue
                        # A refused or failed call. Announced as an action
                        # rather than as output: read out as an ordinary
                        # "Result:" row, a refusal sounded exactly like the
                        # command output it never produced.
                        call_id = block.get("tool_use_id")
                        name = (
                            tool_names.get(call_id, "tool") if isinstance(call_id, str) else "tool"
                        )
                        refused = _looks_like_refusal(result)
                        first = " ".join(result.split())[:200]
                        verb = "Refused" if refused else "Failed"
                        self._on_activity("tool", f"{verb}: {name}: {first}")
                        # The full text still deserves a row of its own; the
                        # line above is the part that gets spoken.
                        self._on_activity("result", result)
                        if (
                            refused
                            and not gap_note_said
                            and self._permission_mode == "bypassPermissions"
                        ):
                            gap_note_said = True
                            self._on_activity("tool", _CLAUDE_BYPASS_GAP_NOTE)

            elif etype == "result":
                complete = True
                usage_by_model = event.get("modelUsage") or {}
                main = usage_by_model.get(getattr(self, "_context_model", ""))
                if isinstance(main, dict) and getattr(self, "context_tokens", None):
                    window = int(main.get("contextWindow") or 0)
                    if window:
                        self.context_tokens = (self.context_tokens[0], window)
                queued = self._count(event.get("queued_turn_count"))
                if not event.get("is_error") and queued:
                    # A resumed CLI can have a turn of its own to run first.
                    # That turn's result arrives before ours and says nothing.
                    logging.getLogger("blindpilot.claude").info(
                        "result for another turn: %d still queued, reading on", queued
                    )
                    continue
                if event.get("is_error"):
                    detail = (event.get("result") or "").strip()
                    if _looks_like_auth_error(detail):
                        self._fail(AUTH_HINT)
                        return
                    note = detail or "Claude Code returned an error"
                    if text_parts:
                        # The turn answered before it ended badly. Saying how it
                        # ended instead of the answer threw away work.
                        self._on_activity("notice", note)
                        break
                    self._fail(note)
                    return
                # The turn is over. The process is not: it belongs to the tab,
                # and whatever it left running keeps running.
                break

        # A turn that ended on its result without a final whole message for
        # a block still owes the listener that block's unfinished tail.
        for live in partial_live.values():
            live.flush()
        partial_live.clear()

        if self._cancelled:
            return

        if died:
            # The stream ends before the process is reaped, so the code is
            # waited for rather than asked for once.
            rc = session.wait()
            session.wait_stderr()
            stderr_text = session.stderr_since(mark)
            if _looks_like_auth_error(stderr_text):
                self._fail(AUTH_HINT)
                return
            if self._retry_without_prompt_tool(stderr_text):
                # The installed Claude Code is older than the flag. Turn it off
                # for the rest of the session and send the message again.
                self._do_run()
                return
            if self._retry_without_partial_messages(stderr_text):
                self._do_run()
                return
            self._log_unfinished_turn(rc, complete, stderr_text)
            detail = f": {stderr_text}" if stderr_text else ""
            if not detail:
                detail = (
                    " without finishing the turn, and without saying why. "
                    f"BlindPilot kept a note of it in {self._diagnostic_path()}."
                )
            note = self._ending_note(rc, detail)
            if not text_parts:
                self._fail(note)
                return
            self._on_activity("notice", note)
            self._on_complete("\n\n".join(text_parts).strip())
            return

        if not text_parts:
            self._on_activity("notice", "Claude Code finished the turn without saying anything.")
        self._on_complete("\n\n".join(text_parts).strip())
