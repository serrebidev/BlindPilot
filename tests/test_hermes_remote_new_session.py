"""New Session when the Hermes is on another machine.

Why this file exists
--------------------

The New Session dialog asked for a folder ON THIS DISK and required it to
exist here. For a Hermes running elsewhere that question has no answer, and
the consequence was not a refusal but a silence.

Measured against a live gateway, reading the result back from the SERVER's own
state.db rather than from the reply (the reply looks fine either way):

    cwd sent = C:\\Users\\g\\Desktop\\projekt   (Windows path, Linux Hermes)
    -> session.create returns OK, no error
    -> stored cwd = '/home/ubuntu'             the SERVER's home directory

So the remote end checks the path against its own filesystem, quietly
substitutes its own directory, and says nothing. The user browsed to a folder,
passed a validation that only proved the folder exists HERE, and got a session
running somewhere else with a tab named after a directory it was never in.

The same measurement established the other half: ``session.create`` accepts a
``title``, and a title given that way is stored with ``title_source='user'``
and is NOT replaced by the automatic name Hermes derives from the first
message.

    created with title    -> title='Nazwa z BlindPilota'      source='user'
    created without title -> title='Odpowiedź jednym słowem OK #9'  source='llm'

Hence: a name is what the dialog asks for in remote mode, the folder becomes an
optional path on the server, and an empty name still means "let Hermes name it"
— which is the behaviour anyone who does not want to name things keeps.
"""

from __future__ import annotations

import importlib

import pytest


def _worker_module():
    """Resolved per test: a sibling suite clears ``sys.modules``."""
    return importlib.import_module("hermes_worker")


# --------------------------------------------------------------------------
# The title reaches session.create
# --------------------------------------------------------------------------


def _params(**kwargs) -> dict:
    m = _worker_module()
    worker = m.HermesWorker(
        "hello",
        None,
        "",
        "default",
        on_session=lambda _s: None,
        on_started=lambda: None,
        on_activity=lambda _k, _t: None,
        on_complete=lambda _t: None,
        on_failed=lambda _m: None,
        on_done=lambda: None,
        **kwargs,
    )
    return worker._session_params()


def test_a_named_session_sends_its_name_to_hermes() -> None:
    """The name is the whole point: without this it never leaves the dialog."""
    assert _params(session_title="Radio pipeline")["title"] == "Radio pipeline"


def test_an_unnamed_session_sends_no_title_at_all() -> None:
    """Absence is meaningful: it is what makes Hermes name the conversation.

    Sending an empty string instead would be a user-set title of "", which is
    not the same request and would suppress the automatic name.
    """
    assert "title" not in _params()
    assert "title" not in _params(session_title="   ")


def test_a_name_is_trimmed_rather_than_sent_with_its_whitespace() -> None:
    assert _params(session_title="  Radio pipeline  ")["title"] == "Radio pipeline"


# --------------------------------------------------------------------------
# Where the session actually landed
# --------------------------------------------------------------------------


class _CreateReply:
    """A transport that answers session.create with a chosen resolved cwd.

    Registered in tests/test_transport_contract.py: its stream ends when the
    scripted reply is taken, and it refuses writes once closed, because that is
    what both real transports do.
    """

    def __init__(self, landed: str) -> None:
        self._landed = landed
        self.sent: list[dict] = []
        self.closed = False
        self.alive = True
        self._frames: list[dict] = []

    def start(self) -> None:
        return None

    def send(self, message: dict) -> bool:
        if not self.connected():
            return False
        self.sent.append(message)
        if message.get("method") == "session.create":
            self._frames.append(
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "result": {
                        "session_id": "live1",
                        "stored_session_id": "stored1",
                        "info": {"cwd": self._landed},
                    },
                }
            )
        return True

    def receive(self, timeout: float) -> dict | None:  # noqa: ARG002 - interface
        if self.closed:
            return None
        if self._frames:
            return self._frames.pop(0)
        self.alive = False
        return None

    def close(self) -> None:
        self.closed = True

    def connected(self) -> bool:
        return self.alive and not self.closed

    def failure_detail(self) -> str:
        return "create-reply transport ended"


def _ensure_session(asked_for: str, landed: str) -> list[tuple[str, str]]:
    """Run the real session-creation path and collect what it announced."""
    m = _worker_module()
    notes: list[tuple[str, str]] = []
    worker = m.HermesWorker(
        "hello",
        None,
        asked_for,
        "default",
        on_session=lambda _s: None,
        on_started=lambda: None,
        on_activity=lambda kind, text: notes.append((kind, text)),
        on_complete=lambda _t: None,
        on_failed=lambda msg: notes.append(("failed", msg)),
        on_done=lambda: None,
    )
    worker._transport = _CreateReply(landed)
    assert worker._ensure_session() is True
    return notes


def test_a_folder_the_remote_hermes_could_not_use_is_reported() -> None:
    """The measured silence, turned into a sentence.

    This is the defect the whole change exists for: the turn succeeded, the
    conversation ran in the server's home directory, and nothing told the user.
    """
    notes = _ensure_session(r"C:\Users\g\Desktop\projekt", "/home/ubuntu")
    said = " ".join(text for _kind, text in notes)
    assert r"C:\Users\g\Desktop\projekt" in said
    assert "/home/ubuntu" in said


def test_a_folder_that_was_honoured_says_nothing() -> None:
    """Negative control: a note on every session would train people to ignore it."""
    assert _ensure_session("/srv/app", "/srv/app") == []


def test_a_session_with_no_folder_asked_for_says_nothing() -> None:
    """The normal remote case. Hermes chose the directory because it was asked
    to, so reporting it as a substitution would be a false alarm on the very
    path this change makes ordinary."""
    assert _ensure_session("", "/home/ubuntu") == []


@pytest.mark.parametrize(
    ("asked", "landed"),
    [
        ("/srv/app/", "/srv/app"),  # trailing separator only
        ("/srv/app", "/srv/app/"),
        (r"C:\Projects\App", "C:/Projects/App"),  # separator style only
        ("/SRV/App", "/srv/app"),  # case only
    ],
)
def test_the_same_folder_written_differently_is_not_a_relocation(asked: str, landed: str) -> None:
    """One of the two strings comes from ANOTHER machine, so it cannot be
    compared with os.path.samefile: the path may not exist here, and a Windows
    client and a Linux server share neither separator nor case rules. A
    cosmetic difference must not be announced as the session having moved."""
    assert _ensure_session(asked, landed) == []


def test_a_genuinely_different_folder_is_still_caught() -> None:
    """Validity control for the comparison above: if flattening separators and
    case made everything look equal, the previous test would pass for the wrong
    reason and this one would fail."""
    assert _ensure_session("/srv/app", "/srv/other") != []
