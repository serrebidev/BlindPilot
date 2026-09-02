"""Reading the prompt back, and when not to.

Dictation puts text in the prompt without any keystrokes, so the screen reader
says nothing and the person dictating has no idea what landed. Hence a pause
timer: when the prompt stops changing, read it out.

But `EVT_TEXT` fires for typing too. Stop to think for a second and a half
mid-sentence — which is what composing a prompt is mostly made of — and the
whole prompt is read back at you, over the top of the character and word echo
the screen reader has already been giving you the entire time. The longer the
prompt, the worse it gets, and the only way to avoid it was to keep typing.

A keystroke adds one character and has already been spoken. Dictation and
paste arrive in bulk and have not been spoken by anything.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app


class _Prompt:
    def __init__(self, text=""):
        self.text = text

    def GetValue(self):
        return self.text


class _Timer:
    def __init__(self, ms, fn):
        self.ms = ms
        self.fn = fn
        self.stopped = False

    def Stop(self):
        self.stopped = True


class _Event:
    def Skip(self):
        pass


@pytest.fixture
def panel(monkeypatch):
    said: list[str] = []
    timers: list[_Timer] = []
    monkeypatch.setattr(app, "announce", lambda text, urgent=False: said.append(text))
    monkeypatch.setattr(
        app.wx, "CallLater", lambda ms, fn: timers.append(_Timer(ms, fn)) or timers[-1]
    )

    stub = type("PanelStub", (), {})()
    stub.prompt = _Prompt()
    stub._dictation_timer = None
    stub._prompt_text = ""
    stub._dictation_pending = ""
    stub._read_prompt_text = lambda: app.SessionPanel._read_prompt_text(stub)
    stub.said = said
    stub.timers = timers
    return stub


def _change(panel, text):
    """The prompt now holds `text`, however it got there."""
    panel.prompt.text = text
    app.SessionPanel._on_prompt_text_changed(panel, _Event())


def _pause(panel):
    """The pause timer, if one is pending, comes due."""
    live = [t for t in panel.timers if not t.stopped]
    if live:
        live[-1].fn()


def _type(panel, text):
    """Type it, one character per event, as a keyboard does."""
    for i in range(1, len(text) + 1):
        _change(panel, text[:i])


# ----- typing -----
def test_typing_and_pausing_to_think_says_nothing(panel):
    """The bug: a second and a half of thought read the whole prompt back."""
    _type(panel, "look at the worker")
    _pause(panel)

    assert panel.said == [], f"the prompt was read back mid-composition: {panel.said}"


def test_a_keystroke_does_not_even_schedule_a_read_back(panel):
    _type(panel, "hello")

    assert [t for t in panel.timers if not t.stopped] == []


def test_deleting_says_nothing(panel):
    _type(panel, "abc")
    _change(panel, "ab")
    _change(panel, "a")
    _pause(panel)

    assert panel.said == []


# ----- dictation -----
def test_dictated_text_is_read_back(panel):
    """The feature this exists for: nothing else will have spoken it."""
    _change(panel, "open the config file")
    _pause(panel)

    assert panel.said == ["open the config file"]


def test_a_burst_of_dictation_is_read_once_when_it_settles(panel):
    _change(panel, "open the config file")
    _change(panel, "open the config file and find the timeout")
    _pause(panel)

    assert panel.said == ["open the config file and find the timeout"]


def test_only_the_new_words_are_read_not_the_whole_prompt_again(panel):
    """Dictating a second sentence onto a long prompt should not replay the
    first one. That is the same wall of speech the pause timer caused."""
    _change(panel, "here is a long first sentence that was already read out")
    _pause(panel)
    panel.said.clear()

    _change(panel, "here is a long first sentence that was already read out and a second one")
    _pause(panel)

    assert panel.said == ["and a second one"]


def test_typing_after_dictating_cancels_the_pending_read_back(panel):
    """Carrying on by hand means the screen reader is echoing again."""
    _change(panel, "dictated words")
    _type(panel, "dictated words typed")
    _pause(panel)

    assert panel.said == []


def test_an_empty_prompt_is_not_read_back(panel):
    _change(panel, "something")
    _change(panel, "")
    _pause(panel)

    assert panel.said == []
