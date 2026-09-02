"""One place that says what a Transport must do, so a fake cannot lie about it.

Why this exists
---------------

Two of the three defects the fork's CI found in PR #30 shared a single root
cause, and it was not in the product: the test's fake transport had a shape no
real transport has. Its ``receive`` returned ``None`` once the scripted frames
ran out while ``connected()`` went on answering ``True`` — "connected but
silent, for ever". A loop written against that fake spins out its whole
deadline, which on Linux is a 60-second ``pytest-timeout`` failure and on
Windows was invisible.

A fake built for the test's convenience freezes the bug instead of catching it.
The remedy is not to fix the two fakes that were caught; it is to make the
third one fail at write time, here, rather than on a runner in another country.

How the clauses were arrived at
-------------------------------

Every clause below was measured against the two real transports
(``StdioTransport`` over real pipes with a child that exits, and
``WebSocketTransport``), not reasoned about. Both real transports are included
in the parametrisation as positive controls: if a clause is wrong about what a
transport actually does, it fails on the real one first, and the clause is
wrong rather than the fake.

The measurement that shaped the design
--------------------------------------

The obvious phrasing — "``receive`` exhausting implies ``connected()`` going
False" — is FALSE for a live transport, and asserting it unconditionally would
have broken the two tests that guard against a silent screen reader.

Measured: a ``StdioTransport`` whose child is alive and simply not talking (a
Hermes in the middle of a long turn) answers ``None`` to five reads in a row
while ``connected()`` stays ``True`` and ``send()`` still succeeds. ``None``
means "nothing yet", which is the documented meaning in ``Transport.receive``
— it does not mean the connection ended.

So the clause has to distinguish two states a single ``None`` cannot:

* a stream that has ENDED — the pipe closed, the socket went away. Here
  ``connected()`` must turn ``False`` within a bounded number of reads, because
  a caller polling for frames has no other way to learn that nobody is left to
  send one.
* a peer that is merely QUIET — alive, working, saying nothing yet. Here
  ``connected()`` staying ``True`` is correct, and a fake modelling this is
  modelling a real thing.

Fakes therefore declare which they are (``stream_ends``), and the clause is
only applied to the ones claiming a finite stream. ``_ChatterTransport`` and
``_ScriptedTransport`` model a busy Hermes on purpose and are registered as
such — that is not an exemption, it is the distinction being respected.
"""

from __future__ import annotations

import time
from typing import Any, Callable

# How long a finite stream gets to admit it has ended, and how patiently each
# read waits.
#
# A BUDGET IN SECONDS, NOT A COUNT OF READS — and that distinction is not
# stylistic, it is the first thing the positive control caught. The clause was
# written as "poll 60 times with a 1 ms timeout, then require connected() to be
# False", and it FAILED on the real StdioTransport. Not because the transport
# lies: because 60 reads of 1 ms is 60 milliseconds, and the child process had
# not finished starting yet. I was measuring process start-up and calling it a
# closed stream. Measured: the same transport answers connected() False after
# ~0.05 s once the pipe genuinely drains, and answers True if asked sooner.
#
# So an early read is its own kind of false reading, and the fix is to give the
# stream real time and ask again rather than to count harder. A per-read
# timeout of 50 ms also lets StdioTransport's reader thread actually deliver,
# which a 1 ms timeout does not.
#
# THE BUDGET IS ALSO WHAT KEEPS THIS TEST FROM BEING FLAKY, and that was
# measured rather than guessed. Twelve runs of the real StdioTransport, timed
# from start() to connected() answering False:
#
#   budget 0.06s, read timeout 0.001s -> ended in time in 10 of 12 runs
#   budget 5.0s,  read timeout 0.05s  -> ended in time in 12 of 12 runs
#
# Mean time to actually end: 0.053s. So the tight budget does not merely risk a
# false accusation, it delivers one roughly one run in six — on a runner in
# another country, at whatever load it happens to be under. 5 seconds is a ~94x
# margin over the measured figure and still well inside the suite's 60s
# pytest-timeout. A guard that accuses a correct transport at random teaches
# people to re-run the build until it is green, which is worse than no guard.
SECONDS_A_FINITE_STREAM_GETS_TO_ADMIT_IT_ENDED = 5.0
READ_TIMEOUT_WHILE_WAITING_FOR_THE_END = 0.05


class ContractViolation(AssertionError):
    """A transport (real or fake) that does not behave like a connection."""


def _fail(who: str, what: str) -> None:
    raise ContractViolation(f"{who}: {what}")


def check_closed_state(transport: Any, who: str) -> None:
    """After ``close()``, every method must answer like a dead connection.

    This is the half a caller relies on when a turn ends badly. Measured
    identical on both real transports, before start and after close alike:
    ``connected()`` False, ``receive()`` None, ``send()`` False, and
    ``failure_detail()`` a non-empty string — the last one because it is what
    the user is told instead of "Hermes did not respond in time", and an empty
    string there is a turn that failed without saying why.
    """
    transport.close()

    # Idempotent: a held connection is closed on teardown paths that can run
    # twice, and the second close must not raise. Measured: both real
    # transports tolerate it.
    try:
        transport.close()
    except Exception as exc:  # noqa: BLE001 - any exception is the defect
        _fail(who, f"close() is not idempotent: {type(exc).__name__}: {exc}")

    if transport.connected() is not False:
        _fail(who, "connected() does not answer False after close()")

    detail = transport.failure_detail()
    if not isinstance(detail, str) or not detail.strip():
        _fail(who, f"failure_detail() is empty after close(): {detail!r}")

    if transport.send({"jsonrpc": "2.0", "method": "ping"}) is not False:
        _fail(who, "send() does not answer False after close()")

    if transport.receive(0.001) is not None:
        _fail(who, "receive() still yields a frame after close()")


def check_finite_stream_ends(transport: Any, who: str) -> None:
    """A stream that has ended must stop claiming to be connected.

    Only for transports whose frames genuinely run out. This is the clause the
    two PR #30 defects would have failed: the fake answered None for ever while
    ``connected()`` stayed True, so a poll loop had nothing to stop it.

    Deliberately NOT applied to a fake that models a live-but-quiet peer — see
    the module docstring. Asserting it there would be asserting something false
    about real transports.

    Reads until the time budget runs out rather than a fixed number of times,
    and re-checks ``connected()`` on every pass: an answer of True early on is
    not a violation, it is an unfinished start-up (see the constants above).
    Only "still True when the budget is gone" is the defect.
    """
    deadline = time.monotonic() + SECONDS_A_FINITE_STREAM_GETS_TO_ADMIT_IT_ENDED
    frames_the_whole_time = True

    while time.monotonic() < deadline:
        if transport.receive(READ_TIMEOUT_WHILE_WAITING_FOR_THE_END) is None:
            frames_the_whole_time = False
        if transport.connected() is False:
            return  # ended, and said so — the contract holds

    if frames_the_whole_time:
        _fail(
            who,
            "declares a finite stream but produced frames for the whole "
            f"{SECONDS_A_FINITE_STREAM_GETS_TO_ADMIT_IT_ENDED:g}s budget — either its "
            "stream does not end (register it with stream_ends=False) or it never "
            "runs dry",
        )

    _fail(
        who,
        "receive() has run dry but connected() still answers True after "
        f"{SECONDS_A_FINITE_STREAM_GETS_TO_ADMIT_IT_ENDED:g}s — this is the shape that "
        "made a poll loop spin out its whole deadline (silence at the screen "
        "reader, a 60s timeout on Linux, invisible on Windows)",
    )


def check_transport_contract(
    build: Callable[[], Any],
    who: str,
    *,
    stream_ends: bool,
) -> None:
    """Run every clause that applies to this transport.

    ``build`` is called per clause so a clause never inherits state another one
    left behind; ``stream_ends`` says whether this transport's frames run out
    (a scripted fake) or whether it models a peer that stays connected and
    quiet (a busy Hermes).
    """
    if stream_ends:
        transport = build()
        _start_if_it_has_one(transport)
        check_finite_stream_ends(transport, who)
        transport.close()

    transport = build()
    _start_if_it_has_one(transport)
    check_closed_state(transport, who)


def _start_if_it_has_one(transport: Any) -> None:
    """Some fakes carry ``start()``, the protocol does not require it.

    A ``start()`` that refuses is not a contract violation — a real
    ``WebSocketTransport`` pointed at an unreachable address raises ``OSError``
    on purpose, and what matters is that the object still answers correctly
    afterwards, which is precisely what the closed-state clause then checks.
    """
    start = getattr(transport, "start", None)
    if not callable(start):
        return
    try:
        start()
    except OSError:
        pass
