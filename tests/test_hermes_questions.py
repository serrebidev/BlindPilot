"""Questions Hermes stops its turn to ask, and how BlindPilot answers them.

Measured against a live gateway: Hermes' ``clarify`` tool blocks the agent
until a ``clarify.respond`` arrives, and with ``clarify_timeout`` at zero it
blocks with no deadline at all. The worker used to announce the event and
send nothing back, so the first question a turn asked ended it -- the window
read out "Hermes is asking: a question needing the terminal" and then went
quiet for good. That wording was itself the tell: it is the fallback text,
reached because a batch clarify carries ``questions`` and the worker only
ever looked for ``question``.
"""

from __future__ import annotations

import json

import hermes_worker
from hermes_worker import HermesWorker
from transport_contract import check_transport_contract


class _RecordingTransport:
    """Records what the worker sent, and models a peer that stays quiet.

    The full Transport surface, not just the one method these tests use: a
    fake with a shape no real transport has freezes the bug instead of
    catching it, which is what ``transport_contract`` exists to prevent. This
    one is a Hermes that is connected and simply not talking -- the state a
    turn is in while it waits on the answer to a question.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    def send(self, message: dict) -> bool:
        if not self.connected():
            return False
        self.sent.append(message)
        return True

    def receive(self, timeout: float) -> dict | None:  # noqa: ARG002 - interface
        return None

    def close(self) -> None:
        self.closed = True

    def connected(self) -> bool:
        return not self.closed

    def failure_detail(self) -> str:
        return "recording transport"


def test_the_fake_used_here_behaves_like_a_real_transport():
    check_transport_contract(_RecordingTransport, "recording transport", stream_ends=False)


def _worker(activities, sent, questions_asked, answers):
    def ask(questions):
        questions_asked.append(list(questions))
        return answers

    worker = HermesWorker(
        "hi",
        None,
        "C:/Users/admin",
        "default",
        on_session=lambda _sid: None,
        on_started=lambda: None,
        on_activity=lambda kind, text: activities.append((kind, text)),
        on_complete=lambda _txt: None,
        on_failed=lambda _msg: None,
        on_done=lambda: None,
        on_question=ask,
    )
    transport = _RecordingTransport()
    transport.sent = sent
    worker._transport = transport
    return worker


def _frame(event: str, payload: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {"type": event, "session_id": "s", "payload": payload},
    }


def _responses(sent, method="clarify.respond"):
    return [m["params"] for m in sent if m.get("method") == method]


# -- the single-question shape ------------------------------------------


def test_single_clarify_is_asked_and_answered():
    activities, sent, asked = [], [], []
    worker = _worker(activities, sent, asked, [["Chromecast"]])
    worker._handle_event(
        _frame(
            "clarify.request",
            {
                "request_id": "abc123",
                "question": "Which device should this target first?",
                "choices": ["Chromecast", "AirPlay"],
            },
        )
    )

    assert len(asked) == 1 and len(asked[0]) == 1
    question = asked[0][0]
    assert question.question == "Which device should this target first?"
    # The choices Hermes offered have to reach the person deciding.
    assert [option.label for option in question.options] == ["Chromecast", "AirPlay"]

    assert _responses(sent) == [{"request_id": "abc123", "answer": "Chromecast"}]


def test_single_clarify_carries_no_question_id():
    """The single shape is keyed by request id alone; a stray question_id is
    answered with "unknown question_id" and the turn stays blocked."""
    sent = []
    worker = _worker([], sent, [], [["yes"]])
    worker._handle_event(_frame("clarify.request", {"request_id": "r1", "question": "Go ahead?"}))
    assert "question_id" not in _responses(sent)[0]


# -- the batch shape, which is what actually broke -----------------------


def test_batch_clarify_answers_every_question_by_its_own_id():
    activities, sent, asked = [], [], []
    worker = _worker(activities, sent, asked, [["AirPlay"], ["Yes"]])
    worker._handle_event(
        _frame(
            "clarify.request",
            {
                "request_id": "batch1",
                "questions": [
                    {
                        "qid": "q1",
                        "question": "Which device?",
                        "choices": ["Chromecast", "AirPlay"],
                    },
                    {"qid": "q2", "question": "Keep the audio lossless?", "choices": ["Yes", "No"]},
                ],
            },
        )
    )

    assert [q.question for q in asked[0]] == ["Which device?", "Keep the audio lossless?"]
    assert _responses(sent) == [
        {"request_id": "batch1", "question_id": "q1", "answer": "AirPlay"},
        {"request_id": "batch1", "question_id": "q2", "answer": "Yes"},
    ]


def test_batch_clarify_answers_every_question_even_when_the_user_declines():
    """Hermes releases a batch only once EVERY id is locked.

    Answering some and not others leaves the turn hanging exactly as it did
    before, so a closed dialog still sends one empty answer per question.
    """
    sent = []
    worker = _worker([], sent, [], None)
    worker._handle_event(
        _frame(
            "clarify.request",
            {
                "request_id": "batch2",
                "questions": [
                    {"qid": "a", "question": "First?"},
                    {"qid": "b", "question": "Second?"},
                ],
            },
        )
    )
    assert _responses(sent) == [
        {"request_id": "batch2", "question_id": "a", "answer": ""},
        {"request_id": "batch2", "question_id": "b", "answer": ""},
    ]


def test_multi_select_goes_as_a_json_array():
    """A comma-separated string is also accepted by the tool, but not by an
    answer that itself contains a comma -- which the free-text row allows for
    any question, whatever its own choices look like."""
    sent = []
    worker = _worker([], sent, [], [["Chromecast", "AirPlay, second zone"]])
    worker._handle_event(
        _frame(
            "clarify.request",
            {
                "request_id": "m1",
                "question": "Which devices?",
                "choices": ["Chromecast", "AirPlay, second zone"],
                "multi_select": True,
            },
        )
    )
    answer = _responses(sent)[0]["answer"]
    assert json.loads(answer) == ["Chromecast", "AirPlay, second zone"]


def test_multi_select_without_choices_is_not_offered_as_multi_select():
    questions = hermes_worker._clarify_questions(
        {"request_id": "x", "question": "Anything else?", "multi_select": True}
    )
    assert questions[0].multi_select is False


# -- what the transcript says -------------------------------------------


def test_the_question_and_the_answer_both_become_a_row():
    """Read back later, a bare question says nothing about why the rest of the
    turn went the way it did."""
    activities, sent = [], []
    worker = _worker(activities, sent, [], [["AirPlay"]])
    worker._handle_event(
        _frame(
            "clarify.request",
            {"request_id": "r", "question": "Which device?", "choices": ["Chromecast", "AirPlay"]},
        )
    )
    row = " ".join(text for _kind, text in activities)
    assert "Which device?" in row
    assert "AirPlay" in row


# -- passwords and secrets ----------------------------------------------


def test_sudo_request_is_answered_with_the_password_key():
    sent = []
    worker = _worker([], sent, [], [["hunter2"]])
    worker._handle_event(
        _frame("sudo.request", {"request_id": "s1", "prompt": "Password for admin:"})
    )
    assert _responses(sent, "sudo.respond") == [{"request_id": "s1", "password": "hunter2"}]


def test_secret_request_uses_the_value_key():
    sent = []
    worker = _worker([], sent, [], [["sk-live-xyz"]])
    worker._handle_event(_frame("secret.request", {"request_id": "s2", "question": "API key?"}))
    assert _responses(sent, "secret.respond") == [{"request_id": "s2", "value": "sk-live-xyz"}]


def test_a_secret_is_never_echoed_into_the_transcript():
    """These rows are read aloud, copied, and saved."""
    activities, sent = [], []
    worker = _worker(activities, sent, [], [["hunter2"]])
    worker._handle_event(
        _frame("sudo.request", {"request_id": "s3", "prompt": "Password for admin:"})
    )
    assert "hunter2" not in " ".join(text for _kind, text in activities)


def test_a_declined_password_still_releases_the_turn():
    sent = []
    worker = _worker([], sent, [], None)
    worker._handle_event(_frame("sudo.request", {"request_id": "s4", "prompt": "Password:"}))
    assert _responses(sent, "sudo.respond") == [{"request_id": "s4", "password": ""}]


# -- a window that cannot ask -------------------------------------------


def test_no_question_callback_still_releases_the_turn():
    """A worker built without ``on_question`` must not leave Hermes waiting."""
    sent = []
    worker = HermesWorker(
        "hi",
        None,
        "C:/Users/admin",
        "default",
        on_session=lambda _sid: None,
        on_started=lambda: None,
        on_activity=lambda _k, _t: None,
        on_complete=lambda _t: None,
        on_failed=lambda _m: None,
        on_done=lambda: None,
    )

    transport = _RecordingTransport()
    transport.sent = sent
    worker._transport = transport
    worker._handle_event(_frame("clarify.request", {"request_id": "n1", "question": "Which?"}))
    assert _responses(sent) == [{"request_id": "n1", "answer": ""}]


def test_a_clarify_without_a_request_id_is_left_alone():
    """Nothing to answer, and a reply with an empty id is refused as 4009."""
    sent = []
    worker = _worker([], sent, [], [["x"]])
    worker._handle_event(_frame("clarify.request", {"question": "Which?"}))
    assert _responses(sent) == []


def test_a_clarify_with_no_readable_question_is_still_answered():
    """Hermes blocks on the answer with no deadline when clarify_timeout is 0.

    A request whose text cannot be read is answered empty and said so, rather
    than left to hang the turn.
    """
    activities: list = []
    sent: list = []
    worker = _worker(activities, sent, [], None)
    worker._handle_event(_frame("clarify.request", {"request_id": "r9"}))

    assert _responses(sent) == [{"request_id": "r9", "answer": ""}]
    assert activities
