"""Every backend's worker really does provide what the window calls on it.

`AgentWorker` is the Protocol the window holds a worker through, so that it
can drive whichever backend was chosen without knowing which one it is. It
declared start, is_alive, join and cancel — but not `steer`, which
`_on_steer` calls directly. All four workers happened to implement it, so
nothing ever broke; a fifth that did not would have type-checked clean and
failed the first time somebody pressed Steer.

The window's own call site used to be `getattr(worker, "steer")(text)`, which
is what hid it: written that way, neither a reader nor a checker could see
that the Protocol was short.
"""

from __future__ import annotations

import agent_backends
import blindpilot_app
from hermes_worker import HermesWorker

WORKERS = [
    blindpilot_app.ClaudeWorker,
    agent_backends.CodexWorker,
    agent_backends.FreebuffWorker,
    agent_backends.OpencodeWorker,
    HermesWorker,
]


def test_there_is_a_worker_for_every_backend():
    assert len(WORKERS) == len(agent_backends.BACKEND_IDS)


def test_every_worker_provides_what_the_window_drives_it_through():
    """The Protocol is the promise; this is the four things keeping it."""
    promised = [
        name
        for name in dir(agent_backends.AgentWorker)
        if not name.startswith("_") and callable(getattr(agent_backends.AgentWorker, name, None))
    ]
    assert "steer" in promised, "the Protocol has lost the method Steer depends on"

    for worker in WORKERS:
        missing = [name for name in promised if not hasattr(worker, name)]
        assert not missing, f"{worker.__name__} does not provide {missing}"


def test_the_window_only_calls_what_the_protocol_promises():
    """Anything the window reaches for must be in the contract, or the next
    backend can be written without it and nobody will find out until it is
    asked to do that thing."""
    for name in ("start", "is_alive", "join", "cancel", "steer"):
        assert hasattr(agent_backends.AgentWorker, name), f"AgentWorker is missing {name}"
