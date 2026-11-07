"""Provider-specific turn worker; shared lifecycle stays in agent_backends."""

from __future__ import annotations

from typing import Optional

import agent_backends as ab
from agent_backends import OpencodeServer, _TurnWorker


class OpencodeWorker(_TurnWorker):
    """Run one opencode turn against the shared headless server."""

    _backend = ab.BACKEND_OPENCODE

    # Every route this worker calls names the session, so a turn that has not
    # got one yet carries the empty string rather than None.
    _session_id: str

    def _setup(self) -> None:
        self._session_id = self._session_id or ""
        self._server: Optional[OpencodeServer] = None
        self._stream: object = None
        # Resolved once the turn starts. Working out whether a model offers the
        # chosen effort can mean reading opencode's catalog, and a message can
        # be steered into a running turn from the window's own thread.
        self._variant = ""
        self._roles: dict[str, str] = {}
        self._emitted: set[str] = set()
        self._answer: list[str] = []
        # Child sessions the task tool started for this conversation, with
        # where each stands, and which task part started each. A child
        # shares the event stream and asks its permissions there too.
        self._children: dict[str, str] = {}
        self._task_children: dict[str, str] = {}
        self._tools_running: set[str] = set()
        # Set when a question was answered this turn. The provider poison that
        # a broken question replay leaves behind is only worth the surgery
        # below where a question was actually part of the turn.
        self._question_answered = False
        # One repair per turn: if the retry is refused too, report it rather
        # than looping.
        self._history_repaired = False

    # ----- what the window drives -----

    def steer(self, text: str) -> bool:
        """Add a message to the turn that is already running.

        opencode admits a prompt sent mid-turn and hands it to the model at the
        next step, which is exactly what steering means here.

        Answers from what this worker already knows and sends on a thread of
        its own, because this is called from the window's thread: a request
        waited on there is a window that stops answering the screen reader.
        Whether the turn is still accepting input is the question being asked,
        and that is known here; a message opencode then refuses is rare enough
        to belong in the transcript rather than in a frozen window.
        """
        if not self.accepting_input() or not self._session_id or self._server is None:
            return False
        server, session = self._server, self._session_id
        # A steering message is its own message, not the prompt again: the
        # turn's attachments went out with the prompt and stay there.
        body = self._prompt_body(text, with_attachments=False)

        def deliver() -> None:
            try:
                server.request(
                    "POST",
                    f"/session/{session}/prompt_async",
                    params={"directory": self._cwd},
                    body=body,
                    timeout=60,
                )
            except (OSError, ValueError) as exc:
                if not self._cancelled:
                    detail = ab.opencode_error_text(exc, "the request failed")
                    self._on_activity(
                        "tool", f"opencode did not take the steering message: {detail}"
                    )

        ab.threading.Thread(target=deliver, daemon=True).start()
        return True

    def cancel(self) -> None:
        self._cancelled = True
        self._accepting_input.clear()
        server, session = self._server, self._session_id
        if server is not None:
            # Aborting the conversation does not abort the sessions its task
            # tool opened; each running agent is aborted by its own id.
            for child, status in list(self._children.items()):
                if status != ab.SUBAGENT_RUNNING:
                    continue
                try:
                    server.request(
                        "POST",
                        f"/session/{child}/abort",
                        params={"directory": self._cwd},
                        timeout=10,
                    )
                except (OSError, ValueError):
                    pass
        if server is not None and session:
            try:
                server.request(
                    "POST",
                    f"/session/{session}/abort",
                    params={"directory": self._cwd},
                    timeout=30,
                )
            except (OSError, ValueError):
                pass
        # Closing the event stream is what unblocks the run loop. The server
        # itself is shared, and stays up for the next turn.
        self._close_stream()

    def _teardown(self) -> None:
        self._close_stream()

    def _close_stream(self) -> None:
        stream = self._stream
        if stream is not None:
            try:
                stream.close()  # type: ignore[attr-defined]
            except Exception:
                pass

    # ----- the turn -----

    def _on_session_error(self, properties: dict) -> bool:
        """A session.error ends the turn — unless one repair attempt fits.

        A provider that refuses a stored message refuses every following
        request too, so an ordinary failure here would leave the conversation
        permanently stuck behind a turn that can never be replayed. The one
        break BlindPilot itself can walk into is a question round-trip, so
        when the error reads like refused history and a question was answered
        this turn, the question's step is deleted and the prompt sent again.

        Returns False when the turn is over for good (failure reported, or the
        reader should keep going on the repaired conversation).
        """
        text = ab.opencode_error_text(properties.get("error"), "opencode reported an error")
        if not (ab._poison_history_error(text) and self._question_answered):
            self._fail(text)
            return False
        if self._history_repaired or self._cancelled:
            self._fail(text)
            return False
        self._history_repaired = True
        if self._repair_history():
            self._on_activity(
                "tool",
                "opencode refused the conversation after the question; "
                "removing the broken step and trying again.",
            )
            self._resend()
            return True
        self._fail(text)
        return False

    def _repair_history(self) -> bool:
        """Delete the poisoned question step and everything after it.

        The message the provider cannot replay is two-fold: the question
        step whose tool call comes back unpaired, and the empty assistant
        step it died on — neither content nor tool calls, which is the very
        text of the 400. Both go, and so does anything written after them,
        since every later step is refused for the same reason.

        Returns True when something was actually removed. The listing's order
        is not counted on: the question step to cut at is the latest one by
        its own timestamp.
        """
        server = self._server
        session = self._session_id
        if server is None or not session:
            return False
        try:
            messages = server.request(
                "GET", f"/session/{session}/message", params={"directory": self._cwd}, timeout=60
            )
        except (OSError, ValueError):
            return False
        entries = [
            entry
            for entry in (messages if isinstance(messages, list) else [])
            if isinstance(entry, dict)
        ]

        def stamp(entry: dict) -> float:
            info = ab._opencode_entry_info(entry)
            stamped = info.get("time")
            time = stamped if isinstance(stamped, dict) else {}
            try:
                return float(time.get("created") or info.get("time_created") or 0)
            except (TypeError, ValueError):
                return 0.0

        cutoff_id = ""
        cutoff_stamp = -1.0
        for entry in entries:
            info = ab._opencode_entry_info(entry)
            if str(info.get("role") or "") != "assistant":
                continue
            message_id = str(info.get("id") or "")
            if not message_id:
                continue
            listed = entry.get("parts")
            parts = listed if isinstance(listed, list) else []
            for part in parts:
                if not isinstance(part, dict) or str(part.get("tool") or "") != "question":
                    continue
                reported = part.get("state")
                state = reported if isinstance(reported, dict) else {}
                if str(state.get("status") or "") != "completed":
                    continue
                when = stamp(entry)
                if when >= cutoff_stamp:
                    cutoff_id, cutoff_stamp = message_id, when
                break
        if not cutoff_id:
            return False
        # The step the question died on has no timestamp of its own worth
        # trusting, so the cut is by position in the listing relative to the
        # question step, and by time for anything ordered oddly.
        try:
            index = next(
                position
                for position, entry in enumerate(entries)
                if ab._opencode_entry_info(entry).get("id") == cutoff_id
            )
        except StopIteration:
            return False
        doomed = [
            entry
            for position, entry in enumerate(entries)
            if position >= index and stamp(entry) >= cutoff_stamp
        ]
        removed = False
        for entry in doomed:
            info = ab._opencode_entry_info(entry)
            message_id = str(info.get("id") or "")
            if not message_id:
                continue
            try:
                server.request(
                    "DELETE",
                    f"/session/{session}/message/{message_id}",
                    params={"directory": self._cwd},
                    timeout=60,
                )
            except (OSError, ValueError):
                return removed
            removed = True
        return removed

    def _resend(self) -> None:
        """Send this turn's prompt again on the repaired conversation."""
        server = self._server
        if server is None or not self._session_id:
            self._fail("opencode's server went away while repairing the conversation")
            return
        command = self._as_command()
        try:
            if command is not None:
                self._start_command(*command)
            else:
                server.request(
                    "POST",
                    f"/session/{self._session_id}/prompt_async",
                    params={"directory": self._cwd},
                    body=self._prompt_body(self._prompt),
                    timeout=120,
                )
        except (OSError, ValueError) as exc:
            self._fail(ab.opencode_error_text(exc, "opencode would not take the message again."))
            return
        self._answer.clear()
        self._emitted.clear()
        self._accepting_input.set()

    def _prompt_body(self, text: str, with_attachments: bool = True) -> dict:
        parts: list[dict] = []
        named: list[str] = []
        if with_attachments:
            for path in self._attachments:
                part = ab._opencode_file_part(path)
                if part is not None:
                    parts.append(part)
                elif str(path or "").strip():
                    named.append(str(path).strip())
        if named:
            listing = "\n".join(named)
            text = (
                f"{text}\n\nAttached files (please read them):\n{listing}"
                if text
                else f"Attached files (please read them):\n{listing}"
            )
        # Text first, files after, the way the server stores them; a turn of
        # bare files carries no text part at all.
        if text or not parts:
            parts.insert(0, {"type": "text", "text": text})
        body: dict = {"parts": parts}
        provider_id, model_id = ab.opencode_split_model(self._model)
        if provider_id:
            body["model"] = {"providerID": provider_id, "modelID": model_id}
        if self._permission_mode == "plan":
            # opencode's own "plan" agent is the mode BlindPilot calls plan,
            # so plan mode selects it rather than describing it in rules.
            body["agent"] = "plan"
        if self._variant:
            body["variant"] = self._variant
        return body

    def _do_run(self) -> None:
        try:
            self._server = ab.opencode_server()
        except OSError as exc:
            self._fail(ab.opencode_error_text(exc, "opencode's server could not be started."))
            return
        if self._compact and not self._session_id:
            self._fail("There is no opencode conversation to compact yet")
            return
        # Effort levels are per model in opencode, and it rejects one the model
        # does not define, so the pooled choice from the picker is checked
        # against the model this turn will use.
        if self._effort:
            model = self._model or ab.opencode_default_model(self._cwd)
            if self._effort in ab.opencode_model_efforts(model, self._cwd):
                self._variant = self._effort

        # Subscribing before anything is asked for is what makes the first
        # words of the answer part of this turn rather than of the next one.
        try:
            self._stream = self._server.open(
                "GET", "/event", params={"directory": self._cwd}, timeout=None
            )
        except (OSError, ValueError) as exc:
            self._fail(ab.opencode_error_text(exc, "Could not follow opencode's progress."))
            return

        try:
            self._open_session()
        except (OSError, ValueError) as exc:
            self._fail(ab.opencode_error_text(exc, "Could not start an opencode conversation."))
            return
        self._on_session(self._session_id)

        command = self._as_command()
        try:
            if self._compact:
                self._start_compaction()
            elif command is not None:
                self._start_command(*command)
            else:
                self._server.request(
                    "POST",
                    f"/session/{self._session_id}/prompt_async",
                    params={"directory": self._cwd},
                    body=self._prompt_body(self._prompt),
                    timeout=120,
                )
        except (OSError, ValueError) as exc:
            self._fail(ab.opencode_error_text(exc, "opencode would not accept the message."))
            return

        self._accepting_input.set()
        self._on_started()
        self._read_events()

    def _open_session(self) -> None:
        assert self._server is not None
        rules = ab._OPENCODE_PERMISSIONS.get(self._permission_mode)
        if self._session_id:
            # A resumed conversation keeps its id, but the permission mode may
            # have been changed since, so its rules are re-stated every turn.
            if rules is not None:
                self._server.request(
                    "PATCH",
                    f"/session/{self._session_id}",
                    params={"directory": self._cwd},
                    body={"permission": rules},
                    timeout=60,
                )
            return
        body: dict = {}
        if rules is not None:
            body["permission"] = rules
        if self._permission_mode == "plan":
            # opencode's own "plan" agent is the mode BlindPilot calls plan,
            # so plan mode selects it rather than describing it in rules.
            body["agent"] = "plan"
        provider_id, model_id = ab.opencode_split_model(self._model)
        if provider_id:
            body["model"] = {"providerID": provider_id, "id": model_id}
        created = self._server.request(
            "POST", "/session", params={"directory": self._cwd}, body=body, timeout=120
        )
        session_id = created.get("id") if isinstance(created, dict) else None
        if not session_id:
            raise OSError("opencode did not return a conversation id")
        self._session_id = str(session_id)

    def _as_command(self) -> Optional[tuple[str, str]]:
        """(command, arguments) if the prompt names one of opencode's commands.

        opencode's commands are prompt templates its server expands — the text
        "/init" sent as a message is just those five characters, and would be
        answered rather than run. Anything it does not recognise stays an
        ordinary message, so a sentence that happens to start with a slash is
        not swallowed.
        """
        text = self._prompt.strip()
        if not text.startswith("/"):
            return None
        # Split on any whitespace, not a space: a command sent with attached
        # files has their paths on the lines after it.
        parts = text[1:].split(None, 1)
        if not parts:
            return None
        name, arguments = parts[0], parts[1] if len(parts) > 1 else ""
        known = {command for command, _description in ab.opencode_commands(self._cwd)}
        return (name, arguments.strip()) if name in known else None

    def _start_command(self, command: str, arguments: str) -> None:
        """Run one of opencode's commands, and narrate it like any other turn.

        Its command request only answers once the whole turn is over, so it is
        made from a thread of its own and the event stream is what the window
        hears from — the same way it hears an ordinary message.
        """
        body: dict = {"command": command, "arguments": arguments}
        provider_id, model_id = ab.opencode_split_model(self._model)
        if provider_id:
            body["model"] = f"{provider_id}/{model_id}"
        if self._permission_mode == "plan":
            # opencode's own "plan" agent is the mode BlindPilot calls plan,
            # so plan mode selects it rather than describing it in rules.
            body["agent"] = "plan"

        def work() -> None:
            try:
                assert self._server is not None
                self._server.request(
                    "POST",
                    f"/session/{self._session_id}/command",
                    params={"directory": self._cwd},
                    body=body,
                    timeout=None,
                )
            except (OSError, ValueError) as exc:
                if self._cancelled:
                    return
                detail = ab.opencode_error_text(exc, "the request failed")
                self._fail(f"opencode could not run /{command}: {detail}")
                # Nothing else will end the turn now, so release the reader.
                self._close_stream()

        ab.threading.Thread(target=work, daemon=True).start()

    def _start_compaction(self) -> None:
        assert self._server is not None
        provider_id, model_id = ab.opencode_split_model(
            self._model or ab.opencode_default_model(self._cwd)
        )
        if not provider_id:
            raise OSError("Choose an opencode model before compacting")
        self._server.request(
            "POST",
            f"/session/{self._session_id}/summarize",
            params={"directory": self._cwd},
            body={"providerID": provider_id, "modelID": model_id},
            timeout=120,
        )

    def _read_events(self) -> None:
        stream = self._stream
        assert stream is not None
        try:
            for raw in stream:  # type: ignore[attr-defined]
                if self._cancelled:
                    return
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                try:
                    event = ab.json.loads(line[5:].strip())
                except ValueError:
                    continue
                kind = str(event.get("type") or "")
                properties = event.get("properties") or {}
                # Every session on the server shares one stream — the title
                # writer and any subagent included — so anything that names a
                # different conversation belongs to somebody else's turn.
                session = properties.get("sessionID")
                if session in self._children:
                    # One of this turn's agents. Its idle or error is its own
                    # ending, never this turn's; the task part says how it went.
                    if kind not in ("session.idle", "session.error", "session.status"):
                        self._handle_event(kind, properties)
                    continue
                if session not in (None, self._session_id):
                    continue
                if kind == "session.error":
                    # The repair path re-sends and keeps reading; only an
                    # unrepaired failure returns here for good.
                    if not self._on_session_error(properties):
                        return
                if kind in ("session.idle", "session.compacted"):
                    self._finish()
                    return
                self._handle_event(kind, properties)
        except Exception as exc:
            # Deliberately broad: Stop closes this connection from another
            # thread, mid-read, and what a socket torn out from under the HTTP
            # reader raises is not something to enumerate. A stop is expected
            # and silent; anything else is still reported as the failure it is.
            if not self._cancelled:
                self._fail(ab.opencode_error_text(exc, "opencode's event stream ended"))
            return
        finally:
            self._accepting_input.clear()
        if not self._cancelled:
            self._fail("opencode closed the connection before the turn finished")

    def _finish(self) -> None:
        if self._settled.is_set():
            return
        self._settled.set()
        if self._compact:
            # A compaction produces no answer of its own, so say what happened
            # rather than finishing in silence.
            self._on_complete("Conversation compacted.")
        else:
            # opencode writes an answer as one text part per step, and a step
            # boundary is a paragraph boundary — run together they read as one
            # sentence that was never written.
            self._on_complete("\n\n".join(self._answer).strip())

    def _handle_event(self, kind: str, properties: dict) -> None:
        if kind == "message.updated":
            info = properties.get("info") or {}
            if isinstance(info, dict) and info.get("id"):
                self._roles[str(info["id"])] = str(info.get("role") or "")
        elif kind == "message.part.updated":
            self._part(properties.get("part") or {})
        elif kind in ("permission.asked", "permission.v2.asked"):
            self._answer_permission(properties)
        elif kind in ("question.asked", "question.v2.asked"):
            self._answer_question(properties)
        elif kind == "session.status":
            status = properties.get("status") or {}
            if isinstance(status, dict) and status.get("type") == "retry":
                self._on_activity(
                    "tool", f"opencode is retrying (attempt {status.get('attempt') or 1})"
                )

    def _part(self, part: object) -> None:
        if not isinstance(part, dict):
            return
        owner = part.get("sessionID")
        if owner in self._children:
            self._child_part(str(owner), part)
            return
        if owner not in (None, self._session_id):
            # Somebody else's conversation: its words are not this answer.
            return
        if part.get("type") == "tool" and part.get("tool") == "task":
            self._task_part(part)
        part_id = str(part.get("id") or "")
        kind = str(part.get("type") or "")
        if kind in ("text", "reasoning"):
            # The user's own message arrives as a part too; only the model's
            # side of the conversation belongs in the response rows.
            if self._roles.get(str(part.get("messageID") or "")) != "assistant":
                return
            # opencode opens a part empty, streams it a token at a time, then
            # repeats it in full: that last repeat is what says it is finished
            # and can be read out as one row rather than a hundred.
            text = str(part.get("text") or "").strip()
            if not text or part_id in self._emitted:
                return
            self._emitted.add(part_id)
            if kind == "reasoning":
                self._on_activity("thinking", text)
            else:
                self._answer.append(text)
                self._on_activity("assistant", text)
        elif kind == "tool":
            self._tool(part_id, part)
        elif kind in ("step-start", "step-finish"):
            # Lifecycle bookkeeping. A step-finish also carries the step's
            # tokens and cost, which no report reads, so there is nothing
            # here worth interrupting the transcript for.
            return
        elif kind in ("patch", "subtask", "file"):
            self._artifact_part(part_id, kind, part)
        elif kind:
            self._unknown_part(part_id, kind, part)

    def _task_part(self, part: dict) -> None:
        """Follow the agent a task tool call runs, by its child session.

        The task tool opens a child session (`parentID` is this one) and names
        it in the running part's `state.metadata.sessionId`; the part's own
        completion or error is how the agent ended.
        """
        state = part.get("state")
        if not isinstance(state, dict):
            return
        part_id = str(part.get("id") or "")
        metadata = state.get("metadata")
        child = str(metadata.get("sessionId") or "") if isinstance(metadata, dict) else ""
        child = child or self._task_children.get(part_id, "")
        if not child:
            return
        status = ab.subagent_status(state.get("status"))
        if child not in self._children:
            self._children[child] = ab.SUBAGENT_RUNNING
            self._task_children[part_id] = child
            raw_input = state.get("input")
            params = raw_input if isinstance(raw_input, dict) else {}
            name = ab.subagent_line(params.get("description") or params.get("subagent_type"), 80)
            task = ab.subagent_line(params.get("prompt"))
            self._on_subagent(
                child, name or "Subagent", ab.SUBAGENT_RUNNING, f"Task: {task}" if task else ""
            )
        if status and status != ab.SUBAGENT_RUNNING and self._children.get(child) != status:
            self._children[child] = status
            detail = ab.subagent_line(state.get("error") or state.get("title"))
            self._on_subagent(child, "", status, detail or f"Finished: {status}")

    def _child_part(self, child: str, part: dict) -> None:
        """What one of this turn's agents wrote or ran, once per part."""
        part_id = str(part.get("id") or "")
        kind = str(part.get("type") or "")
        if kind == "text":
            if self._roles.get(str(part.get("messageID") or "")) != "assistant":
                return
            text = ab.subagent_line(part.get("text"))
            if text and part_id not in self._emitted:
                self._emitted.add(part_id)
                self._on_subagent(child, "", "", text)
        elif kind == "tool":
            state = part.get("state")
            if not isinstance(state, dict):
                return
            status = str(state.get("status") or "")
            if status == "running" and part_id not in self._tools_running:
                self._tools_running.add(part_id)
                label = ab._opencode_tool_label(str(part.get("tool") or "tool"), state.get("input"))
                self._on_subagent(child, "", "", label)
            elif status == "error":
                self._on_subagent(child, "", "", f"Failed: {ab.subagent_line(state.get('error'))}")

    def _artifact_part(self, part_id: str, kind: str, part: dict) -> None:
        """Say a patch, subtask, or file the turn produced, once.

        opencode streams every part whole and then repeats it, so anything
        said here is said once per part id like the text rows. The user's own
        file parts are their message going out, not the turn coming back, and
        are left to the prompt row.
        """
        if self._roles.get(str(part.get("messageID") or "")) != "assistant":
            return
        if not part_id or part_id in self._emitted:
            return
        if kind == "patch":
            files = part.get("files")
            names = [
                ab._opencode_file_name(entry)
                for entry in (files if isinstance(files, list) else [])
                if ab._opencode_file_name(entry)
            ]
            line = f"Changed: {', '.join(names)}" if names else "Changed files"
        elif kind == "subtask":
            agent = str(part.get("agent") or "").strip()
            detail = str(part.get("description") or "").strip().splitlines()
            first = detail[0][:200] if detail and detail[0] else ""
            line = (
                f"Subagent {agent}: {first}"
                if agent and first
                else (first or f"Subagent {agent}".rstrip())
            )
        else:
            name = ab._opencode_file_name(part.get("filename"))
            line = f"Attached: {name}" if name else "Attached a file"
        self._emitted.add(part_id)
        self._on_activity("tool", line)

    def _unknown_part(self, part_id: str, kind: str, part: dict) -> None:
        """Say something for a part kind this file has never heard of.

        A newer opencode that streams a kind we do not know about should
        still be visible in the transcript rather than silently swallowed.
        """
        if self._roles.get(str(part.get("messageID") or "")) != "assistant":
            return
        if not part_id or part_id in self._emitted:
            return
        detail = (
            str(part.get("text") or "")
            or str(part.get("description") or "")
            or str(part.get("delta") or "")
        ).strip()
        first = detail.splitlines()[0][:120] if detail else ""
        self._emitted.add(part_id)
        self._on_activity("tool", f"{kind}: {first}" if first else kind)

    def _tool(self, part_id: str, part: dict) -> None:
        state = part.get("state")
        if not isinstance(state, dict):
            return
        status = str(state.get("status") or "")
        name = str(part.get("tool") or "tool")
        if status == "running" and part_id not in self._tools_running:
            self._tools_running.add(part_id)
            self._on_activity("tool", ab._opencode_tool_label(name, state.get("input")))
        elif status == "completed":
            output = str(state.get("output") or "").strip()
            if output:
                self._on_activity("result", output)
        elif status == "error":
            message = str(state.get("error") or "").strip()
            self._on_activity("result", message or f"{name} failed")

    def _post(self, routes: list[tuple[str, Optional[dict]]], what: str) -> bool:
        """POST to the first of these routes opencode accepts.

        A permission request and a question both hold the turn open until they
        are answered, and opencode is in the middle of moving both onto new
        endpoints. Trying the old one and then the new one costs one failed
        request in the worst case; getting it wrong costs a turn that never
        ends, so the routes are tried rather than guessed at.
        """
        if self._server is None:
            return False
        problem = ""
        for path, body in routes:
            try:
                self._server.request(
                    "POST", path, params={"directory": self._cwd}, body=body, timeout=30
                )
                return True
            except (OSError, ValueError) as exc:
                problem = ab.opencode_error_text(exc, f"opencode would not accept {what}")
        # Saying so matters: unanswered, the turn waits for an answer that is
        # never coming, and silence would look like the model thinking.
        self._on_activity("notice", f"Could not answer {what}: {problem}")
        return False

    def _answer_permission(self, properties: dict) -> None:
        """Answer a permission request the way the chosen mode says to.

        BlindPilot decides from the permission mode rather than interrupting
        with a dialog, the same way its Codex adapter does, so a run never
        stops waiting on an answer nobody was asked for.
        """
        request_id = str(properties.get("id") or "")
        if not request_id or self._server is None:
            return
        permission = str(properties.get("permission") or "")
        mode = self._permission_mode
        if mode == "bypassPermissions":
            reply = "always"
        elif mode == "auto":
            reply = "once"
        elif mode == "acceptEdits" and permission in ("edit", "write", "patch"):
            reply = "once"
        else:
            reply = "reject"
        answered = self._post(
            [
                (
                    f"/session/{self._session_id}/permissions/{request_id}",
                    {"response": reply},
                ),
                (
                    f"/api/session/{self._session_id}/permission/{request_id}/reply",
                    {"reply": reply},
                ),
            ],
            "a permission request",
        )
        if answered and reply == "reject":
            self._on_activity(
                "tool",
                f"Declined {permission or 'a request'} — the permission mode does not allow it",
            )

    def _answer_question(self, properties: dict) -> None:
        """Put a mid-run question to the person and send opencode their answers.

        opencode takes one list of chosen labels per question, in the order it
        asked them, and a question left unanswered has to be rejected rather
        than replied to — a turn waiting on an answer that is never coming
        never ends. It is in the middle of moving both onto new endpoints, so
        each is tried in turn the same way a permission reply is.
        """
        request_id = str(properties.get("id") or "")
        if not request_id or self._server is None:
            return
        asked = ab._opencode_questions(properties.get("questions"))
        answers = self._on_question(asked) if (asked and self._on_question) else None
        self._on_activity("tool", ab.questions.question_summary(asked, answers))
        if answers is None:
            self._post(
                [
                    (f"/question/{request_id}/reject", None),
                    (f"/api/session/{self._session_id}/question/{request_id}/reject", None),
                ],
                "a question",
            )
            return
        self._question_answered = True
        body = {"answers": [list(answer) for answer in answers]}
        self._post(
            [
                (f"/question/{request_id}/reply", body),
                (f"/api/session/{self._session_id}/question/{request_id}/reply", body),
            ],
            "a question",
        )
