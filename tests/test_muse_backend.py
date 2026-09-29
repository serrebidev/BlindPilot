"""The Muse adapter without a Muse install: discovery, credentials, catalog.

Muse's launcher only runs on macOS and Linux, and on this machine that means
inside WSL. Everything here is answered from fakes so the suite stays hermetic
-- no distribution boot, no network, no Meta account.
"""

from __future__ import annotations

import json
import platform
import shutil
from pathlib import Path

import agent_backends
import muse_backend
from muse_backend import (
    muse_command,
    muse_signed_in,
    muse_version,
    reset_discovery,
)


def _no_wsl(monkeypatch) -> None:
    """The machine the protocol was measured on has WSL; these tests may not."""
    monkeypatch.setattr(muse_backend, "wsl_exe", lambda: None)
    monkeypatch.setattr(muse_backend.platform, "system", lambda: "Windows")


def _wsl(monkeypatch, launcher: str = "wsl.exe") -> None:
    monkeypatch.setattr(muse_backend, "wsl_exe", lambda: launcher)
    monkeypatch.setattr(muse_backend.platform, "system", lambda: "Windows")


# --------------------------------------------------------------------------
# Command building (the WSL bridge)
# --------------------------------------------------------------------------


def test_without_wsl_there_is_no_command(monkeypatch):
    _no_wsl(monkeypatch)
    assert muse_command() is None


def test_the_windows_command_goes_through_wsl_with_the_workspace_translated(monkeypatch):
    _wsl(monkeypatch)
    monkeypatch.setattr(muse_backend, "wsl_muse_path", lambda: "/home/u/.local/bin/muse")
    monkeypatch.setattr(
        muse_backend, "windows_path_to_wsl", lambda path: "/mnt/c/work", raising=False
    )

    argv = muse_command("C:\\work")

    assert argv[:2] == ["wsl.exe", "--cd"]
    assert argv[2] == "/mnt/c/work"
    assert argv[-2:] == ["-e", "/home/u/.local/bin/muse"]


def test_a_workspace_is_made_absolute_before_it_is_handed_to_wsl(monkeypatch, tmp_path):
    # Measured: WSL's own --cd rejects a relative argument (Wsl/E_INVALIDARG)
    # and prints its error on stdout, which would poison the protocol stream.
    # The fake translator is the real function for a C:\-style drive path; the
    # assertion is that what reaches it was resolved to an absolute Windows
    # path (no .. segments), which is what `Path.resolve()` guarantees.
    _wsl(monkeypatch)
    monkeypatch.setattr(muse_backend, "wsl_muse_path", lambda: "/home/u/.local/bin/muse")
    received: list[str] = []
    real = muse_backend.windows_path_to_wsl

    def _spy(path: str) -> str:
        received.append(path)
        return real(path)

    monkeypatch.setattr(muse_backend, "windows_path_to_wsl", _spy, raising=False)

    muse_command(str(tmp_path))

    assert received, "the workspace never reached the translator"
    resolved = received[0]
    # Absolute in this process's own terms - whatever drive the suite's temp
    # directory lands on - with nothing relative left to resolve.
    assert Path(resolved).is_absolute(), resolved
    assert ".." not in resolved


def test_without_a_launcher_there_is_no_command_even_with_wsl(monkeypatch):
    _wsl(monkeypatch)
    monkeypatch.setattr(muse_backend, "wsl_muse_path", lambda: None)
    assert muse_command() is None


def test_on_posix_the_command_is_the_launcher_itself(monkeypatch):
    monkeypatch.setattr(muse_backend.platform, "system", lambda: "Linux")
    # muse_command imports `which` from shutil inside the function, so the
    # module's own namespace has nothing to patch.
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/muse")
    assert muse_command() == ["/usr/bin/muse"]


# --------------------------------------------------------------------------
# Credentials and the signed-in answer
# --------------------------------------------------------------------------


def test_a_credential_file_with_providers_means_signed_in(monkeypatch, tmp_path):
    monkeypatch.setattr(muse_backend.platform, "system", lambda: "Linux")
    config = tmp_path / ".config" / "muse"
    config.mkdir(parents=True)
    (config / "auth.json").write_text(
        json.dumps({"providers": {"meta": {"token": "t"}}, "schema_version": 1}),
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))

    assert muse_signed_in() is True


def test_an_empty_or_absent_credential_file_means_not_signed_in(monkeypatch, tmp_path):
    monkeypatch.setattr(muse_backend.platform, "system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    assert muse_signed_in() is False

    config = tmp_path / ".config" / "muse"
    config.mkdir(parents=True)
    (config / "auth.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    # A schema_version alone says the file exists but nothing else.
    assert muse_signed_in() is False


def test_a_credential_file_that_is_not_json_is_not_signed_in(monkeypatch, tmp_path):
    monkeypatch.setattr(muse_backend.platform, "system", lambda: "Linux")
    config = tmp_path / ".config" / "muse"
    config.mkdir(parents=True)
    (config / "auth.json").write_text("not json", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))

    assert muse_signed_in() is False


def test_the_session_log_makes_a_missing_spark_entitlement_actionable(monkeypatch):
    monkeypatch.setattr(
        muse_backend,
        "_muse_session_log_tail",
        lambda _path: '{"payload":{"event":{"details":{"http_status":402}}}}',
    )

    detail = muse_backend.muse_session_access_error("/sessions/example/session.jsonl")

    assert "HTTP 402" in detail
    assert "Muse Spark inference access" in detail


# --------------------------------------------------------------------------
# Version and discovery
# --------------------------------------------------------------------------


def test_the_version_is_asked_through_the_bridge(monkeypatch):
    _wsl(monkeypatch)
    monkeypatch.setattr(muse_backend, "wsl_muse_path", lambda: "/home/u/.local/bin/muse")

    class _Result:
        returncode = 0
        stdout = "muse 1.0.3\n"
        stderr = ""

    seen: list = []
    monkeypatch.setattr(
        muse_backend.subprocess, "run", lambda *a, **_k: seen.append(a[0]) or _Result()
    )
    assert muse_version() == "muse 1.0.3"
    # Bare `muse` starts the interactive agent, which is what the status
    # report showed until the flag was passed: a trust warning, not a version.
    assert seen and seen[0][-1] == "--version"


def test_a_failed_version_probe_answers_empty_rather_than_crashing(monkeypatch):
    _wsl(monkeypatch)
    monkeypatch.setattr(muse_backend, "wsl_muse_path", lambda: None)
    assert muse_version() == ""


def test_discovery_reset_makes_the_next_probe_real(monkeypatch):
    # The install flow runs the installer and then checks whether a launcher
    # appeared; a cached "no" from before the install would read as failure.
    _wsl(monkeypatch)
    muse_backend._WSL_MUSE = "/old/path"
    muse_backend._WSL_MUSE_CHECKED = True

    reset_discovery()

    assert muse_backend._WSL_MUSE is None
    assert muse_backend._WSL_MUSE_CHECKED is False


# --------------------------------------------------------------------------
# Integration with the shared registry
# --------------------------------------------------------------------------


def test_the_registry_knows_muse():
    assert agent_backends.BACKEND_MUSE in agent_backends.BACKEND_IDS
    assert agent_backends.normalize_backend("Muse Code") == agent_backends.BACKEND_MUSE
    assert agent_backends.backend_label("muse") == "Muse Code"


def test_muse_can_compact():
    request = agent_backends.compaction_request("muse")
    assert request is not None
    text, extra = request
    assert text == "/compact"
    assert extra == {"compact": True}


def test_auth_check_asks_the_adapter_not_a_windows_popen(monkeypatch):
    # The launcher cannot be run by Popen on Windows, so backend_auth_ok must
    # not fall through to the generic CLI probe. Measured failure mode: a
    # bash script handed to CreateProcess dies on WinError 193.
    asked = {"adapter": False}

    def _fake_signed_in():
        asked["adapter"] = True
        return True

    monkeypatch.setattr("agent_backends.muse_signed_in", _fake_signed_in, raising=False)
    # Also patch the import site actually used.
    import muse_backend as mb

    monkeypatch.setattr(mb, "muse_signed_in", _fake_signed_in)
    assert agent_backends.backend_auth_ok("muse") is True
    assert asked["adapter"] is True


def test_status_reports_the_muse_cli_path_from_the_adapter(monkeypatch):
    # On Windows the answer lives inside WSL; the fallback path search must
    # never see it, or it could hand back a Windows-side launcher Popen
    # cannot run.
    _wsl(monkeypatch)
    monkeypatch.setattr(muse_backend, "wsl_muse_path", lambda: "/home/u/.local/bin/muse")
    monkeypatch.setattr(muse_backend, "muse_version", lambda: "muse 1.0.3")
    monkeypatch.setattr(
        muse_backend, "muse_account_lines", lambda: ["Signed in: yes"], raising=False
    )

    report = agent_backends.backend_status("muse")
    assert "/home/u/.local/bin/muse" in report
    assert "Version: muse 1.0.3" in report
    assert "Signed in: yes" in report


def test_status_says_not_installed_when_wsl_has_no_muse(monkeypatch):
    _no_wsl(monkeypatch)
    reset_discovery()
    report = agent_backends.backend_status("muse")
    assert "not installed" in report
    if platform.system() == "Windows":
        # Only Windows reaches the WSL branch; on the other platforms the
        # real launcher search runs and must not be lied to.
        reset_discovery()


# --------------------------------------------------------------------------
# Image parts
# --------------------------------------------------------------------------


def test_pictures_become_image_parts_by_their_magic_bytes(tmp_path):
    from muse_backend import muse_image_part

    cases = {
        "shot.png": (b"\x89PNG\r\n\x1a\nrest", "image/png"),
        "photo.jpg": (b"\xff\xd8\xffrest", "image/jpeg"),
        "anim.gif": (b"GIF89arest", "image/gif"),
        "still.webp": (b"RIFF\x00\x00\x00\x00WEBPrest", "image/webp"),
    }
    for name, (raw, media) in cases.items():
        path = tmp_path / name
        path.write_bytes(raw)

        part = muse_image_part(str(path))

        assert part is not None, name
        assert part["type"] == "image", name
        assert part["mediaType"] == media, name
        import base64

        assert base64.b64decode(part["base64Data"]) == raw, name


def test_anything_that_is_not_a_picture_is_not_a_part(tmp_path, monkeypatch):
    from muse_backend import muse_image_part

    note = tmp_path / "notes.txt"
    note.write_text("read me", encoding="utf-8")
    assert muse_image_part(str(note)) is None
    assert muse_image_part(str(tmp_path / "missing.png")) is None

    monkeypatch.setattr(muse_backend, "_MAX_IMAGE_BYTES", 4)
    big = tmp_path / "big.png"
    big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    assert muse_image_part(str(big)) is None


# --------------------------------------------------------------------------
# Skills for the slash picker
# --------------------------------------------------------------------------


def _skills_result(payload: str, returncode: int = 0):
    class _Result:
        stdout = payload
        stderr = ""

    _Result.returncode = returncode
    return _Result()


def test_skills_come_from_the_cli_offline(monkeypatch):
    import json as _json

    monkeypatch.setattr(muse_backend, "muse_command", lambda _cwd: ["muse"])
    monkeypatch.setattr(muse_backend, "_skills_cache", {})
    seen: list = []

    def _run(argv, **_kwargs):
        seen.append(argv)
        return _skills_result(
            _json.dumps(
                {
                    "skills": [
                        {"name": "doctor", "short_description": "Diagnose it."},
                        {"name": "zz", "description": "Long form.\nSecond line."},
                    ]
                }
            )
        )

    monkeypatch.setattr(muse_backend.subprocess, "run", _run)

    rows = muse_backend.muse_skills("/work")

    assert rows == [("doctor", "Diagnose it."), ("zz", "Long form.")]
    assert seen[0][:4] == ["muse", "skills", "list", "--json"]


def test_skills_are_cached_per_directory(monkeypatch):
    monkeypatch.setattr(muse_backend, "muse_command", lambda _cwd: ["muse"])
    monkeypatch.setattr(muse_backend, "_skills_cache", {})
    calls: list = []
    monkeypatch.setattr(
        muse_backend.subprocess,
        "run",
        lambda *_a, **_k: calls.append(1) or _skills_result('{"skills": []}'),
    )

    assert muse_backend.muse_skills("/work") == []
    assert muse_backend.muse_skills("/work") == []
    assert len(calls) == 1


def test_skills_fail_empty_never_loud(monkeypatch):
    monkeypatch.setattr(muse_backend, "muse_command", lambda _cwd: None)
    monkeypatch.setattr(muse_backend, "_skills_cache", {})
    assert muse_backend.muse_skills("/work") == []

    monkeypatch.setattr(muse_backend, "muse_command", lambda _cwd: ["muse"])
    monkeypatch.setattr(muse_backend.subprocess, "run", lambda *_a, **_k: _skills_result("nope", 1))
    assert muse_backend.muse_skills("/elsewhere") == []


# --------------------------------------------------------------------------
# Last-observed usage
# --------------------------------------------------------------------------


def _usage_payload():
    return {
        "tier": "pro",
        "observedAtMs": 1_800_000_000_000,
        "window": {
            "usedPercent": 12,
            "resetsAtMs": 9_999_999_999_999,
            "windowDurationMins": 300,
        },
        "weekly": {"usedPercent": 3, "resetsAtMs": 9_999_999_999_999},
    }


def test_usage_is_stashed_by_turns_and_read_by_status(monkeypatch):
    monkeypatch.setattr(muse_backend, "_usage_payload", None)
    muse_backend.note_muse_usage(_usage_payload())

    assert muse_backend.muse_usage_payload() == _usage_payload()

    lines = agent_backends.backend_usage_lines("muse", "muse")
    assert any("12% used" in line for line in lines)
    assert any("Weekly" in line for line in lines)


def test_usage_without_windows_is_noted_nowhere(monkeypatch):
    monkeypatch.setattr(muse_backend, "_usage_payload", None)
    muse_backend.note_muse_usage({})
    muse_backend.note_muse_usage({"tier": "pro"})
    assert muse_backend.muse_usage_payload() is None
    assert agent_backends.backend_usage_lines("muse", "muse") == []


def test_usage_dies_with_its_windows(monkeypatch):
    monkeypatch.setattr(muse_backend, "_usage_payload", None)
    stale = _usage_payload()
    stale["window"]["resetsAtMs"] = 1_000
    stale["weekly"]["resetsAtMs"] = 1_000
    muse_backend.note_muse_usage(stale)
    assert muse_backend.muse_usage_payload() is None


# --------------------------------------------------------------------------
# The history store layout
# --------------------------------------------------------------------------


def test_the_index_is_found_under_the_data_home(monkeypatch, tmp_path):
    data = tmp_path / "data" / "muse"
    data.mkdir(parents=True)
    db = data / "session-index.db"
    db.write_bytes(b"")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    assert muse_backend.muse_index_db() == db


def test_without_a_store_the_index_is_nowhere(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert muse_backend.muse_index_db() is None


def test_a_store_nobody_guessed_is_asked_of_a_live_host(monkeypatch, tmp_path):
    # The guessed directories miss; initialize.museHome names the real one.
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(muse_backend.platform, "system", lambda: "Linux")
    real = tmp_path / "custom" / "muse"
    real.mkdir(parents=True)
    db = real / "session-index.db"
    db.write_bytes(b"")
    monkeypatch.setattr(muse_backend, "_HOME_CACHE", None)
    monkeypatch.setattr(muse_backend, "_HOME_CHECKED", False)

    class _Host:
        def __init__(self, *args, **kwargs):
            self.closed = False

        def start(self):
            return None

        def send(self, message):
            return True

        def receive(self, timeout):
            return {"jsonrpc": "2.0", "id": 1, "result": {"museHome": str(real)}}

        def connected(self):
            return True

        def close(self):
            self.closed = True

    monkeypatch.setattr(muse_backend, "StdioTransport", _Host)
    monkeypatch.setattr(muse_backend, "muse_command", lambda _cwd=None: ["muse"])

    assert muse_backend.muse_index_db() == db


def test_the_live_home_is_never_asked_on_windows(monkeypatch, tmp_path):
    # A Windows-side host would answer a WSL path this process cannot open;
    # the history reader goes through the WSL bridge instead.
    monkeypatch.setattr(muse_backend.platform, "system", lambda: "Windows")
    monkeypatch.setattr(muse_backend, "_HOME_CACHE", None)
    monkeypatch.setattr(muse_backend, "_HOME_CHECKED", False)

    def _boom(*args, **kwargs):
        raise AssertionError("no host may be opened")

    monkeypatch.setattr(muse_backend, "StdioTransport", _boom)

    assert muse_backend._muse_home_live() is None


def test_a_host_that_never_answers_is_closed_not_kept(monkeypatch):
    closed: list = []

    class _Silent:
        def __init__(self, *args, **kwargs):
            return None

        def start(self):
            return None

        def send(self, message):
            return True

        def receive(self, timeout):
            return None

        def connected(self):
            return False

        def close(self):
            closed.append(True)

    monkeypatch.setattr(muse_backend, "StdioTransport", _Silent)

    transport, init = muse_backend._open_host(["muse"], None, timeout=0.01)

    assert (transport, init) == (None, None)
    assert closed == [True]


def test_discovery_reset_forgets_the_live_home_too(monkeypatch):
    muse_backend._HOME_CACHE = "/old/home"
    muse_backend._HOME_CHECKED = True

    reset_discovery()

    assert muse_backend._HOME_CACHE is None
    assert muse_backend._HOME_CHECKED is False


def test_a_local_log_is_read_and_a_missing_one_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(muse_backend, "wsl_exe", lambda: None)
    log = tmp_path / "session.jsonl"
    log.write_text('{"a": 1}\n', encoding="utf-8")
    assert muse_backend.muse_session_log_text(str(log)) == '{"a": 1}\n'
    assert muse_backend.muse_session_log_text("") == ""
    assert muse_backend.muse_session_log_text(str(tmp_path / "missing.jsonl")) == ""
