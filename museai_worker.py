"""One muse.ai turn: send the message to the agent and wait for its reply.

The conversation is a muse.ai side chat. A tab's first message opens one
(``muse-cli session-start``) and reports its id as the session, so later
messages and a reopened conversation land in the same chat. The message goes
out with ``muse-cli send --thread <id> --wait <seconds>``, which watches the
chat's history and prints the agent's reply as JSON once it is complete.
While it waits, the chat is read every few seconds and each status update the
agent posts (``## Status ...``, progress notes) goes into the conversation as
it arrives, so the transcript holds everything the agent said, not only its
last word. Connection notices are left out.

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

    def _messages(self, binary: str, session: str) -> list[tuple[int, str, str]]:
        """The chat's finished agent messages as (seq, message id, text), oldest first."""
        code, out, _err = self._run(
            binary, ["history", "--thread", session, "--limit", "40", "--raw"], 60
        )
        payload = _json_from(out) if code == 0 else None
        events = (payload or {}).get("chat_events") or []
        found = []
        for event in events if isinstance(events, list) else []:
            if not isinstance(event, dict) or event.get("event_name") != "message.assistant":
                continue
            raw = event.get("payload")
            body: dict = raw if isinstance(raw, dict) else {}
            text = str(body.get("content") or body.get("display_text") or "").strip()
            # No status yet means the agent is still writing it.
            if not text or not body.get("status") or _is_connection_notice(text):
                continue
            seq = body.get("seq") or event.get("seq") or 0
            message_id = str(body.get("message_id") or event.get("message_id") or "")
            found.append((int(seq), message_id, text))
        return sorted(found)

    def _do_run(self) -> None:
        if self._cancelled:
            return
        binary = find_backend_cli(BACKEND_MUSEAI)
        if not binary:
            self._fail("muse-cli is not installed. Run: uv tool install muse-cli")
            return
        session = self._session_id or ""
        if not session:
            title = " ".join(self._prompt.split())[:_TITLE_CHARS] or "BlindPilot"
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
        self._on_started()
        self._on_activity("tool", "Sent to muse.ai. Waiting for its reply.")
        # Everything the agent says in this chat from here on is part of the
        # turn: its status updates while it works as well as the answer.
        baseline = max((seq for seq, _id, _text in self._messages(binary, session)), default=0)
        relayed: set[str] = set()

        def relay(skip: str = "") -> None:
            for seq, message_id, text in self._messages(binary, session):
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
        relay(skip=final_id)
        if not text:
            self._fail(
                "muse.ai has the message but did not answer within an hour. "
                "Its reply will appear in the muse.ai chat."
            )
            return
        self._settled.set()
        if not final_id or final_id not in relayed:
            self._on_activity("assistant", text)
        self._on_complete(text)

    def _teardown(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            end_process_group(proc)
        self._proc = None
