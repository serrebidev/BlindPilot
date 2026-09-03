"""Attachments that travel to Hermes as bytes rather than as a path.

Every one of these locks down a fault that a path-only attachment produces,
and each was measured against a live Hermes gateway before being written:

  - the file's bytes have to arrive, because Hermes may be running in WSL or on
    another machine, where the client's path means nothing;
  - the name has to be sent separately, because a Linux gateway does not read a
    backslash as a separator and stores "D:\\dir\\report.xlsx" as ONE filename;
  - the prompt has to carry the path Hermes stored the file at, because Hermes
    stages uploads outside the conversation workspace and its own "@file:"
    expansion refuses anything out there ("path is outside the allowed
    workspace") -- the ref would come back as a warning with no content;
  - a file that cannot be sent has to be reported by name, because the
    alternative is an answer about a file the model never received.
"""

from __future__ import annotations

import base64

import pytest

from agent_backends import BACKEND_CLAUDE, BACKEND_CODEX, BACKEND_FREEBUFF, BACKEND_HERMES, BACKENDS
from hermes_worker import (
    ATTACHMENT_MAX_BYTES,
    AttachmentError,
    HermesWorker,
    attachment_data_url,
    attachment_name,
    check_attachment,
)


def _callbacks() -> dict:
    return {
        "on_session": lambda _value: None,
        "on_started": lambda: None,
        "on_activity": lambda _kind, _value: None,
        "on_complete": lambda _value: None,
        "on_failed": lambda _value: None,
        "on_done": lambda: None,
    }


class _FakeTransport:
    """Replays scripted replies and records what was sent.

    Its stream ends when the replies run out, like a real pipe — see
    tests/transport_contract.py.
    """

    def __init__(self, replies: list[dict] | None = None) -> None:
        self.replies = list(replies or [])
        self.sent: list[dict] = []
        self.closed = False
        self.alive = True

    def send(self, message: dict) -> bool:
        if not self.connected():
            return False
        self.sent.append(message)
        return True

    def receive(self, timeout: float) -> dict | None:  # noqa: ARG002 - interface
        if self.replies:
            return self.replies.pop(0)
        self.alive = False
        return None

    def close(self) -> None:
        self.closed = True

    def connected(self) -> bool:
        return self.alive and not self.closed

    def failure_detail(self) -> str:
        return "fake transport ended"


def _attach_reply(rid: int, stored: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "result": {"attached": True, "name": stored.rsplit("/", 1)[-1], "path": stored},
    }


# -- naming across the WSL boundary ---------------------------------------


def test_a_windows_path_does_not_become_the_stored_filename():
    """Measured on a live gateway: it stored a file called "D:\\...\\x.txt".

    The gateway runs on Linux, so it splits on "/" only. Sending the raw path
    as the name produced one file whose name was the whole Windows path, which
    a screen reader then reads out drive letter and all.
    """
    assert attachment_name(r"D:\projekty\zestawienie stacji.xlsx") == "zestawienie stacji.xlsx"
    assert attachment_name("/mnt/d/projekty/report.docx") == "report.docx"
    assert attachment_name(r"C:/mixed\separators/file.txt") == "file.txt"
    # A quoted drop and a trailing separator both come from real pickers.
    assert attachment_name('"D:\\dir\\quoted.txt"') == "quoted.txt"
    assert attachment_name("") == ""


def test_the_upload_carries_the_bare_name_not_the_client_path(tmp_path):
    sample = tmp_path / "stacje.xlsx"
    sample.write_bytes(b"payload")
    worker = HermesWorker(
        "read it", None, ".", "default", attachments=[str(sample)], **_callbacks()
    )
    worker._transport = _FakeTransport(
        [_attach_reply(101, "/home/u/.hermes/attachments/stacje.xlsx")]
    )
    worker._request_id = 100
    worker._live_session = "live-1"

    assert worker._upload_attachments() is not None
    params = worker._transport.sent[0]["params"]
    assert params["name"] == "stacje.xlsx"
    # The property that matters: no separator survives into the name, so the
    # gateway cannot store the path as a filename.
    assert "\\" not in params["name"] and "/" not in params["name"]


# -- the bytes themselves --------------------------------------------------


def test_the_file_bytes_are_what_is_sent_not_the_path(tmp_path):
    """The whole point: the far side may not have this file at all."""
    sample = tmp_path / "raport.txt"
    content = b"sekretny znacznik: 7731\n"
    sample.write_bytes(content)
    worker = HermesWorker("", None, ".", "default", attachments=[str(sample)], **_callbacks())
    worker._transport = _FakeTransport([_attach_reply(101, "/gw/attachments/raport.txt")])
    worker._request_id = 100
    worker._live_session = "live-1"

    assert worker._upload_attachments() is not None
    frame = worker._transport.sent[0]
    assert frame["method"] == "file.attach"
    data_url = frame["params"]["data_url"]
    encoded = data_url.split(",", 1)[1]
    # Decoding is the discriminating check: a frame that merely mentions the
    # file would pass any assertion about the method name.
    assert base64.b64decode(encoded) == content
    # A path from this machine must NOT be offered as a place to look: two
    # machines can mount the same drive letter with different files on it.
    assert "path" not in frame["params"]


def test_the_data_url_declares_the_media_type(tmp_path):
    sample = tmp_path / "sheet.xlsx"
    sample.write_bytes(b"x")
    assert attachment_data_url(str(sample)).startswith(
        "data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,"
    )
    other = tmp_path / "unknown.zzz"
    other.write_bytes(b"x")
    assert attachment_data_url(str(other)).startswith("data:application/octet-stream;base64,")


def test_every_attachment_is_sent_not_only_the_first(tmp_path):
    one = tmp_path / "one.txt"
    two = tmp_path / "two.txt"
    one.write_bytes(b"1")
    two.write_bytes(b"22")
    worker = HermesWorker(
        "", None, ".", "default", attachments=[str(one), str(two)], **_callbacks()
    )
    worker._transport = _FakeTransport(
        [_attach_reply(101, "/gw/a/one.txt"), _attach_reply(102, "/gw/a/two.txt")]
    )
    worker._request_id = 100
    worker._live_session = "live-1"

    uploaded = worker._upload_attachments()
    assert [name for name, _ in uploaded] == ["one.txt", "two.txt"]
    assert [f["params"]["name"] for f in worker._transport.sent] == ["one.txt", "two.txt"]


# -- what the prompt then says --------------------------------------------


def test_the_prompt_points_at_the_path_hermes_stored_the_file_at(tmp_path):
    """Measured: an "@file:" ref into the staging dir is REFUSED.

    Hermes stages uploads in its own attachments dir, outside the workspace,
    and context expansion answers a ref out there with "path is outside the
    allowed workspace" and no content. A plain path is read by the agent's file
    tools instead, which is what the probe observed.
    """
    sample = tmp_path / "raport.txt"
    sample.write_bytes(b"x")
    worker = HermesWorker(
        "Summarise this", None, ".", "default", attachments=[str(sample)], **_callbacks()
    )
    text = worker._prompt_with_attachments([("raport.txt", "/gw/attachments/raport.txt")])

    assert "Summarise this" in text
    assert "/gw/attachments/raport.txt" in text
    # The property, not the wording: nothing in the prompt may invite the
    # refused expansion path.
    assert "@file:" not in text


def test_a_prompt_less_send_still_asks_for_the_file_to_be_read(tmp_path):
    """Attaching with no typed question is a legitimate send in BlindPilot."""
    worker = HermesWorker("", None, ".", "default", attachments=["/x/a.txt"], **_callbacks())
    text = worker._prompt_with_attachments([("a.txt", "/gw/a/a.txt")])
    assert text.strip()
    assert "/gw/a/a.txt" in text
    assert not text.startswith("\n")


def test_the_turn_submits_the_rewritten_prompt_not_the_original(tmp_path):
    """The upload is worthless if prompt.submit still carries the bare text."""
    sample = tmp_path / "dane.csv"
    sample.write_bytes(b"a,b\n")
    worker = HermesWorker(
        "What is in it?", None, ".", "default", attachments=[str(sample)], **_callbacks()
    )
    worker._transport = _FakeTransport(
        [
            _attach_reply(101, "/gw/attachments/dane.csv"),
            {"jsonrpc": "2.0", "id": 102, "result": {"status": "streaming"}},
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.complete",
                    "session_id": "s",
                    "payload": {"status": "complete", "text": "done"},
                },
            },
        ]
    )
    worker._request_id = 100
    worker._live_session = "live-1"

    worker._run_turn()
    submits = [f for f in worker._transport.sent if f["method"] == "prompt.submit"]
    assert len(submits) == 1
    sent_text = submits[0]["params"]["text"]
    assert "/gw/attachments/dane.csv" in sent_text
    assert "What is in it?" in sent_text


def test_the_upload_happens_before_the_prompt(tmp_path):
    """Order is the contract: Hermes stages the file for the NEXT submit."""
    sample = tmp_path / "x.txt"
    sample.write_bytes(b"x")
    worker = HermesWorker("q", None, ".", "default", attachments=[str(sample)], **_callbacks())
    worker._transport = _FakeTransport(
        [
            _attach_reply(101, "/gw/a/x.txt"),
            {"jsonrpc": "2.0", "id": 102, "result": {"status": "streaming"}},
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.complete",
                    "session_id": "s",
                    "payload": {"status": "complete", "text": "ok"},
                },
            },
        ]
    )
    worker._request_id = 100
    worker._live_session = "live-1"

    worker._run_turn()
    methods = [f["method"] for f in worker._transport.sent]
    assert methods.index("file.attach") < methods.index("prompt.submit")


def test_a_turn_without_attachments_sends_exactly_what_it_used_to(tmp_path):
    """The old path must not gain a file.attach frame or a rewritten prompt."""
    worker = HermesWorker("plain question", None, ".", "default", **_callbacks())
    worker._transport = _FakeTransport(
        [
            {"jsonrpc": "2.0", "id": 101, "result": {"status": "streaming"}},
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "message.complete",
                    "session_id": "s",
                    "payload": {"status": "complete", "text": "ok"},
                },
            },
        ]
    )
    worker._request_id = 100
    worker._live_session = "live-1"

    worker._run_turn()
    assert [f["method"] for f in worker._transport.sent] == ["prompt.submit"]
    assert worker._transport.sent[0]["params"]["text"] == "plain question"


# -- failures a listener can act on ---------------------------------------


def test_a_missing_file_is_refused_by_name_before_the_turn_starts(tmp_path):
    failed: list[str] = []
    callbacks = _callbacks()
    callbacks["on_failed"] = failed.append
    missing = tmp_path / "nie_ma.txt"
    worker = HermesWorker("q", None, ".", "default", attachments=[str(missing)], **callbacks)
    worker._transport = _FakeTransport()
    worker._request_id = 100
    worker._live_session = "live-1"

    assert worker._upload_attachments() is None
    assert len(failed) == 1
    # The name has to be in the message: a screen reader user cannot see which
    # of several attachments failed.
    assert "nie_ma.txt" in failed[0]
    # And nothing may be sent, so the model never answers about a file it lacks.
    assert worker._transport.sent == []


def test_the_turn_does_not_run_when_an_attachment_cannot_be_sent(tmp_path):
    """The whole turn is abandoned, rather than answering without the file."""
    failed: list[str] = []
    callbacks = _callbacks()
    callbacks["on_failed"] = failed.append
    worker = HermesWorker(
        "q", None, ".", "default", attachments=[str(tmp_path / "absent.txt")], **callbacks
    )
    worker._transport = _FakeTransport()
    worker._request_id = 100
    worker._live_session = "live-1"

    worker._run_turn()
    assert [f["method"] for f in worker._transport.sent] == []
    assert failed


def test_an_empty_file_is_refused_rather_than_silently_useless(tmp_path):
    empty = tmp_path / "pusty.txt"
    empty.write_bytes(b"")
    with pytest.raises(AttachmentError) as excinfo:
        check_attachment(str(empty))
    assert "pusty.txt" in str(excinfo.value)


def test_an_oversized_file_is_refused_with_its_size_and_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("hermes_worker.ATTACHMENT_MAX_BYTES", 1024)
    big = tmp_path / "duzy.bin"
    big.write_bytes(b"0" * 4096)
    with pytest.raises(AttachmentError) as excinfo:
        check_attachment(str(big))
    message = str(excinfo.value)
    assert "duzy.bin" in message
    # Both numbers, so the user knows how much smaller it has to be.
    assert "limit" in message.lower()


def test_a_file_within_the_limit_reports_its_size(tmp_path):
    sample = tmp_path / "ok.txt"
    sample.write_bytes(b"12345")
    assert check_attachment(str(sample)) == 5
    assert ATTACHMENT_MAX_BYTES > 0


def test_a_gateway_refusal_is_reported_with_hermes_own_words(tmp_path):
    failed: list[str] = []
    callbacks = _callbacks()
    callbacks["on_failed"] = failed.append
    sample = tmp_path / "a.txt"
    sample.write_bytes(b"x")
    worker = HermesWorker("q", None, ".", "default", attachments=[str(sample)], **callbacks)
    worker._transport = _FakeTransport(
        [{"jsonrpc": "2.0", "id": 101, "error": {"code": 5028, "message": "disk is full"}}]
    )
    worker._request_id = 100
    worker._live_session = "live-1"

    assert worker._upload_attachments() is None
    assert failed == ["disk is full"]


def test_an_accepted_upload_with_no_path_is_treated_as_a_failure(tmp_path):
    """Without a path the prompt has nothing to point at, so the turn is lost."""
    failed: list[str] = []
    callbacks = _callbacks()
    callbacks["on_failed"] = failed.append
    sample = tmp_path / "a.txt"
    sample.write_bytes(b"x")
    worker = HermesWorker("q", None, ".", "default", attachments=[str(sample)], **callbacks)
    worker._transport = _FakeTransport(
        [{"jsonrpc": "2.0", "id": 101, "result": {"attached": True}}]
    )
    worker._request_id = 100
    worker._live_session = "live-1"

    assert worker._upload_attachments() is None
    assert failed and "a.txt" in failed[0]


def test_the_user_hears_that_the_file_is_on_its_way(tmp_path):
    """A 20 MB upload is a silence otherwise, which reads as a hang."""
    rows: list[tuple[str, str]] = []
    callbacks = _callbacks()
    callbacks["on_activity"] = lambda kind, text: rows.append((kind, text))
    sample = tmp_path / "raport.xlsx"
    sample.write_bytes(b"0" * 2048)
    worker = HermesWorker("q", None, ".", "default", attachments=[str(sample)], **callbacks)
    worker._transport = _FakeTransport([_attach_reply(101, "/gw/a/raport.xlsx")])
    worker._request_id = 100
    worker._live_session = "live-1"

    worker._upload_attachments()
    spoken = " ".join(text for _kind, text in rows)
    assert "raport.xlsx" in spoken
    # The size, so a long wait is explained rather than merely announced.
    assert "KB" in spoken or "MB" in spoken


# -- which backends upload ------------------------------------------------


def test_only_hermes_declares_that_it_takes_an_upload():
    assert BACKENDS[BACKEND_HERMES].uploads_attachments is True
    # The CLI backends run on this machine; a path is all they need, and
    # claiming otherwise would stop them being told where the file is.
    for backend in (BACKEND_CLAUDE, BACKEND_CODEX, BACKEND_FREEBUFF):
        assert BACKENDS[backend].uploads_attachments is False


# -- the window's side of it ----------------------------------------------
#
# The window decides whether a turn's files are described in the prompt or
# handed to the worker. Driven through the unbound methods with a stub panel,
# the way the other window tests here do it, so no toolkit is needed.


def _panel(backend: str, attachments: list[str]):
    """A stand-in for the tab, carrying only what these methods touch."""
    import blindpilot_app

    return type(
        "PanelStub",
        (),
        {
            "_attachments": attachments,
            "selected_backend": lambda self: backend,
            "_backend_uploads_attachments": (
                lambda self: blindpilot_app.SessionPanel._backend_uploads_attachments(self)
            ),
        },
    )()


def test_a_cli_backend_is_still_told_where_the_files_are():
    """Claude and Codex read the disk this app runs on. Nothing changes."""
    import blindpilot_app

    panel = _panel(BACKEND_CLAUDE, [r"D:\projekty\raport.xlsx"])
    text = blindpilot_app.SessionPanel._build_send_text(panel, "look")

    assert r"D:\projekty\raport.xlsx" in text
    assert "Attached files" in text


def test_an_uploading_backend_is_not_given_the_client_path_in_the_prompt():
    """The path is meaningless (or wrong) on the far side, so it stays out.

    The worker sends the bytes and puts the gateway's own path in the prompt;
    naming this machine's path as well would invite the model to read a file
    that either does not exist there or is a different file of the same name.
    """
    import blindpilot_app

    panel = _panel(BACKEND_HERMES, [r"D:\projekty\raport.xlsx"])
    text = blindpilot_app.SessionPanel._build_send_text(panel, "look")

    assert text == "look"
    assert "raport.xlsx" not in text


def test_the_transcript_still_names_the_files_that_went_out():
    """Otherwise the user's own row reads as a question about nothing."""
    import blindpilot_app

    panel = _panel(BACKEND_HERMES, [r"D:\projekty\raport.xlsx", "/tmp/dane.csv"])
    summary = blindpilot_app.SessionPanel._attachment_summary(panel)

    assert "raport.xlsx" in summary
    assert "dane.csv" in summary
    # Named by file, not by path: the row is read aloud.
    assert "D:\\projekty" not in summary


def test_no_attachments_means_no_summary_line():
    import blindpilot_app

    panel = _panel(BACKEND_HERMES, [])
    assert blindpilot_app.SessionPanel._attachment_summary(panel) == ""


def test_the_turn_is_handed_the_files_so_their_bytes_can_be_sent():
    """The bug being fixed, one layer up: without this nothing leaves the disk.

    A green test run missed a mutation that dropped this hand-off, because the
    decision lived inline in the send path where no test could reach it. It is
    a method of its own now, and this is the test that mutation has to fail.
    """
    import blindpilot_app

    panel = type("PanelStub", (), {"_held_hermes": None})()
    extra = blindpilot_app.SessionPanel._hermes_worker_extra(panel, ["/tmp/a.txt", "/tmp/b.txt"])

    assert extra["attachments"] == ["/tmp/a.txt", "/tmp/b.txt"]
    # The held connection still has to be set up: attachments must not be
    # bolted on at the cost of the turn reusing its connection.
    assert extra["held"] is panel._held_hermes is not None


def test_a_turn_with_no_files_is_given_no_attachments_argument():
    """An older worker without the argument must keep working unchanged."""
    import blindpilot_app

    panel = type("PanelStub", (), {"_held_hermes": None})()
    extra = blindpilot_app.SessionPanel._hermes_worker_extra(panel, [])

    assert "attachments" not in extra
    assert "held" in extra
