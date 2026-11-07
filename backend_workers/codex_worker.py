"""Provider-specific turn worker; shared lifecycle stays in agent_backends."""

from __future__ import annotations

import queue
from typing import Optional

import agent_backends as ab
import backend_pool
from agent_backends import CodexServer, _TurnWorker
from markdown_rows import SentenceStreamer


class CodexWorker(_TurnWorker):
    """Run one Codex turn through the official app-server JSONL protocol."""

    _backend = ab.BACKEND_CODEX

    def _setup(self) -> None:
        # Borrowed from the pool, which starts and stops it; other tabs read it too.
        self._server: Optional[CodexServer] = None
        self._held: Optional[backend_pool.HeldProcess] = None
        # The pool key this turn borrowed its process under, which carries the
        # Account it runs on. Remembered rather than recomputed, so a choice
        # changed while the turn is running cannot make the turn give up
        # somebody else's process.
        self._pool_key: Optional[tuple] = None
        # `_plan_account` -- the Account this turn runs on, or None for Codex's
        # own login -- is the base class's, given by the tab that owns the
        # conversation. What decides whether a refusal is a plan's, and so
        # whether it is reported in the chat half's words.
        # Where this turn's stderr begins in a process older than the turn.
        self._stderr_mark = 0
        # This turn's queue, read by it alone. Unbounded, because a turn's own
        # answer text must never be dropped to make room for more of it. Empty
        # until `expect` or `attach` says what should arrive in it; one of
        # those has to be called before it is read, because they are also what
        # wakes a reader of a server already closed.
        self._inbox: Optional["queue.Queue[object]"] = None
        self._expected: list[int] = []
        self._borrowed = False
        self._thread_id = self._session_id or ""
        self._turn_id = ""
        # Agents this turn's thread spawned (spawnAgent), by their own thread
        # id, with where each stands, and the turn each is running. Their
        # threads are read on this turn's inbox so what they do reaches the
        # window and what they ask is answered, not declined unread.
        self._children: dict[str, str] = {}
        self._child_turns: dict[str, str] = {}
        # The conversation given up after an unconfirmed interrupt. The next
        # turn resumes it from disk; the server stays, other tabs are on it.
        self.abandoned_thread = ""
        # Set when Codex answered `thread/resume` with an error: the tab's
        # session id names a conversation that no longer exists, and the
        # window clears it so the next message starts a new one.
        self.lost_session = False
        # Set when the Account this conversation runs on could not produce a
        # token, so nothing was started on it. The window lets go of that
        # Account while no conversation has begun, where retrying its dead id
        # would fail every turn.
        self.plan_token_failed = False
        # True from the moment `turn/start` goes out, before any id names it.
        self._turn_requested = False
        # Decides whether the turn was asked for or the cancel landed first.
        self._start_gate = ab.threading.Lock()
        # Set once `_turn_id` is final, so a cancel can wait instead of polling.
        self._turn_id_known = ab.threading.Event()
        # Set when the turn ended by itself, so a cancel has nothing to interrupt.
        self._finished = ab.threading.Event()
        # The turn id registered with the server, given back exactly once.
        self._watched = ""
        # Until this turn asks for a turn, anything naming one names an older one.
        self._turn_asked = False
        # Turns known to be somebody else's, however late the rest of them arrive.
        self._stale_turns: set[str] = set()
        self._assistant_parts: list[str] = []
        self._assistant_delta_seen: set[str] = set()
        self._assistant_streams: dict[str, list[str]] = {}
        # Live sentence release per message item, so the listener hears a
        # Codex answer as it is written rather than when its item completes.
        self._assistant_live: dict[str, SentenceStreamer] = {}
        self._reasoning_streams: dict[str, list[str]] = {}
        self._tool_outputs: dict[str, list[str]] = {}

    @property
    def _stderr_lines(self) -> list[str]:
        """What the shared server has said on stderr, newest last.

        Only what has been said since this turn borrowed the process. The pipe
        belongs to the process now rather than the turn, because one process
        outlives many turns and is read by one thread for all of them -- so
        without the mark, a death at turn fifty would be explained with a
        warning from turn three, out loud, to somebody who cannot scroll back
        through the transcript and check.

        It cannot be called `_stderr`: `threading.Thread` keeps a copy of the
        real `sys.stderr` under that name, to report a thread that dies.
        """
        server = self._server
        return server.stderr_since(self._stderr_mark) if server is not None else []

    def _send(self, message: dict) -> bool:
        server = self._server
        if server is None:
            return False
        return server.send(message)

    def _next_id(self) -> int:
        # Ids are the server's, not the turn's: several tabs write to one
        # stdin, and two turns numbering from ten would each be handed the
        # other's replies. Every caller runs after `_borrow_server` set it.
        return ab.cast(ab.CodexServer, self._server).next_id()

    def steer(self, text: str) -> bool:
        if not self.accepting_input() or not self._thread_id or not self._turn_id:
            return False
        return self._send(
            {
                "method": "turn/steer",
                "id": self._next_id(),
                "params": {
                    "threadId": self._thread_id,
                    "input": [{"type": "text", "text": text}],
                    "expectedTurnId": self._turn_id,
                },
            }
        )

    def cancel(self) -> None:
        """Stop this turn without stopping the server the other tabs share.

        Codex's app-server multiplexes: one process holds every tab's thread.
        Killing it here would end four other conversations to stop one, so the
        usual "kill if unsure" is replaced by a middle rung. The interrupt is
        sent and waited on, and if Codex does not confirm the turn stopped, the
        THREAD is abandoned rather than the process: the next turn resumes it
        from its rollout, and the wedged conversation is the only thing that
        pays.

        This runs off the window's thread, and everything it waits on is
        bounded, because the window is waiting on it: at worst the grace for
        naming the turn and then the verify, which together fit inside the
        teardown budget with room for the join that follows.
        """
        # Read before the flag is set, so that this only ever means "the turn
        # had already ended when Stop was pressed" and never "the turn ended
        # because of this cancel".
        finished = self._finished.is_set()
        with self._start_gate:
            # Under the gate, so that a turn about to be asked for either sees
            # this and is never asked for, or was asked for first and is read
            # as running below. Between the two there is no third state in
            # which Codex is starting a turn nobody here knows about.
            self._cancelled = True
            server = self._server
            thread_id = self._thread_id
            asked_for_a_turn = self._turn_requested
        self._accepting_input.clear()
        if finished:
            # The answer landed while Stop was on its way. There is nothing to
            # interrupt, and waiting for a confirmation of it would spend the
            # whole verify budget and then give up a perfectly good thread:
            # `_release` has already handed back the watch whose completion
            # the reader set, so a fresh one would never be set at all.
            return
        if server is None or not thread_id or not asked_for_a_turn:
            # No turn of this conversation's has been asked for, and the gate
            # means none ever will be: the turn reads the flag set above before
            # it asks. So there is nothing running to stop, and nothing to give
            # up a perfectly good conversation over.
            return
        turn_id = self._turn_id
        if not turn_id and self._turn_requested:
            # Stop landed between asking for a turn and being told its id. The
            # turn is running -- writing files, spending tokens -- and the only
            # thing missing is its name, which is usually already on its way.
            self._turn_id_known.wait(ab._CODEX_TURN_ID_GRACE_SECONDS)
            turn_id = self._turn_id
        if not turn_id:
            if self._turn_requested:
                # A turn was asked for and never named, so there is nothing to
                # interrupt by name. Saying the tab stopped while trusting the
                # conversation anyway would be the lie: whatever that turn goes
                # on to do is not in this thread's history as the next prompt
                # would read it, so the thread is given up instead.
                self._abandon(thread_id)
            return
        self._stop_children(server)
        if not server.interrupt(thread_id, turn_id, ab._CODEX_INTERRUPT_VERIFY_SECONDS):
            self._abandon(thread_id, turn_id)

    def _ask_for_a_turn(self) -> bool:
        """Commit to starting a turn, unless Stop got here first.

        The reply that names the conversation and the Stop that arrives while
        this turn is waiting for it are on different threads, and pressing
        Escape within a second of Send puts them in that order. Reading the
        reply and asking for a turn anyway leaves the turn running under a tab
        that has said it stopped -- and, because nothing here has a name to
        interrupt or give up, leaves its whole answer to arrive in the middle
        of the next prompt's.

        The cheapest place to close that is before the request goes out. A turn
        that is never asked for needs no interrupt, and costs the conversation
        nothing: the next prompt resumes it in the ordinary way rather than
        paying for a resume from disk it did not need.

        Returning True says the request is going out, so `cancel` from here on
        finds something running and stops it by the usual route.
        """
        with self._start_gate:
            if self._cancelled:
                return False
            # Set before the send, not after the reply: from here on a turn may
            # be running that a cancel has no name for, and treating that as
            # "nothing was started" is exactly how one gets left running under
            # a tab that says it stopped.
            self._turn_requested = True
        return True

    def _abandon(self, thread_id: str, turn_id: str = "") -> None:
        """Give up this conversation, and warn whoever resumes it next.

        Giving up the thread is only half of it. The turn is STILL GENERATING
        on the server -- that is what an unconfirmed interrupt means -- and the
        next prompt in this tab resumes the same conversation, so that turn's
        words arrive in the middle of somebody else's. `_stale_turns` is how a
        turn recognises another turn's words, and it can only learn a name from
        messages that arrive before this turn asks for anything. A turn that is
        quiet across that window -- mid tool call -- and speaks after it used to
        have its text appended to the next answer.

        So the name is left on the process, which outlives this worker and is
        the one thing the next worker is certain to be holding.
        """
        self.abandoned_thread = thread_id
        server = self._server
        if server is not None:
            server.abandon_turn(thread_id, turn_id)

    def _teardown(self) -> None:
        # This turn is over, however it ended. Read by `cancel` before it
        # interrupts anything, because the watch it would wait on is about
        # to be given back below.
        self._finished.set()
        # Nothing more will be read, so no further name can be learned. A
        # cancel racing the end of the turn stops waiting for one rather
        # than spending its whole grace on a thread that has gone.
        self._turn_id_known.set()
        # The process belongs to the pool now, not to this turn. It is
        # stopped when the conversation goes away, when it is found dead,
        # or when the reaper decides nobody is using it. All the turn
        # gives back is its place in the server's routing tables.
        self._release()

    def _release(self) -> None:
        """Stop the shared reader delivering to a turn that has ended."""
        server = self._server
        if server is None:
            return
        if self._watched:
            server.forget_turn(self._watched)
            self._watched = ""
        server.unexpect(self._expected)
        self._expected = []
        inbox = self._inbox
        if inbox is not None:
            # By identity: a turn cancelled before it read the reply that named
            # its conversation never learned the id to detach by.
            server.detach_listener(inbox)
        held = self._held
        if held is not None:
            # Touched before the borrow is given back, so a reaper sweep
            # between the two sees a busy server or a freshly touched one,
            # never one idle since the start of a turn that just finished.
            held.touch()
        if self._borrowed:
            self._borrowed = False
            server.give_back()
            # A turn that has just finished may be the last one on a process
            # whose Account was signed out, deleted or switched away from while
            # it ran. That drop was waiting for exactly this.
            ab.drop_pending_codex_processes()

    def _borrow_server(self) -> Optional[backend_pool.HeldProcess]:
        """The app-server this turn will speak through, or None having failed.

        Taking and handing back happen under one lock so that two tabs sending
        their first Codex message together cannot each launch a server, with
        the second one kept displacing and stopping the first mid-turn.

        Which process this is depends on the Account the conversation is bound
        to: the tabs on one Account share its process, and a tab on another
        Account is given -- or starts -- that Account's own. A conversation
        resumed from history comes through here too, so it lands on the Account
        its conversation was held under rather than on whichever tab resumed
        it.

        The Account is the tab's answer, taken when the conversation began, and
        only a turn told `UNBOUND` reads the saved choice -- once, and then it
        is this turn's too. So choosing another Account cannot move a
        conversation that has already started, and the rest of a running turn
        cannot be sent somewhere else under it.
        """
        account = self._plan_account
        if account is ab.UNBOUND:
            account = ab.codex_plan_account()
        account = ab.cast(ab.Optional[ab.PlanAccount], account)
        self._plan_account = account
        key = ab.codex_pool_key(account)
        shared = ab.backend_pool.pool()
        with ab._CODEX_START_LOCK:
            held = shared.take(key)
            # A process started for this turn has said nothing that is not
            # this turn's, so the mark stays where it began.
            reused = held is not None
            if held is None:
                try:
                    started = ab._start_codex_server(plan_account=account)
                except OSError as exc:
                    self.plan_token_failed = isinstance(exc, ab.PlanTokenError)
                    self._fail(f"Failed to launch Codex: {exc}")
                    return None
                held = ab.backend_pool.HeldProcess(started, ab.codex_adapter())
                self._server = started
                # The handshake belongs to the process, not the turn: a reused
                # server has already been initialized and would refuse a second.
                if not self._handshake():
                    detail = self._why_it_died("Codex did not answer the initialize handshake")
                    self._server = None
                    held.stop()
                    self._fail(detail)
                    return None
            shared.keep(key, held)
            self._pool_key = key
            # Registered under the same lock the drop takes, so a turn that
            # has been handed this server is never counted as absent by a turn
            # deciding whether anyone still wants it.
            self._held = held
            server = ab.cast(ab.CodexServer, held.handle)
            self._server = server
            if reused:
                # Everything after this point on stderr is this turn's to be
                # explained by; anything before it belonged to another turn.
                self._stderr_mark = server.stderr_mark()
            self._inbox = ab.queue.Queue()
            server.borrow()
            self._borrowed = True
        return held

    def _discard_server(self) -> None:
        """Take a server that could not start this conversation out of the pool.

        A turn that cannot start or resume its thread has usually been handed a
        process that will not serve any turn -- an app-server whose handshake
        went unanswered, say -- and leaving it there would fail every prompt
        for the next quarter of an hour, where a per-turn process used to get a
        fresh start each time. It is only dropped while no other tab is holding
        it -- borrowing, not reading: a turn that has sent `thread/start` and
        is still waiting on the reply is bound to no conversation yet, and is
        exactly the turn that must not have its process stopped underneath it.
        This must never be the way one conversation's bad session id ends
        another's live turn.

        Dropped under the key this turn borrowed, which remembers the Account
        the process was started on: a choice changed mid-turn must not take a
        different Account's process down with it.
        """
        server = self._server
        if server is None:
            return
        key = self._pool_key or ab.codex_pool_key()
        with ab._CODEX_START_LOCK:
            if server.borrower_count() > 1:
                return
            ab.backend_pool.pool().drop(key)

    @staticmethod
    def _policy(mode: str) -> tuple[str, dict]:
        if mode == "bypassPermissions":
            return "never", {"type": "dangerFullAccess"}
        if mode == "plan":
            return "never", {"type": "readOnly", "networkAccess": False}
        if mode == "dontAsk":
            return "never", {"type": "workspaceWrite", "networkAccess": True}
        # Auto asks too, but Codex's automatic approval review answers in the
        # person's place (see `_reviewer`), so a step outside the sandbox is
        # judged rather than refused or put to somebody.
        return "on-request", {"type": "workspaceWrite", "networkAccess": True}

    @staticmethod
    def _reviewer(mode: str) -> dict:
        """Who answers Codex's approval requests: its own reviewer in Auto.

        ``auto_review`` is Codex's risk-judging subagent (app-server
        ``approvalsReviewer``, codex-cli 0.164.0). Every other mode leaves the
        requests to the person, Codex's default.
        """
        return {"approvalsReviewer": "auto_review"} if mode == "auto" else {}

    def _do_run(self) -> None:
        if self._compact and not self._session_id:
            self._fail("There is no Codex conversation to compact yet")
            return
        held = self._borrow_server()
        if held is None:
            return
        server = ab.cast(ab.CodexServer, held.handle)
        inbox = ab.cast("queue.Queue[object]", self._inbox)

        thread_request = self._next_id()
        if self._session_id:
            request = {
                "method": "thread/resume",
                "id": thread_request,
                "params": {"threadId": self._session_id},
            }
        else:
            approval, sandbox = self._policy(self._permission_mode)
            sandbox_type = str(sandbox.get("type", ""))
            params: dict = {
                "cwd": self._cwd,
                "approvalPolicy": approval,
                "sandbox": {
                    "readOnly": "read-only",
                    "workspaceWrite": "workspace-write",
                    "dangerFullAccess": "danger-full-access",
                }.get(sandbox_type, "workspace-write"),
                "serviceName": "blindpilot",
                **self._reviewer(self._permission_mode),
            }
            if self._model:
                params["model"] = self._model
            request = {"method": "thread/start", "id": thread_request, "params": params}
        # `binds_thread`: this is the reply that names the conversation, and
        # the reader subscribes it before handing the reply on, leaving no
        # window in which a notification for it has nowhere to go.
        self._expect(thread_request, binds_thread=True)
        self._send(request)

        turn_request = 0
        compact_request = 0
        started_notified = False
        while True:
            if self._cancelled:
                self._name_the_stopped_turn(inbox, turn_request)
                return
            try:
                queued = inbox.get(timeout=ab._CODEX_POLL_SECONDS)
            except ab.queue.Empty:
                continue
            if queued is ab._CODEX_CLOSED:
                break
            if queued is ab._CODEX_ASKED:
                # Everything read before here was said before this turn asked
                # for anything, so none of it was about this turn.
                self._turn_asked = True
                continue
            if not isinstance(queued, dict):
                continue
            message: dict = queued

            if message.get("id") == thread_request:
                error = message.get("error")
                if error and self._session_id:
                    # The conversation is gone, its rollout rotated or
                    # deleted, and only this tab's session id is wrong. The
                    # server is serving every other tab perfectly well.
                    self.lost_session = True
                    detail = self._error_text(error, "Codex could not resume this conversation")
                    self._fail(f"{detail}. The next message starts a new conversation.")
                    return
                if error:
                    # A server that cannot start a conversation will not start
                    # the next one either; it goes rather than failing every
                    # prompt until the reaper notices it.
                    self._discard_server()
                    self._fail(self._error_text(error, "Could not start a Codex session"))
                    return
                thread = (message.get("result") or {}).get("thread") or {}
                self._thread_id = str(thread.get("id") or self._session_id or "")
                if not self._thread_id:
                    self._discard_server()
                    self._fail("Codex did not return a session id")
                    return
                # Anything given up on this conversation is somebody else's
                # turn, however late it speaks. Read once and cleared: the
                # only turn that has to know is the one now resuming.
                self._stale_turns.update(server.take_abandoned_turns(self._thread_id))
                # The reader has already subscribed this conversation from the
                # reply. This is the belt and braces for the fall back to the
                # session id above, and it is idempotent.
                server.attach(self._thread_id, inbox)
                self._on_session(self._thread_id)
                if self._compact:
                    # Compaction is not a message: it answers immediately with
                    # an empty result and then runs a turn of its own, whose
                    # notifications the loop below already understands.
                    compact_request = self._next_id()
                    # Compaction runs a turn of its own, so from here a cancel
                    # has something to stop -- but the reply to this request is
                    # empty, so nothing but the turn's own notifications will
                    # ever name it.
                    if not self._ask_for_a_turn():
                        return
                    inbox.put(ab._CODEX_ASKED)
                    self._expect(compact_request)
                    self._send(
                        {
                            "method": "thread/compact/start",
                            "id": compact_request,
                            "params": {"threadId": self._thread_id},
                        }
                    )
                    continue
                approval, sandbox = self._policy(self._permission_mode)
                params = {
                    "threadId": self._thread_id,
                    "input": [{"type": "text", "text": self._prompt}],
                    "cwd": self._cwd,
                    "approvalPolicy": approval,
                    "sandboxPolicy": sandbox,
                    **self._reviewer(self._permission_mode),
                }
                if self._model:
                    params["model"] = self._model
                if self._effort:
                    params["effort"] = self._effort
                turn_request = self._next_id()
                if not self._ask_for_a_turn():
                    # Stopped while waiting to be told this conversation's
                    # name. Nothing is running yet, and this is the last
                    # moment at which that is still true.
                    return
                inbox.put(ab._CODEX_ASKED)
                self._expect(turn_request)
                self._send({"method": "turn/start", "id": turn_request, "params": params})
                continue

            if compact_request and message.get("id") == compact_request:
                error = message.get("error")
                if error:
                    self._fail(self._error_text(error, "Codex could not compact"))
                    return
                # The result is empty; the compaction turn announces itself.
                continue

            if turn_request and message.get("id") == turn_request:
                error = message.get("error")
                if error:
                    self._fail(self._error_text(error, "Codex could not start the turn"))
                    return
                turn = (message.get("result") or {}).get("turn") or {}
                self._turn_id = str(turn.get("id") or "")
                self._watch_turn()
                self._accepting_input.set()
                if not started_notified:
                    self._on_started()
                    started_notified = True
                continue

            method = str(message.get("method") or "")
            params = message.get("params") or {}
            child = str(params.get("threadId") or "") if isinstance(params, dict) else ""
            if child and child in self._children:
                self._child_message(child, method, params, message)
                continue
            if not self._is_this_turn(method, params):
                # The conversation is right but the turn is not: Codex is still
                # winding up a turn that was interrupted, and its trailing
                # deltas and its `turn/completed` are not this turn's to act on.
                if not self._turn_asked:
                    # Learned by name, so the rest of that turn is recognised
                    # however late it comes -- including after the point where
                    # position alone would have let it through.
                    named = self._turn_named(params)
                    if named:
                        self._stale_turns.add(named)
                if method and "id" in message:
                    # It asked something, though, and an answer it never gets
                    # is a request id Codex holds for ever.
                    self._send(ab._declined_request(message))
                continue
            if self._compact and not self._turn_id:
                # Only compaction: `thread/compact/start` answers empty, so
                # nothing but the turn's own messages will ever name its turn.
                # Everywhere else the `turn/start` reply names it, and adopting
                # from a notification instead would give a straggler from the
                # turn before this one a way in.
                named = self._turn_named(params)
                if named:
                    self._turn_id = named
                    self._watch_turn()

            if method and "id" in message:
                self._handle_server_request(message)
                continue

            if method == "turn/started":
                turn = params.get("turn") or {}
                self._turn_id = str(turn.get("id") or self._turn_id)
                self._watch_turn()
                self._accepting_input.set()
                if not started_notified:
                    self._on_started()
                    started_notified = True
            elif method == "item/started":
                self._item_started(params.get("item") or {})
            elif method == "item/completed":
                self._item_completed(params.get("item") or {})
            elif method == "item/agentMessage/delta":
                self._agent_delta(params)
            elif method in (
                "item/reasoning/summaryTextDelta",
                "item/reasoning/textDelta",
                "item/plan/delta",
            ):
                delta = str(params.get("delta") or "")
                if delta:
                    item_id = str(params.get("itemId") or "")
                    self._reasoning_streams.setdefault(item_id, []).append(delta)
            elif method == "item/autoApprovalReview/completed":
                said = ab.auto_review_line(params)
                if said:
                    self._on_activity("notice", said)
            elif method == "turn/plan/updated":
                report_tasks = getattr(self, "on_tasks", None)
                if report_tasks:
                    report_tasks("codex", params.get("plan"))
            elif method == "thread/tokenUsage/updated":
                usage = params.get("tokenUsage") or {}
                last = usage.get("last") or {}
                used = ab._as_number(last.get("totalTokens"))
                if used:
                    window = ab._as_number(usage.get("modelContextWindow")) or 0
                    self.context_tokens = (int(used), int(window))
            elif method == "item/commandExecution/outputDelta":
                item_id = str(params.get("itemId") or "")
                delta = str(params.get("delta") or "")
                if delta:
                    self._tool_outputs.setdefault(item_id, []).append(delta)
            elif method in ("warning", "configWarning"):
                warning = str(params.get("message") or params.get("summary") or "")
                if warning and ab._CODEX_QUESTION_FEATURE not in warning:
                    # The one warning that is skipped is Codex's notice that
                    # BlindPilot switched a still-developing feature on. It is
                    # true, it is deliberate, and repeating it at the top of
                    # every single turn is noise nobody can act on.
                    self._on_activity("tool", f"Codex warning: {warning}")
            elif method == "turn/completed":
                self._accepting_input.clear()
                turn = params.get("turn") or {}
                status = turn.get("status")
                if status == "failed":
                    self._fail(self._error_text(turn.get("error"), "Codex turn failed"))
                elif status == "interrupted":
                    if not self._cancelled:
                        self._fail("Codex turn was interrupted")
                elif self._compact:
                    # A compaction turn produces no answer text of its own, so
                    # say what happened rather than finishing in silence.
                    self._on_complete("Conversation compacted.")
                else:
                    self._on_complete("".join(self._assistant_parts).strip())
                return

        if not self._cancelled:
            self._fail(self._why_it_died("Codex app server closed before the turn completed"))

    def _handshake(self) -> bool:
        """Introduce BlindPilot to a freshly started app-server, once.

        This is the process's handshake, not the turn's: a server taken back
        out of the pool has already been through it, and a second `initialize`
        is not something the protocol offers.
        """
        sent = self._send(
            {
                "method": "initialize",
                "id": self._next_id(),
                "params": {
                    "clientInfo": {
                        "name": "blindpilot",
                        "title": "BlindPilot",
                        "version": ab._app_version(),
                    },
                    # request_user_input is still marked experimental in the
                    # app-server protocol, and Codex only sends experimental
                    # requests to a client that asked for them.
                    "capabilities": {"experimentalApi": True},
                },
            }
        )
        return sent and self._send({"method": "initialized", "params": {}})

    def _expect(self, request_id: int, binds_thread: bool = False) -> None:
        """Have the shared reader deliver this request's reply to this turn."""
        server, inbox = self._server, self._inbox
        if server is None or inbox is None:
            return
        self._expected.append(request_id)
        server.expect(request_id, inbox, binds_thread=binds_thread)

    def _watch_turn(self) -> None:
        """Let a cancel on another thread find out when this turn stops.

        Registered once per turn id, and given back in `_release`: the server
        counts its watchers, and one that is never given back is one event kept
        for the life of a process that now outlives thousands of turns.
        """
        if not self._turn_id:
            return
        # A cancel on another thread may be waiting to learn this, so that it
        # has something to name in its interrupt.
        self._turn_id_known.set()
        server = self._server
        if server is None or self._turn_id == self._watched:
            return
        if self._watched:
            server.forget_turn(self._watched)
        self._watched = self._turn_id
        server.watch_turn(self._turn_id)

    def _name_the_stopped_turn(self, inbox: "queue.Queue[object]", turn_request: int) -> None:
        """Before leaving, learn the id of a turn that was asked for unnamed.

        Stop can land in the gap between `turn/start` and the reply that names
        the turn. The turn is running on the server -- under `workspaceWrite`
        or `dangerFullAccess`, writing files and spending tokens -- and the
        only thing missing is the name to interrupt it by, which the reply is
        about to carry. So the last thing this turn does is look for that one
        reply, and hand the id to the `cancel` waiting on `_turn_id_known`.

        Only the reply to this turn's own request id is taken. Everything else
        read here is dropped unlooked at: the answer is already abandoned, and
        a message acted on after Stop is how a stopped turn's words get spliced
        into the next one's.
        """
        try:
            if self._turn_id or not turn_request:
                return
            deadline = ab.time.monotonic() + ab._CODEX_TURN_ID_GRACE_SECONDS
            while True:
                left = deadline - ab.time.monotonic()
                if left <= 0:
                    return
                try:
                    queued = inbox.get(timeout=min(left, ab._CODEX_POLL_SECONDS))
                except ab.queue.Empty:
                    continue
                if queued is ab._CODEX_CLOSED:
                    # The server has gone, so the turn has gone with it.
                    return
                if not isinstance(queued, dict) or queued.get("id") != turn_request:
                    continue
                if queued.get("method") is not None:
                    # A request from Codex, not the reply to ours. Its ids come
                    # from the other direction's namespace and both counters
                    # start small, so one can carry the number this turn is
                    # waiting on; read as the reply it would name no turn and
                    # end the search early.
                    continue
                if queued.get("error"):
                    # Codex refused the turn, so nothing is running to name and
                    # the conversation is as it was.
                    self._turn_requested = False
                    return
                # Shape-checked at every step: a malformed reply here would
                # come back as "BlindPilot stopped reading Codex" on a turn the
                # person has already stopped.
                result = queued.get("result")
                turn = result.get("turn") if isinstance(result, dict) else None
                self._turn_id = str(turn.get("id") or "") if isinstance(turn, dict) else ""
                return
        finally:
            # Whether or not it was found, nobody else is going to find it.
            self._turn_id_known.set()
            if self.abandoned_thread and self._turn_id:
                # The cancel gave up waiting for this name before the reply
                # carrying it arrived. Late is still in time to interrupt the
                # turn, which is better than leaving it to leak output into a
                # later conversation.  The normal cancel path has already
                # returned, so this worker owns the final interrupt attempt.
                abandoned_thread = self.abandoned_thread
                server = self._server
                if server is not None and server.interrupt(
                    abandoned_thread, self._turn_id, ab._CODEX_INTERRUPT_VERIFY_SECONDS
                ):
                    self.abandoned_thread = ""
                else:
                    self._abandon(abandoned_thread, self._turn_id)

    @staticmethod
    def _turn_named(params: dict) -> str:
        turn = params.get("turn")
        if isinstance(turn, dict) and turn.get("id"):
            return str(turn["id"])
        return str(params.get("turnId") or "")

    def _is_this_turn(self, method: str, params: dict) -> bool:
        """Whether a message about a turn is about the turn this worker is running.

        A stopped turn goes on producing for a moment after the interrupt, on
        the same thread. What separates its messages from this turn's is where
        they sit relative to `_CODEX_ASKED`, the mark this turn put in its inbox
        when it asked for a turn, plus the names of turns already rejected, for
        stragglers that arrive after the mark.
        """
        named = self._turn_named(params)
        if not named:
            # Nothing to attribute it to: process-wide, or about the thread
            # rather than a turn.
            return True
        if named in self._stale_turns:
            # Already known to belong to the turn before this one. Position
            # cannot vouch for a straggler that arrives after the mark; a name
            # already seen on the wrong side of it can.
            return False
        if self._turn_id:
            return named == self._turn_id
        if not self._turn_asked:
            # Nothing has been asked for, so this is the tail of the turn that
            # ran before this one -- the one somebody stopped.
            return False
        # Asked, and not yet told which turn we were given. An ending is the
        # exception: a turn cannot finish before the beginning we never saw.
        return method != "turn/completed"

    def _why_it_died(self, fallback: str) -> str:
        """The best account available of why the app-server stopped talking.

        stderr is a different pipe read by a different thread, and the line
        worth having - the panic, the unauthorized, the out of memory - is the
        last one written, which is exactly the one still in flight. Everything
        earlier is already in the list, so the race did not lose noise, it lost
        the reason, and it lost it differently each time.

        Codex's words are passed on as Codex wrote them: a ChatGPT-plan failure
        this app can name is one its own token command failed on, and that is
        reported where the command is run.
        """
        self._await_last_words()
        detail = "\n".join(self._stderr_lines[-10:]).strip()
        if detail:
            return detail
        server = self._server
        return (server.read_error() if server is not None else "") or fallback

    def _await_last_words(self) -> None:
        """Let the final line of stderr land before reporting why Codex died.

        Bounded, because a pipe that never closes must not hold the turn open.
        """
        server = self._server
        if server is not None:
            server.await_last_words(ab._CODEX_LAST_WORDS_SECONDS)

    def _error_text(self, error: object, fallback: str) -> str:
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
        return fallback

    def _handle_server_request(self, message: dict) -> None:
        method = str(message.get("method") or "")
        request_id = message.get("id")
        mode = self._permission_mode
        if method == "item/commandExecution/requestApproval":
            if mode == "default" and self._on_permission is not None:
                decision = self._ask_codex_permission(message, "command")
            else:
                decision = "accept" if mode in ("auto", "bypassPermissions") else "decline"
            self._send({"id": request_id, "result": {"decision": decision}})
        elif method == "item/fileChange/requestApproval":
            if mode == "default" and self._on_permission is not None:
                decision = self._ask_codex_permission(message, "file change")
            else:
                decision = (
                    "accept" if mode in ("acceptEdits", "auto", "bypassPermissions") else "decline"
                )
            self._send({"id": request_id, "result": {"decision": decision}})
        elif method == "item/tool/requestUserInput":
            self._answer_user_input(request_id, message.get("params") or {})
        else:
            self._send(ab._unhandled_request(request_id))

    def _ask_codex_permission(self, message: dict, kind: str) -> str:
        """Ask the person about a Codex approval request; worker thread blocks.

        Translates the backend-neutral dialog answer to Codex's accept/decline.
        A stop or closed tab denies, the same as the dialog's default.
        """
        params = message.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        tool = str(params.get("command") or params.get("tool") or kind)
        payload = {
            key: params[key] for key in ("command", "cwd", "reason", "explanation") if key in params
        }
        ask = self._on_permission
        if ask is None:
            return "decline"
        try:
            answer = ask(tool, payload, [])
        except Exception:
            return "decline"
        if isinstance(answer, dict) and answer.get("behavior") == "allow":
            return "accept"
        return "decline"

    def _answer_user_input(self, request_id: object, params: dict) -> None:
        """Put request_user_input in front of the person and answer it.

        Codex keys the answers by each question's own id, and takes a list per
        question even where only one answer was asked for. A question nobody
        answered is sent back with an empty list, which is how Codex reads
        "the person had nothing to say to this" — the turn then carries on
        rather than waiting for an answer that is not coming.
        """
        asked = ab._codex_questions(params.get("questions"))
        answers = self._on_question(asked) if (asked and self._on_question) else None
        self._on_activity("tool", ab.questions.question_summary(asked, answers))
        payload = {
            question.id or question.question: {
                "answers": answers[index] if answers is not None and index < len(answers) else []
            }
            for index, question in enumerate(asked)
        }
        self._send({"id": request_id, "result": {"answers": payload}})

    def _item_started(self, item: dict) -> None:
        self._collab(item)
        label = ab._codex_item_label(item)
        if label:
            self._on_activity("tool", label)

    def _collab(self, item: dict) -> None:
        """Follow the agents this thread spawns, from its collab tool calls.

        A spawnAgent call names the new agent's thread in
        `receiverThreadIds`, and every collab call carries the last known
        state of the agents it touched in `agentsStates`, keyed by thread.
        """
        if item.get("type") != "collabAgentToolCall":
            return
        receivers = item.get("receiverThreadIds")
        threads = [str(t) for t in receivers if t] if isinstance(receivers, list) else []
        states = item.get("agentsStates")
        states = states if isinstance(states, dict) else {}
        if item.get("tool") == "spawnAgent":
            prompt = ab.subagent_line(item.get("prompt"))
            server, inbox = self._server, self._inbox
            for thread in threads:
                if thread in self._children:
                    continue
                self._children[thread] = ab.SUBAGENT_RUNNING
                if server is not None and inbox is not None:
                    server.attach(thread, inbox)
                self._on_subagent(
                    thread,
                    ab.subagent_line(prompt, 60) or "Subagent",
                    ab.SUBAGENT_RUNNING,
                    f"Task: {prompt}" if prompt else "",
                )
        for thread, state in states.items():
            if thread not in self._children or not isinstance(state, dict):
                continue
            status = ab.subagent_status(state.get("status"))
            if status and status != self._children[thread]:
                self._children[thread] = status
                message = ab.subagent_line(state.get("message"))
                self._on_subagent(thread, "", status, message or f"Finished: {status}")

    def _child_message(self, child: str, method: str, params: dict, message: dict) -> None:
        """Something one of this thread's agents said or asked."""
        if method and "id" in message:
            # Answered as this conversation's own would be: the mode the
            # person chose covers the agents it runs.
            self._handle_server_request(message)
            return
        if method == "turn/started":
            turn = params.get("turn") or {}
            self._child_turns[child] = str(turn.get("id") or "")
            if self._children.get(child) != ab.SUBAGENT_RUNNING:
                self._children[child] = ab.SUBAGENT_RUNNING
                self._on_subagent(child, "", ab.SUBAGENT_RUNNING, "Working")
        elif method == "item/started":
            item = params.get("item") or {}
            label = ab._codex_item_label(item) if isinstance(item, dict) else ""
            if label:
                self._on_subagent(child, "", "", label)
        elif method == "item/completed":
            item = params.get("item") or {}
            if isinstance(item, dict) and item.get("type") == "agentMessage":
                text = ab.subagent_line(item.get("text"))
                if text:
                    self._on_subagent(child, "", "", text)
        elif method == "turn/completed":
            turn = params.get("turn") or {}
            status = ab.subagent_status(turn.get("status")) or ab.SUBAGENT_COMPLETED
            self._children[child] = status
            self._on_subagent(child, "", status, f"Finished: {status}")

    def _stop_children(self, server: "CodexServer") -> None:
        """Interrupt each agent still running; the parent's interrupt does not.

        Sent and not waited on: the verify that follows is for the
        conversation this tab owns, and an agent that ignores the stop is
        let go with the turn anyway.
        """
        for child, status in list(self._children.items()):
            turn = self._child_turns.get(child, "")
            if status != ab.SUBAGENT_RUNNING or not turn:
                continue
            server.send(
                {
                    "method": "turn/interrupt",
                    "id": server.next_id(),
                    "params": {"threadId": child, "turnId": turn},
                }
            )

    def _agent_delta(self, params: dict) -> None:
        """One streamed fragment of a Codex answer, said as it arrives.

        The terminal shows these deltas as they land; holding them until
        the item completed was the whole wait a listener sat through.
        Only finished sentences go out, so nothing torn is spoken.
        """
        item_id = str(params.get("itemId") or "")
        delta = str(params.get("delta") or "")
        if not delta:
            return
        self._assistant_delta_seen.add(item_id)
        self._assistant_streams.setdefault(item_id, []).append(delta)
        self._assistant_parts.append(delta)
        live = self._assistant_live.get(item_id)
        if live is None:
            live = self._assistant_live[item_id] = SentenceStreamer(
                lambda text: self._on_activity("assistant", text)
            )
        live.feed(delta)

    def _item_completed(self, item: dict) -> None:
        self._collab(item)
        kind = item.get("type")
        item_id = str(item.get("id") or "")
        if kind == "agentMessage":
            text = str(item.get("text") or "")
            if item_id in self._assistant_delta_seen:
                streamed = "".join(self._assistant_streams.pop(item_id, []))
                live = self._assistant_live.pop(item_id, None)
                if live is not None:
                    # Whatever the last sentence left unfinished is all that
                    # has not been heard yet; the rest went out live.
                    live.flush()
                elif streamed:
                    self._on_activity("assistant", streamed)
            elif text:
                self._assistant_parts.append(text)
                self._on_activity("assistant", text)
        elif kind in ("reasoning", "plan"):
            reasoning = "".join(self._reasoning_streams.pop(item_id, []))
            if reasoning:
                self._on_activity("thinking", reasoning)
        elif kind == "commandExecution":
            chunks = self._tool_outputs.pop(item_id, [])
            output = "".join(chunks) or str(item.get("aggregatedOutput") or "")
            if output.strip():
                self._on_activity("result", output.strip())
        elif kind == "fileChange":
            changes = item.get("changes") or []
            summary = []
            for change in changes:
                if isinstance(change, dict) and change.get("path"):
                    summary.append(f"{change.get('kind') or 'changed'}: {change['path']}")
            if summary:
                self._on_activity("result", "\n".join(summary))
