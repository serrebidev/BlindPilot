"""Secure GitHub Releases updater for packaged BlindPilot installations.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
Based on the original Claude Code Reader application by doubletaponair:
https://github.com/doubletaponair/claude-code-reader
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol
from urllib.parse import urlparse
from urllib.request import Request

from certificates import open_url


GITHUB_REPOSITORY = "serrebidev/BlindPilot"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
MAX_UPDATE_BYTES = 750 * 1024 * 1024
# Every file the updater leaves in the temp folder starts with this, so
# sweep_temporary_files can find them.
TEMPORARY_PREFIX = "BlindPilot-update-"
_ALLOWED_DOWNLOAD_HOSTS = ("github.com", "githubusercontent.com")


class UpdateError(RuntimeError):
    """An update could not be checked, verified, or scheduled safely."""


class UpdateCancelled(UpdateError):
    """The person cancelled an update download before it completed."""


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    title: str
    notes: str
    page_url: str
    asset_name: str
    asset_url: str
    asset_size: int
    sha256: str
    checksum_url: str = ""

    def is_newer_than(self, current_version: str) -> bool:
        return version_tuple(self.version) > version_tuple(current_version)


def version_tuple(value: str) -> tuple[int, ...]:
    """Return the numeric portion of a release version for safe comparisons."""
    match = re.search(r"\d+(?:\.\d+)+", value or "")
    return tuple(int(part) for part in match.group(0).split(".")) if match else ()


INSTALL_SETUP = "setup"
INSTALL_PORTABLE = "portable"


def install_kind(executable: Optional[Path] = None) -> str:
    """Say whether this copy was put here by the installer or unpacked by hand.

    The two need different updates. A copy from the setup program is registered
    in Add or Remove Programs and keeps its uninstaller beside the executable;
    replacing that directory wholesale deletes the uninstaller and leaves the
    registered version behind, so the installer has to do the update itself.
    An unpacked copy owns nothing but its own folder and is simply swapped.
    """
    if platform.system() != "Windows":
        return INSTALL_PORTABLE
    folder = (executable or Path(sys.executable)).resolve().parent
    try:
        uninstallers = any(folder.glob("unins*.exe"))
    except OSError:
        uninstallers = False
    return INSTALL_SETUP if uninstallers else INSTALL_PORTABLE


def asset_name_for_platform(
    system: Optional[str] = None,
    machine: Optional[str] = None,
    kind: Optional[str] = None,
) -> str:
    """Return the release asset expected by this operating system and CPU."""
    system = (system or platform.system()).casefold()
    machine = (machine or platform.machine()).casefold()
    if system == "windows" and machine in ("amd64", "x86_64"):
        if (kind or install_kind()) == INSTALL_SETUP:
            return "BlindPilot-Setup-x64.exe"
        return "BlindPilot-Windows-x64.zip"
    if system == "darwin":
        if machine in ("arm64", "aarch64"):
            return "BlindPilot-macOS-arm64.zip"
        if machine in ("x86_64", "amd64"):
            return "BlindPilot-macOS-x64.zip"
    raise UpdateError(
        f"Automatic updates are not available for {platform.system()} {platform.machine()}."
    )


def _request(url: str, current_version: str) -> Request:
    return Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"BlindPilot/{current_version}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


class HttpResponse(Protocol):
    """What this module asks of whatever `opener` hands back.

    urlopen's own response in a running BlindPilot, a stand-in in the tests.
    Either one is entered with `with` and read a piece at a time; nothing else
    about it is this module's business.
    """

    def read(self, amount: int = -1, /) -> bytes: ...

    def __enter__(self) -> "HttpResponse": ...

    def __exit__(self, *exc_info: object) -> object: ...


# The opener is a parameter so the tests can answer without a network.
Opener = Callable[..., HttpResponse]


def _verified_urlopen(request: Request, timeout: Optional[float] = None) -> HttpResponse:
    """The real opener: GitHub over a trust store a packaged build still has."""
    return open_url(request, timeout=timeout)


def _read_limited(response: HttpResponse, limit: int) -> bytes:
    chunks: list[bytes] = []
    received = 0
    while True:
        chunk = response.read(min(1024 * 1024, limit + 1 - received))
        if not chunk:
            return b"".join(chunks)
        received += len(chunk)
        if received > limit:
            raise UpdateError("The update response exceeded the allowed size.")
        chunks.append(chunk)


def fetch_latest_release(
    current_version: str,
    *,
    opener: Opener = _verified_urlopen,
    system: Optional[str] = None,
    machine: Optional[str] = None,
    kind: Optional[str] = None,
) -> ReleaseInfo:
    """Query GitHub at runtime and select the asset for this computer."""
    try:
        with opener(_request(LATEST_RELEASE_API, current_version), timeout=20) as response:
            payload = json.loads(_read_limited(response, 4 * 1024 * 1024))
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError(f"Could not contact GitHub: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("draft"):
        raise UpdateError("GitHub returned an invalid latest release.")
    tag = str(payload.get("tag_name") or "").strip()
    if not version_tuple(tag):
        raise UpdateError("The latest GitHub release has no valid version tag.")
    expected_name = asset_name_for_platform(system, machine, kind)
    assets = payload.get("assets")
    if not isinstance(assets, list):
        assets = []
    asset = next(
        (
            entry
            for entry in assets
            if isinstance(entry, dict) and entry.get("name") == expected_name
        ),
        None,
    )
    if asset is None:
        raise UpdateError(f"The release does not contain {expected_name}.")
    size = int(asset.get("size") or 0)
    if size <= 0 or size > MAX_UPDATE_BYTES:
        raise UpdateError("The release asset has an invalid size.")
    digest = str(asset.get("digest") or "")
    sha256 = digest.removeprefix("sha256:").casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        sha256 = ""
    checksum_name = expected_name + ".sha256"
    checksum_asset = next(
        (
            entry
            for entry in assets
            if isinstance(entry, dict) and entry.get("name") == checksum_name
        ),
        None,
    )
    checksum_url = str(checksum_asset.get("browser_download_url") or "") if checksum_asset else ""
    if not sha256 and not checksum_url:
        raise UpdateError("The release has no SHA-256 verification data.")
    return ReleaseInfo(
        version=".".join(map(str, version_tuple(tag))),
        tag=tag,
        title=str(payload.get("name") or tag),
        notes=str(payload.get("body") or "").strip(),
        page_url=str(payload.get("html_url") or ""),
        asset_name=expected_name,
        asset_url=str(asset.get("browser_download_url") or ""),
        asset_size=size,
        sha256=sha256,
        checksum_url=checksum_url,
    )


def _allowed_download_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    return parsed.scheme == "https" and any(
        host == allowed or host.endswith("." + allowed) for allowed in _ALLOWED_DOWNLOAD_HOSTS
    )


def _checksum_from_sidecar(release: ReleaseInfo, current_version: str, opener: Opener) -> str:
    if not _allowed_download_url(release.checksum_url):
        raise UpdateError("The checksum download URL is not trusted.")
    try:
        with opener(_request(release.checksum_url, current_version), timeout=20) as response:
            text = _read_limited(response, 4096).decode("ascii", errors="strict")
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError(f"Could not download the update checksum: {exc}") from exc
    match = re.search(r"\b[0-9a-fA-F]{64}\b", text)
    if not match:
        raise UpdateError("The update checksum file is invalid.")
    return match.group(0).casefold()


def download_update(
    release: ReleaseInfo,
    current_version: str,
    *,
    progress: Optional[Callable[[int, int], None]] = None,
    cancel: Optional[threading.Event] = None,
    opener: Opener = _verified_urlopen,
) -> Path:
    """Download a release archive and reject it unless SHA-256 matches."""
    if not _allowed_download_url(release.asset_url):
        raise UpdateError("The update download URL is not trusted.")
    expected = release.sha256 or _checksum_from_sidecar(release, current_version, opener)
    # Keep the published extension: an installer has to stay a .exe to be run.
    suffix = ".exe" if release.asset_name.casefold().endswith(".exe") else ".zip"
    fd, temporary_name = tempfile.mkstemp(prefix=TEMPORARY_PREFIX, suffix=suffix)
    os.close(fd)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    received = 0
    try:
        with opener(_request(release.asset_url, current_version), timeout=60) as response:
            final_url = getattr(response, "geturl", lambda: release.asset_url)()
            if not _allowed_download_url(final_url):
                raise UpdateError("GitHub redirected the update to an untrusted host.")
            with temporary.open("wb") as handle:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise UpdateCancelled("The update download was cancelled.")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > MAX_UPDATE_BYTES or received > release.asset_size:
                        raise UpdateError("The update download exceeded its published size.")
                    digest.update(chunk)
                    handle.write(chunk)
                    if progress:
                        progress(received, release.asset_size)
        if cancel is not None and cancel.is_set():
            raise UpdateCancelled("The update download was cancelled.")
        if received != release.asset_size:
            raise UpdateError("The update download was incomplete.")
        if digest.hexdigest().casefold() != expected:
            raise UpdateError("The update failed SHA-256 verification.")
        return temporary
    except UpdateError:
        temporary.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise UpdateError(f"Could not download the update: {exc}") from exc


# Everything the two Windows helpers share. An update runs with no application
# left to report to, so the rules here are: write down what happened, be sure
# nothing is still holding the files before touching them, and never leave the
# user without a working BlindPilot.
_WINDOWS_PRELUDE = r"""
$ErrorActionPreference = "Stop"

function Write-Log([string]$Message) {
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    try { Add-Content -LiteralPath $LogFile -Value "$stamp  $Message" } catch { }
}

function Save-Failure([string]$Message) {
    # BlindPilot is not running to be told, so the reason is left where its next
    # start will find it. UTF8, because Windows PowerShell's default is the
    # ANSI code page and the reader expects UTF-8.
    try { Set-Content -LiteralPath $StatusFile -Value @($Message, $LogFile) -Encoding UTF8 } catch { }
}

function Get-Blockers([int]$TargetPid, [string]$Folder, [bool]$Deep = $false) {
    # Waiting for the one process id is not enough. Anything still running out
    # of the folder being replaced keeps its files mapped, and BlindPilot leaves
    # descendants behind: the agent command-line tools it starts, and the
    # console host bundled in _internal for FreeBuff's terminal.
    #
    # A deep pass also asks which processes have *loaded* something from the
    # folder. A program running from somewhere else entirely can still hold a
    # library of ours open — that is what the installer's restart manager sees
    # and what a check on the executable's own path misses completely. It reads
    # every process's module list, so it is used at the point where something
    # has to be closed, not in the polling loop.
    $root = ([IO.Path]::GetFullPath($Folder)).TrimEnd('\') + '\'
    $found = @()
    if ($TargetPid -gt 0) {
        $one = Get-Process -Id $TargetPid -ErrorAction SilentlyContinue
        if ($one) { $found += $one }
    }
    foreach ($item in (Get-Process -ErrorAction SilentlyContinue)) {
        # Never count this script: killing the updater cancels the update.
        if ($item.Id -eq $PID) { continue }
        $hit = $false
        try {
            $path = $item.Path
            if ($path) {
                $full = [IO.Path]::GetFullPath($path)
                if ($full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { $hit = $true }
            }
        } catch { }
        if (-not $hit -and $Deep) {
            try {
                foreach ($module in $item.Modules) {
                    $name = $module.FileName
                    if (-not $name) { continue }
                    $full = [IO.Path]::GetFullPath($name)
                    if ($full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
                        $hit = $true
                        break
                    }
                }
            } catch { }
        }
        if ($hit) { $found += $item }
    }
    return @($found | Sort-Object Id -Unique)
}

function Wait-ForExit([int]$TargetPid, [string]$Folder) {
    $stages = @(30, 10, 10)
    for ($stage = 0; $stage -lt $stages.Count; $stage++) {
        $deadline = [DateTime]::UtcNow.AddSeconds($stages[$stage])
        while ([DateTime]::UtcNow -lt $deadline) {
            if ((Get-Blockers $TargetPid $Folder $false).Count -eq 0) {
                # The cheap check is clear; confirm with the expensive one
                # before deciding the folder is free. If something is still
                # holding a library of ours there is no point waiting for it to
                # exit on its own, so stop polling and start closing.
                if ((Get-Blockers $TargetPid $Folder $true).Count -eq 0) {
                    # Give Windows a moment to release the image mappings of a
                    # process that has only just gone.
                    Start-Sleep -Milliseconds 1500
                    return $true
                }
                break
            }
            Start-Sleep -Milliseconds 250
        }
        $blockers = Get-Blockers $TargetPid $Folder $true
        if ($blockers.Count -eq 0) { Start-Sleep -Milliseconds 1500; return $true }
        Write-Log (
            "Still holding files: " +
            (($blockers | ForEach-Object { "$($_.ProcessName) ($($_.Id))" }) -join ", ")
        )
        foreach ($item in $blockers) {
            try {
                if ($stage -eq 0) { $item.CloseMainWindow() | Out-Null }
                else { Stop-Process -Id $item.Id -Force -ErrorAction SilentlyContinue }
            } catch { }
        }
    }
    return ((Get-Blockers $TargetPid $Folder $true).Count -eq 0)
}

function Enter-UpdateTurn() {
    # Two BlindPilot windows check for updates independently, and two installers
    # running over the same folder are guaranteed to find each other's files in
    # use. Whoever gets here first does the update; the other steps aside.
    $script:UpdateMutex = New-Object System.Threading.Mutex($false, "Local\BlindPilotUpdate")
    try {
        $mine = $script:UpdateMutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        # The previous updater died holding it; the turn is ours.
        $mine = $true
    }
    return $mine
}

function Wait-Unlocked([string]$Folder) {
    # A process that has just exited can leave its libraries mapped a moment
    # longer, and a virus scanner reading the new files holds them too. Opening
    # each one with no sharing allowed is the only way to know the swap can go
    # ahead rather than fail halfway.
    $targets = @()
    $exe = Join-Path $Folder $Executable
    if (Test-Path -LiteralPath $exe -PathType Leaf) { $targets += $exe }
    $internal = Join-Path $Folder "_internal"
    if (Test-Path -LiteralPath $internal -PathType Container) {
        $targets += @(
            Get-ChildItem -LiteralPath $internal -File -Filter *.dll -ErrorAction SilentlyContinue |
                ForEach-Object { $_.FullName }
        )
        $targets += @(
            Get-ChildItem -LiteralPath $internal -File -Filter *.exe -Recurse -ErrorAction SilentlyContinue |
                ForEach-Object { $_.FullName }
        )
    }
    $locked = @()
    foreach ($path in $targets) {
        $opened = $false
        for ($attempt = 0; $attempt -lt 10 -and -not $opened; $attempt++) {
            try {
                $handle = [IO.File]::Open(
                    $path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None
                )
                $handle.Close()
                $opened = $true
            } catch { Start-Sleep -Milliseconds 500 }
        }
        if (-not $opened) { $locked += $path }
    }
    if ($locked.Count -gt 0) {
        Write-Log ("Locked after waiting: " + ($locked -join "; "))
        return $false
    }
    return $true
}

function Invoke-Robocopy([string]$From, [string]$To, [bool]$Move) {
    $arguments = @($From, $To, "/E", "/R:10", "/W:3", "/NFL", "/NDL", "/NP", "/NJH", "/NJS")
    if ($Move) { $arguments += "/MOVE" }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & robocopy.exe @arguments 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    foreach ($line in $output) {
        $text = "$line".Trim()
        if ($text) { Write-Log ("robocopy: " + $text) }
    }
    # Robocopy reports through a bit mask: anything under 8 copied cleanly.
    return $code
}

function Test-Drained([string]$Folder) {
    if (-not (Test-Path -LiteralPath $Folder -PathType Container)) { return $true }
    $left = @(Get-ChildItem -LiteralPath $Folder -Recurse -Force -File -ErrorAction SilentlyContinue)
    if ($left.Count -eq 0) { return $true }
    Write-Log ("Folder still holds " + $left.Count + " file(s), first: " + $left[0].FullName)
    return $false
}

function Start-BlindPilot([string]$Folder) {
    $exe = Join-Path $Folder $Executable
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        Write-Log ("Cannot restart: " + $exe + " is missing.")
        return $false
    }
    # Never start it with the install folder as its working directory. That
    # directory handle is what stops the *next* update from replacing it.
    try {
        Start-Process -FilePath $exe -WorkingDirectory $HOME -ErrorAction Stop | Out-Null
        return $true
    } catch {
        Write-Log ("Could not restart BlindPilot: " + $_.Exception.Message)
        return $false
    }
}
"""


_WINDOWS_HELPER = (
    r"""param(
    [Parameter(Mandatory=$true)][int]$ParentPid,
    [Parameter(Mandatory=$true)][string]$Archive,
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$Executable,
    [Parameter(Mandatory=$true)][string]$LogFile,
    [Parameter(Mandatory=$true)][string]$StatusFile
)
"""
    + _WINDOWS_PRELUDE
    + r"""
$token = [guid]::NewGuid().ToString("N")
$stage = Join-Path ([IO.Path]::GetTempPath()) ("BlindPilot-stage-" + $token)
$backup = (Join-Path (Split-Path -Parent $InstallDir) (Split-Path -Leaf $InstallDir)) +
    ".update-backup-" + $token
$movedAside = $false
$replaced = $false
$handedOver = $false
if (-not (Enter-UpdateTurn)) {
    Write-Log "Another update is already running; leaving it to finish."
    Remove-Item -LiteralPath $Archive -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
    exit 0
}
try {
    Write-Log ("Updating " + $InstallDir)
    New-Item -ItemType Directory -Path $stage | Out-Null
    Expand-Archive -LiteralPath $Archive -DestinationPath $stage -Force
    $source = Join-Path $stage "BlindPilot"
    if (-not (Test-Path -LiteralPath (Join-Path $source $Executable) -PathType Leaf)) {
        throw "The update archive does not contain $Executable."
    }

    if (-not (Wait-ForExit $ParentPid $InstallDir)) {
        throw "BlindPilot is still running, so its files could not be replaced."
    }
    if (-not (Wait-Unlocked $InstallDir)) {
        throw "Some BlindPilot files are still in use, so they could not be replaced."
    }

    # Move what is *inside* the folder rather than renaming the folder itself.
    # The folder can be held open by a shortcut's working directory or a file
    # sync client, and a rename fails where moving its contents succeeds.
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    # Set before the first move, not after a successful one: the moment
    # robocopy starts, files may already have left the install folder, and a
    # failure from here on has to put them back.
    $movedAside = $true
    $drained = $false
    for ($attempt = 1; $attempt -le 5 -and -not $drained; $attempt++) {
        $code = Invoke-Robocopy $InstallDir $backup $true
        if ($code -ge 8) { throw "Could not move the current version aside (robocopy $code)." }
        $drained = Test-Drained $InstallDir
        if (-not $drained) {
            # Robocopy retries a copy that fails, but not the delete that
            # follows it, so one briefly locked file leaves the folder
            # half-emptied. Waiting and repeating the whole move clears it.
            Write-Log ("Move left files behind; retrying (attempt " + $attempt + ").")
            Start-Sleep -Seconds 2
        }
    }
    if (-not $drained) { throw "The current version could not be moved aside." }

    $code = Invoke-Robocopy $source $InstallDir $true
    if ($code -ge 8) { throw "Could not put the new version in place (robocopy $code)." }
    $replaced = $true
    if (-not (Test-Path -LiteralPath (Join-Path $InstallDir $Executable) -PathType Leaf)) {
        throw "The installed folder is missing $Executable after the update."
    }

    Write-Log "Update applied. Restarting BlindPilot."
    Start-BlindPilot $InstallDir | Out-Null
    $handedOver = $true
    Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue
    Write-Log "Done."
} catch {
    $message = $_.Exception.Message
    Write-Log ("Update failed: " + $message)
    if (-not $handedOver -and $movedAside -and (Test-Path -LiteralPath $backup)) {
        Write-Log "Putting the previous version back."
        if ($replaced) {
            Get-ChildItem -LiteralPath $InstallDir -Force -ErrorAction SilentlyContinue |
                Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }
        # Copy the backup back rather than moving it. Whatever caused the
        # failure must not be able to consume the only remaining copy of the
        # version that was working a minute ago.
        $code = Invoke-Robocopy $backup $InstallDir $false
        if ($code -ge 8) {
            Write-Log ("Restore reported robocopy " + $code + "; the backup is kept at " + $backup)
        } else {
            Write-Log ("Previous version restored; its backup is kept at " + $backup)
        }
    }
    if (-not $handedOver) { Start-BlindPilot $InstallDir | Out-Null }
    Save-Failure $message
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Archive -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
    exit 1
}
Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $Archive -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $LogFile -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
"""
)


_WINDOWS_SETUP_HELPER = (
    r"""param(
    [Parameter(Mandatory=$true)][int]$ParentPid,
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$Executable,
    [Parameter(Mandatory=$true)][string]$LogFile,
    [Parameter(Mandatory=$true)][string]$StatusFile
)
"""
    + _WINDOWS_PRELUDE
    + r"""
function Get-InstallerFailure([int]$Code, [string]$SetupLog) {
    # The installer's own numbers mean nothing to the person being told. Only
    # the reason is worth reading aloud; the log path follows for a bug report.
    $reasons = @{
        1 = "the installer could not start"
        2 = "the installation was cancelled"
        3 = "the installer hit a fatal error while preparing to install"
        4 = "the installer hit a fatal error while installing"
        5 = "some of BlindPilot's files were still in use, so the installer stopped rather than replace them"
        6 = "the installer was terminated"
        7 = "the installer decided it could not run on this computer"
        8 = "the installer needs this computer to be restarted first"
    }
    $reason = $reasons[$Code]
    if (-not $reason) { $reason = "the installer failed with code $Code" }
    $text = "The update could not be installed: $reason."
    if ($SetupLog -and (Test-Path -LiteralPath $SetupLog -PathType Leaf)) {
        $text += " Its log is at $SetupLog."
    }
    return $text
}

if (-not (Enter-UpdateTurn)) {
    Write-Log "Another update is already running; leaving it to finish."
    Remove-Item -LiteralPath $Installer -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
    exit 0
}
$setupLog = [IO.Path]::ChangeExtension($LogFile, ".setup.log")
try {
    Write-Log ("Updating the installed copy in " + $InstallDir)
    if (-not (Wait-ForExit $ParentPid $InstallDir)) {
        throw "BlindPilot is still running, so the installer could not replace its files."
    }
    # The installer replaces the same files this checks, and it aborts rather
    # than wait for one that is briefly locked — by a virus scanner reading
    # what a process just released, most often. Better to wait here, where
    # waiting costs nothing, than to have the installer roll back.
    if (-not (Wait-Unlocked $InstallDir)) {
        throw "Some BlindPilot files are still in use, so the installer could not replace them."
    }

    # The setup program owns this installation: it keeps the uninstaller and
    # the Add or Remove Programs entry correct, and it remembers the shortcuts
    # chosen last time. Silent, because there is no one at the keyboard, and
    # pointed at the existing folder so an update never moves the application.
    #
    # /FORCECLOSEAPPLICATIONS matters as much as any of that. Without it the
    # installer asks anything holding our files to close, waits half a minute,
    # and — with message boxes suppressed — takes the silent default of Abort
    # when one of them does not. That is the exit code 5 that quietly undid
    # every update. Anything still holding a file here has already been asked
    # to close and refused, so the installer may close it outright.
    $run = Start-Process -FilePath $Installer -ArgumentList @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/NOCANCEL",
        "/CLOSEAPPLICATIONS",
        "/FORCECLOSEAPPLICATIONS",
        # Quoted by hand. Windows PowerShell joins this list with spaces and
        # quotes nothing, so a path with a space arrives as two arguments.
        ('/LOG="' + $setupLog + '"'),
        ('/DIR="' + $InstallDir + '"')
    ) -Wait -PassThru
    if ($run.ExitCode -ne 0) {
        throw (Get-InstallerFailure $run.ExitCode $setupLog)
    }
    Write-Log "Installer finished. Restarting BlindPilot."
    # The installer's own run entry is skipped when it runs silently, so
    # restarting is this script's job.
    if (-not (Start-BlindPilot $InstallDir)) {
        throw "BlindPilot was updated but could not be restarted."
    }
    Write-Log "Done."
} catch {
    $message = $_.Exception.Message
    Write-Log ("Update failed: " + $message)
    # The installer either succeeded or left the old copy alone, so there is
    # nothing to undo — but the user must not be left with no application.
    Start-BlindPilot $InstallDir | Out-Null
    Save-Failure $message
    Remove-Item -LiteralPath $Installer -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
    exit 1
}
Remove-Item -LiteralPath $Installer -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $LogFile -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $setupLog -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
"""
)


_MACOS_HELPER = r"""#!/bin/sh
# BlindPilot's macOS update helper.
#
# It runs after BlindPilot has closed, so it follows the same rules as the
# Windows helpers above: write down what happened, be sure nothing is still
# running before touching the bundle, put the previous version back when the
# swap fails, and never leave the user without an application to reopen. A Mac
# update used to do none of that -- it closed BlindPilot, and on any failure at
# all simply never came back and never said why.
set -u

parent_pid="$1"
archive="$2"
app_path="$3"
log_file="$4"
status_file="$5"

executable="$app_path/Contents/MacOS/BlindPilot"
stage=""
backup=""
lock=""
moved_aside=0
replaced=0

log() {
    printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >>"$log_file" 2>/dev/null || true
}

save_failure() {
    # BlindPilot is not running to be told, so the reason is left where its next
    # start will find it. This is the file report_failed_update reads.
    printf '%s\n%s\n' "$1" "$log_file" >"$status_file" 2>/dev/null || true
}

discard() {
    if [ -n "$stage" ]; then rm -rf "$stage"; fi
    if [ -n "$lock" ]; then rm -rf "$lock"; fi
    rm -f "$archive"
    rm -f "$0"
    return 0
}

start_blindpilot() {
    if [ ! -x "$executable" ]; then
        log "Cannot restart: $executable is missing."
        return 1
    fi
    # "open" is what puts BlindPilot back as a real application, with a Dock
    # icon and VoiceOver treating it as the frontmost app. It can still refuse a
    # bundle Launch Services holds stale information about, and being left with
    # no application at all is far worse than being left without a Dock icon.
    if open -n "$app_path" >/dev/null 2>&1; then
        return 0
    fi
    log "open refused the bundle; starting its executable instead."
    "$executable" >/dev/null 2>&1 &
    return 0
}

clear_quarantine() {
    target="$1"
    # A missing quarantine attribute makes xattr -d return an error, so the
    # delete itself cannot decide success. Remove what is present, then inspect
    # the whole bundle. If even one nested executable is still quarantined, an
    # unsigned update can install cleanly and still be refused at relaunch.
    xattr -dr com.apple.quarantine "$target" >>"$log_file" 2>&1 || true
    if xattr -lr "$target" 2>>"$log_file" | grep -q 'com\.apple\.quarantine:'; then
        return 1
    fi
    return 0
}

restore() {
    if [ "$moved_aside" -ne 1 ] || [ ! -d "$backup" ]; then
        return 0
    fi
    log "Putting the previous version back."
    if [ "$replaced" -eq 1 ]; then rm -rf "$app_path"; fi
    # Copy the backup back rather than moving it. Whatever caused the failure
    # must not be able to consume the only remaining copy of the version that
    # was working a minute ago.
    if ditto "$backup" "$app_path" >>"$log_file" 2>&1; then
        log "Previous version restored; its backup is kept at $backup"
    else
        log "Restore failed; the backup is kept at $backup"
    fi
    return 0
}

fail() {
    log "Update failed: $1"
    restore
    save_failure "$1"
    start_blindpilot
    discard
    exit 1
}

# Two BlindPilot windows check for updates independently, and two updaters over
# the same bundle are guaranteed to meet in the middle. Whoever gets here first
# does the update; the other steps aside. A lock left behind by an updater that
# died would block every update after it, so one nobody has touched for half an
# hour is taken over rather than obeyed.
lock_candidate="${TMPDIR:-/tmp}/BlindPilot-update-lock"
if mkdir "$lock_candidate" 2>/dev/null; then
    lock="$lock_candidate"
elif [ -z "$(find "$lock_candidate" -maxdepth 0 -mmin -30 2>/dev/null)" ]; then
    rm -rf "$lock_candidate"
    if mkdir "$lock_candidate" 2>/dev/null; then lock="$lock_candidate"; fi
fi
if [ -z "$lock" ]; then
    log "Another update is already running; leaving it to finish."
    rm -f "$archive"
    rm -f "$0"
    exit 0
fi

log "Updating $app_path"

waited=0
while kill -0 "$parent_pid" 2>/dev/null; do
    if [ "$waited" -ge 30 ]; then
        log "BlindPilot has not closed after 30 seconds; asking it to quit."
        kill -TERM "$parent_pid" 2>/dev/null || true
        sleep 2
        kill -KILL "$parent_pid" 2>/dev/null || true
        forced=0
        while kill -0 "$parent_pid" 2>/dev/null && [ "$forced" -lt 10 ]; do
            sleep 1
            forced=$((forced + 1))
        done
        if kill -0 "$parent_pid" 2>/dev/null; then
            fail "BlindPilot is still running, so its files could not be replaced."
        fi
        break
    fi
    sleep 1
    waited=$((waited + 1))
done

stage="$(mktemp -d "${TMPDIR:-/tmp}/BlindPilot-stage.XXXXXX" 2>/dev/null)" || stage=""
if [ -z "$stage" ]; then
    fail "A temporary folder for the update could not be created."
fi
if ! ditto -x -k "$archive" "$stage" >>"$log_file" 2>&1; then
    fail "The update archive could not be expanded."
fi
new_app="$stage/BlindPilot.app"
if [ ! -x "$new_app/Contents/MacOS/BlindPilot" ]; then
    fail "The update archive does not contain BlindPilot.app."
fi
# Anything that arrives in a downloaded archive can be quarantined, and
# Gatekeeper refuses to open a quarantined build that Apple has not notarised:
# the update would install and then die on "BlindPilot is damaged". The archive
# was checked against the release's published SHA-256 before this script was
# ever written, so its provenance is already settled by that verification.
if ! clear_quarantine "$new_app"; then
    fail "macOS would not allow the update to be prepared for automatic launch."
fi

backup="${app_path}.update-backup-$$"
rm -rf "$backup"
if ! mv "$app_path" "$backup" 2>>"$log_file"; then
    fail "The current version could not be moved aside."
fi
moved_aside=1
if ! ditto "$new_app" "$app_path" >>"$log_file" 2>&1; then
    fail "The new version could not be put in place."
fi
replaced=1
if [ ! -x "$executable" ]; then
    fail "The installed application has no BlindPilot in it after the update."
fi
if ! clear_quarantine "$app_path"; then
    fail "macOS would not allow the updated application to launch automatically."
fi

log "Update applied. Restarting BlindPilot."
start_blindpilot
rm -rf "$backup"
log "Done."
rm -f "$log_file"
discard
exit 0
"""


def macos_install_problem(app_path: Path) -> str:
    """Why this bundle cannot be replaced where it stands, or "" when it can.

    Both answers here are things no amount of retrying fixes, and both used to
    end the same way: BlindPilot closed to install an update and never came
    back. Saying so while there is still a window to say it in is the whole
    point of asking before the application quits.
    """
    if "/AppTranslocation/" in str(app_path):
        # Gatekeeper runs a quarantined application from a read-only copy it
        # makes somewhere in /private/var/folders. Replacing that copy updates
        # nothing: it is thrown away when the application quits, and the real
        # BlindPilot -- still sitting wherever it was unzipped -- stays old.
        return (
            "BlindPilot is running from a read-only copy macOS made because the "
            "application is still where it was unzipped. Drag BlindPilot to your "
            "Applications folder, open it from there, and check for updates again."
        )
    parent = app_path.parent
    if not os.access(parent, os.W_OK | os.X_OK) or not os.access(app_path, os.W_OK):
        return (
            f"This account is not allowed to change {app_path}. Move BlindPilot to a "
            "folder you can write to, such as the Applications folder inside your "
            "home folder, and check for updates again."
        )
    return ""


STATUS_FILE_NAME = "BlindPilot-update-status.txt"


def _status_path() -> Path:
    return Path(tempfile.gettempdir()) / STATUS_FILE_NAME


def pending_failure() -> tuple[str, str]:
    """(reason, log path) from an update that failed after BlindPilot closed.

    An update runs with no application to report to, so the helper writes the
    reason down and this is read on the next start. Returns empty strings when
    the last update did not fail.
    """
    try:
        # utf-8-sig, because Windows PowerShell writes UTF8 with a byte order mark.
        lines = _status_path().read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return "", ""
    reason = lines[0].strip() if lines else ""
    log = lines[1].strip() if len(lines) > 1 else ""
    return reason, log


def clear_pending_failure() -> None:
    _status_path().unlink(missing_ok=True)


def sweep_temporary_files(minimum_age_seconds: float = 6 * 60 * 60) -> int:
    """Delete abandoned update downloads and helpers, returning how many went.

    A failed update leaves its archive behind, and those are tens of megabytes
    each. Only files old enough to belong to a finished attempt are touched, so
    this can never delete the download of an update that is running right now.
    """
    removed = 0
    now = time.time()
    try:
        candidates = list(Path(tempfile.gettempdir()).glob(f"{TEMPORARY_PREFIX}*"))
    except OSError:
        return 0
    for path in candidates:
        if path.name == STATUS_FILE_NAME or path.suffix.casefold() == ".log":
            continue
        try:
            if not path.is_file() or now - path.stat().st_mtime < minimum_age_seconds:
                continue
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def _windows_helper_flags() -> int:
    """Creation flags for a helper that must outlive the process starting it.

    DETACHED_PROCESS must never be used here. It leaves PowerShell with no
    console at all, and Windows PowerShell then exits reporting success
    without running the script. CREATE_NO_WINDOW gives the helper a console
    of its own that is never shown.
    """
    return (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    )


def _spawn_detached(command: list[str], flags: int = 0) -> None:
    """Start a helper that has to outlive the BlindPilot starting it.

    ``creationflags`` is the Windows half and ``start_new_session`` the POSIX
    one: without a session of its own the helper is in BlindPilot's process
    group, and anything that signals that group on the way out takes the
    updater with it.
    """
    extra: dict[str, object] = (
        {"creationflags": flags} if platform.system() == "Windows" else {"start_new_session": True}
    )
    # The platform split above makes `extra` a dict of mixed value types,
    # which no Popen overload accepts as **kwargs.
    subprocess.Popen(  # type: ignore[call-overload]
        command,
        # The helper outlives BlindPilot, so it is left holding nothing of it.
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        cwd=tempfile.gettempdir(),
        **extra,
    )


def _start_windows_helper(command: list[str]) -> None:
    flags = _windows_helper_flags()
    try:
        _spawn_detached(command, flags)
    except OSError:
        # Breaking out of a job object is refused when the job forbids it, and
        # BlindPilot can be started from inside one. Staying in the job is
        # better than not updating at all.
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        if not breakaway:
            raise
        _spawn_detached(command, flags & ~breakaway)


def schedule_install(archive: Path) -> None:
    """Install the verified archive after this packaged process exits."""
    if not getattr(sys, "frozen", False):
        raise UpdateError("Automatic installation is available in packaged builds only.")
    executable = Path(sys.executable).resolve()
    if platform.system() == "Windows":
        install_dir = executable.parent
        log = Path(tempfile.gettempdir()) / f"{TEMPORARY_PREFIX}{os.getpid()}.log"
        shared = [
            "-InstallDir",
            str(install_dir),
            "-Executable",
            executable.name,
            "-LogFile",
            str(log),
            "-StatusFile",
            str(_status_path()),
        ]
        if install_kind(executable) == INSTALL_SETUP:
            script = _WINDOWS_SETUP_HELPER
            arguments = ["-Installer", str(archive), *shared]
        else:
            script = _WINDOWS_HELPER
            arguments = ["-Archive", str(archive), *shared]
        # A stale reason from a previous attempt must not be reported as this
        # one's outcome.
        clear_pending_failure()
        helper: Optional[Path] = None
        try:
            fd, helper_name = tempfile.mkstemp(prefix=TEMPORARY_PREFIX, suffix=".ps1")
            os.close(fd)
            helper = Path(helper_name)
            helper.write_text(script, encoding="utf-8-sig")
            powershell = os.environ.get("SystemRoot", r"C:\Windows")
            powershell = str(
                Path(powershell) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            )
            _start_windows_helper(
                [
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-WindowStyle",
                    "Hidden",
                    "-File",
                    str(helper),
                    "-ParentPid",
                    str(os.getpid()),
                    *arguments,
                ]
            )
        except (OSError, ValueError) as exc:
            if helper is not None:
                helper.unlink(missing_ok=True)
            raise UpdateError(f"Could not start the update installer: {exc}") from exc
        return
    if platform.system() == "Darwin":
        app_path = next(
            (parent for parent in executable.parents if parent.suffix == ".app"),
            None,
        )
        if app_path is None:
            raise UpdateError("BlindPilot is not running from an application bundle.")
        # Asked here, while there is still a window to answer into. Both of the
        # things this catches make the swap impossible, and finding that out in
        # the helper means finding it out after BlindPilot has already closed.
        problem = macos_install_problem(app_path)
        if problem:
            raise UpdateError(problem)
        log = Path(tempfile.gettempdir()) / f"{TEMPORARY_PREFIX}{os.getpid()}.log"
        # A stale reason from a previous attempt must not be reported as this
        # one's outcome.
        clear_pending_failure()
        helper = None
        try:
            fd, helper_name = tempfile.mkstemp(prefix=TEMPORARY_PREFIX, suffix=".sh")
            os.close(fd)
            helper = Path(helper_name)
            helper.write_text(_MACOS_HELPER, encoding="utf-8")
            helper.chmod(0o700)
            _spawn_detached(
                [
                    "/bin/sh",
                    str(helper),
                    str(os.getpid()),
                    str(archive),
                    str(app_path),
                    str(log),
                    str(_status_path()),
                ]
            )
        except (OSError, ValueError) as exc:
            if helper is not None:
                helper.unlink(missing_ok=True)
            raise UpdateError(f"Could not start the update installer: {exc}") from exc
        return
    raise UpdateError("Automatic installation is not supported on this platform.")
