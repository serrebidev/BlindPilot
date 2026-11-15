"""Sound packs, per-cue custom sounds, and system speech settings."""

from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Callable

from accessible_ai.storage.paths import app_data_dir

import soundpacks
from earcons import Earcons
from system_speech import SystemSpeech

PAD = 8
PAD_DIALOG = 12

CUE_LABELS = (
    ("send", "Message sent"),
    ("received", "Response received"),
    ("in_progress", "Working (progress)"),
    ("error", "Error"),
)


def packs_dir() -> Path:
    return app_data_dir() / "soundpacks"


def _config() -> dict:
    from accessible_ai.storage import preferences

    from agent_backends import blindpilot_config_dir

    return preferences.load_config([blindpilot_config_dir() / "config.json"])


def _save(patch: dict) -> bool:
    from accessible_ai.storage import preferences

    from agent_backends import blindpilot_config_dir

    return preferences.update_config(blindpilot_config_dir() / "config.json", patch)


def show_pack_dialog(parent, earcons: Earcons, speak: Callable[[str], None]) -> None:
    import wx

    class PackDialog(wx.Dialog):
        def __init__(self):
            super().__init__(parent, title="Sound Pack", style=wx.DEFAULT_DIALOG_STYLE)
            base = packs_dir()
            base.mkdir(parents=True, exist_ok=True)
            self._packs = sorted({Path(p.name).name for p in base.glob("*") if p.is_dir()} | set())
            outer = wx.BoxSizer(wx.VERTICAL)
            pad = self.FromDIP(PAD)
            outer.Add(wx.StaticText(self, label="Sound &pack:"), 0, wx.ALL, pad)
            self.choice = wx.Choice(self, choices=["Built-in"] + self._packs)
            self.choice.SetName("Sound pack")
            outer.Add(self.choice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
            current = str(_config().get("sound_pack") or "")
            if current in self._packs:
                self.choice.SetSelection(1 + self._packs.index(current))
            row = wx.BoxSizer(wx.HORIZONTAL)
            self.use = wx.Button(self, label="&Use this pack")
            self.import_button = wx.Button(self, label="&Import pack…")
            close = wx.Button(self, wx.ID_CANCEL, "&Close")
            for button in (self.use, self.import_button):
                row.Add(button, 0, wx.RIGHT, pad)
            row.Add(close, 0, wx.RIGHT, pad)
            outer.Add(row, 0, wx.ALL, pad)
            self.status = wx.StaticText(self, label="")
            outer.Add(self.status, 0, wx.ALL, pad)
            self.SetSizer(outer)
            self.use.Bind(wx.EVT_BUTTON, self._use)
            self.import_button.Bind(wx.EVT_BUTTON, self._import)
            close.Bind(wx.EVT_BUTTON, self._close)
            self.Bind(wx.EVT_CLOSE, self._close)
            self.choice.SetFocus()
            self.CentreOnParent()

        def _apply(self, name: str) -> None:
            if name == "Built-in":
                earcons.set_pack(None)
                saved = _save({"sound_pack": ""})
                self.status.SetLabel("Built-in sounds.")
                speak("Built-in sounds in use." if saved else "Built-in sounds (not saved).")
            else:
                earcons.set_pack(str(packs_dir() / name))
                saved = _save({"sound_pack": name})
                self.status.SetLabel(f"Sound pack {name}.")
                speak(f"Sound pack {name}." if saved else f"Sound pack {name} (not saved).")

        def _use(self, _event) -> None:
            self._apply(self.choice.GetString(self.choice.GetSelection()))

        def _import(self, _event) -> None:
            dialog = wx.FileDialog(
                self,
                "Import sound pack",
                defaultDir=str(Path.home()),
                style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
                wildcard="Sound packs (*.zip)|*.zip",
            )
            try:
                if dialog.ShowModal() != wx.ID_OK or not dialog.GetPath():
                    return
                path = Path(dialog.GetPath())
            finally:
                dialog.Destroy()
            try:
                result = soundpacks.import_sound_pack(path, packs_dir())
            except ValueError as exc:
                self.status.SetLabel(str(exc))
                speak(str(exc))
                return
            note = ""
            if result.unknown:
                note = " Not installed: " + ", ".join(result.unknown) + "."
            self.status.SetLabel(f"Pack {result.folder.name} imported.{note}")
            speak(f"Pack {result.folder.name} imported.{note}")
            earcons.set_pack(str(result.folder))
            _save({"sound_pack": result.folder.name})

        def _close(self, _event) -> None:
            self.Destroy()

    PackDialog().ShowModal()


def show_custom_cues_dialog(parent, earcons: Earcons, speak: Callable[[str], None]) -> None:
    import wx

    class CuesDialog(wx.Dialog):
        def __init__(self):
            super().__init__(
                parent, title="Custom Cue Sounds", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
            )
            outer = wx.BoxSizer(wx.VERTICAL)
            pad = self.FromDIP(PAD)
            self._rows = []
            overrides = _config().get("sound_overrides") or {}
            outer.Add(
                wx.StaticText(self, label="Each cue can use its own file instead of the pack."),
                0,
                wx.ALL,
                pad,
            )
            for cue, label in CUE_LABELS:
                row = wx.BoxSizer(wx.HORIZONTAL)
                row.Add(wx.StaticText(self, label=f"{label}:"), 0, wx.RIGHT, pad)
                field = wx.TextCtrl(self)
                field.SetName(f"{label} sound file")
                field.SetValue(str(overrides.get(cue) or ""))
                row.Add(field, 1, wx.RIGHT, pad)
                choose = wx.Button(self, label="&Choose…")
                choose.Bind(wx.EVT_BUTTON, lambda _e, f=field: self._choose(f))
                row.Add(choose, 0, wx.RIGHT, pad)
                clear = wx.Button(self, label="&Clear")
                clear.Bind(wx.EVT_BUTTON, lambda _e, f=field: f.SetValue(""))
                row.Add(clear, 0, 0, 0)
                self._rows.append((cue, field))
                outer.Add(row, 0, wx.EXPAND | wx.ALL, pad)
            row = wx.BoxSizer(wx.HORIZONTAL)
            self.save = wx.Button(self, label="&Save")
            close = wx.Button(self, wx.ID_CANCEL, "&Close")
            row.Add(self.save, 0, wx.RIGHT, pad)
            row.Add(close, 0, wx.RIGHT, pad)
            outer.Add(row, 0, wx.ALL, pad)
            self.SetSizer(outer)
            self.save.Bind(wx.EVT_BUTTON, self._save)
            close.Bind(wx.EVT_BUTTON, self._close)
            self.Bind(wx.EVT_CLOSE, self._close)
            self.CentreOnParent()

        def _choose(self, field) -> None:
            dialog = wx.FileDialog(
                self,
                "Choose a sound file",
                defaultDir=str(Path.home()),
                style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
                wildcard="Audio files (*.wav;*.ogg;*.aiff;*.aif;*.mp3)|*.wav;*.ogg;*.aiff;*.aif;*.mp3",
            )
            try:
                if dialog.ShowModal() == wx.ID_OK and dialog.GetPath():
                    field.SetValue(dialog.GetPath())
            finally:
                dialog.Destroy()

        def _save(self, _event) -> None:
            saved = {}
            for cue, field in self._rows:
                value = str(field.GetValue()).strip()
                if value:
                    saved[cue] = value
                earcons.set_override(cue, value or None)
            ok = _save({"sound_overrides": saved})
            speak("Custom cue sounds saved." if ok else "Custom cue sounds (not saved).")
            self.Destroy()

        def _close(self, _event) -> None:
            self.Destroy()

    CuesDialog().ShowModal()


def show_speech_dialog(
    parent, speech: SystemSpeech, speak: Callable[[str], None], session_id: str = ""
) -> None:
    import wx
    from system_speech import SpeechSettings

    class SpeechDialog(wx.Dialog):
        def __init__(self):
            super().__init__(parent, title="System Speech", style=wx.DEFAULT_DIALOG_STYLE)
            saved = _config().get("system_speech") or {}
            self._settings = SpeechSettings(
                enabled=bool(saved.get("enabled", False)),
                voice=str(saved.get("voice") or ""),
                rate=int(saved.get("rate", 0)),
                volume=int(saved.get("volume", 100)),
                speak_responses=bool(saved.get("speak_responses", True)),
            )
            self._session_id = session_id
            outer = wx.BoxSizer(wx.VERTICAL)
            pad = self.FromDIP(PAD)
            self.enabled = wx.CheckBox(self, label="Speak responses with an independent voice")
            self.enabled.SetName("Speak responses with an independent voice")
            self.enabled.SetValue(bool(self._settings.enabled and self._settings.speak_responses))
            outer.Add(self.enabled, 0, wx.ALL, pad)
            outer.Add(wx.StaticText(self, label="&Voice:"), 0, wx.LEFT | wx.RIGHT, pad)
            self.voice = wx.Choice(self, choices=speech.voices() or ["(none available)"])
            self.voice.SetName("System speech voice")
            if self._settings.voice in speech.voices():
                self.voice.SetSelection(list(speech.voices()).index(self._settings.voice))
            outer.Add(self.voice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
            self.rate = wx.Slider(self, -10, 10, int(self._settings.rate))
            self.rate.SetName("Speech rate")
            outer.Add(wx.StaticText(self, label="Rate (slow to fast):"), 0, wx.LEFT | wx.RIGHT, pad)
            outer.Add(self.rate, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
            self.volume = wx.Slider(self, 0, 100, int(self._settings.volume))
            self.volume.SetName("Speech volume")
            outer.Add(wx.StaticText(self, label="Volume:"), 0, wx.LEFT | wx.RIGHT, pad)
            outer.Add(self.volume, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, pad)
            if session_id:
                outer.Add(
                    wx.StaticText(self, label="This conversation:"), 0, wx.LEFT | wx.RIGHT, pad
                )
                overrides = _config().get("speech_overrides") or {}
                current = str(overrides.get(session_id) or "inherit")
                self.override = wx.Choice(
                    self, choices=["Follow the global setting", "Speak here", "Never speak here"]
                )
                self.override.SetName("This conversation's speech choice")
                self.override.SetSelection({"inherit": 0, "on": 1, "off": 2}.get(current, 0))
                outer.Add(self.override, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, pad)
            row = wx.BoxSizer(wx.HORIZONTAL)
            preview = wx.Button(self, label="&Test voice")
            save = wx.Button(self, label="&Save")
            close = wx.Button(self, wx.ID_CANCEL, "&Close")
            for button in (preview, save):
                row.Add(button, 0, wx.RIGHT, pad)
            row.Add(close, 0, wx.RIGHT, pad)
            outer.Add(row, 0, wx.ALL, pad)
            self.SetSizer(outer)
            preview.Bind(wx.EVT_BUTTON, self._preview)
            save.Bind(wx.EVT_BUTTON, self._save)
            close.Bind(wx.EVT_BUTTON, self._close)
            self.Bind(wx.EVT_CLOSE, self._close)
            self.enabled.SetFocus()
            self.CentreOnParent()

        def _current_settings(self) -> SpeechSettings:
            from system_speech import SpeechSettings

            voice_index = self.voice.GetSelection()
            return SpeechSettings(
                enabled=bool(self.enabled.GetValue()),
                voice=self.voice.GetString(voice_index) if voice_index != wx.NOT_FOUND else "",
                rate=int(self.rate.GetValue()),
                volume=int(self.volume.GetValue()),
                speak_responses=True,
            )

        def _preview(self, _event) -> None:
            settings = self._current_settings()
            speech.apply(settings)
            cancel = Event()
            spoken = "BlindPilot system speech test."

            def work() -> None:
                speech.speak(spoken, cancel)

            import threading

            threading.Thread(target=work, daemon=True).start()
            speak("Previewing the voice.")

        def _save(self, _event) -> None:
            settings = self._current_settings()
            speech.apply(settings)
            saved = _save(
                {
                    "system_speech": {
                        "enabled": settings.enabled,
                        "voice": settings.voice,
                        "rate": settings.rate,
                        "volume": settings.volume,
                        "speak_responses": True,
                    }
                }
            )
            if self._session_id and hasattr(self, "override"):
                overrides = dict(_config().get("speech_overrides") or {})
                overrides[self._session_id] = ("inherit", "on", "off")[self.override.GetSelection()]
                _save({"speech_overrides": overrides})
            speak("System speech settings saved." if saved else "System speech (not saved).")
            self.Destroy()

        def _close(self, _event) -> None:
            self.Destroy()

    SpeechDialog().ShowModal()
