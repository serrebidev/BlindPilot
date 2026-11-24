"""Independent system speech: a voice separate from the screen reader.

``SAPI5`` speaks through module-level SAPI directly on Windows, and Speech
Dispatcher directly on Linux, so nothing here changes NVDA/JAWS/Orca
navigation speech. Settings persist per user (voice, rate, volume,
enabled, speak-responses); a per-conversation override (inherit/on/off)
lives in the preferences config keyed by session id.
"""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from typing import Any

DEFAULT_RATE = 0
DEFAULT_VOLUME = 100
OVERRIDE_INHERIT = "inherit"
OVERRIDE_ON = "on"
OVERRIDE_OFF = "off"


@dataclass
class SpeechSettings:
    enabled: bool = False
    voice: str = ""
    rate: int = DEFAULT_RATE
    volume: int = DEFAULT_VOLUME
    speak_responses: bool = True


# ----- Linux: Speech Dispatcher, beside Orca -----
#
# On Linux the counterpart of SAPI5 is Speech Dispatcher, spoken to
# directly through the ``speechd`` module (python3-speechd), so the voice,
# rate and volume here are BlindPilot's own and Orca's are untouched. The
# module is imported lazily and only on Linux: absent, it means no voices
# and no speech, never a crash.


def primary_language(tag: str) -> str:
    """``en`` from ``en_US.UTF-8`` or ``en-US``; ``""`` for the C locale."""
    primary = str(tag or "").split(":", 1)[0]
    for separator in ("-", "_", ".", "@"):
        primary = primary.split(separator, 1)[0]
    primary = primary.strip().lower()
    return "" if primary in ("c", "posix") else primary


def user_language(env: Any | None = None) -> str:
    """The user's language, from the locale environment, else English."""
    source = os.environ if env is None else env
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = source.get(name) if hasattr(source, "get") else None
        language = primary_language(str(value or ""))
        if language:
            return language
    return "en"


def speechd_voice_label(name: str, module: str, language: str) -> str:
    """``Name (module, language)`` -- the module is part of the label
    because the same voice name repeats across output modules."""
    return f"{name} ({module}, {language})" if language else f"{name} ({module})"


def parse_speechd_voice_label(label: str) -> tuple[str, str] | None:
    """(name, module) out of a label, or None when it is not one."""
    if not label or not label.endswith(")"):
        return None
    open_at = label.rfind(" (")
    if open_at <= 0:
        return None
    inside = label[open_at + 2 : -1]
    module = inside.split(", ", 1)[0]
    if not module:
        return None
    return label[:open_at], module


def speechd_rate(rate: int) -> int:
    """BlindPilot's rate (-10..10, 0 normal) on Speech Dispatcher's
    -100..100, where 0 is also normal."""
    return max(-100, min(100, int(rate) * 10))


def speechd_volume(volume: int) -> int:
    """Volume 0..100 on Speech Dispatcher's -100..100."""
    return max(-100, min(100, max(0, min(100, int(volume))) * 2 - 100))


def _voice_fields(entry: Any) -> tuple[str, str, str]:
    """(name, language, variant) from one synthesis-voice entry, which
    the real client gives as a triplet and a fake may give as a dict or
    an object with those attributes."""
    if isinstance(entry, dict):
        return (
            str(entry.get("name") or ""),
            str(entry.get("language") or ""),
            str(entry.get("variant") or ""),
        )
    if isinstance(entry, (tuple, list)):
        values = [str(value or "") for value in entry[:3]]
        return tuple(values + [""] * (3 - len(values)))  # type: ignore[return-value]
    return (
        str(getattr(entry, "name", "") or ""),
        str(getattr(entry, "language", "") or ""),
        str(getattr(entry, "variant", "") or ""),
    )


def collect_speechd_voices(client: Any, language: str | None = None) -> list[str]:
    """Every output module's voices as labels, the user's language first.

    espeak-ng lists each voice with each of its variants (some fifteen
    thousand combinations): a name carrying a ``+variant`` is kept only
    for the user's own language. Each group is sorted and de-duplicated,
    and the module the client started on is restored afterwards.
    """
    own = language or user_language()
    mine: list[str] = []
    others: list[str] = []
    saved_module = None
    try:
        saved_module = client.get_output_module()
    except Exception:
        saved_module = None
    try:
        modules = client.list_output_modules() or []
    except Exception:
        return []
    for module in modules:
        module_name = str(module or "")
        if not module_name or module_name == "dummy":
            continue
        try:
            client.set_output_module(module_name)
            entries = client.list_synthesis_voices() or []
        except Exception:
            continue
        for entry in entries:
            name, voice_language, variant = _voice_fields(entry)
            if not name:
                continue
            is_mine = primary_language(voice_language) == own
            is_combination = bool(variant) and variant != "none" and "+" in name
            if is_combination and not is_mine:
                continue
            label = speechd_voice_label(name, module_name, voice_language)
            (mine if is_mine else others).append(label)
    if saved_module:
        try:
            client.set_output_module(saved_module)
        except Exception:
            pass
    return sorted(set(mine)) + sorted(set(others))


class SpeechdOutput:
    """The SAPI-shaped adapter over one Speech Dispatcher connection.

    One client of BlindPilot's own: cancelling it (``silence``) cancels
    only this connection's speech, so Orca is never silenced with it.
    Sentences are spoken at message priority, so they queue instead of
    replacing each other and other clients' text does not cut them off.
    """

    def __init__(self, client: Any | None = None) -> None:
        if client is None:
            import speechd

            client = speechd.SSIPClient("BlindPilot")
        self._client = client
        self._known: dict[str, set[str]] = {}

    def list_voices(self) -> list[str]:
        # Remember each module's plain names while listing, so choosing a
        # voice later does not have to ask every module again.
        labels = collect_speechd_voices(self._client)
        for label in labels:
            parsed = parse_speechd_voice_label(label)
            if parsed is not None:
                name, module = parsed
                self._known.setdefault(module, set()).add(name)
        return labels

    def _module_voice_names(self, module: str) -> set[str]:
        if module in self._known:
            return self._known[module]
        names: set[str] = set()
        saved_module = None
        try:
            saved_module = self._client.get_output_module()
        except Exception:
            saved_module = None
        try:
            self._client.set_output_module(module)
            for entry in self._client.list_synthesis_voices() or []:
                name, _language, _variant = _voice_fields(entry)
                if name:
                    names.add(name)
        except Exception:
            names = set()
        if saved_module:
            try:
                self._client.set_output_module(saved_module)
            except Exception:
                pass
        self._known[module] = names
        return names

    def set_voice(self, label: str) -> None:
        """Choose a labelled voice, checked against its module's list:
        Speech Dispatcher accepts any name and silently falls back."""
        parsed = parse_speechd_voice_label(label)
        if parsed is None:
            raise ValueError(f"Not a Speech Dispatcher voice label: {label!r}")
        name, module = parsed
        if name not in self._module_voice_names(module):
            raise ValueError(f"Module {module!r} has no voice {name!r}")
        self._client.set_output_module(module)
        self._client.set_synthesis_voice(name)

    def set_rate(self, rate: int) -> None:
        self._client.set_rate(speechd_rate(rate))

    def set_volume(self, volume: int) -> None:
        self._client.set_volume(speechd_volume(volume))

    def speak(self, text: str) -> None:
        try:
            # Priority.MESSAGE is the string "message"; setting it before
            # speaking queues this client's sentences at that priority.
            self._client.set_priority("message")
        except Exception:
            pass
        self._client.speak(text)

    def silence(self) -> None:
        # Default scope is this client only: Orca keeps speaking.
        self._client.cancel()


def resolve_override(override: str, settings: SpeechSettings) -> bool:
    """Whether this conversation should speak, honor per-conversation choice."""
    if override == OVERRIDE_ON:
        return True
    if override == OVERRIDE_OFF:
        return False
    return settings.enabled and settings.speak_responses


class SystemSpeech:
    """One independent voice for BlindPilot's own announcements."""

    def __init__(self) -> None:
        self._output = None
        self._settings = SpeechSettings()
        self._lock = threading.Lock()

    def _ensure_output(self):
        if self._output is None:
            if sys.platform.startswith("linux"):
                self._output = SpeechdOutput()
                return self._output
            from accessible_output2.outputs.sapi5 import SAPI5

            self._output = SAPI5()
        return self._output

    def apply(self, settings: SpeechSettings) -> None:
        """Store settings; push voice/rate/volume when the voice is live."""
        with self._lock:
            self._settings = settings
            if settings.enabled and settings.voice:
                try:
                    output = self._ensure_output()
                    output.set_voice(settings.voice)
                    output.set_rate(settings.rate)
                    output.set_volume(settings.volume)
                except Exception:
                    pass

    def voices(self) -> list[str]:
        """Voices SAPI reports; empty when speech is unavailable."""
        try:
            return list(self._ensure_output().list_voices())
        except Exception:
            return []

    def speak(self, text: str, cancel) -> None:
        """Speak on the caller's thread; cancel stops it promptly."""
        if not text or cancel.is_set():
            return
        try:
            output = self._ensure_output()
            stop = threading.Event()

            def watch() -> None:
                while not stop.wait(0.05):
                    if cancel.is_set():
                        try:
                            output.silence()
                        except Exception:
                            pass
                        return

            watcher = threading.Thread(target=watch, daemon=True)
            watcher.start()
            try:
                output.speak(text)
            finally:
                stop.set()
        except Exception:
            pass
