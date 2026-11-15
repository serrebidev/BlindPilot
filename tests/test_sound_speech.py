"""Sound-pack import, Earcons resolution, and speech overrides."""

from __future__ import annotations

import json
import zipfile

import pytest

import soundpacks
from earcons import Earcons
from system_speech import SpeechSettings, resolve_override


def _write_pack(path, files):
    with zipfile.ZipFile(path, "w") as bundle:
        for name, data in files.items():
            bundle.writestr(name, data)


def test_pack_import_installs_known_cues(tmp_path):
    pack = tmp_path / "pack.zip"
    _write_pack(pack, {"send.wav": b"send", "received.ogg": b"recv", "bonus.txt": b"?"})
    result = soundpacks.import_sound_pack(pack, tmp_path / "packs")
    assert result.folder.name == "pack"
    assert sorted(p.name for p in result.folder.iterdir()) == ["received.ogg", "send.wav"]
    assert "bonus.txt" in result.unknown


def test_pack_import_rejects_traversal(tmp_path):
    pack = tmp_path / "evil.zip"
    _write_pack(pack, {"../escape.wav": b"x", "send.wav": b"s"})
    with pytest.raises(ValueError, match="safe name"):
        soundpacks.import_sound_pack(pack, tmp_path / "packs")


def test_pack_import_rejects_non_zip(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"no")
    with pytest.raises(ValueError, match="not a sound-pack"):
        soundpacks.import_sound_pack(bad, tmp_path / "packs")


def test_pack_import_rejects_duplicate_name(tmp_path):
    pack = tmp_path / "pack.zip"
    _write_pack(pack, {"send.wav": b"s"})
    soundpacks.import_sound_pack(pack, tmp_path / "packs")
    with pytest.raises(ValueError, match="already exists"):
        soundpacks.import_sound_pack(pack, tmp_path / "packs")


def test_earcons_pack_and_override_resolution(tmp_path):
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    (builtin / "send.wav").write_bytes(b"builtin-send")
    (builtin / "received.wav").write_bytes(b"builtin-recv")
    pack_folder = tmp_path / "pack"
    pack_folder.mkdir()
    (pack_folder / "send.wav").write_bytes(b"pack-send")
    player = Earcons(str(builtin), enabled=False)
    player.set_pack(str(pack_folder))
    assert player.send == str(pack_folder / "send.wav")
    assert player.received == str(builtin / "received.wav")  # fallback
    custom = tmp_path / "custom.wav"
    custom.write_bytes(b"custom")
    player.set_override("send", str(custom))
    assert player.send == str(custom)
    player.set_override("send", None)
    assert player.send == str(pack_folder / "send.wav")


def test_speech_override_resolution():
    settings = SpeechSettings(enabled=True, speak_responses=True)
    assert resolve_override("inherit", settings) is True
    assert resolve_override("on", SpeechSettings(enabled=False)) is True
    assert resolve_override("off", settings) is False
    assert resolve_override("inherit", SpeechSettings(enabled=False)) is False
    assert resolve_override("inherit", SpeechSettings(enabled=True, speak_responses=False)) is False


def test_speech_settings_round_trip(monkeypatch, tmp_path):
    from accessible_ai.storage import preferences

    path = tmp_path / "config.json"
    settings = SpeechSettings(enabled=True, voice="Voice A", rate=3, volume=80)
    preferences.save_config(
        path,
        {
            "system_speech": {
                "enabled": settings.enabled,
                "voice": settings.voice,
                "rate": settings.rate,
                "volume": settings.volume,
                "speak_responses": True,
            }
        },
    )
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["system_speech"]["voice"] == "Voice A"
    assert loaded["system_speech"]["rate"] == 3
