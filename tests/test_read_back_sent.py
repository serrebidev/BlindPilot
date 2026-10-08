"""Hearing what was actually sent.

Dictation and paste put text in the prompt that nobody heard, and Send only
said "Sending". The message is now read back as it goes, as The Chat Place
does: cut short when long, code blocks left out.
"""

from __future__ import annotations

import blindpilot_app as app


def test_a_short_message_is_read_whole():
    assert app._sent_readback("fix the build") == "Sent: fix the build"


def test_a_code_block_is_left_out():
    said = app._sent_readback("look\n```py\nx = 1\n```\nthen run it")

    assert "x = 1" not in said and "Code block omitted." in said


def test_a_long_message_says_how_much_was_left():
    said = app._sent_readback("word " * 200)

    assert said.endswith("... and 140 more words")
