"""Live release: a listener hears an answer while it is still being written.

A terminal renders a coding CLI's tokens as they arrive. BlindPilot used
to hold two of its backends back: Codex deltas were buffered until the
whole message item completed, and Claude was never asked for partial
messages at all, so its first words waited for the whole assistant
message. The streaming backends that did release live re-joined the
whole answer on every delta, so a long answer cost the square of its
length. These tests pin the release timing, not just the final text.
"""

from __future__ import annotations

from markdown_rows import SentenceStreamer, release_finished, release_remainder
from test_claude_stream_resilience import _FakeProc, _drive, _line


def test_a_finished_sentence_is_released_before_the_stream_ends():
    said: list[str] = []
    streamer = SentenceStreamer(said.append)
    streamer.feed("First sentence. Sec")
    assert said == ["First sentence."]
    streamer.feed("ond sentence, still going")
    # Nothing new is finished, so nothing new is said: no torn words.
    assert said == ["First sentence."]
    streamer.flush()
    assert said == ["First sentence.", " Second sentence, still going"]


def test_the_streamer_releases_what_the_whole_text_rule_releases():
    """The same words, in the same order, as the whole-text rule releases.

    Chunk boundaries differ on purpose: the whole-text rule called once
    says every finished sentence in one breath, while the streamer says
    each sentence in the delta that finished it — earlier, which is the
    point. What must not differ is the text a listener ends up hearing.
    """
    deltas = ["One. Tw", "o. Three", " is unfin", "ished. Four."]
    whole = "".join(deltas)
    expected: list[str] = []
    streamed = release_finished(whole, 0, expected.append)
    release_remainder(whole, streamed, expected.append)

    said: list[str] = []
    streamer = SentenceStreamer(said.append)
    for delta in deltas:
        streamer.feed(delta)
    streamer.flush()
    assert "".join(said) == "".join(expected)
    assert said[0] == "One."


def test_codex_deltas_are_said_before_the_item_completes():
    import agent_backends

    events: list[tuple[str, str]] = []
    worker = agent_backends.CodexWorker(
        "test",
        None,
        ".",
        "default",
        on_session=lambda _sid: None,
        on_started=lambda: None,
        on_activity=lambda kind, text: events.append((kind, text)),
        on_complete=lambda _text: None,
        on_failed=lambda _msg: None,
        on_done=lambda: None,
    )
    worker._agent_delta({"itemId": "m1", "delta": "First sentence. Sec"})
    # The item has not completed, and the first sentence is already out.
    assert events == [("assistant", "First sentence.")]
    worker._agent_delta({"itemId": "m1", "delta": "ond sentence."})
    worker._item_completed(
        {"id": "m1", "type": "agentMessage", "text": "First sentence. Second sentence."}
    )
    # Completion says only the tail, never the whole message a second time.
    assert events == [
        ("assistant", "First sentence."),
        ("assistant", " Second sentence."),
    ]
    assert "".join(worker._assistant_parts) == "First sentence. Second sentence."


def test_claude_partial_messages_are_said_before_the_whole_message():
    # The shape the CLI really sends: a thinking block first, so the text
    # streams at block index 1, yet its assistant event carries only that
    # one block. The two must still be recognised as the same text.
    def stdout():
        yield _line(
            {
                "type": "assistant",
                "message": {"content": [{"type": "thinking", "thinking": "Plan."}]},
            }
        )
        yield _line(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "text_delta", "text": "First sentence. Sec"},
                },
            }
        )
        yield _line(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "text_delta", "text": "ond sentence."},
                },
            }
        )
        yield _line(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "First sentence. Second sentence."}]
                },
            }
        )
        yield _line({"type": "result"})

    proc = _FakeProc(stdout())
    activity, completed, failures, _raised, finished = _drive(proc)

    assert finished and not failures
    # Both sentences arrived live, in order, and the whole message that
    # followed them did not say either of them again.
    assert activity == [
        ("thinking", "Plan."),
        ("assistant", "First sentence."),
        ("assistant", " Second sentence."),
    ]
    assert completed == ["First sentence. Second sentence."]
