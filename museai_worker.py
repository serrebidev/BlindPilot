"""One muse.ai turn: send the message to the agent and wait for its reply.

The conversation is the main chat or a side chat. Selecting the backend opens
the main chat; a new conversation's first message opens a side chat
(``muse-cli session-start``) and reports its id as the session, so later
messages and a reopened conversation land in the same chat. The message goes
out with ``muse-cli send --thread <id> --wait <seconds>``, which watches the
chat's history and prints the agent's reply as JSON once it is complete.
While it waits, the chat is read every few seconds and each status update the
agent posts (``## Status ...``, progress notes) goes into the conversation as
it arrives, so the transcript holds everything the agent said, not only its
last word. Connection notices are left out. Alongside, ``muse-cli watch``
(patched by ``museai_cli_patch`` to subscribe like the web client) streams the
chat's live activity, and its text ("Fetching release") goes on the status
line as the web client shows it.

Never ``--wait 0``: the CLI closes the stream before the message is
delivered, and the message is lost without an error.

Cancelling stops the wait, not the agent: once a message is delivered,
muse.ai finishes the work on its own machine, and the reply turns up in the
muse.ai chat.

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from typing import Optional

from agent_backends import (
    BACKEND_MUSEAI,
    _TurnWorker,
    end_process_group,
    find_backend_cli,
    no_window_kwargs,
    own_group_kwargs,
    subprocess_env,
)

# Real muse.ai work (browsing, connectors, SSH) takes minutes; an hour covers
# it. Past that the reply still lands in the chat, just not in this turn.
REPLY_WAIT_SECONDS = 3600
# How often the chat is read for status updates while the agent works.
STATUS_POLL_SECONDS = 5
# An answer ends the turn once the chat has been quiet this long.
QUIET_SECONDS = 45
# How far the PC's clock may run ahead of muse.ai's before a fresh status looks old.
STATUS_SKEW_SECONDS = 60
_TITLE_CHARS = 60
# The agent coming online is not something it said about the work.
_CONNECTION_NOTICES = (
    "connected",
    "reconnected",
    "online",
    "you're connected",
    "you are connected",
)


def _is_connection_notice(text: str) -> bool:
    return text.strip(" .!#\n").casefold() in _CONNECTION_NOTICES


def _json_from(text: str) -> Optional[dict]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


class MuseAiWorker(_TurnWorker):
    """Run one muse.ai turn through ``muse-cli``."""

    _backend = BACKEND_MUSEAI

    def _setup(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        # Each message's text as last read: muse.ai marks a message complete
        # while its text is still arriving, so one is taken only once two
        # reads in a row agree on it.
        self._last_text: dict[str, str] = {}
        # (seq, when) of every agent event last read, text or not: muse.ai's
        # working steps carry no text, but their count and time say it is busy.
        self._steps: list[tuple[int, int]] = []
        # The newest event seen in the chat, for the tab to watch past.
        self.last_seq = 0
        self.follow_after: Optional[int] = None
        self._step_said = ""
        self._approval_seen: set[str] = set()
        self._approval_error_said = False
        # `muse-cli watch`, streaming the chat's live status ("Fetching
        # release") while the turn runs; while it does, the step count is
        # left alone.
        self._watcher: Optional[subprocess.Popen] = None
        self._live_status = False

    def _check_approvals(self, binary: str) -> None:
        from museai_backend import (
            museai_result,
            museai_pending,
            museai_item_text,
            museai_decision_args,
            museai_allowed_decisions,
        )

        code, out, _err = self._run(binary, ["raw", "egress.approvals"], 60)
        try:
            pending = museai_pending(museai_result(_json_from(out) if code == 0 else None))
        except ValueError:
            if not self._approval_error_said:
                self._on_activity(
                    "tool",
                    "muse.ai approvals could not be checked. Open Model, muse.ai, Approvals to retry.",
                )
                self._approval_error_said = True
            return
        for approval in pending:
            approval_id = str(approval["approval_id"])
            if approval_id in self._approval_seen or self._cancelled:
                continue
            self._approval_seen.add(approval_id)
            try:
                allowed = museai_allowed_decisions(approval)
                request = museai_item_text("approvals", dict(approval, _pending=True))
            except ValueError:
                self._on_activity(
                    "tool",
                    "muse.ai returned unreadable approval details. Review the request in the muse.ai app.",
                )
                continue
            if self._on_permission is None or not {"allow_once", "deny"}.issubset(allowed):
                self._on_activity(
                    "tool",
                    "muse.ai needs an approval. Open Model, muse.ai, Approvals to review it.",
                )
                continue
            answer = self._on_permission(
                "Perform an action",
                {"Request": request},
                [],
            )
            if self._cancelled:
                return
            decision = "allow_once" if answer.get("behavior") == "allow" else "deny"
            args = museai_decision_args(approval_id, decision, str(answer.get("message") or ""))
            code, out, _err = self._run(binary, args, 60)
            try:
                museai_result(_json_from(out) if code == 0 else None)
            except ValueError:
                self._on_activity(
                    "tool",
                    "The muse.ai approval decision could not be sent. Open Model, muse.ai, Approvals to retry.",
                )

    def _start_status(self, binary: str, session: str) -> None:
        """Put what muse.ai says it is doing on the status line as it changes."""
        env = subprocess_env(binary)
        env["PYTHONIOENCODING"] = "utf-8"
        env["BLINDPILOT_MUSEAI_WATCH"] = session
        try:
            proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                [binary, "watch"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                **own_group_kwargs(),
                **no_window_kwargs(),
            )
        except (OSError, ValueError):
            return
        self._watcher = proc
        threading.Thread(target=self._read_status, args=(proc, session), daemon=True).start()

    def _read_status(self, proc: subprocess.Popen, session: str) -> None:
        said = listed = ""
        # The subscription replays from the start of what the server keeps; a
        # status older than this watcher (less a minute for clock skew) is
        # history, not current work.
        since_ms = (time.time() - STATUS_SKEW_SECONDS) * 1000
        # Rows are permanent, so they belong to this turn's work only. A sent
        # message's work starts once the message itself comes through the
        # stream; a reply already being written before then (a job left
        # running after Stop) is not this turn's. Following sends nothing, so
        # the work under way is the work being followed.
        prompt = " ".join((self._prompt or "").split())
        anchored = not prompt
        earlier: set[str] = set()
        for line in proc.stdout or ():
            event = _json_from(line) or {}
            raw = event.get("payload")
            body: dict = raw if isinstance(raw, dict) else {}
            name = event.get("event") or event.get("type")
            stamped = event.get("ts_ms")
            if isinstance(stamped, (int, float)) and stamped < since_ms:
                continue
            if name == "message.user" and not anchored:
                sent = body.get("content") or body.get("display_text") or ""
                anchored = " ".join(str(sent).split()) == prompt
                continue
            if name != "agent.status":
                continue
            if body.get("session_id") not in (None, session):
                continue
            reply = str(body.get("message_id") or "")
            if not anchored and reply:
                earlier.add(reply)
            text = " ".join(str(body.get("activity_text") or "").split())
            if not text or body.get("activity_code") == "online" or self._settled.is_set():
                continue
            generic = text.startswith("is ")
            text = f"muse.ai {text}" if generic else f"muse.ai: {text}"
            if text != said:
                said = text
                self._live_status = True
                self._on_activity("step", text)
            # What it is actually doing ("Fetching release") also stays in the
            # conversation; "is working"/"is responding" would only be noise,
            # and returning to the same work after one of them is no new step.
            ours = anchored and reply not in earlier
            if ours and not generic and text != listed:
                listed = text
                self._on_activity("tool", text)
        # The stream ended early, so the step count takes over again.
        self._live_status = False

    def steer(self, text: str) -> bool:
        # The message is already with the agent; a second one waits its turn.
        return False

    def cancel(self) -> None:
        self._cancelled = True
        proc = self._proc
        if proc is not None:
            end_process_group(proc)

    def _run(
        self, binary: str, args: list[str], timeout: Optional[float], track: bool = False
    ) -> tuple[Optional[int], str, str]:
        """Run muse-cli once. `track` marks the send, the one Stop ends."""
        env = subprocess_env(binary)
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                [binary, *args],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                **own_group_kwargs(),
                **no_window_kwargs(),
            )
        except (OSError, ValueError) as exc:
            return None, "", str(exc)
        if track:
            self._proc = proc
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            end_process_group(proc)
            out, err = proc.communicate()
        finally:
            if track:
                self._proc = None
        return proc.returncode, out or "", err or ""

    def _messages(self, binary: str, session: str) -> list[tuple[int, str, str, bool]]:
        """The chat's finished agent messages, oldest first, as (seq, message
        id, text, direct). Direct means an answer to a message, rather than an
        update the agent posted on its own (those carry a reply-to id)."""
        self._check_approvals(binary)
        code, out, _err = self._run(
            binary, ["history", "--thread", session, "--limit", "40", "--raw"], 60
        )
        payload = _json_from(out) if code == 0 else None
        events = (payload or {}).get("chat_events") or []
        found = []
        steps = []
        for event in events if isinstance(events, list) else []:
            if not isinstance(event, dict) or event.get("event_name") != "message.assistant":
                continue
            raw = event.get("payload")
            body: dict = raw if isinstance(raw, dict) else {}
            steps.append(
                (
                    int(body.get("seq") or event.get("seq") or 0),
                    int(event.get("occurred_at_ms") or 0),
                )
            )
            text = str(body.get("content") or body.get("display_text") or "").strip()
            # No status yet means the agent is still writing it.
            if not text or not body.get("status") or _is_connection_notice(text):
                continue
            seq = body.get("seq") or event.get("seq") or 0
            message_id = str(body.get("message_id") or event.get("message_id") or "")
            direct = not (body.get("reply_to_message_id") or event.get("reply_to_message_id"))
            found.append((int(seq), message_id, text, direct))
        if code == 0:
            self._steps = steps
            if steps:
                self.last_seq = max(self.last_seq, max(seq for seq, _when in steps))
        return sorted(found)

    def _report_steps(self, baseline: int) -> None:
        """Keep the tab's status line on how far muse.ai has got, quietly."""
        newer = [when for seq, when in self._steps if seq > baseline]
        if not newer or self._live_status:
            return
        latest = time.strftime("%I:%M:%S %p", time.localtime(max(newer) / 1000)).lstrip("0")
        count = len(newer)
        said = f"muse.ai is working: {count} step{'s' if count != 1 else ''} so far, latest at {latest}"
        if said != self._step_said:
            self._step_said = said
            self._on_activity("step", said)

    def _steady(
        self, binary: str, session: str, wait: bool = True
    ) -> list[tuple[int, str, str, bool]]:
        """The chat's messages whose text has stopped changing (all of them
        when `wait` is false, once the agent has answered and nothing more is
        coming)."""
        current = self._messages(binary, session)
        ready = [m for m in current if not wait or self._last_text.get(m[1]) == m[2]]
        self._last_text = {m[1]: m[2] for m in current}
        return ready

    def _do_run(self) -> None:
        if self._cancelled:
            return
        binary = find_backend_cli(BACKEND_MUSEAI)
        if not binary:
            self._fail("muse-cli is not installed. Run: uv tool install muse-cli")
            return
        session = self._session_id or ""
        if not session:
            title = " ".join((self._prompt or "").split())[:_TITLE_CHARS] or "BlindPilot"
            code, out, err = self._run(binary, ["session-start", "--title", title], 120)
            started = _json_from(out) if code == 0 else None
            session = str((started or {}).get("session_id") or "")
            if not session:
                self._fail(f"muse.ai did not open a chat. {(err or out).strip()[-300:]}".strip())
                return
            self._session_id = session
            self._on_session(session)
        if self._cancelled:
            return
        if not (self._prompt or "").strip():
            self._follow(binary, session)
            return
        self._on_started()
        self._start_status(binary, session)
        self._on_activity("tool", "Sent to muse.ai. Waiting for its reply.")
        # Everything the agent says in this chat from here on is part of the
        # turn: its status updates while it works as well as the answer.
        baseline = max((m[0] for m in self._messages(binary, session)), default=0)
        if self._cancelled:
            return
        relayed: set[str] = set()

        def relay(skip: str = "", wait: bool = True) -> None:
            for seq, message_id, text, _direct in self._steady(binary, session, wait):
                if seq <= baseline or message_id in relayed or message_id == skip:
                    continue
                relayed.add(message_id)
                self._on_activity("assistant", text)

        sent: dict[str, tuple[Optional[int], str, str]] = {}
        args = ["send", "--thread", session, "--wait", str(REPLY_WAIT_SECONDS), self._prompt]
        sender = threading.Thread(
            target=lambda: sent.update(
                result=self._run(binary, args, REPLY_WAIT_SECONDS + 120, track=True)
            ),
            daemon=True,
        )
        sender.start()
        while sender.is_alive():
            sender.join(STATUS_POLL_SECONDS)
            if self._cancelled:
                break
            if sender.is_alive():
                relay()
                self._report_steps(baseline)
        sender.join(10)
        code, out, err = sent.get("result", (None, "", ""))
        if self._cancelled:
            if not self._settled.is_set():
                self._settled.set()
                self._on_complete(
                    "Stopped waiting. muse.ai keeps working, and its reply will be in the muse.ai chat."
                )
            return
        result = _json_from(out) if code == 0 else None
        if not result or not result.get("sent"):
            detail = (err or out).strip()[-400:]
            self._fail(f"muse.ai did not take the message. {detail}".strip())
            return
        reply = result.get("reply")
        text = str(reply.get("text") or "").strip() if isinstance(reply, dict) else ""
        final_id = str(reply.get("message_id") or "") if isinstance(reply, dict) else ""
        # Status updates posted just before the answer, in order, then the answer.
        relay(skip=final_id, wait=False)
        if not text:
            self._fail(
                "muse.ai has the message but did not answer within an hour. "
                "Its reply will appear in the muse.ai chat."
            )
            return
        if not final_id or final_id not in relayed:
            self._on_activity("assistant", text)
            relayed.add(final_id)
        # muse.ai often answers and carries on (background workers, follow-up
        # updates), so the turn stays open until the chat has gone quiet.
        later = self._watch(binary, session, baseline, relayed, text)
        self._settled.set()
        self._on_complete(later or text)

    def _follow(self, binary: str, session: str) -> None:
        """Sit in on a chat whose last message has no answer yet.

        A reopened conversation can still be running on muse.ai's machine.
        Nothing is sent: every update it posts from here is relayed, and the
        turn ends once it has answered and the chat has gone quiet.
        """
        self._on_started()
        self._start_status(binary, session)
        self._on_activity("tool", "muse.ai is still working on this. Following it.")
        messages = self._messages(binary, session)
        baseline = (
            self.follow_after
            if self.follow_after is not None
            else max((m[0] for m in messages), default=0)
        )
        relayed: set[str] = set()
        answer = self._watch(binary, session, baseline, relayed)
        if self._cancelled:
            self._settled.set()
            self._on_complete("Stopped following. muse.ai keeps working on its own machine.")
            return
        if answer:
            self._settled.set()
            self._on_complete(answer)
            return
        self._fail(
            "muse.ai is still working after an hour. Reopen the chat later to read its answer."
        )

    def _watch(
        self, binary: str, session: str, baseline: int, relayed: set[str], answer: str = ""
    ) -> Optional[str]:
        """Relay what the agent posts until it has answered and gone quiet.

        Its latest direct answer is the turn's answer. Quiet means no event of
        any kind, working steps included, for QUIET_SECONDS: an answer with
        work still visibly going on is not the end of the turn. Returns None
        if Stop was pressed.
        """
        deadline = time.monotonic() + REPLY_WAIT_SECONDS
        while time.monotonic() < deadline:
            for _ in range(int(STATUS_POLL_SECONDS * 10) or 1):
                if self._cancelled:
                    return None
                time.sleep(0.1)
            ready = self._steady(binary, session)
            self._report_steps(baseline)
            for seq, message_id, text, direct in ready:
                if seq <= baseline or message_id in relayed:
                    continue
                relayed.add(message_id)
                self._on_activity("assistant", text)
                if direct:
                    answer = text
            latest = max((when for seq, when in self._steps if seq > baseline), default=0)
            if answer and time.time() - latest / 1000 >= QUIET_SECONDS:
                return answer
        return answer

    def _teardown(self) -> None:
        for proc in (self._proc, self._watcher):
            if proc is not None and proc.poll() is None:
                end_process_group(proc)
        self._proc = None
        self._watcher = None
