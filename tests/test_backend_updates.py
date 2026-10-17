import json
import time

import agent_backends
from backend_updates import (
    BackendUpdateService,
    BackendUpdateState,
    compare_versions,
    parse_version,
)


def test_versions_are_numeric_and_prereleases_are_explicit():
    assert parse_version("v1.10.2") == (1, 10, 2, ())
    assert compare_versions("1.10.0", "1.9.99") == 1
    assert parse_version("1.2.3-beta.1")[3] == ("beta", "1")
    assert parse_version("not-a-version") is None


def test_npm_check_compares_package_metadata_to_same_executable(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _backend: "C:/bin/cmdc.exe")
    monkeypatch.setattr(agent_backends, "subprocess_env", lambda _binary: {})
    monkeypatch.setattr(
        "backend_updates._run_version_probe",
        lambda _binary: "command-code 1.2.3",
    )
    monkeypatch.setattr(
        "backend_updates._run_npm_metadata",
        lambda _npm, _package: "1.4.0",
    )
    service = BackendUpdateService(cache_path=tmp_path / "cache.json")

    result = service.check("commandcode")

    assert result.state is BackendUpdateState.UPDATE_AVAILABLE
    assert result.installed_version == "1.2.3"
    assert result.available_version == "1.4.0"
    assert result.source == "npm"


def test_failed_check_is_cached_and_force_refresh_bypasses_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _backend: "C:/bin/cmdc.exe")
    monkeypatch.setattr(agent_backends, "subprocess_env", lambda _binary: {})
    calls = []

    def probe(_binary):
        calls.append(time.monotonic())
        return "1.2.3"

    monkeypatch.setattr("backend_updates._run_version_probe", probe)
    monkeypatch.setattr("backend_updates._run_npm_metadata", lambda _npm, _package: None)
    service = BackendUpdateService(cache_path=tmp_path / "cache.json", ttl=60)

    first = service.check("commandcode")
    second = service.check("commandcode")
    forced = service.check("commandcode", force=True)

    assert first.state is BackendUpdateState.FAILED
    assert second.checked_at == first.checked_at
    assert forced.checked_at >= first.checked_at
    assert len(calls) == 2
    assert json.loads((tmp_path / "cache.json").read_text())


def test_unknown_latest_is_installed_only(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _backend: "C:/bin/hermes.exe")
    monkeypatch.setattr(agent_backends, "subprocess_env", lambda _binary: {})
    monkeypatch.setattr("backend_updates._run_version_probe", lambda _binary: "0.4.0")
    service = BackendUpdateService(cache_path=tmp_path / "cache.json")

    result = service.check("hermes")

    assert result.state is BackendUpdateState.CURRENT
    assert result.available_version is None
    assert result.source == "official_installer"
