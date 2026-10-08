"""Ctrl+Shift+C: the response's last code block, straight to the clipboard.

The code an answer ends on is usually the one wanted, and reaching it meant
arrowing past everything written after it. The Chat Place copies it in one
key; so does BlindPilot now.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app
from markdown_rows import Row


@pytest.fixture
def panel(monkeypatch):
    copied: list[str] = []
    monkeypatch.setattr(app, "_copy_to_clipboard", lambda text: copied.append(text) or True)
    stub = type("PanelStub", (), {})()
    stub._rows = [
        Row(kind="code", label="a", payload="first", response_number=1, language="python"),
        Row(kind="prose", label="then", payload="then", response_number=1),
        Row(kind="code", label="b", payload="second", response_number=1, language="python"),
        Row(kind="code", label="c", payload="other", response_number=2),
    ]
    stub.copied = copied
    stub.spoken: list[str] = []
    stub._announce = lambda text, urgent=False: stub.spoken.append(text)
    stub._copy_message = app.SessionPanel._copy_message
    return stub


def test_the_last_code_block_of_that_response_is_copied(panel):
    app.SessionPanel.copy_last_code(panel, 1)

    assert panel.copied == ["second"]
    assert panel.spoken == ["Copied 1 line of python"]


def test_a_response_without_code_says_so(panel):
    app.SessionPanel.copy_last_code(panel, 3)

    assert panel.copied == [] and panel.spoken == ["Response 3 has no code block"]
