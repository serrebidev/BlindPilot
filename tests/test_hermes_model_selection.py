"""Picking a model and a reasoning level for Hermes, and having it take effect.

Three defects live behind these tests, and all three were silent -- the window
reported the pick, the turn ran, and nothing said the choice had gone nowhere:

1. A picker row was joined with a colon (``palantir-gpt:<model>``), the form
   Hermes' own ``/model`` command takes. Hermes only reads a colon prefix as a
   provider when the left side is a provider it ships with; a user-defined
   entry -- which is what every Foundry/Palantir endpoint and every self-hosted
   gateway is -- is not on that list, so the whole row arrived as a MODEL NAME
   and the provider silently stayed put. Measured against a live gateway with
   the two forms interleaved: the qualified row failed (one turn produced no
   answer in four minutes, the retry came back ``HTTP 404 NOT_FOUND /
   LanguageModelService:ProxyModelNotFound`` after ten retries) while the split
   form answered in under four seconds both times. It matters beyond the label
   because each entry carries its own endpoint AND API mode -- ``…/openai/v1``
   with chat_completions versus ``…/anthropic`` with anthropic_messages -- so a
   wrong provider sends the turn to the wrong place.

2. ``supports_effort`` was False, with a comment claiming the protocol had no
   such control. ``session.create`` accepts ``reasoning_effort``: a session
   created with "low" reads back "low" while one created in the same second
   without it reads the profile's own level.

3. A Hermes reached over the network was refused a model list at all, on the
   advice that the model is chosen "on the machine it runs on" -- which for a
   headless server, the entire point of the remote mode, is nowhere.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import time

import agent_backends
import hermes_backend
from agent_backends import BACKEND_HERMES, BACKENDS
from hermes_backend import (
    HERMES_EFFORTS,
    MODEL_ROW_SEPARATOR,
    _model_rows,
    hermes_model_options,
    split_model_row,
)
from hermes_worker import HermesWorker

_QUALIFIED_MODEL = "ri.language-model-service..language-model.gpt-5-4"
_PROVIDER = "palantir-gpt"


def _callbacks() -> dict:
    return {
        "on_session": lambda _value: None,
        "on_started": lambda: None,
        "on_activity": lambda _kind, _value: None,
        "on_complete": lambda _value: None,
        "on_failed": lambda _value: None,
        "on_done": lambda: None,
    }


def _worker(**overrides) -> HermesWorker:
    kwargs = _callbacks()
    kwargs.update(overrides)
    return HermesWorker("test", None, ".", "default", **kwargs)


class _CatalogTransport:
    """Answers one ``model.options`` request and nothing else."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.sent: list[dict] = []
        self.started = False
        self.closed = False
        self.alive = True
        self._frames: list[dict] = [
            {"jsonrpc": "2.0", "method": "event", "params": {"type": "gateway.ready"}}
        ]

    def start(self) -> None:
        self.started = True

    def send(self, message: dict) -> bool:
        if self.closed:
            return False
        self.sent.append(message)
        if message.get("method") == "model.options":
            self._frames.append({"jsonrpc": "2.0", "id": message.get("id"), "result": self.payload})
        return True

    def receive(self, timeout: float) -> dict | None:  # noqa: ARG002 - interface
        if self.closed:
            return None
        if self._frames:
            return self._frames.pop(0)
        # The catalog request is answered and the stream is over: a real
        # transport's pipe has closed by now, so stop claiming otherwise.
        self.alive = False
        return None

    def close(self) -> None:
        self.closed = True

    def connected(self) -> bool:
        return self.alive and not self.closed

    def failure_detail(self) -> str:
        return "catalog transport ended"


_CATALOG = {
    "providers": [
        {
            "slug": _PROVIDER,
            "is_current": True,
            "authenticated": True,
            "models": [_QUALIFIED_MODEL, "other-model"],
        },
        {
            "slug": "locked-out",
            "authenticated": False,
            "models": ["never-offered"],
        },
    ]
}


# -- the row, and the two fields it becomes --------------------------------


def test_a_picker_row_is_not_joined_with_a_colon() -> None:
    """The separator has to be something Hermes never has to parse.

    A colon is what made the original defect possible, so this is asserted
    directly rather than only through behaviour: a future tidy-up that
    "restores" the documented ``provider:model`` spelling breaks the turn again.
    """
    assert ":" not in MODEL_ROW_SEPARATOR
    rows, current = _model_rows(_CATALOG)
    assert rows[0] == f"{_PROVIDER}{MODEL_ROW_SEPARATOR}{_QUALIFIED_MODEL}"
    assert current == rows[0]
    # A provider that is listed but not signed in cannot be picked: the first
    # turn on it would fail.
    assert not any("never-offered" in row for row in rows)


def test_a_row_splits_back_into_provider_and_model() -> None:
    provider, model = split_model_row(f"{_PROVIDER}{MODEL_ROW_SEPARATOR}{_QUALIFIED_MODEL}")
    assert (provider, model) == (_PROVIDER, _QUALIFIED_MODEL)


def test_a_model_typed_by_hand_carries_no_provider() -> None:
    """The box is editable, and a bare name is a legitimate thing to type.

    Hermes resolves a bare name against the provider already in use, which is
    exactly the right behaviour -- so no provider field is sent at all.
    """
    assert split_model_row(_QUALIFIED_MODEL) == ("", _QUALIFIED_MODEL)
    assert split_model_row("  ") == ("", "")


def test_a_model_name_containing_a_colon_survives_the_round_trip() -> None:
    """Aggregator ids are full of colons (``vendor/model:free``).

    Joining on a colon made these ambiguous; joining on the separator does not.
    """
    row = f"nous-api{MODEL_ROW_SEPARATOR}stepfun/step-3.7-flash:free"
    assert split_model_row(row) == ("nous-api", "stepfun/step-3.7-flash:free")


# -- what the turn actually sends ------------------------------------------


def test_the_provider_travels_as_its_own_field_not_glued_to_the_model() -> None:
    """The whole point: Hermes must never have to guess where the split is."""
    worker = _worker(model=f"{_PROVIDER}{MODEL_ROW_SEPARATOR}{_QUALIFIED_MODEL}")
    params = worker._session_params()
    assert params["model"] == _QUALIFIED_MODEL
    assert params["provider"] == _PROVIDER
    # The failure mode being prevented, stated as an assertion: the model field
    # must not carry the provider's name.
    assert _PROVIDER not in params["model"]


def test_the_reasoning_level_is_sent_on_the_field_hermes_reads() -> None:
    worker = _worker(model=_QUALIFIED_MODEL, effort="low")
    assert worker._session_params()["reasoning_effort"] == "low"


def test_no_reasoning_field_is_sent_when_none_was_picked() -> None:
    """Absence has a meaning of its own: inherit the profile's level.

    Sending an empty value instead would be a request to think at "", and the
    "leave it alone" entry in the picker would stop meaning that.
    """
    assert "reasoning_effort" not in _worker(model=_QUALIFIED_MODEL)._session_params()


def test_the_levels_offered_are_the_ones_hermes_validates() -> None:
    """Every level offered must be one Hermes accepts.

    Hermes' own validator takes minimal/low/medium/high/xhigh/max/ultra, plus
    "none" for "do not think". A level outside that set is ignored by Hermes,
    which would make the picker's choice quietly do nothing.
    """
    assert set(HERMES_EFFORTS) == {
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    }


# -- the catalog, local and remote ----------------------------------------


def test_the_catalog_is_read_over_the_network_too(monkeypatch) -> None:
    """A remote Hermes answers the same request as a local one.

    ``hermes serve`` dispatches its WebSocket through the identical JSON-RPC
    handler table, so refusing to ask was the bug -- not a limitation.
    """
    made: dict = {}

    def fake_ws(url, token, credential, username):
        made.update(url=url, token=token, credential=credential, username=username)
        return _CatalogTransport(_CATALOG)

    monkeypatch.setattr(hermes_backend, "WebSocketTransport", fake_ws)
    # Deliberately claim Hermes is NOT installed here: a remote catalog must
    # not depend on a local copy, which is the whole point of the remote mode.
    monkeypatch.setattr(hermes_backend, "hermes_installed", lambda: False)

    models, efforts, current, _current_effort, error = hermes_model_options(
        remote_url="ws://server:9119/api/ws",
        remote_token="secret",
        remote_credential="password",
        remote_username="pilot",
    )
    assert error == ""
    assert models[0] == f"{_PROVIDER}{MODEL_ROW_SEPARATOR}{_QUALIFIED_MODEL}"
    assert current == models[0]
    assert list(efforts) == list(HERMES_EFFORTS)
    assert made["url"] == "ws://server:9119/api/ws"
    assert made["credential"] == "password"
    assert made["username"] == "pilot"


def test_a_local_catalog_still_uses_the_pipe(monkeypatch) -> None:
    """Control for the test above: with no remote address, nothing dials out.

    Without this, a mistake that always took the network path would pass the
    remote test and go unnoticed on the zero-configuration local path.
    """

    def exploding_ws(*_args, **_kwargs):
        raise AssertionError("the local path must not open a network connection")

    monkeypatch.setattr(hermes_backend, "WebSocketTransport", exploding_ws)
    monkeypatch.setattr(hermes_backend, "hermes_installed", lambda: True)
    monkeypatch.setattr(hermes_backend, "StdioTransport", lambda _cwd: _CatalogTransport(_CATALOG))

    models, efforts, _current, _effort, error = hermes_model_options(".")
    assert error == ""
    assert models and list(efforts) == list(HERMES_EFFORTS)


def test_a_gateway_that_dies_before_ready_is_reported_at_once(monkeypatch) -> None:
    """The model query had its own ready loop, which never asked connected()."""

    class _Dead(_CatalogTransport):
        def receive(self, timeout: float) -> dict | None:  # noqa: ARG002 - interface
            self.alive = False
            return None

    monkeypatch.setattr(hermes_backend, "hermes_installed", lambda: True)
    monkeypatch.setattr(hermes_backend, "StdioTransport", lambda _cwd: _Dead(_CATALOG))
    # Bounded so a regression fails instead of hanging the run.
    monkeypatch.setattr(hermes_backend, "MODEL_QUERY_TIMEOUT", 2.0)

    started = time.monotonic()
    models, _efforts, _current, _effort, error = hermes_model_options(".")

    assert models == []
    assert error == "catalog transport ended"
    assert time.monotonic() - started < 1.0


def test_a_missing_local_hermes_is_reported_rather_than_dialled(monkeypatch) -> None:
    monkeypatch.setattr(hermes_backend, "hermes_installed", lambda: False)
    models, _efforts, _current, _effort, error = hermes_model_options(".")
    assert models == []
    assert "not found" in error.lower()


# -- what the window promises ---------------------------------------------


def test_hermes_declares_the_effort_control_it_has() -> None:
    """The flag drives the picker AND the setup wizard's summary.

    While it said False, the wizard told the user in plain words that Hermes
    "does not expose a reasoning effort level".
    """
    assert BACKENDS[BACKEND_HERMES].supports_effort is True
    assert agent_backends.BACKENDS[BACKEND_HERMES].supports_model is True


# -- a turn that goes quiet has to say so ---------------------------------


class _ChatterTransport:
    """Emits frames that carry no content, forever.

    This is what a real turn looked like while it retried a rejected model: a
    steady trickle of ``sessions.changed`` housekeeping and ``thinking.delta``
    frames with empty text. Bytes were arriving the whole time; nothing a
    listener could hear was.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    def start(self) -> None:
        return None

    def send(self, message: dict) -> bool:
        if self.closed:
            # Measured on a real StdioTransport: a closed pipe answers False.
            # The frames-for-ever behaviour below is deliberate and real (a busy
            # Hermes trickling content-free housekeeping); writing to a closed
            # connection is not.
            return False
        self.sent.append(message)
        return True

    def receive(self, timeout: float) -> dict | None:  # noqa: ARG002 - interface
        if self.closed:
            return None
        return {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {"type": "sessions.changed", "payload": {}},
        }

    def close(self) -> None:
        self.closed = True

    def connected(self) -> bool:
        return not self.closed

    def failure_detail(self) -> str:
        return "chatter transport ended"


def test_content_free_frames_do_not_pass_for_progress(monkeypatch) -> None:
    """A silent screen reader is the worst outcome this loop can produce.

    Measured on a live gateway: a turn on a model the endpoint rejected ran for
    over four minutes and the window heard NOTHING after "started", because
    every housekeeping frame reset the quiet timer. The user cannot tell that
    from a hang.

    Run with a hard deadline of its own: with the defect present this loop
    never announces AND never ends, so a plain assertion would hang the suite
    instead of failing it. A test that cannot fail in bounded time is not a
    guard.
    """
    import hermes_worker

    monkeypatch.setattr(hermes_worker, "_PROGRESS_NOTICE_SECONDS", 1.0)
    monkeypatch.setattr(hermes_worker, "_IDLE_LIMIT", 3.0)
    monkeypatch.setattr(hermes_worker, "_READ_TIMEOUT", 0.01)

    heard: list[str] = []
    failed: list[str] = []
    worker = _worker(
        on_activity=lambda _kind, text: heard.append(text),
        on_failed=failed.append,
    )
    worker._transport = _ChatterTransport()

    def give_up() -> None:
        import time

        time.sleep(8.0)
        worker._cancelled = True

    import threading

    threading.Thread(target=give_up, daemon=True).start()
    worker._consume_turn()

    assert any("Still working" in text for text in heard), heard
    # And the turn does not run forever: the idle limit still ends it.
    assert failed


def test_a_frame_that_produces_a_row_does_reset_the_timer(monkeypatch) -> None:
    """Control for the test above.

    A loop that announced "still working" regardless would pass that test while
    talking over a turn that is reporting itself perfectly well -- noise a
    listener cannot skip. Real steps must keep the announcement quiet.
    """
    import hermes_worker

    monkeypatch.setattr(hermes_worker, "_PROGRESS_NOTICE_SECONDS", 5.0)
    monkeypatch.setattr(hermes_worker, "_IDLE_LIMIT", 0.5)
    monkeypatch.setattr(hermes_worker, "_READ_TIMEOUT", 0.01)

    class _WorkingTransport(_ChatterTransport):
        def __init__(self) -> None:
            super().__init__()
            self._left = 20

        def receive(self, timeout: float) -> dict:  # noqa: ARG002 - interface
            self._left -= 1
            return {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "status.update",
                    "payload": {"text": "running tests"},
                },
            }

    heard: list[str] = []
    worker = _worker(on_activity=lambda _kind, text: heard.append(text))
    worker._transport = _WorkingTransport()

    # Real steps keep the clock at zero, so nothing here will ever end the
    # loop on its own -- which is the point. Stop it from outside.
    def stop_soon() -> None:
        import time

        time.sleep(0.4)
        worker._cancelled = True

    import threading

    threading.Thread(target=stop_soon, daemon=True).start()
    worker._consume_turn()

    assert heard, "a reported step must still produce a row"
    assert not any("Still working" in text for text in heard), heard


# -- moving a reused session onto the picked model ---------------------------


class _ReplyTransport:
    """Answers with scripted frames and ends when they run out, like a pipe."""

    def __init__(self, frames: list[dict]) -> None:
        self.frames = list(frames)
        self.sent: list[dict] = []
        self.closed = False
        self.alive = True

    def send(self, message: dict) -> bool:
        if not self.connected():
            return False
        self.sent.append(message)
        return True

    def receive(self, timeout: float) -> dict | None:  # noqa: ARG002 - interface
        if self.frames:
            return self.frames.pop(0)
        self.alive = False
        return None

    def close(self) -> None:
        self.closed = True

    def connected(self) -> bool:
        return self.alive and not self.closed

    def failure_detail(self) -> str:
        return "reply transport ended"


_ROW = f"{_PROVIDER}{MODEL_ROW_SEPARATOR}{_QUALIFIED_MODEL}"


def _reused(model: str, session_model: str, frames: list[dict]):
    from hermes_worker import HeldConnection

    activities: list[tuple[str, str]] = []
    kwargs = _callbacks()
    kwargs["on_activity"] = lambda kind, text: activities.append((kind, text))
    worker = HermesWorker("test", None, ".", "default", model=model, **kwargs)
    transport = _ReplyTransport(frames)
    held = HeldConnection()
    held.keep(transport, "live-1", session_model)
    worker._held = held
    return worker, held, transport, activities


def test_a_reused_session_already_on_the_picked_model_is_left_alone() -> None:
    """A /model round trip on every turn was a 60 s wait budget for nothing."""
    worker, _held, transport, _rows = _reused(_ROW, _ROW, [])
    assert worker._open_transport() is True

    worker._apply_live_selection()

    assert transport.sent == []


def test_a_changed_pick_moves_the_session_and_is_remembered() -> None:
    worker, held, transport, _rows = _reused(
        _ROW,
        f"{_PROVIDER}{MODEL_ROW_SEPARATOR}other-model",
        [
            {"jsonrpc": "2.0", "id": 101, "result": {"output": "switched"}},
            {"jsonrpc": "2.0", "id": 102, "result": {}},
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.complete",
                    "session_id": "live-1",
                    "payload": {"status": "complete", "text": "done"},
                },
            },
        ],
    )

    worker.run()

    first = transport.sent[0]
    assert first["method"] == "slash.exec"
    assert first["params"]["command"].startswith(f"/model {_QUALIFIED_MODEL}")
    assert f"--provider {_PROVIDER}" in first["params"]["command"]
    assert transport.sent[1]["method"] == "prompt.submit"
    # The next turn on this connection knows what the session now runs.
    assert held.take() == (transport, "live-1")
    assert held.model == _ROW


def test_a_refused_switch_is_heard_and_not_recorded_as_applied() -> None:
    """Best effort. The message still goes, on whatever model the session had."""
    worker, _held, transport, rows = _reused(
        _ROW,
        "",
        [{"jsonrpc": "2.0", "id": 101, "error": {"message": "unknown model"}}],
    )
    assert worker._open_transport() is True

    worker._apply_live_selection()

    assert [m["method"] for m in transport.sent] == ["slash.exec"]
    assert any("refused" in text and "unknown model" in text for _kind, text in rows)
    assert worker._session_model == ""
