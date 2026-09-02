"""A long turn must stay connected, and must say that it is still working.

Michal's complaint, and what each test here holds to account: "I just lost the
connection and got a timeout on a longer session ... sometimes you work for
hours. I don't want to guess whether you did something, I want to always be
told."

Measured before writing any of this, against a server pinging exactly as Hermes
does on a public bind (20s interval, 20s pong deadline):

    before: after 21s of quiet the reading thread was DEAD, connected() False,
            and send() still returned True -- a silent loss
    after:  180s of quiet, thread alive, connection alive, next turn answered

The cause was not the network. ``create_connection(timeout=20)`` applies that
timeout to every later socket operation, so ``recv()`` raised a timeout during
any quiet stretch, and a bare ``except Exception: return`` ended the reader.
With nobody reading, nobody answered the server's pings either.
"""

from __future__ import annotations

import threading
import time

import hermes_backend
from hermes_backend import WebSocketTransport
from hermes_worker import HermesWorker


def _callbacks() -> dict:
    return {
        "on_session": lambda _v: None,
        "on_started": lambda: None,
        "on_activity": lambda _k, _v: None,
        "on_complete": lambda _v: None,
        "on_failed": lambda _v: None,
        "on_done": lambda: None,
    }


class _QuietSocket:
    """A socket that times out on read, the way a quiet connection does.

    ``recv`` raises the library's timeout exception, which is what happens when
    no frame arrives within the socket timeout -- NOT when the peer goes away.
    """

    def __init__(self, error, frames_after: int = 0) -> None:
        self.error = error
        self.reads = 0
        self.frames_after = frames_after
        self.sent: list[str] = []
        self.closed = False

    def recv(self):
        self.reads += 1
        if self.frames_after and self.reads > self.frames_after:
            return '{"jsonrpc":"2.0","id":7,"result":{"ok":true}}'
        raise self.error

    def send(self, payload):
        self.sent.append(payload)

    def close(self):
        self.closed = True


def _transport_with(sock) -> WebSocketTransport:
    """A transport wrapped around a prepared socket, with its reader running."""
    t = WebSocketTransport("ws://127.0.0.1:9999/api/ws", "k", "token", "")
    t._ws = sock
    t._closing.clear()
    t._reader = threading.Thread(target=t._read_forever, daemon=True)
    t._reader.start()
    return t


def _timeout_error():
    """The timeout the websocket library raises, or the socket one without it."""
    try:
        from websocket import WebSocketTimeoutException

        return WebSocketTimeoutException("Connection timed out")
    except ImportError:  # pragma: no cover - remote path dependency absent
        return TimeoutError("timed out")


# -- the reading thread survives quiet ------------------------------------


def test_a_quiet_stretch_does_not_kill_the_reading_thread():
    """The measured bug: 21 seconds of quiet ended the reader, so pings stopped.

    Checked as a property (is the thread alive, is the connection usable) rather
    than as a symptom, and driven by many timed-out reads rather than by real
    waiting, so the test is fast and still discriminating.
    """
    sock = _QuietSocket(_timeout_error())
    t = _transport_with(sock)
    try:
        deadline = time.time() + 3.0
        while sock.reads < 40 and time.time() < deadline:
            time.sleep(0.02)

        assert sock.reads >= 40, f"reader stopped reading after {sock.reads} reads"
        assert t._reader.is_alive()
        # The connection is what the turn asks about before reusing it.
        assert t.connected() is True
        # And nothing may be recorded as a failure: this was normal quiet.
        assert t._error == ""
    finally:
        t.close()


def test_a_frame_after_a_long_quiet_stretch_still_arrives():
    """Surviving is worthless if the frame after the wait is lost."""
    sock = _QuietSocket(_timeout_error(), frames_after=15)
    t = _transport_with(sock)
    try:
        frame = None
        deadline = time.time() + 5.0
        while frame is None and time.time() < deadline:
            frame = t.receive(0.2)
        assert frame is not None, "the frame after the quiet stretch never arrived"
        assert frame["id"] == 7
    finally:
        t.close()


def test_a_real_failure_still_ends_the_reader():
    """Tolerating timeouts must not turn a dead peer into an endless wait."""
    sock = _QuietSocket(OSError("connection reset by peer"))
    t = _transport_with(sock)
    try:
        deadline = time.time() + 3.0
        while t._reader.is_alive() and time.time() < deadline:
            time.sleep(0.02)
        assert not t._reader.is_alive()
        assert t.connected() is False
        assert "reset" in t._error
    finally:
        t.close()


def test_the_peer_closing_the_connection_ends_the_reader():
    """The library's own "connection closed" is a real end, not a quiet spell.

    Separate from the reset case above because a mutation that tolerated EVERY
    exception passed that one: an OSError is not what a closed websocket raises.
    """
    try:
        from websocket import WebSocketConnectionClosedException

        error = WebSocketConnectionClosedException("socket is already closed")
    except ImportError:  # pragma: no cover - remote dependency absent
        error = ConnectionResetError("closed")
    sock = _QuietSocket(error)
    t = _transport_with(sock)
    try:
        deadline = time.time() + 3.0
        while t._reader.is_alive() and time.time() < deadline:
            time.sleep(0.02)
        assert not t._reader.is_alive(), "a closed connection was treated as quiet"
        assert t.connected() is False
        assert t._error
    finally:
        t.close()


def test_the_timeouts_that_are_tolerated_are_named_not_guessed():
    """Both the library's timeout and the socket's own one mean "not yet"."""
    assert TimeoutError in hermes_backend._WS_TIMEOUT_ERRORS
    try:
        from websocket import WebSocketTimeoutException

        assert WebSocketTimeoutException in hermes_backend._WS_TIMEOUT_ERRORS
    except ImportError:  # pragma: no cover
        pass
    # A dead peer must NOT be on that list, or a real failure becomes a hang.
    assert OSError not in hermes_backend._WS_TIMEOUT_ERRORS
    assert Exception not in hermes_backend._WS_TIMEOUT_ERRORS


# -- no silent loss -------------------------------------------------------


def test_sending_into_a_connection_nobody_reads_is_refused():
    """Measured: send() returned True with the reader dead, and the answer
    never came. A refusal becomes a message; success becomes silence."""
    sock = _QuietSocket(OSError("gone"))
    t = _transport_with(sock)
    try:
        deadline = time.time() + 3.0
        while t._reader.is_alive() and time.time() < deadline:
            time.sleep(0.02)
        assert not t._reader.is_alive()

        assert t.send({"jsonrpc": "2.0", "id": 1, "method": "prompt.submit"}) is False
        # And the reason has to be available for the message the user hears.
        assert t.failure_detail()
    finally:
        t.close()


def test_a_closing_transport_does_not_report_a_phantom_failure():
    """Shutting down is not a fault, and must not be announced as one."""
    sock = _QuietSocket(_timeout_error())
    t = _transport_with(sock)
    t.close()
    time.sleep(0.1)
    assert t._error == ""
    # And a send during shutdown must not invent a reason either: the reader is
    # gone because we stopped it, which is not a lost connection.
    t.send({"jsonrpc": "2.0", "id": 1, "method": "session.interrupt"})
    assert t._error == ""


# -- the turn loop itself -------------------------------------------------
#
# The tests above check each piece in isolation. These drive the real waiting
# loop, which is where the pieces have to work together -- and where a mutation
# run showed the isolated tests defended nothing: removing the progress notice
# from the loop entirely left them all green.


class _ScriptedTransport:
    """A transport that answers a fixed number of reads with nothing.

    Stands in for a Hermes that is busy: the connection is fine, no frames
    arrive. Optionally delivers a frame partway through, and can be declared
    dead at a chosen read so a mid-turn drop can be timed.
    """

    def __init__(self, *, empty_reads: int, frame_at: int | None = None,
                 dead_at: int | None = None, end_after: bool = True) -> None:
        self.empty_reads = empty_reads
        self.frame_at = frame_at
        self.dead_at = dead_at
        self.end_after = end_after
        self.reads = 0
        self.alive = True
        self.sent: list[dict] = []

    def send(self, message: dict) -> bool:
        self.sent.append(message)
        return True

    def receive(self, timeout: float) -> dict | None:  # noqa: ARG002 - interface
        self.reads += 1
        if self.dead_at is not None and self.reads >= self.dead_at:
            self.alive = False
        if self.frame_at is not None and self.reads == self.frame_at:
            return {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {"type": "tool.start", "session_id": "s",
                           "payload": {"name": "terminal", "tool_id": "t1"}},
            }
        if self.reads >= self.empty_reads and self.end_after:
            # End the turn so the test cannot run forever.
            return {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {"type": "message.complete", "session_id": "s",
                           "payload": {"status": "complete", "text": "done"}},
            }
        return None

    def close(self) -> None:
        self.alive = False

    def connected(self) -> bool:
        return self.alive

    def failure_detail(self) -> str:
        return "Lost the connection to Hermes at ws://example: reset"


def _run_loop(transport, monkeypatch, *, notice: float = 5.0, check: float = 2.0,
              idle_limit: float = 1000.0) -> tuple[HermesWorker, list, list]:
    """Drive the real loop with the clock scaled down, not with real waiting.

    The loop measures quiet against a clock (it used to count empty reads,
    which a trickle of content-free frames defeated entirely -- see
    test_hermes_model_selection.py). So the clock is what gets substituted
    here: each read advances it by ``_READ_TIMEOUT``, which is exactly what an
    empty read costs in reality, and a two-minute wait runs in milliseconds.
    """
    import hermes_worker as hw

    monkeypatch.setattr(hw, "_PROGRESS_NOTICE_SECONDS", notice)
    monkeypatch.setattr(hw, "_CONNECTION_CHECK_SECONDS", check)
    monkeypatch.setattr(hw, "_IDLE_LIMIT", idle_limit)

    fake_clock = {"t": 0.0}

    def clock() -> float:
        # Advances on every read of it, so the loop sees time passing at the
        # rate its own reads would take.
        fake_clock["t"] += hw._READ_TIMEOUT
        return fake_clock["t"]

    monkeypatch.setattr(hw, "_now", clock)

    rows: list[tuple[str, str]] = []
    failed: list[str] = []
    callbacks = _callbacks()
    callbacks["on_activity"] = lambda kind, text: rows.append((kind, text))
    callbacks["on_failed"] = failed.append
    callbacks["on_complete"] = lambda _t: None
    worker = HermesWorker("q", None, ".", "default", **callbacks)
    worker._transport = transport
    worker._consume_turn()
    return worker, rows, failed


def test_the_loop_speaks_up_during_a_long_quiet_turn(monkeypatch):
    """Michal's requirement, driven through the real loop: a long turn must
    never pass in silence. A mutation removing the notice left every isolated
    test green, so this one goes through _consume_turn itself."""
    transport = _ScriptedTransport(empty_reads=60)
    _worker, rows, failed = _run_loop(transport, monkeypatch, notice=5.0)

    notices = [text for kind, text in rows if "still working" in text.lower()]
    assert notices, "the loop said nothing during a long quiet turn"
    assert not failed


def test_the_loop_repeats_the_notice_while_the_wait_goes_on(monkeypatch):
    """One notice then silence again would be the same complaint back."""
    transport = _ScriptedTransport(empty_reads=90)
    _worker, rows, _failed = _run_loop(transport, monkeypatch, notice=5.0)

    notices = [t for k, t in rows if "still working" in t.lower()]
    assert len(notices) >= 3, f"only {len(notices)} notice(s) over a long wait"


def test_the_notice_is_not_repeated_for_every_single_read(monkeypatch):
    """The bound matters as much as the notice: a row every half second is
    worse than silence for someone listening to it."""
    transport = _ScriptedTransport(empty_reads=40)
    _worker, rows, _failed = _run_loop(transport, monkeypatch, notice=5.0)

    notices = [t for k, t in rows if "still working" in t.lower()]
    # 40 reads at 0.5s = 20s of quiet; at one notice per 5s that is about 4.
    assert len(notices) <= 6, f"{len(notices)} notices for 20 seconds of quiet"


def test_a_frame_resets_the_quiet_clock(monkeypatch):
    """Work that reports steps is not quiet, and the clock starts again.

    Measured as a count, which is what the reset actually changes: a run where
    a frame lands every few reads must produce FEWER notices than the same
    number of reads with no frames at all. Without the reset both are equal.
    """
    with_frames = _ScriptedTransport(empty_reads=60, frame_at=None)
    # Ten frames spread through the run, each one resetting the count.
    class _Interrupted(_ScriptedTransport):
        def receive(self, timeout: float):  # noqa: ARG002 - interface
            self.reads += 1
            if self.reads >= self.empty_reads:
                return {"jsonrpc": "2.0", "method": "event",
                        "params": {"type": "message.complete", "session_id": "s",
                                   "payload": {"status": "complete", "text": "done"}}}
            if self.reads % 8 == 0:
                return {"jsonrpc": "2.0", "method": "event",
                        "params": {"type": "tool.start", "session_id": "s",
                                   "payload": {"name": "terminal", "tool_id": "t1"}}}
            return None

    _w1, rows_quiet, _f1 = _run_loop(with_frames, monkeypatch, notice=5.0)
    _w2, rows_busy, _f2 = _run_loop(_Interrupted(empty_reads=60), monkeypatch, notice=5.0)

    quiet_notices = len([t for k, t in rows_quiet if "still working" in t.lower()])
    busy_notices = len([t for k, t in rows_busy if "still working" in t.lower()])

    assert quiet_notices > 0, "the quiet run should have been narrated"
    assert busy_notices < quiet_notices, (
        f"a run reporting steps was narrated as often as a silent one "
        f"({busy_notices} vs {quiet_notices}) -- the clock is not being reset"
    )


def test_notices_keep_their_cadence_after_a_burst_of_activity(monkeypatch):
    """A quiet stretch AFTER notices have already fired must still be narrated.

    Measured with the loop's own arithmetic: the next threshold is stored as
    "idle + interval", so once notices have fired the threshold sits high. If a
    frame resets only the elapsed count and not the threshold, the quiet
    stretch that follows produces NOTHING (0 notices instead of 2 over 15
    seconds) -- Michal's original complaint, returning after the first tool
    call in a long turn.
    """
    class _QuietBurstQuiet(_ScriptedTransport):
        def receive(self, timeout: float):  # noqa: ARG002 - interface
            self.reads += 1
            # Long quiet first, so several notices fire and the threshold rises.
            if self.reads == 61:
                return {"jsonrpc": "2.0", "method": "event",
                        "params": {"type": "tool.start", "session_id": "s",
                                   "payload": {"name": "terminal", "tool_id": "t1"}}}
            if self.reads >= 91:
                return {"jsonrpc": "2.0", "method": "event",
                        "params": {"type": "message.complete", "session_id": "s",
                                   "payload": {"status": "complete", "text": "done"}}}
            return None

    rows: list[tuple[str, str]] = []
    callbacks = _callbacks()
    callbacks["on_activity"] = lambda kind, text: rows.append((kind, text))
    callbacks["on_complete"] = lambda _t: None

    import hermes_worker as hw

    monkeypatch.setattr(hw, "_PROGRESS_NOTICE_SECONDS", 5.0)
    monkeypatch.setattr(hw, "_CONNECTION_CHECK_SECONDS", 2.0)
    monkeypatch.setattr(hw, "_IDLE_LIMIT", 1000.0)
    # Same substituted clock as _run_loop: the loop reads elapsed time, so a
    # test left on the real clock finishes in milliseconds and never crosses a
    # five-second threshold.
    fake_clock = {"t": 0.0}

    def clock() -> float:
        fake_clock["t"] += hw._READ_TIMEOUT
        return fake_clock["t"]

    monkeypatch.setattr(hw, "_now", clock)
    worker = HermesWorker("q", None, ".", "default", **callbacks)
    worker._transport = _QuietBurstQuiet(empty_reads=91)
    worker._consume_turn()

    # Count only what came AFTER the frame: that is where the two behaviours
    # differ. Before it they are identical, which is why an overall count
    # cannot tell them apart.
    frame_index = next(i for i, (k, t) in enumerate(rows) if t == "terminal")
    after = [t for k, t in rows[frame_index + 1:] if "still working" in t.lower()]

    assert after, (
        "the quiet stretch after a tool call was never narrated -- the notice "
        "threshold was carried over instead of reset"
    )


def test_a_connection_that_drops_mid_wait_is_reported_within_seconds(monkeypatch):
    """Not after the idle limit. Fifteen minutes of silence after a drop is
    exactly the "did anything happen?" problem being fixed."""
    transport = _ScriptedTransport(empty_reads=10_000, dead_at=6, end_after=False)
    _worker, _rows, failed = _run_loop(
        transport, monkeypatch, check=2.0, idle_limit=1000.0
    )

    assert failed == ["Lost the connection to Hermes at ws://example: reset"]
    # The property: noticed in a handful of reads, nowhere near the idle limit.
    # idle_limit 1000s would be 2000 reads.
    assert transport.reads < 50, f"took {transport.reads} reads to notice a drop"


def test_a_turn_that_never_goes_quiet_gets_no_notices(monkeypatch):
    """Notices are for silence. A chatty turn must read exactly as before."""
    class _Chatty:
        def __init__(self) -> None:
            self.reads = 0
            self.sent: list[dict] = []

        def send(self, message: dict) -> bool:
            self.sent.append(message)
            return True

        def receive(self, timeout: float):  # noqa: ARG002 - interface
            self.reads += 1
            if self.reads > 30:
                return {"jsonrpc": "2.0", "method": "event",
                        "params": {"type": "message.complete", "session_id": "s",
                                   "payload": {"status": "complete", "text": "ok"}}}
            return {"jsonrpc": "2.0", "method": "event",
                    "params": {"type": "message.delta", "session_id": "s",
                               "payload": {"text": "word. "}}}

        def close(self) -> None:
            pass

        def connected(self) -> bool:
            return True

        def failure_detail(self) -> str:
            return "n/a"

    _worker, rows, failed = _run_loop(_Chatty(), monkeypatch, notice=5.0)
    assert not [t for k, t in rows if "still working" in t.lower()]
    assert not failed


# -- the turn says it is still working ------------------------------------


def _worker_with_rows() -> tuple[HermesWorker, list[tuple[str, str]]]:
    rows: list[tuple[str, str]] = []
    callbacks = _callbacks()
    callbacks["on_activity"] = lambda kind, text: rows.append((kind, text))
    return HermesWorker("q", None, ".", "default", **callbacks), rows


def test_a_quiet_turn_says_it_is_still_working():
    """Hours-long work must never be indistinguishable from a hang."""
    worker, rows = _worker_with_rows()
    worker._announce_still_working(125.0)

    assert rows, "a quiet turn said nothing at all"
    kind, text = rows[0]
    assert kind == "tool"
    assert "still working" in text.lower()
    # The elapsed time, so the listener can judge whether to keep waiting.
    assert "2 minute" in text


def test_the_notice_names_what_the_turn_is_waiting_on():
    """"Still working on terminal" is worth far more than "still working"."""
    worker, rows = _worker_with_rows()
    worker._tool_start({"name": "terminal", "tool_id": "t1"})
    rows.clear()
    worker._announce_still_working(65.0)

    assert "terminal" in rows[0][1]


def test_the_notice_repeats_while_the_wait_lasts_but_not_every_read():
    """A notice every half second would be worse than silence."""
    from hermes_worker import _PROGRESS_NOTICE_SECONDS, _READ_TIMEOUT

    # The property: notices are minutes apart, not reads apart.
    assert _PROGRESS_NOTICE_SECONDS >= 30
    assert _PROGRESS_NOTICE_SECONDS / _READ_TIMEOUT > 10


def test_hermes_own_account_of_what_it_is_doing_becomes_a_row():
    """Hermes sends status.update; BlindPilot used to drop it entirely."""
    worker, rows = _worker_with_rows()
    worker._handle_event(
        {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {
                "type": "status.update",
                "session_id": "s",
                "payload": {"kind": "process", "text": "running the test suite"},
            },
        }
    )
    assert rows == [("tool", "running the test suite")]
    # Remembered, so a following quiet stretch can name it.
    assert worker._last_step == "running the test suite"


def test_compacting_is_reported_in_words_rather_than_as_a_status_code():
    """A mid-turn compaction otherwise looks like the transcript resetting."""
    worker, rows = _worker_with_rows()
    worker._handle_event(
        {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {
                "type": "status.update",
                "session_id": "s",
                "payload": {"kind": "compacting", "text": "compaction in progress"},
            },
        }
    )
    assert rows and "summaris" in rows[0][1].lower()


def test_an_empty_status_update_produces_no_row():
    """A row that says nothing is noise a screen reader has to walk past."""
    worker, rows = _worker_with_rows()
    worker._handle_event(
        {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {"type": "status.update", "session_id": "s", "payload": {}},
        }
    )
    assert rows == []


# -- a dead connection is reported promptly, not after the idle limit -----


class _DeadAfterConnect:
    """A transport that is connected, then quietly is not."""

    def __init__(self) -> None:
        self.alive = True
        self.sent: list[dict] = []

    def send(self, message: dict) -> bool:
        self.sent.append(message)
        return True

    def receive(self, timeout: float) -> None:  # noqa: ARG002 - interface
        return None

    def close(self) -> None:
        self.alive = False

    def connected(self) -> bool:
        return self.alive

    def failure_detail(self) -> str:
        return "Lost the connection to Hermes at ws://example: reset"


def test_a_connection_that_dies_mid_turn_is_reported_with_its_reason():
    """Without this the turn sat out the whole idle limit -- fifteen minutes of
    silence that looks exactly like work."""
    failed: list[str] = []
    callbacks = _callbacks()
    callbacks["on_failed"] = failed.append
    worker = HermesWorker("q", None, ".", "default", **callbacks)
    transport = _DeadAfterConnect()
    transport.alive = False
    worker._transport = transport

    worker._consume_turn()

    assert failed == ["Lost the connection to Hermes at ws://example: reset"]


def test_the_connection_is_checked_far_sooner_than_the_idle_limit():
    """The property: a drop is noticed in seconds, not in a quarter of an hour."""
    from hermes_worker import _CONNECTION_CHECK_SECONDS, _IDLE_LIMIT

    assert _CONNECTION_CHECK_SECONDS < 60
    assert _CONNECTION_CHECK_SECONDS * 10 < _IDLE_LIMIT
