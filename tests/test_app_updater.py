"""GitHub updater selection, version, and verification regression tests."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath

import pytest

import app_updater
from app_updater import (
    ReleaseInfo,
    UpdateCancelled,
    UpdateError,
    _WINDOWS_HELPER,
    asset_name_for_platform,
    download_update,
    fetch_latest_release,
    schedule_install,
    version_tuple,
)


class _Response(io.BytesIO):
    def __init__(self, payload: bytes, url: str = "https://github.com/release"):
        super().__init__(payload)
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def geturl(self) -> str:
        return self._url


def test_version_and_platform_asset_selection():
    assert version_tuple("v1.2.10-beta") == (1, 2, 10)
    assert asset_name_for_platform("Windows", "AMD64") == "BlindPilot-Windows-x64.zip"
    assert asset_name_for_platform("Darwin", "arm64") == "BlindPilot-macOS-arm64.zip"
    assert asset_name_for_platform("Darwin", "x86_64") == "BlindPilot-macOS-x64.zip"


def test_latest_release_is_discovered_from_github_at_runtime():
    archive = b"verified archive"
    digest = hashlib.sha256(archive).hexdigest()
    payload = {
        "tag_name": "v0.4.0",
        "name": "BlindPilot 0.4.0",
        "body": "Accessible update",
        "html_url": "https://github.com/serrebidev/BlindPilot/releases/tag/v0.4.0",
        "draft": False,
        "assets": [
            {
                "name": "BlindPilot-Windows-x64.zip",
                "size": len(archive),
                "digest": f"sha256:{digest}",
                "browser_download_url": "https://github.com/download/update.zip",
            }
        ],
    }

    release = fetch_latest_release(
        "0.3.0",
        opener=lambda *_args, **_kwargs: _Response(json.dumps(payload).encode()),
        system="Windows",
        machine="AMD64",
    )

    assert release.version == "0.4.0"
    assert release.is_newer_than("0.3.0")
    assert release.asset_size == len(archive)
    assert release.sha256 == digest


def test_download_rejects_a_hash_mismatch():
    archive = b"tampered"
    release = ReleaseInfo(
        version="0.4.0",
        tag="v0.4.0",
        title="Update",
        notes="",
        page_url="https://github.com/release",
        asset_name="BlindPilot-Windows-x64.zip",
        asset_url="https://github.com/download/update.zip",
        asset_size=len(archive),
        sha256="0" * 64,
    )

    with pytest.raises(UpdateError, match="SHA-256"):
        download_update(
            release,
            "0.3.0",
            opener=lambda *_args, **_kwargs: _Response(archive),
        )


def test_download_accepts_the_published_hash():
    archive = b"verified"
    release = ReleaseInfo(
        version="0.4.0",
        tag="v0.4.0",
        title="Update",
        notes="",
        page_url="https://github.com/release",
        asset_name="BlindPilot-Windows-x64.zip",
        asset_url="https://github.com/download/update.zip",
        asset_size=len(archive),
        sha256=hashlib.sha256(archive).hexdigest(),
    )

    downloaded = download_update(
        release,
        "0.3.0",
        opener=lambda *_args, **_kwargs: _Response(archive),
    )
    try:
        assert downloaded.read_bytes() == archive
    finally:
        downloaded.unlink(missing_ok=True)


def test_cancelled_download_is_removed_before_any_bytes_are_kept(tmp_path, monkeypatch):
    archive = b"verified"
    release = ReleaseInfo(
        version="0.4.0",
        tag="v0.4.0",
        title="Update",
        notes="",
        page_url="https://github.com/release",
        asset_name="BlindPilot-Windows-x64.zip",
        asset_url="https://github.com/download/update.zip",
        asset_size=len(archive),
        sha256=hashlib.sha256(archive).hexdigest(),
    )
    temporary = tmp_path / "cancelled.zip"
    descriptor = os.open(temporary, os.O_CREAT | os.O_RDWR)
    monkeypatch.setattr(tempfile, "mkstemp", lambda **_kwargs: (descriptor, str(temporary)))
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(UpdateCancelled, match="cancelled"):
        download_update(
            release,
            "0.3.0",
            cancel=cancel,
            opener=lambda *_args, **_kwargs: _Response(archive),
        )

    assert not temporary.exists()


def test_schedule_install_rejects_source_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    with pytest.raises(UpdateError, match="packaged builds only"):
        schedule_install(tmp_path / "update.zip")


def test_macos_installer_is_preflighted_started_detached_and_reports_failures(
    monkeypatch, tmp_path
):
    app = tmp_path / "Applications" / "BlindPilot.app"
    executable = app / "Contents" / "MacOS" / "BlindPilot"
    archive = tmp_path / "BlindPilot update.zip"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"old application")
    archive.write_bytes(b"verified archive")
    launched = {}

    def popen(argv, **kwargs):
        launched["argv"] = argv
        launched["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr("app_updater.platform.system", lambda: "Darwin")
    monkeypatch.setattr("app_updater.subprocess.Popen", popen)
    monkeypatch.setattr("app_updater.tempfile.gettempdir", lambda: str(tmp_path))

    schedule_install(archive)

    argv = launched["argv"]
    helper = Path(argv[1])
    try:
        script = helper.read_text(encoding="utf-8")
        assert argv[0] == "/bin/sh"
        assert argv[2] == str(os.getpid())
        assert argv[3] == str(archive)
        assert argv[4] == str(app.resolve())
        assert argv[5].endswith(".log")
        assert argv[6].endswith(app_updater.STATUS_FILE_NAME)
        assert launched["kwargs"]["start_new_session"] is True
        assert "creationflags" not in launched["kwargs"]
        assert launched["kwargs"]["close_fds"] is True
        assert launched["kwargs"]["cwd"] == str(tmp_path)

        # An unsigned update must have quarantine removed before the old bundle
        # is touched and checked again before Launch Services is asked to open it.
        assert script.index('if ! clear_quarantine "$new_app"; then') < script.index(
            'mv "$app_path" "$backup"'
        )
        assert script.index('if ! clear_quarantine "$app_path"; then') < script.index(
            'log "Update applied. Restarting BlindPilot."'
        )
        assert 'open -n "$app_path"' in script
        assert 'save_failure "$1"' in script
        assert 'ditto "$backup" "$app_path"' in script
    finally:
        helper.unlink(missing_ok=True)


def test_macos_update_rejects_a_translocated_application():
    problem = app_updater.macos_install_problem(
        PurePosixPath("/private/var/folders/example/AppTranslocation/token/d/BlindPilot.app")
    )

    assert "read-only copy" in problem
    assert "Applications folder" in problem


def test_macos_update_rejects_an_application_the_user_cannot_replace(monkeypatch):
    monkeypatch.setattr("app_updater.os.access", lambda *_args: False)

    problem = app_updater.macos_install_problem(Path("/Applications/BlindPilot.app"))

    assert "not allowed to change" in problem
    assert "home folder" in problem


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS ditto and xattr")
def test_macos_helper_replaces_a_quarantined_unsigned_update_and_relaunches(tmp_path):
    install_parent = tmp_path / "Installed Applications"
    app = install_parent / "BlindPilot.app"
    old_executable = app / "Contents" / "MacOS" / "BlindPilot"
    old_executable.parent.mkdir(parents=True)
    old_executable.write_text("old application", encoding="utf-8")
    old_executable.chmod(0o755)

    package = tmp_path / "package" / "BlindPilot.app"
    new_executable = package / "Contents" / "MacOS" / "BlindPilot"
    new_executable.parent.mkdir(parents=True)
    new_executable.write_text("new application", encoding="utf-8")
    new_executable.chmod(0o755)
    archive = tmp_path / "BlindPilot update.zip"
    subprocess.run(
        ["/usr/bin/ditto", "-c", "-k", "--keepParent", str(package), str(archive)],
        check=True,
        capture_output=True,
        text=True,
    )

    helper = tmp_path / "BlindPilot updater.sh"
    helper.write_text(app_updater._MACOS_HELPER, encoding="utf-8")
    helper.chmod(0o700)
    log = tmp_path / "update.log"
    status = tmp_path / app_updater.STATUS_FILE_NAME
    open_marker = tmp_path / "opened.txt"

    # The wrappers make the test deterministic: extraction receives the same
    # quarantine metadata as an internet download, while opening records the
    # relaunch request without starting a GUI during CI.
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_open = fake_bin / "open"
    fake_open.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >"$OPEN_MARKER"\n', encoding="utf-8")
    fake_open.chmod(0o755)
    fake_ditto = fake_bin / "ditto"
    fake_ditto.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-x" ]; then\n'
        '    /usr/bin/ditto "$@" || exit $?\n'
        '    /usr/bin/xattr -w com.apple.quarantine test-download "$4/BlindPilot.app"\n'
        "    exit $?\n"
        "fi\n"
        'exec /usr/bin/ditto "$@"\n',
        encoding="utf-8",
    )
    fake_ditto.chmod(0o755)

    environment = os.environ.copy()
    environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]
    environment["TMPDIR"] = str(tmp_path)
    environment["OPEN_MARKER"] = str(open_marker)
    # The helper waits for its parent PID to close before replacing the bundle.
    # Passing a PID that is already gone makes that wait end at once. Zero is
    # wrong here: kill(2) reads pid 0 as the caller's own process group, so the
    # helper would wait out its 30-second window and then signal this test's
    # whole process group.
    dead_parent = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
    dead_parent_pid = dead_parent.pid
    dead_parent.wait()
    result = subprocess.run(
        [
            "/bin/sh",
            str(helper),
            str(dead_parent_pid),
            str(archive),
            str(app),
            str(log),
            str(status),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert old_executable.read_text(encoding="utf-8") == "new application"
    attributes = subprocess.run(
        ["/usr/bin/xattr", "-lr", str(app)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert "com.apple.quarantine:" not in attributes.stdout
    assert open_marker.read_text(encoding="utf-8").splitlines() == ["-n", str(app)]
    assert not list(install_parent.glob("BlindPilot.app.update-backup-*"))
    assert not archive.exists()
    assert not helper.exists()
    assert not log.exists()
    assert not status.exists()


def test_windows_installer_waits_swaps_and_relaunches(monkeypatch, tmp_path):
    install_dir = tmp_path / "installed BlindPilot"
    executable = install_dir / "BlindPilot.exe"
    archive = tmp_path / "BlindPilot update.zip"
    install_dir.mkdir()
    archive.write_bytes(b"archive")
    launched = {}

    def popen(argv, **kwargs):
        launched["argv"] = argv
        launched["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr("app_updater.platform.system", lambda: "Windows")
    monkeypatch.setattr("app_updater.subprocess.Popen", popen)

    schedule_install(archive)

    argv = launched["argv"]
    helper = Path(argv[argv.index("-File") + 1])
    try:
        script = helper.read_text(encoding="utf-8-sig")
        assert argv[argv.index("-ParentPid") + 1] == str(os.getpid())
        assert argv[argv.index("-Archive") + 1] == str(archive)
        assert argv[argv.index("-InstallDir") + 1] == str(install_dir.resolve())
        assert argv[argv.index("-Executable") + 1] == "BlindPilot.exe"
        # The helper reports to a file because there is no application left to
        # report to by the time it runs.
        assert argv[argv.index("-LogFile") + 1].endswith(".log")
        assert argv[argv.index("-StatusFile") + 1].endswith(app_updater.STATUS_FILE_NAME)
        assert launched["kwargs"]["cwd"] == tempfile.gettempdir()
        assert launched["kwargs"]["close_fds"] is True

        # Nothing may be touched until everything running from the folder has
        # gone and its files can actually be opened.
        assert script.index("Wait-ForExit $ParentPid $InstallDir") < script.index(
            "Invoke-Robocopy $InstallDir $backup $true"
        )
        assert script.index("Wait-Unlocked $InstallDir") < script.index(
            "Invoke-Robocopy $InstallDir $backup $true"
        )
        assert script.index("Invoke-Robocopy $source $InstallDir $true") < script.index(
            "Start-BlindPilot $InstallDir"
        )
        # Rolling back copies the backup rather than moving it.
        assert "Invoke-Robocopy $backup $InstallDir $false" in script
        # Restarting must not hand the new copy the install folder as its
        # working directory, or the next update cannot replace it.
        assert "-WorkingDirectory $HOME" in script
        assert "-WorkingDirectory $InstallDir" not in script
    finally:
        helper.unlink(missing_ok=True)


@pytest.mark.skipif(sys.platform != "win32", reason="creation flags are a Windows concept")
def test_the_update_helper_is_never_started_detached(monkeypatch, tmp_path):
    """DETACHED_PROCESS is why no update installed between 0.3.0 and 0.3.9.

    It leaves PowerShell with no console at all, and Windows PowerShell then
    exits reporting success without ever running the script it was given. The
    update looked like it had started and nothing happened, every time.
    """
    detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    flags = app_updater._windows_helper_flags()

    assert not flags & detached
    assert flags & no_window

    install_dir = tmp_path / "installed"
    install_dir.mkdir()
    archive = tmp_path / "update.zip"
    archive.write_bytes(b"archive")
    launched: dict = {}

    def popen(argv, **kwargs):
        launched["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(install_dir / "BlindPilot.exe"))
    monkeypatch.setattr("app_updater.platform.system", lambda: "Windows")
    monkeypatch.setattr("app_updater.subprocess.Popen", popen)

    schedule_install(archive)

    assert not launched["kwargs"]["creationflags"] & detached


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are a Windows concept")
def test_a_helper_that_cannot_leave_its_job_object_still_starts(monkeypatch, tmp_path):
    """Breaking out of a job is refused when the job forbids it.

    BlindPilot can be started from inside one, and staying in the job is far
    better than not updating at all.
    """
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
    attempts: list[int] = []

    def popen(argv, **kwargs):
        attempts.append(kwargs["creationflags"])
        if len(attempts) == 1:
            raise OSError(87, "The parameter is incorrect")
        return object()

    install_dir = tmp_path / "installed"
    install_dir.mkdir()
    archive = tmp_path / "update.zip"
    archive.write_bytes(b"archive")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(install_dir / "BlindPilot.exe"))
    monkeypatch.setattr("app_updater.platform.system", lambda: "Windows")
    monkeypatch.setattr("app_updater.subprocess.Popen", popen)

    schedule_install(archive)

    assert len(attempts) == 2
    assert attempts[0] & breakaway
    assert not attempts[1] & breakaway


def test_a_failed_update_leaves_its_reason_for_the_next_start(monkeypatch, tmp_path):
    """The update finishes after BlindPilot has closed, so this is the only
    way its failure can ever reach the user."""
    monkeypatch.setattr("app_updater.tempfile.gettempdir", lambda: str(tmp_path))

    assert app_updater.pending_failure() == ("", "")

    status = tmp_path / app_updater.STATUS_FILE_NAME
    status.write_text("Could not move the current version aside.\nC:\\temp\\u.log\n", "utf-8")

    assert app_updater.pending_failure() == (
        "Could not move the current version aside.",
        "C:\\temp\\u.log",
    )

    app_updater.clear_pending_failure()

    assert app_updater.pending_failure() == ("", "")


def test_abandoned_downloads_are_swept_but_a_running_one_is_left_alone(monkeypatch, tmp_path):
    monkeypatch.setattr("app_updater.tempfile.gettempdir", lambda: str(tmp_path))
    old = tmp_path / f"{app_updater.TEMPORARY_PREFIX}old.zip"
    fresh = tmp_path / f"{app_updater.TEMPORARY_PREFIX}fresh.zip"
    log = tmp_path / f"{app_updater.TEMPORARY_PREFIX}old.log"
    status = tmp_path / app_updater.STATUS_FILE_NAME
    unrelated = tmp_path / "something-else.zip"
    for path in (old, fresh, log, status, unrelated):
        path.write_bytes(b"x")
    stale = time.time() - 24 * 60 * 60
    os.utime(old, (stale, stale))
    os.utime(log, (stale, stale))

    removed = app_updater.sweep_temporary_files()

    assert removed == 1
    assert not old.exists()
    # A download in flight belongs to an update that is running right now.
    assert fresh.exists()
    # The log and the recorded reason are what the next start reads.
    assert log.exists()
    assert status.exists()
    assert unrelated.exists()


def test_windows_installer_spawn_error_is_accessible_and_cleans_helper(monkeypatch, tmp_path):
    executable = tmp_path / "BlindPilot" / "BlindPilot.exe"
    archive = tmp_path / "update.zip"
    created_helper = None
    real_mkstemp = tempfile.mkstemp

    def mkstemp(*args, **kwargs):
        nonlocal created_helper
        fd, name = real_mkstemp(*args, dir=tmp_path, **kwargs)
        created_helper = Path(name)
        return fd, name

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr("app_updater.platform.system", lambda: "Windows")
    monkeypatch.setattr("app_updater.tempfile.mkstemp", mkstemp)
    monkeypatch.setattr(
        "app_updater.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("launch failed")),
    )

    with pytest.raises(UpdateError, match="Could not start.*launch failed"):
        schedule_install(archive)

    assert created_helper is not None
    assert not created_helper.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows PowerShell")
def test_windows_helper_script_has_valid_powershell_syntax(tmp_path):
    helper = tmp_path / "updater.ps1"
    helper.write_text(_WINDOWS_HELPER, encoding="utf-8-sig")
    powershell = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    command = (
        "$errors=$null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "'" + str(helper).replace("'", "''") + "', [ref]$null, [ref]$errors) | Out-Null; "
        "if ($errors.Count) { $errors | ForEach-Object { $_.Message }; exit 1 }"
    )

    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
        # A child with no console of its own must not be handed the parent's
        # console as stdin: PowerShell blocks on reading it and never exits.
        stdin=subprocess.DEVNULL,
        timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows PowerShell")
def test_windows_helper_waits_for_old_process_then_replaces_and_launches(tmp_path):
    install_dir = tmp_path / "installed BlindPilot"
    payload = tmp_path / "payload" / "BlindPilot"
    archive = tmp_path / "BlindPilot update.zip"
    helper = tmp_path / "updater.ps1"
    log_file = tmp_path / "updater.log"
    status_file = tmp_path / "updater-status.txt"
    marker = install_dir / "new-launched.txt"
    running_marker = install_dir / "new-running.txt"
    install_dir.mkdir()
    payload.mkdir(parents=True)
    # The stand-in for the app is a script host rather than a batch file: it
    # keeps running long enough for the helper's launch check, and it does it
    # without putting a console window on screen during the test run.
    (install_dir / "BlindPilot.vbs").write_text("WScript.Quit 0\r\n", encoding="ascii")
    (install_dir / "obsolete.txt").write_text("old", encoding="ascii")
    (payload / "BlindPilot.vbs").write_text(
        'Set fso = CreateObject("Scripting.FileSystemObject")\r\n'
        "home = fso.GetParentFolderName(WScript.ScriptFullName)\r\n"
        'fso.CreateTextFile(fso.BuildPath(home, "new-launched.txt"), True).WriteLine "launched"\r\n'
        'fso.CreateTextFile(fso.BuildPath(home, "new-running.txt"), True).WriteLine "running"\r\n'
        "WScript.Sleep 5000\r\n"
        # Step out of the install directory before finishing, or the running
        # process keeps a handle on it and the temporary tree cannot be removed.
        'CreateObject("WScript.Shell").CurrentDirectory = fso.GetSpecialFolder(2)\r\n'
        'fso.DeleteFile fso.BuildPath(home, "new-running.txt")\r\n',
        encoding="ascii",
    )
    (payload / "current.txt").write_text("new", encoding="ascii")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in payload.rglob("*"):
            if path.is_file():
                bundle.write(path, Path("BlindPilot") / path.relative_to(payload))
    helper.write_text(_WINDOWS_HELPER, encoding="utf-8-sig")
    powershell = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    blocker = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=install_dir,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    updater = None
    try:
        updater = subprocess.Popen(
            [
                str(powershell),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(helper),
                "-ParentPid",
                str(blocker.pid),
                "-Archive",
                str(archive),
                "-InstallDir",
                str(install_dir),
                "-Executable",
                "BlindPilot.vbs",
                "-LogFile",
                str(log_file),
                "-StatusFile",
                str(status_file),
            ],
            cwd=tempfile.gettempdir(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        # Nothing may be replaced while the old process is still alive.
        time.sleep(3)

        assert blocker.poll() is None
        assert (install_dir / "obsolete.txt").read_text(encoding="ascii") == "old"
        assert not marker.exists()

        blocker.terminate()
        blocker.wait(timeout=10)
        stdout, stderr = updater.communicate(timeout=120)
        detail = stdout + stderr
        if log_file.exists():
            detail += log_file.read_text(encoding="utf-8", errors="replace")
        assert updater.returncode == 0, detail

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.05)
        assert marker.exists()
        assert (install_dir / "current.txt").read_text(encoding="ascii") == "new"
        assert not (install_dir / "obsolete.txt").exists()
        assert not archive.exists()
        assert not helper.exists()
        assert not list(tmp_path.glob("installed BlindPilot.update-*"))
        # A clean run leaves neither a reason to report nor a log to read.
        assert not status_file.exists()
        assert not log_file.exists()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and running_marker.exists():
            time.sleep(0.05)
        assert not running_marker.exists()
    finally:
        if blocker.poll() is None:
            blocker.kill()
            blocker.wait(timeout=10)
        if updater is not None and updater.poll() is None:
            updater.kill()
            updater.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="install kinds are a Windows distinction")
def test_a_setup_installation_updates_through_its_own_installer(tmp_path, monkeypatch):
    """Swapping the directory would delete the uninstaller beside the app.

    That leaves Add or Remove Programs pointing at a file that no longer
    exists and the registered version stuck at the old one, so an installed
    copy has to be updated by the installer instead.
    """
    installed = tmp_path / "installed"
    installed.mkdir()
    (installed / "BlindPilot.exe").touch()
    (installed / "unins000.exe").touch()
    assert app_updater.install_kind(installed / "BlindPilot.exe") == app_updater.INSTALL_SETUP
    assert (
        app_updater.asset_name_for_platform("windows", "amd64", app_updater.INSTALL_SETUP)
        == "BlindPilot-Setup-x64.exe"
    )

    portable = tmp_path / "portable"
    portable.mkdir()
    (portable / "BlindPilot.exe").touch()
    assert app_updater.install_kind(portable / "BlindPilot.exe") == app_updater.INSTALL_PORTABLE
    assert (
        app_updater.asset_name_for_platform("windows", "amd64", app_updater.INSTALL_PORTABLE)
        == "BlindPilot-Windows-x64.zip"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows PowerShell")
def test_setup_helper_script_has_valid_powershell_syntax(tmp_path):
    helper = tmp_path / "setup-updater.ps1"
    helper.write_text(app_updater._WINDOWS_SETUP_HELPER, encoding="utf-8-sig")
    powershell = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    command = (
        "$errors=$null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "'" + str(helper).replace("'", "''") + "', [ref]$null, [ref]$errors) | Out-Null; "
        "if ($errors.Count) { $errors | ForEach-Object { $_.Message }; exit 1 }"
    )
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_installer_is_never_left_to_abort_over_a_file_in_use():
    """Message boxes are suppressed, so the default answer is the one taken.

    Asked to close a program and told no, the installer shows an
    Abort / Retry / Ignore box; suppressed, that box answers Abort and setup
    exits with code 5, having rolled the update back. Forcing the close is what
    makes a silent update silent in the intended sense.
    """
    script = app_updater._WINDOWS_SETUP_HELPER
    assert "/FORCECLOSEAPPLICATIONS" in script
    assert "/CLOSEAPPLICATIONS" in script
    # And the failure that reaches the user says what happened, not a number.
    assert "still in use" in script


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows PowerShell")
def test_a_program_holding_one_of_our_libraries_counts_as_a_blocker(tmp_path):
    """The blocker that broke updates ran from somewhere else entirely.

    BlindPilot used to hand its own DLL folder to every process it started, so
    an agent CLI — or anything that CLI started, outliving BlindPilot by hours
    — kept a library of ours loaded. Looking only at where a process runs from
    finds none of them; the installer's restart manager finds all of them, and
    stops. The deep check has to see what the installer sees.
    """
    install_dir = tmp_path / "installed BlindPilot"
    install_dir.mkdir()
    # Any real DLL will do: what is being tested is that a loaded module inside
    # the folder is noticed, not which module it is.
    library = install_dir / "borrowed.dll"
    library.write_bytes((Path(os.environ["SystemRoot"]) / "System32" / "version.dll").read_bytes())

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import ctypes, sys, time; ctypes.WinDLL(sys.argv[1]); time.sleep(60)",
            str(library),
        ],
        # Deliberately started from outside the install folder, exactly like the
        # processes that used to go unnoticed.
        cwd=tempfile.gettempdir(),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    probe = tmp_path / "probe.ps1"
    probe.write_text(
        "param([string]$Folder, [int]$Holder)\n"
        "$LogFile = Join-Path ([IO.Path]::GetTempPath()) 'blindpilot-probe.log'\n"
        + app_updater._WINDOWS_PRELUDE
        + "\n"
        "$shallow = @(Get-Blockers 0 $Folder $false | ForEach-Object { $_.Id })\n"
        "$deep = @(Get-Blockers 0 $Folder $true | ForEach-Object { $_.Id })\n"
        "'shallow:' + ($shallow -contains $Holder)\n"
        "'deep:' + ($deep -contains $Holder)\n",
        encoding="utf-8-sig",
    )
    powershell = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    try:
        # Give the holder a moment to actually load the library.
        deadline = time.monotonic() + 20
        seen = ""
        while time.monotonic() < deadline:
            result = subprocess.run(
                [
                    str(powershell),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(probe),
                    "-Folder",
                    str(install_dir),
                    "-Holder",
                    str(holder.pid),
                ],
                check=False,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            seen = result.stdout + result.stderr
            if "deep:True" in seen:
                break
            time.sleep(0.5)
        assert "deep:True" in seen, seen
        # The old check really did miss it, which is why this test exists.
        assert "shallow:False" in seen, seen
    finally:
        holder.kill()
        holder.wait(timeout=10)
