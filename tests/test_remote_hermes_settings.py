"""Remote Hermes settings: the parts that are not GUI.

Loaded with a stub for wx, because the settings object and the URL it builds
are the behaviour worth testing and neither needs a window. The dialog itself
is left to manual testing with a screen reader.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest


def _install_wx_stub() -> None:
    """Minimal stand-in for wx, so the module under test can be imported.

    The stub has to hand back real classes, not instances: the module defines
    dialogs and frames that subclass wx types, and a subclass needs a type.
    """
    if "wx" in sys.modules:
        return

    class _Stub:
        """A class that can be subclassed, instantiated, and called freely."""

        def __init__(self, *args, **kwargs):
            pass

        def __getattr__(self, name):
            return _Stub()

        def __call__(self, *args, **kwargs):
            return _Stub()

    class _Module(types.ModuleType):
        def __getattr__(self, name):
            # Anything asked of wx is a subclassable class, which also stands
            # in for its constants and functions.
            value = type(name, (_Stub,), {})
            setattr(self, name, value)
            return value

    wx = _Module("wx")
    sys.modules["wx"] = wx
    for name in ("wx.adv", "wx.lib", "wx.lib.newevent", "wx.html", "wx.richtext", "wx.stc"):
        sys.modules[name] = _Module(name)


_install_wx_stub()

import blindpilot_app  # noqa: E402


@pytest.fixture
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point BlindPilot's own config at a throwaway directory."""
    monkeypatch.setattr(blindpilot_app, "_config_dir", lambda: tmp_path)
    monkeypatch.setattr(blindpilot_app, "_legacy_config_path", lambda: tmp_path / "absent.json")
    return tmp_path


def _settings() -> "blindpilot_app._RemoteHermes":
    return blindpilot_app._RemoteHermes()


def test_remote_mode_is_off_until_it_is_configured(config_dir: Path) -> None:
    """The local Hermes must need no setup at all."""
    remote = _settings()

    assert remote.enabled is False
    assert remote.url() == ""
    assert "runs the copy installed here" in remote.describe()


def test_an_address_survives_a_restart(config_dir: Path) -> None:
    remote = _settings()
    remote.enabled = True
    remote.host = "my-server"
    remote.port = 9223
    remote.key = "s3cret"
    remote.save()

    reloaded = _settings()

    assert reloaded.enabled is True
    assert reloaded.host == "my-server"
    assert reloaded.port == 9223
    assert reloaded.key == "s3cret"
    assert reloaded.url() == "ws://my-server:9223/api/ws"


def test_the_key_is_not_written_into_the_settings_file(config_dir: Path) -> None:
    """It is a credential, not a display preference.

    A key sitting in the same file as the checkboxes gets copied around with
    them -- into a backup, a support bundle, a synced settings folder.
    """
    remote = _settings()
    remote.enabled = True
    remote.host = "box"
    remote.key = "super-secret-value"
    remote.save()

    settings_text = (config_dir / "config.json").read_text(encoding="utf-8")

    assert "super-secret-value" not in settings_text
    assert json.loads(settings_text)["remote_hermes"]["host"] == "box"
    # It is kept, just somewhere of its own.
    assert (config_dir / "remote-hermes-key").read_text(encoding="utf-8") == "super-secret-value"


def test_clearing_the_key_removes_the_file(config_dir: Path) -> None:
    remote = _settings()
    remote.key = "gone-soon"
    remote.save()
    assert (config_dir / "remote-hermes-key").is_file()

    remote.key = ""
    remote.save()

    assert not (config_dir / "remote-hermes-key").exists()


def test_the_key_file_is_readable_only_by_its_owner(config_dir: Path) -> None:
    """Where the platform expresses that. On Windows AppData is the boundary."""
    import platform
    import stat

    if platform.system() == "Windows":  # pragma: no cover - platform specific
        pytest.skip("POSIX permissions do not carry this meaning on Windows")

    # A filesystem that discards permissions cannot show whether the code sets
    # them -- a Windows drive mounted under WSL is the common case. Ask it
    # directly rather than reporting a failure the code did not cause.
    canary = config_dir / ".permission-canary"
    canary.write_text("x", encoding="utf-8")
    import os as _os

    _os.chmod(canary, 0o600)
    if canary.stat().st_mode & stat.S_IRGRP:
        canary.unlink()
        pytest.skip("this filesystem does not keep POSIX permissions")
    canary.unlink()

    remote = _settings()
    remote.key = "private"
    remote.save()

    mode = (config_dir / "remote-hermes-key").stat().st_mode

    assert not mode & stat.S_IRGRP
    assert not mode & stat.S_IROTH


def test_enabling_without_an_address_is_not_treated_as_configured(config_dir: Path) -> None:
    """Otherwise every turn would try to reach an empty address."""
    remote = _settings()
    remote.enabled = True
    remote.host = "   "
    remote.save()

    assert remote.url() == ""
    assert "no address" in _settings().describe()


def test_tls_selects_the_secure_scheme(config_dir: Path) -> None:
    remote = _settings()
    remote.enabled = True
    remote.host = "box"
    remote.secure = True
    remote.save()

    assert _settings().url().startswith("wss://")


def test_an_unknown_key_type_falls_back_rather_than_being_sent(config_dir: Path) -> None:
    (config_dir / "config.json").write_text(
        json.dumps({"remote_hermes": {"enabled": True, "host": "b", "credential": "nonsense"}}),
        encoding="utf-8",
    )

    assert _settings().credential == "token"


def test_a_damaged_settings_file_does_not_stop_the_app(config_dir: Path) -> None:
    """A bad port or a wrong shape must fall back, not raise."""
    (config_dir / "config.json").write_text(
        json.dumps({"remote_hermes": {"enabled": True, "host": "b", "port": "not a number"}}),
        encoding="utf-8",
    )

    remote = _settings()

    assert remote.port == 9119
    assert remote.url() == "ws://b:9119/api/ws"

    (config_dir / "config.json").write_text(
        json.dumps({"remote_hermes": "not a dictionary"}), encoding="utf-8"
    )

    assert _settings().enabled is False


def test_the_description_says_where_it_will_connect(config_dir: Path) -> None:
    """Read aloud, it has to answer "what is this set to" on its own."""
    remote = _settings()
    remote.enabled = True
    remote.host = "garfield"
    remote.port = 9119
    remote.save()

    described = _settings().describe()
    assert described.startswith("On. ws://garfield:9119/api/ws")
    # It also has to say HOW it will sign in: a token and a password are
    # different setups and the difference matters when one of them fails.
    assert "session token" in described
