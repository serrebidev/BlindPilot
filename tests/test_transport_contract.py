"""Every transport fake in this suite, run against the real contract.

Brandon's suggestion, closing PR #30: "a contract harness that runs every
transport fake against the real ``Transport`` semantics ... so the third fake
that lies about connection behaviour fails at write time instead of on a runner
in another country."

The registry below is the point. A new fake added to this suite and not listed
here is caught by ``test_every_fake_in_the_suite_is_registered``, which walks
the test modules and finds transport-shaped classes by their methods. Without
that, this file would guard the six fakes that exist today and quietly ignore
the seventh — which is the failure mode it was built to prevent.

The two real transports are parametrised alongside the fakes as positive
controls: a clause that is wrong about how a connection behaves fails on the
real one first, so a wrong clause cannot be mistaken for a lying fake.
"""

from __future__ import annotations

import importlib
import inspect
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import hermes_backend
from transport_contract import ContractViolation, check_transport_contract

# --------------------------------------------------------------------------
# The real transports, as positive controls
# --------------------------------------------------------------------------


class _OneFrameThenExits(hermes_backend.StdioTransport):
    """A real ``StdioTransport`` over real pipes whose child says one thing.

    Not a fake: everything below ``start`` is the shipped implementation —
    real ``subprocess`` pipes, the real reader threads, the real frame queue.
    Only the child is swapped, because launching Hermes itself in a unit test
    would make this an integration test with a Hermes-shaped prerequisite.

    This is what proves the clauses describe reality: the ended-stream clause
    passes here because a closed pipe genuinely turns ``connected()`` False.
    """

    def start(self) -> None:
        self._proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [
                sys.executable,
                "-c",
                'import sys; sys.stdout.write(\'{"jsonrpc":"2.0","id":1,"result":{}}\\n\');'
                " sys.stdout.flush()",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()


def _real_websocket() -> hermes_backend.WebSocketTransport:
    """A real ``WebSocketTransport`` that was never connected.

    Never started, and that is not laziness: ``start()`` on an unreachable
    address raises ``OSError`` by design (it is how the user is told "check the
    host name"), so calling it here would test the connect failure rather than
    the contract. The closed-state clause is exactly what a caller meets after
    that OSError, and it must answer the same way as a connection that dropped
    mid-turn. Measured identical on both.
    """
    return hermes_backend.WebSocketTransport("ws://example.invalid/ws", None, None, None)


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------

# (module, class name, how to build one, does its stream end)
#
# ``stream_ends=False`` is not an exemption — it is the distinction the
# contract makes. ``_ChatterTransport`` and ``_ScriptedTransport`` model a
# Hermes that is alive and working while saying nothing a listener can hear,
# which is a real state: measured on a real StdioTransport whose child sleeps,
# five reads in a row answer None while connected() stays True. Asserting the
# ended-stream clause against them would assert something false about real
# transports and destroy the two guards against a silent screen reader.
FAKES: list[tuple[str, str, str, bool]] = [
    ("test_hermes_backend", "_FakeTransport", "kl([])", True),
    ("test_hermes_attachments", "_FakeTransport", "kl([])", True),
    ("test_hermes_sessions", "_FakeTransport", "kl([])", True),
    ("test_hermes_model_selection", "_CatalogTransport", "kl({})", True),
    ("test_hermes_model_selection", "_ChatterTransport", "kl()", False),
    (
        "test_long_turn_connection",
        "_ScriptedTransport",
        "kl(empty_reads=1, end_after=False)",
        False,
    ),
    # Found by test_every_fake_in_the_suite_is_registered, not by reading the
    # files: the registry check earned its place on its first run.
    ("test_long_turn_connection", "_DeadAfterConnect", "kl()", False),
    # Found by test_every_fake_in_the_suite_is_registered on the very next
    # change after it was written: a new fake in the remote-session tests.
    ("test_hermes_remote_new_session", "_CreateReply", 'kl("/srv/app")', True),
    # The questions and slash-command tests. The first models a Hermes waiting
    # on the answer to a question it asked -- connected, and saying nothing
    # until it gets one -- so its stream does not end; the second replays a
    # script and ends like a pipe when it runs out.
    ("test_hermes_questions", "_RecordingTransport", "kl()", False),
    ("test_hermes_slash", "_ScriptedTransport", "kl([])", True),
]

REAL: list[tuple[str, object, bool]] = [
    ("hermes_backend.StdioTransport (real pipes)", lambda: _OneFrameThenExits("."), True),
    ("hermes_backend.WebSocketTransport (never connected)", _real_websocket, False),
]

# The methods that make something a transport. Used to find fakes nobody
# registered, so this file cannot rot into a list of yesterday's fakes.
TRANSPORT_METHODS = frozenset({"send", "receive", "close", "connected", "failure_detail"})

# A module that will not import because wxPython is absent is tolerated during
# the sweep; anything else that will not import is reported.
#
# Not a list of module names, because that list would be wrong within a week:
# the GUI modules here are gated on wx either by ``pytest.importorskip`` or by
# importing ``blindpilot_app``, and which ones those are changes. The test asks
# WHY a module would not import instead.
#
# This tolerance costs nothing on CI, which is the point: the Linux job installs
# Ubuntu's python3-wxgtk4.0 and the Windows job has wx in the venv, so on every
# runner these modules import and ARE swept. It only relaxes the sweep on a
# developer machine with no GUI stack — measured here, where 17 modules cannot
# import for exactly that reason. A transport fake lives beside the worker, not
# beside a wx dialog, so a wx-gated module is the one place the sweep can afford
# to miss on a dev box while staying complete where it is enforced.
WX_IS_MISSING = ("wx",)


def _is_only_missing_wx(exc: BaseException) -> bool:
    """Is this import failure just "no GUI stack on this machine"?

    Two shapes, both measured on Linux without wxPython: a module gated with
    ``pytest.importorskip("wx")`` raises pytest's ``Skipped`` (a
    ``BaseException``, which is why a plain ``except Exception`` let it escape
    and silently skipped the whole registry guard), and a module that imports
    ``blindpilot_app`` raises ``ModuleNotFoundError(name="wx")``.
    """
    if isinstance(exc, ModuleNotFoundError):
        return exc.name in WX_IS_MISSING
    return type(exc).__name__ == "Skipped" and any(w in str(exc) for w in WX_IS_MISSING)


def _build(module_name: str, class_name: str, recipe: str):
    """Import a test module and build one of its fakes."""
    module = importlib.import_module(module_name)
    kl = getattr(module, class_name, None)
    if kl is None:
        pytest.fail(
            f"{module_name}.{class_name} is registered in FAKES but no longer exists — "
            "update the registry rather than deleting the clause it was covering"
        )
    return eval(recipe, {"kl": kl})  # noqa: S307 - recipe is a literal in this file


# --------------------------------------------------------------------------
# The contract, applied
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("module_name", "class_name", "recipe", "stream_ends"),
    FAKES,
    ids=[f"{m}.{c}" for m, c, _r, _s in FAKES],
)
def test_a_fake_transport_behaves_like_a_connection(
    module_name: str, class_name: str, recipe: str, stream_ends: bool
) -> None:
    check_transport_contract(
        lambda: _build(module_name, class_name, recipe),
        f"{module_name}.{class_name}",
        stream_ends=stream_ends,
    )


@pytest.mark.parametrize(("who", "build", "stream_ends"), REAL, ids=[who for who, _b, _s in REAL])
def test_a_real_transport_satisfies_the_same_contract(who: str, build, stream_ends: bool) -> None:
    """The positive control: a clause wrong about reality fails here first."""
    check_transport_contract(build, who, stream_ends=stream_ends)


# --------------------------------------------------------------------------
# The registry cannot go stale
# --------------------------------------------------------------------------


def test_every_fake_in_the_suite_is_registered() -> None:
    """Find transport-shaped classes in the suite and insist they are covered.

    Without this, the harness guards the fakes that existed the day it was
    written — precisely the gap Brandon's suggestion is about. The check is
    structural (does the class have the five protocol methods) rather than
    name-based, because the next fake will not be called ``_FakeTransport``.

    A module that cannot be imported here is NOT waved through. Measured on
    Linux without wxPython: importing ``test_hermes_sessions_ui`` runs its
    module-level ``pytest.importorskip("wx")``, whose ``Skipped`` derives from
    ``BaseException`` — so ``except Exception`` did not catch it and the skip
    propagated out of this test. The whole registry guard then reported
    "skipped" on exactly the platform whose CI found two of the three defects
    that motivated the harness. A guard that disappears where it is needed most
    is worse than none, because the summary line says nothing is wrong.

    So a module that will not import is collected and reported: it cannot be
    swept for fakes, and saying so out loud is the only honest outcome.
    """
    registered = {(module, name) for module, name, _r, _s in FAKES}
    here = Path(__file__).resolve()
    unregistered: list[str] = []
    unreadable: list[str] = []

    for path in sorted(here.parent.glob("test_*.py")):
        if path == here:
            continue
        module_name = path.stem
        try:
            module = importlib.import_module(module_name)
        except BaseException as exc:  # noqa: BLE001 - pytest's Skipped is not an Exception
            if not _is_only_missing_wx(exc):
                unreadable.append(f"{module_name} ({type(exc).__name__}: {exc})")
            continue
        for name, obj in vars(module).items():
            if not inspect.isclass(obj):
                continue
            # Defined here, not imported from elsewhere in the tree.
            if getattr(obj, "__module__", None) != module_name:
                continue
            methods = {m for m in TRANSPORT_METHODS if callable(getattr(obj, m, None))}
            if methods != TRANSPORT_METHODS:
                continue
            if (module_name, name) in registered:
                continue
            unregistered.append(f"{module_name}.{name}")

    assert not unregistered, (
        "these transport-shaped classes are not in FAKES, so nothing checks that they "
        "behave like a connection: " + ", ".join(unregistered) + ". Add each to the "
        "registry with stream_ends=True if its frames run out, or False if it models a "
        "peer that stays connected and quiet."
    )

    # A module that will not import for any reason OTHER than a missing GUI
    # stack is a hole in the sweep, and is named rather than passed over.
    assert not unreadable, (
        "these test modules could not be swept for unregistered transport fakes, so a "
        "lying fake could hide in them: " + ", ".join(unreadable)
    )


def test_the_contract_actually_rejects_the_shape_that_shipped_the_bug() -> None:
    """Validity control: the clause must catch the fake PR #30 was written with.

    A harness that passes everything is decoration. This reconstructs the
    original shape — ``receive`` returning None for ever while ``connected()``
    answers True — and requires the contract to reject it. If this test ever
    passes by NOT raising, the harness above has stopped measuring anything.
    """

    class ConnectedButSilentForEver:
        """The fake as it was when it hid two cross-platform defects."""

        def __init__(self) -> None:
            self.closed = False

        def send(self, message: dict) -> bool:  # noqa: ARG002 - interface
            return True

        def receive(self, timeout: float) -> dict | None:  # noqa: ARG002 - interface
            return None

        def close(self) -> None:
            self.closed = True

        def connected(self) -> bool:
            return True  # the lie

        def failure_detail(self) -> str:
            return "fake transport ended"

    with pytest.raises(ContractViolation, match="connected"):
        check_transport_contract(
            ConnectedButSilentForEver, "reconstructed pre-fix fake", stream_ends=True
        )


def test_the_contract_rejects_a_transport_that_cannot_say_why_it_died() -> None:
    """Second validity control, on the other clause.

    An empty ``failure_detail()`` is how a turn fails without telling the user
    anything — the "Hermes did not respond in time" case that hid a real
    reason. The clause must catch it.
    """

    class SilentAboutItsDeath:
        def send(self, message: dict) -> bool:  # noqa: ARG002 - interface
            return False

        def receive(self, timeout: float) -> dict | None:  # noqa: ARG002 - interface
            return None

        def close(self) -> None:
            return None

        def connected(self) -> bool:
            return False

        def failure_detail(self) -> str:
            return "   "  # whitespace is not a reason

    with pytest.raises(ContractViolation, match="failure_detail"):
        check_transport_contract(SilentAboutItsDeath, "mute transport", stream_ends=False)


def test_the_contract_rejects_a_close_that_only_works_once() -> None:
    """Third validity control: teardown paths run twice and must not raise."""

    class ExplodesOnSecondClose:
        def __init__(self) -> None:
            self._closed = False

        def send(self, message: dict) -> bool:  # noqa: ARG002 - interface
            return False

        def receive(self, timeout: float) -> dict | None:  # noqa: ARG002 - interface
            return None

        def close(self) -> None:
            if self._closed:
                raise RuntimeError("already closed")
            self._closed = True

        def connected(self) -> bool:
            return False

        def failure_detail(self) -> str:
            return "closed"

    with pytest.raises(ContractViolation, match="idempotent"):
        check_transport_contract(ExplodesOnSecondClose, "brittle close", stream_ends=False)
