"""FreeBuff turn worker; shared lifecycle stays in agent_backends."""

from __future__ import annotations

from typing import Callable, Optional

import agent_backends as ab
import questions
from agent_backends import FreebuffTerminal, _TurnWorker


class FreebuffWorker(_TurnWorker):
    """Drive FreeBuff's interactive TUI through a pseudo-terminal.

    FreeBuff has no JSON or headless interface -- upstream's CLI surface is
    ``login``, ``--continue``, ``--cwd`` and ``--trust-agents``, and the
    request for a print mode (CodebuffAI/freebuff#947) is still open. A PTY
    is therefore required so its OpenTUI client behaves exactly as it does
    in a terminal. The PTY only drives the TUI: the finished answer and the
    turn's end come from the chat files FreeBuff itself writes
    (``chat-messages.json`` and ``log.jsonl``), and the screen is read only
    for live narration, the model picker, and its question box.
    """

    _backend = ab.BACKEND_FREEBUFF

    # The quoted form is drawn first on the composer in 0.0.168, before the
    # caption has scrolled in; matching it means readiness is seen at once
    # instead of after a silence long enough to look like a hang.
    _PROMPT_RE = ab.re.compile(
        r"(?mi)(?:^\s*[›>]\s*$|Enter a coding task or / for commands|Describe your task)"
    )
    _BUSY_RE = ab.re.compile(
        r"(?mi)(?:thinking(?:\.\.\.|…)|working(?:\.\.\.|…)|■\s*Esc|Esc\s+to\s+(?:stop|interrupt))"
    )

    def _setup(self) -> None:
        self._model = self._model.strip()
        # FreeBuff is driven through its TUI, which takes no permission mode
        # and no reasoning effort: the window passes both because every
        # backend is built from one set of arguments, and this is what the
        # diagnostics record says was in force instead.
        self._permission_mode = "n/a"
        self._write_lock = ab.threading.Lock()
        self._pty: Optional[FreebuffTerminal] = None
        # Set once the terminal can produce no further output.
        self._stream_ended = ab.threading.Event()
        # Everything typed into FreeBuff's composer this turn, as its echo is
        # recognised on screen: the prompt, and a steer if one is sent. Only
        # the prompt is a boundary -- see `_freebuff_sections`.
        self._echoes: list[tuple[str, str]] = [ab._freebuff_echo(self._prompt)]
        # Everything read out this turn, and the frame each kind is waiting to
        # see a second time before believing it.
        self._narrated: dict[str, str] = {}
        self._pending_frame: dict[str, str] = {}

    def steer(self, text: str) -> bool:
        # A successful pipe write does not confirm input while FreeBuff is busy.
        # The window stops and resumes this backend with the new instruction.
        return False

    def _submit_text(self, text: str) -> bool:
        # OpenTUI handles paste and Enter as separate input events. Sending
        # both in one ConPTY write fills the composer but does not submit it.
        if not self._paste_text(text):
            return False
        ab.time.sleep(0.05)
        return self._write("\r")

    def _paste_text(self, text: str) -> bool:
        # Raw key events discard newlines and some Unicode; paste preserves them.
        return self._write(f"\x1b[200~{text}\x1b[201~")

    def _write(self, text: str) -> bool:
        if self._pty is None:
            return False
        try:
            with self._write_lock:
                self._pty.write(text)
            return True
        except Exception:
            return False

    def cancel(self) -> None:
        self._cancelled = True
        self._accepting_input.clear()
        pty = self._pty
        if pty is not None:
            # Every turn ends here, through `run`'s `finally`, so this is where
            # a terminal that was stopped but never closed piles up.
            ab.end_hidden_terminal(pty)

    def _teardown(self) -> None:
        # Cancelling is how this backend lets go: there is no request to send,
        # only a terminal to close, and `cancel` is already that.
        self.cancel()

    def _do_run(self) -> None:
        binary = ab.find_backend_cli(ab.BACKEND_FREEBUFF)
        if not binary:
            self._fail("FreeBuff is not installed. Run: npm install -g freebuff")
            return
        # Reading the model catalog means scanning the whole of FreeBuff, which
        # is far too slow to do before sending a message. The recorded choice is
        # all that is needed here; the catalog is only fetched if the model
        # picker actually appears, which happens on a first launch.
        try:
            self._model = self._model or ab._read_freebuff_choice() or ab.FREEBUFF_PREFERRED_MODEL
            ab.set_freebuff_model(self._model)
        except OSError as exc:
            self._fail(f"Could not select the FreeBuff model: {exc}")
            return
        models: list[str] = []
        before = ab._freebuff_chat_dirs(self._cwd)
        chat_path = (
            ab._freebuff_resume_chat_path(self._cwd, self._session_id)
            if self._session_id
            else ab._freebuff_chat_path(self._cwd, "")
        )
        if self._session_id and chat_path is None:
            self._fail(
                "FreeBuff cannot resume this conversation in its CLI. "
                "Desktop conversations must be continued in FreeBuff Desktop; "
                "For a CLI conversation, open its original project folder; "
                "if it was deleted, start a new conversation."
            )
            return
        log_offset = 0
        if chat_path is not None:
            try:
                log_offset = (chat_path / "log.jsonl").stat().st_size
            except OSError:
                pass
        # --trust-agents is upstream's opt-in for unattended runs: without
        # it a project carrying .agents or mcp.json stops on a trust prompt
        # inside a terminal nobody can see, and the turn dies on the
        # startup-silence timeout. BlindPilot already runs every agent in
        # this folder without asking, so the prompt is answered here instead.
        args = [binary, "--cwd", self._cwd, "--trust-agents"]
        if self._session_id:
            args.extend(["--continue", self._session_id])
        waiting = ab._take_freebuff_prewarm(self._cwd, self._session_id, self._model)
        adopted = waiting is not None
        if waiting is not None:
            # A terminal was started for this conversation while the message was
            # being typed, and has been buffering its output ever since. Taking
            # it saves the whole of FreeBuff's start-up. Its own record of the
            # chats that existed before it started is what identifies the new
            # conversation, since it created that chat when it started.
            # The prewarm record holds a terminal, a reader, an event and a
            # listing side by side, so what comes back out of it is opaque
            # until it is named.
            self._pty = ab.cast(ab.FreebuffTerminal, waiting["pty"])
            read = waiting["read"]
            self._stream_ended = waiting["ended"]
            before = waiting["before"]
            log_offset = waiting.get("log_offset", log_offset)
        else:
            try:
                read = self._spawn_pty(args)
            except ImportError:
                package = "pyte" if ab.platform.system() == "Windows" else "pexpect and pyte"
                self._fail(f"FreeBuff support needs {package}. Reinstall BlindPilot dependencies.")
                return
            except Exception as exc:
                self._fail(f"Failed to launch FreeBuff: {exc}")
                return
        adopted_at = ab.time.monotonic()

        try:
            import pyte
        except ImportError:
            self._fail("FreeBuff support needs pyte. Reinstall BlindPilot dependencies.")
            return
        from freebuff_screen import repaired_history_screen

        # pyte 0.8.2 crashes with "string index out of range" reading a screen
        # that holds an orphaned wide-character stub -- a redraw that repaints
        # the lead cell of an emoji or CJK text without its blank filler. See
        # freebuff_screen.
        screen = repaired_history_screen(180, 60, history=4000)
        stream = pyte.Stream(screen)
        sent = False
        started = False
        ready_since: Optional[float] = None
        last_visible = ""
        # What has been read out already, in the shape the screen paints it, so
        # the next frame can be compared against it.
        spoken_thinking = ""
        spoken_answer = ""
        last_emit_at = 0.0
        structured_answer = ""
        # A resumed chat already holds the previous turn's answer. Remember its
        # id so it is never mistaken for this turn's, and so the real answer is
        # recognised as new the moment FreeBuff writes it.
        baseline_answer_id = ab._freebuff_answer_id(chat_path)
        structured_answer_id = ""
        chat_stamp: tuple = ()
        run_status = ""
        # A mid-turn drop is awaited, not failed on sight — the patience
        # constant below. And a boot that has not reconnected holds the
        # message rather than feeding it to a composer that would lose it.
        disconnected_since: Optional[float] = None
        boot_hold_started: Optional[float] = None
        agent_states: dict[str, str] = {}
        # How much of each agent's log has been handed to the window.
        agent_logged: dict[str, int] = {}
        screen_dirty = False
        screen_changed_at = ab.time.monotonic()
        accepted_recommended_model = False
        picker_expanded = False
        picker_expanded_at = 0.0
        saw_busy = False
        session_reported = bool(self._session_id)
        next_session_check = ab.time.monotonic()
        turn_started_at: Optional[float] = None
        next_heartbeat = float("inf")
        completion_seen_at: Optional[float] = None
        deadline = ab.time.monotonic() + ab._FREEBUFF_TURN_SECONDS

        def refresh(wait: float) -> str:
            """Feed whatever the terminal has produced and re-read the screen.

            The question box is driven a keystroke at a time, and every one of
            them has to be seen to land before the next is sent. This is the
            main loop's own reading, pulled out so both can use it.
            """
            nonlocal last_visible, screen_changed_at, screen_dirty
            until = ab.time.monotonic() + max(0.0, wait)
            while True:
                waiting = read(0.03)
                if waiting:
                    stream.feed(waiting)
                if ab.time.monotonic() >= until:
                    break
            current = "\n".join(line.rstrip() for line in screen.display).strip()
            if current != last_visible:
                last_visible = current
                screen_changed_at = ab.time.monotonic()
                screen_dirty = True
            return current

        def restart() -> Optional[Callable[[float], str]]:
            """Replace a terminal that was waiting and is no longer usable.

            One started ahead of time can have been sitting for a quarter of an
            hour, and can have died or moved on in that time. Starting again
            costs the usual wait; refusing to would cost the message.
            """
            nonlocal before, log_offset
            ab.end_hidden_terminal(self._pty)
            # The terminal being replaced had a whole FreeBuff in it, and one
            # rewrites the model setting to its own recommendation as it goes.
            # The replacement reads that setting at launch, so the choice has to
            # be put back before it starts rather than only before the first.
            try:
                ab.set_freebuff_model(self._model)
            except OSError:
                pass
            before = ab._freebuff_chat_dirs(self._cwd)
            if chat_path is not None:
                try:
                    log_offset = (chat_path / "log.jsonl").stat().st_size
                except OSError:
                    log_offset = 0
            self._stream_ended = ab.threading.Event()
            try:
                return self._spawn_pty(args)
            except Exception as exc:
                self._fail(f"Failed to launch FreeBuff: {exc}")
                return None

        while not self._cancelled and ab.time.monotonic() < deadline:
            # Short enough that a frame is picked up as soon as it lands. The
            # answer is read off the screen, so this interval is the floor on
            # how quickly a finished sentence can reach the listener.
            chunk = read(0.03)
            if chunk:
                # Take everything already waiting as one frame. A repaint
                # arrives as a burst of small writes, and feeding them one at a
                # time would mean laying out the screen over and over.
                for _ in range(256):
                    more = read(0)
                    if not more:
                        break
                    chunk += more
            stale = adopted and not sent and ab.time.monotonic() - adopted_at >= 12
            if stale or (not chunk and self._stream_ended.is_set() and not sent and adopted):
                adopted = False
                replacement = restart()
                if replacement is None:
                    return
                read = replacement
                screen = repaired_history_screen(180, 60, history=4000)
                stream = pyte.Stream(screen)
                last_visible = ""
                screen_dirty = False
                # The replacement has painted nothing yet, and nothing is what
                # the startup wait is measured in, so start it again here.
                screen_changed_at = ab.time.monotonic()
                accepted_recommended_model = False
                picker_expanded = False
                picker_expanded_at = 0.0
                boot_hold_started = None
                ready_since = None
                continue
            if not chunk and self._stream_ended.is_set():
                if not sent:
                    # A dropped session closes nothing and prints nothing: the
                    # composer just never arrives. FreeBuff's log is the only
                    # place the drop is written down, and it is more use than
                    # any inference from an empty screen.
                    if ab._freebuff_dropped_new_chat(
                        before, self._cwd, getattr(self._pty, "pid", 0)
                    ):
                        self._fail(ab._FREEBUFF_REJOIN_MESSAGE)
                        return
                    # Whatever killed it said so on the terminal before dying —
                    # a missing Node, a failed download, a refused login. That
                    # sentence is the whole of what the person can act on, so
                    # it is reported instead of a guess.
                    self._fail(ab._freebuff_launch_failure(last_visible))
                    return
                # It ended mid-turn, so whatever was captured is the whole
                # answer; fall through to report it rather than wait it out.
                break
            if chunk:
                stream.feed(chunk)
                visible = "\n".join(line.rstrip() for line in screen.display).strip()
                if visible != last_visible:
                    last_visible = visible
                    ready_since = None
                    screen_changed_at = ab.time.monotonic()
                    screen_dirty = True

            # FreeBuff opens on a model chooser rather than the composer.
            # Accept its highlighted model, or navigate to the chosen one, so
            # the hidden terminal reaches a prompt it can be given a message at.
            if (
                not accepted_recommended_model
                and not sent
                and ab._FREEBUFF_PICKER_RE.search(last_visible)
            ):
                if not models:
                    models = ab.freebuff_model_options()[0]
                picker_models, focused = ab._freebuff_picker_options(last_visible, models)
                if (
                    self._model in picker_models
                    and focused >= 0
                    and picker_models[focused] == self._model
                ):
                    self._write("\r")
                    accepted_recommended_model = True
                    continue
                if not picker_expanded and not ab._FREEBUFF_PICKER_EXPANDED_RE.search(last_visible):
                    # Move from the recommended card to "See all models" and
                    # open it. The expanded picker is then navigated from its
                    # actual runtime ordering, so model catalog changes do not
                    # require a BlindPilot update. A release that opens on the
                    # expanded list already — 0.0.168 shows "Show fewer" under
                    # the cards — has no such entry, and a Down here would
                    # walk off the first card and choose the wrong model.
                    self._write("\x1b[B")
                    ab.time.sleep(0.05)
                    self._write("\r")
                    picker_expanded = True
                    picker_expanded_at = ab.time.monotonic()
                    continue
                if self._model in picker_models and focused >= 0:
                    target = picker_models.index(self._model)
                    arrow = "\x1b[B" if target > focused else "\x1b[A"
                    for _step in range(abs(target - focused)):
                        self._write(arrow)
                        ab.time.sleep(0.05)
                    self._write("\r")
                    accepted_recommended_model = True
                    continue
                if ab.time.monotonic() - picker_expanded_at >= ab._FREEBUFF_PICKER_PATIENCE_SECONDS:
                    # FreeBuff drops models between releases. Throwing the
                    # message away over that is a worse answer than running it
                    # on what FreeBuff is offering instead, provided the swap
                    # is said out loud rather than made quietly. "notice" is
                    # the kind that is heard whatever the narration mode;
                    # "tool" would reach Follow everything and nothing else,
                    # and a turn run on a model nobody picked is exactly the
                    # silent substitution keep-up must not swallow.
                    self._on_activity(
                        "notice",
                        f"FreeBuff no longer offers {self._model}; "
                        "using the model it recommends instead",
                    )
                    self._write(ab._KEY_ENTER)
                    accepted_recommended_model = True
                    continue

            if (
                sent
                and ab._FREEBUFF_QUESTION_MARKER in last_visible
                # An answered box stays on screen with every question folded
                # away, so the title alone is not enough: it is a question
                # actually being asked that stops the turn.
                and ab._freebuff_open_question(last_visible)[2] is not None
            ):
                # The turn has stopped to ask something and will not move again
                # until it is answered, so this takes over the loop until the
                # box is gone.
                if self._answer_questions(refresh):
                    continue

            at_prompt = bool(self._PROMPT_RE.search(last_visible))
            busy = bool(self._BUSY_RE.search(last_visible))
            if sent and busy:
                saw_busy = True
            if not sent and at_prompt:
                # 0.0.168 paints its composer before the runtime behind it has
                # reconnected, and a message given to that composer is lost
                # silently: the chat log records no send at all. The boot is
                # readable in that log, so the message is held here — typed
                # nowhere — until the log says the session is live.
                if not ab._freebuff_boot_ready(
                    self._cwd, before, chat_path, log_offset, getattr(self._pty, "pid", 0)
                ):
                    if boot_hold_started is None:
                        boot_hold_started = ab.time.monotonic()
                        self._on_activity(
                            "notice",
                            "FreeBuff is still starting; holding the message until it is ready",
                        )
                    elif (
                        ab.time.monotonic() - boot_hold_started
                        >= ab._FREEBUFF_STARTUP_SILENCE_SECONDS
                    ):
                        self._fail(ab._FREEBUFF_REJOIN_MESSAGE)
                        return
                    continue
                if chat_path is None:
                    discovered = self._adopt_new_chat(before)
                    if discovered:
                        self._session_id = discovered
                        chat_path = ab._freebuff_chat_path(self._cwd, discovered)
                        log_offset = 0
                if not self._submit_text(self._prompt):
                    self._fail("Could not send the prompt to FreeBuff")
                    return
                sent = True
                started = True
                turn_started_at = ab.time.monotonic()
                next_heartbeat = turn_started_at + 30
                # A resumed TUI initially contains the previous turn. Treat it
                # as a baseline so it is not announced again while the new
                # prompt is being painted onto the screen.
                previous_thinking, previous_answer = self._freebuff_sections(last_visible)
                spoken_thinking = ab._complete_sentences(ab._unwrap_screen_text(previous_thinking))
                spoken_answer = ab._complete_sentences(ab._unwrap_screen_text(previous_answer))
                self._accepting_input.set()
                self._on_started()
                continue

            now = ab.time.monotonic()
            if not sent and now - screen_changed_at >= ab._FREEBUFF_STARTUP_SILENCE_SECONDS:
                if ab._freebuff_dropped_new_chat(before, self._cwd, getattr(self._pty, "pid", 0)):
                    self._fail(ab._FREEBUFF_REJOIN_MESSAGE)
                    return
                self._fail(
                    ab._freebuff_startup_silence(last_visible, ab._FREEBUFF_STARTUP_SILENCE_SECONDS)
                )
                return
            if sent and now >= next_session_check:
                next_session_check = now + 1.0
                # Once the chat is known there is nothing left to learn, and
                # the scan is a walk of every project bucket.
                if chat_path is None:
                    discovered = self._adopt_new_chat(before)
                    if discovered:
                        self._session_id = discovered
                        chat_path = ab._freebuff_chat_path(self._cwd, discovered)
                        log_offset = 0
                if self._session_id and not session_reported:
                    self._on_session(self._session_id)
                    session_reported = True

            if sent and chat_path is not None:
                # Parsing the chat costs far more than looking at it, and it is
                # looked at many times a second, so it is only read when
                # FreeBuff has actually written to it.
                stamp = ab._freebuff_chat_stamp(chat_path)
                if stamp != chat_stamp:
                    chat_stamp = stamp
                    answer_id, _thinking, answer, agents = ab._freebuff_chat_snapshot(chat_path)
                    if answer_id and answer_id == baseline_answer_id:
                        # Still the answer this turn was resumed from; nothing
                        # of this turn has been written yet.
                        answer_id, answer, agents = "", "", []
                    elif answer_id and answer_id != structured_answer_id:
                        # FreeBuff opened this turn's answer. Everything tracked
                        # so far belonged to an earlier message.
                        structured_answer_id = answer_id
                        baseline_answer_id = ""
                        agent_states.clear()
                        agent_logged.clear()
                    # The file is written whole, once the reply is finished, so
                    # it is the authoritative text of the answer rather than a
                    # source to read from: the reading happens off the screen.
                    if answer:
                        structured_answer = answer
                    for agent_id, name, status, log in agents:
                        previous_status = agent_states.get(agent_id)
                        if previous_status is None:
                            self._on_activity("tool", f"FreeBuff started {name}")
                        elif previous_status != status and status in ("complete", "completed"):
                            self._on_activity("result", f"FreeBuff finished {name}")
                        agent_states[agent_id] = status
                        # The window's list: new lines of the log, then the
                        # state, so an agent that finished says so last.
                        told = agent_logged.get(agent_id, 0)
                        for line in log[told:]:
                            self._on_subagent(
                                agent_id, name, ab.SUBAGENT_RUNNING if told == 0 else "", line
                            )
                            told += 1
                        agent_logged[agent_id] = told
                        if previous_status != status:
                            self._on_subagent(
                                agent_id,
                                name,
                                ab.subagent_status(status) or ab.SUBAGENT_RUNNING,
                                "",
                            )
                    run_status = ab._freebuff_run_status(chat_path, log_offset)
                if run_status == "cancelled":
                    self._fail("FreeBuff reported that the response was interrupted")
                    return
                if run_status == "disconnected":
                    # Not failed on sight: the session can rejoin on its own,
                    # and a later "Reconnection detected" closes the pending
                    # drop in the same log walk that found it. Only a drop
                    # that outlasts the patience ends the turn.
                    if disconnected_since is None:
                        disconnected_since = now
                        self._on_activity(
                            "notice",
                            "FreeBuff's session dropped mid-turn; "
                            "waiting to see whether it rejoins",
                        )
                    elif now - disconnected_since >= ab._FREEBUFF_DROP_PATIENCE_SECONDS:
                        self._fail(ab._FREEBUFF_REJOIN_MESSAGE)
                        return
                elif disconnected_since is not None:
                    disconnected_since = None
                if run_status == "complete":
                    if completion_seen_at is None:
                        completion_seen_at = now
                    elif now - completion_seen_at >= 0.2:
                        break

            if sent and now >= next_heartbeat:
                elapsed = max(1, int(now - (turn_started_at or now)))
                self._on_activity("notice", f"FreeBuff is still working; {elapsed} seconds elapsed")
                next_heartbeat = now + 30
            # The screen is the only place the answer appears as it is written:
            # the chat file is not saved until the reply is finished. So the
            # reading follows the terminal, one finished sentence at a time.
            # A frame is taken once it has held still for a moment, or, if the
            # text is arriving faster than that, as soon as the wait would start
            # to be audible.
            if started and screen_dirty:
                settled = now - screen_changed_at >= ab._FREEBUFF_FRAME_SECONDS
                overdue = now - last_emit_at >= ab._FREEBUFF_MAX_LAG_SECONDS
                if settled or overdue:
                    thinking, answer = self._freebuff_sections(last_visible)
                    spoken_thinking = self._emit_screen_delta("thinking", spoken_thinking, thinking)
                    spoken_answer = self._emit_screen_delta("assistant", spoken_answer, answer)
                    screen_dirty = False
                    last_emit_at = now
            if sent and saw_busy and at_prompt and not busy and chat_path is None:
                if ready_since is None:
                    ready_since = ab.time.monotonic()
                elif ab.time.monotonic() - ready_since >= 1.0:
                    break

        if self._cancelled:
            return
        if not sent:
            self._fail("FreeBuff did not become ready for input")
            return

        self._accepting_input.clear()
        # The turn ends holding the last sentence, which never became "finished
        # text" while the answer was still growing. Nothing arrives after this,
        # so release the rest of the frame whole.
        screen_thinking, screen_response = self._freebuff_sections(last_visible)
        self._emit_screen_delta("thinking", spoken_thinking, screen_thinking, whole=True)
        self._emit_screen_delta("assistant", spoken_answer, screen_response, whole=True)
        # The saved chat is the answer as FreeBuff wrote it, rather than as the
        # terminal laid it out, so it is what the transcript keeps.
        # The loop ends either because FreeBuff finished - which breaks out of
        # it - or because the hour ran out underneath a turn that was still
        # going. Those were indistinguishable from here, so a turn cut off
        # mid-sentence was delivered through the same `_on_complete` a finished
        # one uses: announced as the answer, kept in the transcript as the
        # answer, with nothing to suggest it was not the whole of it.
        timed_out = not self._cancelled and ab.time.monotonic() >= deadline
        response = structured_answer or ab._unwrap_screen_text(screen_response)
        if response:
            # An answer taller than the terminal scrolls its own beginning off
            # the screen, and the reading stops there. The saved chat is the
            # whole of it, so whatever was never read is read now.
            tail = ab._unspoken_tail(self._narrated.get("assistant", ""), response)
            if tail:
                self._on_activity("assistant", tail)
            if timed_out:
                # Kept, not discarded: an hour of work is worth having. Said
                # first, so it is not mistaken for the end of the answer.
                self._on_activity(
                    "notice",
                    "BlindPilot stopped listening to FreeBuff an hour after the message "
                    "was sent. What follows is as far as it had got, not a finished answer.",
                )
            self._on_complete(response)
        elif timed_out:
            self._fail(
                "FreeBuff was still working an hour after the message was sent and had "
                "produced no answer, so BlindPilot stopped waiting for it."
            )
        else:
            self._fail("No response received from FreeBuff")
            return

        session = self._adopt_new_chat(before) or self._session_id
        if session and not session_reported:
            self._on_session(session)
        # The next message in this conversation should not have to wait for a
        # terminal to start, so start one now, while nobody is waiting on it.
        if session:
            ab.prewarm_freebuff(self._cwd, session, self._model, delay=1.0)

    def _adopt_new_chat(self, before: dict[str, float]) -> Optional[str]:
        """The id of a chat this launch created, or None if this turn has one.

        FreeBuff creates a new conversation's folder when its terminal starts.
        Its boot log names the terminal process, so another tab's newer chat
        cannot be adopted instead. A turn that was *resumed*
        already knows which conversation it is, and the folders that appear
        while it runs are somebody else's: adopting the newest is how a tab
        ends up resuming a conversation nobody opened -- the id is overwritten
        and the next message continues that one instead.
        """
        if self._session_id:
            return None
        chat = ab._freebuff_new_chat_path(before, self._cwd, getattr(self._pty, "pid", 0))
        return chat.name if chat is not None else None

    def _press(self, key: str, times: int = 1) -> None:
        """Send one of the box's keys, giving OpenTUI time to repaint."""
        for _ in range(max(0, times)):
            self._write(key)
            ab.time.sleep(ab._FREEBUFF_KEY_SETTLE)

    def _answer_questions(self, refresh: Callable[[float], str]) -> bool:
        """Work through FreeBuff's question box and submit the answers.

        FreeBuff has no way to be told an answer other than the box it drew, so
        this is the box being used: each question is put to the person in a
        dialog, and what they chose is walked to with the arrow keys and chosen
        with Enter. Only one question's answers are on screen at a time, which
        is why they are asked one at a time rather than all at once.

        Returns whether the box was dealt with, so a frame that turned out to
        be half-drawn can be left for the next pass.
        """
        answered: set[str] = set()
        submit = False
        # Bounded so a box that never changes cannot spin: two passes per
        # question is already more than any answer needs.
        for _pass in range(2 * ab._FREEBUFF_MAX_QUESTIONS):
            if self._cancelled:
                return True
            visible = refresh(ab._FREEBUFF_KEY_SETTLE)
            if ab._FREEBUFF_QUESTION_MARKER not in visible:
                # FreeBuff closed the box itself; nothing left to answer.
                return bool(answered)
            total, index, question = ab._freebuff_open_question(visible)
            if question is None:
                visible = refresh(0.5)
                total, index, question = ab._freebuff_open_question(visible)
                if question is None:
                    return False
            if question.question in answered:
                # The same question is still open, so the keystrokes did not
                # land where they were meant to. Send what has been answered
                # rather than ask again in a loop.
                submit = True
                break
            chosen = self._ask_one(question)
            if chosen is None:
                # Escape closes the box, and FreeBuff tells the model the
                # questions were skipped — which is the truth.
                self._press(ab._KEY_ESCAPE)
                refresh(0.5)
                return True
            answered.add(question.question)
            self._choose(question, chosen)
            if index >= total - 1:
                submit = True
                break
        if submit:
            # Tab moves to Submit from an answer and is ignored once Submit
            # already has the focus, so it is safe either way.
            self._press(ab._KEY_TAB)
            self._press(ab._KEY_ENTER)
        refresh(0.5)
        return True

    def _ask_one(self, question: questions.Question) -> Optional[list[str]]:
        """Put one question to the person. None if they closed the dialog."""
        if self._on_question is None:
            return None
        answers = self._on_question([question])
        chosen = answers[0] if answers else None
        self._on_activity("tool", ab.questions.question_summary([question], answers))
        if not chosen:
            return None
        return chosen

    def _choose(self, question: questions.Question, chosen: list[str]) -> None:
        """Walk the box to the chosen answers and take them."""
        # The focus opens on the first answer, and "Custom" sits one past the
        # last of the model's own, which is where anything typed goes.
        labels = [option.label for option in question.options]
        custom = len(labels)
        position = 0
        if not question.multi_select:
            answer = chosen[0]
            target = labels.index(answer) if answer in labels else custom
            self._press(ab._KEY_DOWN, target)
            self._press(ab._KEY_ENTER)
            if target == custom:
                # Choosing "Custom" opens a text field with the cursor in it;
                # Enter closes it and moves on to the next question.
                self._paste_text(answer)
                ab.time.sleep(ab._FREEBUFF_KEY_SETTLE)
                self._press(ab._KEY_ENTER)
            # A chosen answer moves the box on by itself.
            return
        typed = [text for text in chosen if text not in labels]
        for target in sorted(labels.index(text) for text in chosen if text in labels):
            self._press(ab._KEY_DOWN, target - position)
            self._press(ab._KEY_ENTER)
            position = target
        if typed:
            self._press(ab._KEY_DOWN, custom - position)
            self._press(ab._KEY_ENTER)
            self._paste_text(", ".join(typed))
            ab.time.sleep(ab._FREEBUFF_KEY_SETTLE)
            self._press(ab._KEY_ENTER)
            position = custom
        # Ticking a box leaves the focus where it was, so the next question has
        # to be walked to: one step past the last answer wraps onto it.
        self._press(ab._KEY_DOWN, custom - position + 1)

    def _emit_screen_delta(self, kind: str, spoken: str, current: str, whole: bool = False) -> str:
        """Read out whatever the screen has gained since the last frame.

        Only finished sentences are released while the answer is still being
        written: the live edge of a frame is a half-written word, and half a
        sentence read aloud is what makes a run sound broken. At the end of the
        turn nothing more is coming, so the rest is released as it stands.
        """
        text = ab._unwrap_screen_text(current)
        ready = text if whole else ab._complete_sentences(text)
        if not ready:
            return spoken
        addition = ab._append_delta(spoken, ready)
        unrelated = bool(spoken) and addition == ready and len(ready) < len(spoken)
        if unrelated and not whole:
            # This frame shares nothing with what was read and holds less of it,
            # which is what a half-drawn repaint looks like. Wait to see it
            # again before reading it, rather than read a torn frame aloud —
            # but do not wait forever, because it is also what the start of a
            # genuinely shorter answer looks like.
            if self._pending_frame.get(kind) != ready:
                self._pending_frame[kind] = ready
                return spoken
        self._pending_frame.pop(kind, None)
        addition = addition.strip()
        if addition:
            self._on_activity(kind, addition)
            narrated = self._narrated.get(kind, "")
            self._narrated[kind] = f"{narrated}\n{addition}" if narrated else addition
        return ready

    def _spawn_pty(self, args: list[str]) -> Callable[[float], str]:
        self._pty, read = ab._spawn_freebuff_pty(args, self._cwd, self._stream_ended)
        return read

    def _echo_spans(self, raw_lines: list[str]) -> set[int]:
        """The lines of the transcript that hold echoes of what we typed.

        A line is where a message's first line appears, and it is only the
        start: an echo runs on while what has been collected still spells a
        beginning of the message, because the terminal breaks what it is given
        at its own width. Cutting at the matched line alone left the wrap of a
        long message behind, and that tail was read as the answer.

        The divider FreeBuff draws above the message goes with it, since it is
        part of how the message is drawn rather than anything the model said.
        Left behind, a steer's timestamp was read out on its own: the live
        measurement is in `_FREEBUFF_TIMESTAMP_RE`.

        A reply's first line does not continue the message's own characters
        unless the reply opens by repeating it, so the run stops there. A line
        that matched by accident -- the needle appearing inside a paragraph of
        the answer -- collects nothing, since it is not a beginning of the
        message it matched.
        """
        skipped: set[int] = set()
        for needle, key in self._echoes:
            if not needle or not key:
                continue
            found = -1
            for index, raw in enumerate(raw_lines):
                if needle in raw and key.startswith(ab._keyed(raw, letters_only=True)[0]):
                    found = index
            if found < 0:
                continue
            covered: set[int] = set()
            for position in range(found, len(raw_lines)):
                collected = ab._keyed(
                    "\n".join(raw_lines[found : position + 1]), letters_only=True
                )[0]
                if not key.startswith(collected):
                    break
                covered.add(position)
            if not covered:
                # A line that matched by accident -- the needle appearing inside
                # a paragraph of the answer -- is not a beginning of the message
                # it matched, and takes nothing with it.
                continue
            if found > 0 and ab._FREEBUFF_TIMESTAMP_RE.match(raw_lines[found - 1].strip()):
                covered.add(found - 1)
            skipped |= covered
        return skipped

    def _freebuff_sections(self, visible: str) -> tuple[str, str]:
        """Extract reasoning and answer text from FreeBuff's rendered screen."""
        raw_lines = ab._strip_terminal_noise(visible).splitlines()
        # This turn's output is whatever follows the echo of this turn's prompt.
        # A resumed conversation paints the turn before it above that, reasoning
        # and all, and none of that is an answer to what was just asked.
        prompt_needle = self._echoes[0][0]
        prompt_key = self._echoes[0][1]
        prompt_index = -1
        if prompt_needle:
            for index, raw in enumerate(raw_lines):
                if prompt_needle in raw and prompt_key.startswith(
                    ab._keyed(raw, letters_only=True)[0]
                ):
                    prompt_index = index
        start = prompt_index + 1 if prompt_index >= 0 else 0

        # Every echo this turn produced is then taken out of the lines below
        # that boundary, so nothing the person typed is read as the answer.
        # A steer is a second message typed into the same composer while the
        # turn runs, and FreeBuff writes it into the transcript exactly as it
        # writes the reply: plain text, with nothing to say whose words they
        # are. Cutting only at the prompt's echo left it inside the section
        # that gets spoken, so it was read out as though the model had said it.
        skipped = self._echo_spans(raw_lines)

        thinking_index = -1
        thinking_indent = 0
        for index in range(start, len(raw_lines)):
            raw = raw_lines[index]
            if ab.re.fullmatch(r"\s*[•*]\s*Thinking\s*", raw, ab.re.IGNORECASE):
                thinking_index = index
                thinking_indent = len(raw) - len(raw.lstrip())

        if thinking_index >= 0:
            base = thinking_index + 1
            in_thinking = True
        elif prompt_index >= 0:
            base = start
            in_thinking = False
        else:
            # The prompt has scrolled off the top, so there is no way to tell
            # this turn's output from the conversation above it. What is missed
            # here is read out from the saved chat once the turn ends.
            base = len(raw_lines)
            in_thinking = False
        candidates = [
            raw for index, raw in enumerate(raw_lines) if index >= base and index not in skipped
        ]

        thinking: list[str] = []
        answer: list[str] = []
        for raw in candidates:
            stripped = raw.strip()
            if not stripped:
                continue
            lower = stripped.lower()
            if self._PROMPT_RE.search(stripped):
                break
            # The status bar sits between the answer and the composer, and
            # carries the model, what is left of the session, and the control
            # that ends it. None of that is the answer.
            if lower.endswith("end session") or ab.re.search(
                r"\bunlimited\b.*(?:end session|[✕x])", lower
            ):
                break
            if lower.startswith(
                (
                    "freebuff ",
                    "esc ",
                    "ctrl+",
                    "shift+",
                    "sponsored",
                    "ad ",
                    "learn more",
                    "directory:",
                    "model:",
                    "start monetizing",
                    "get api access",
                )
            ):
                continue
            if lower.startswith(("thinking...", "thinking…", "working...", "working…")):
                continue
            if (
                lower.endswith(" ad │")
                or "learn more" in lower
                or "get api access" in lower
                or "start monetizing" in lower
                or "sponsored" in lower
                or (
                    stripped.startswith("│")
                    and stripped.endswith("│")
                    and ab.re.search(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)+\b", lower)
                )
            ):
                continue
            if ab.re.fullmatch(r"[╭╮╰╯│─━═┄┈\s]+", raw):
                continue
            if stripped.startswith("⎘") or ab.re.fullmatch(r"[⎘•△▽✕xs\d.\s]+", stripped):
                continue
            if self._BUSY_RE.search(stripped):
                continue
            if stripped.startswith("│") and stripped.endswith("│"):
                continue
            if "▸" in stripped and ab.re.search(r"\b(?:running|completed)\b", lower):
                continue

            indent = len(raw) - len(raw.lstrip())
            if in_thinking and indent > thinking_indent:
                thinking.append(stripped)
                continue
            in_thinking = False
            answer.append(stripped)

        return "\n".join(thinking).strip(), "\n".join(answer).strip()
