"""Linux system speech: Speech Dispatcher voices, beside Orca.

BlindPilot speaks on Linux through Speech Dispatcher directly, with a
voice, rate and volume of its own. The live list is only read when a
server answers -- CI has no Speech Dispatcher, so that test skips --
while the label, filtering and mapping logic is unit-tested here with
a fake client that stands in for the server.
"""

from __future__ import annotations

import threading

import pytest

import system_speech
from system_speech import (
    SpeechdOutput,
    SystemSpeech,
    collect_speechd_voices,
    parse_speechd_voice_label,
    primary_language,
    speechd_rate,
    speechd_voice_label,
    speechd_volume,
    user_language,
)


class FakeSpeechdClient:
    """One Speech Dispatcher client, with two modules' voices in memory."""

    def __init__(self):
        self.module = "espeak-ng"
        self.modules = ["espeak-ng", "rhvoice"]
        self.voices = {
            "espeak-ng": [
                ("English (America)", "en-US", "none"),
                ("English (America)+Max", "en-US", "Max"),
                ("Deutsch+Max", "de-DE", "Max"),
                ("Deutsch", "de-DE", "none"),
            ],
            "rhvoice": [("Alan", "en-GB", "none"), ("Aleksandr", "ru-RU", "none")],
        }
        self.priority = None
        self.spoken = []
        self.cancelled = 0
        self.rate = None
        self.volume = None
        self.synthesis_voice = None

    def get_output_module(self):
        return self.module

    def set_output_module(self, name):
        assert name in self.modules
        self.module = name

    def list_output_modules(self):
        return tuple(self.modules)

    def list_synthesis_voices(self):
        return tuple(self.voices[self.module])

    def set_synthesis_voice(self, name):
        self.synthesis_voice = name

    def set_priority(self, priority):
        self.priority = priority

    def speak(self, text):
        self.spoken.append(text)

    def cancel(self):
        self.cancelled += 1

    def set_rate(self, value):
        self.rate = value

    def set_volume(self, value):
        self.volume = value


def test_voice_labels_name_the_module_and_language():
    """Names repeat across modules, so a label is ``Name (module,
    language)`` -- and it parses back into the name and the module."""
    label = speechd_voice_label("Alan", "rhvoice", "en-GB")
    assert label == "Alan (rhvoice, en-GB)"
    assert parse_speechd_voice_label(label) == ("Alan", "rhvoice")
    assert parse_speechd_voice_label("Alan") is None
    assert speechd_voice_label("Alan", "rhvoice", "") == "Alan (rhvoice)"


def test_collect_voices_puts_the_users_language_first_and_filters_variants():
    """Every module's voices, the user's language first: espeak-ng's
    variant combinations are kept only for that language, or the list
    would run to thousands, while plain voices are kept for all."""
    client = FakeSpeechdClient()
    labels = collect_speechd_voices(client, language="en")
    assert labels == [
        "Alan (rhvoice, en-GB)",
        "English (America) (espeak-ng, en-US)",
        "English (America)+Max (espeak-ng, en-US)",
        "Aleksandr (rhvoice, ru-RU)",
        "Deutsch (espeak-ng, de-DE)",
    ]
    assert "Deutsch+Max (espeak-ng, de-DE)" not in labels
    assert client.module == "espeak-ng"  # The starting module is restored.


def test_user_language_comes_from_the_locale_environment():
    """The language that sorts first is read from the locale variables,
    in order, reduced to its primary code."""
    assert user_language({"LANG": "en_US.UTF-8"}) == "en"
    assert user_language({"LC_MESSAGES": "fr-FR", "LANG": "en_US"}) == "fr"
    assert user_language({"LANG": "C"}) == "en"
    assert primary_language("de_DE@euro") == "de"


def test_rate_and_volume_map_onto_speech_dispatchers_range():
    """BlindPilot's rate is -10..10 with 0 normal and its volume is
    0..100; Speech Dispatcher takes -100..100 for both, 0 normal rate
    and full volume at the top. Out-of-range values clamp."""
    assert speechd_rate(-10) == -100
    assert speechd_rate(0) == 0
    assert speechd_rate(10) == 100
    assert speechd_rate(99) == 100
    assert speechd_volume(0) == -100
    assert speechd_volume(50) == 0
    assert speechd_volume(100) == 100
    assert speechd_volume(999) == 100


def test_the_output_adapter_applies_voice_rate_and_volume():
    """Choosing a labelled voice checks it against its module, sets the
    module and the voice, and rate and volume arrive mapped."""
    client = FakeSpeechdClient()
    output = SpeechdOutput(client)
    assert "Alan (rhvoice, en-GB)" in output.list_voices()
    output.set_voice("Alan (rhvoice, en-GB)")
    assert client.module == "rhvoice"
    assert client.synthesis_voice == "Alan"
    output.set_rate(5)
    output.set_volume(75)
    assert client.rate == 50
    assert client.volume == 50
    with pytest.raises(ValueError):
        output.set_voice("Nobody (rhvoice, en-GB)")


def test_speak_uses_message_priority_and_cancel_is_ours_only():
    """Speech goes at message priority, so sentences queue and Orca does
    not cut them off; cancelling cancels this client, and no other."""
    client = FakeSpeechdClient()
    output = SpeechdOutput(client)
    output.speak("A sentence.")
    assert client.priority == "message"
    assert client.spoken == ["A sentence."]
    output.silence()
    assert client.cancelled == 1


def test_system_speech_on_linux_goes_through_speech_dispatcher(monkeypatch):
    """With the platform read as Linux, SystemSpeech's output is the
    Speech Dispatcher adapter, not SAPI."""
    monkeypatch.setattr(system_speech.sys, "platform", "linux")
    client = FakeSpeechdClient()
    monkeypatch.setattr(system_speech, "SpeechdOutput", lambda: SpeechdOutput(client))
    speech = SystemSpeech()
    assert "Alan (rhvoice, en-GB)" in speech.voices()
    speech.speak("Hello.", threading.Event())
    assert client.spoken == ["Hello."]


def test_live_voices_skip_when_no_server_is_reachable():
    """The real list, when Speech Dispatcher answers; skipped, not
    failed, where no server can be reached (as in CI)."""
    try:
        import speechd
    except ImportError:
        pytest.skip("python3-speechd is not installed")
    try:
        client = speechd.SSIPClient("BlindPilot-test")
    except Exception:
        pytest.skip("no Speech Dispatcher server is reachable")
    try:
        labels = collect_speechd_voices(client)
    finally:
        client.close()
    assert isinstance(labels, list)
