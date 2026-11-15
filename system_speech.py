"""Independent system speech: SAPI voice separate from the screen reader.

``SAPI5`` speaks through module-level SAPI directly, so nothing here changes
NVDA/JAWS/VoiceOver navigation speech. Settings persist per user (voice,
rate, volume, enabled, speak-responses); a per-conversation override
(inherit/on/off) lives in the preferences config keyed by session id.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

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


def resolve_override(override: str, settings: SpeechSettings) -> bool:
    """Whether this conversation should speak, honor per-conversation choice."""
    if override == OVERRIDE_ON:
        return True
    if override == OVERRIDE_OFF:
        return False
    return settings.enabled and settings.speak_responses


class SystemSpeech:
    """One SAPI voice for BlindPilot's own announcements."""

    def __init__(self) -> None:
        self._output = None
        self._settings = SpeechSettings()
        self._lock = threading.Lock()

    def _ensure_output(self):
        if self._output is None:
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
