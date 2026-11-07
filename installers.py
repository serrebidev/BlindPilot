"""Installing and updating the provider CLIs BlindPilot drives.

Every backend is installed one of three ways: through its own official script
installer (Claude Code, Hermes, Muse, uv), through npm (Codex, FreeBuff,
opencode, Command Code, Gemini, Copilot), or through Antigravity's own
installer. Which one applies is a table, not a branch, so a missing entry is a
``KeyError`` rather than npm being tried on a backend that never ships there.

Split out of ``blindpilot_app.py`` (issue #34) so the window calls the installer
instead of defining it. The window reaches these as ``installers.install_backend``
and the like, so it keeps no re-export: the names are gone from blindpilot_app
rather than aliased. The URLs, the flags, the messages and the PATH effects are
unchanged by the move.

Nothing here needs the window: this module reads no application state, opens no
dialog and announces nothing. It takes a ``log`` callable and returns the
executable it found, which is what lets it be tested without a display.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Callable, List, Optional, Sequence

import backend_pool
import install_path
import process_utils
import portable_node
from agent_backends import (
    BACKENDS,
    BACKEND_ANTIGRAVITY,
    BACKEND_CLAUDE,
    BACKEND_CODEX,
    BACKEND_COMMANDCODE,
    BACKEND_COPILOT,
    BACKEND_FREEBUFF,
    BACKEND_GEMINI,
    BACKEND_HERMES,
    BACKEND_MUSE,
    BACKEND_MUSEAI,
    BACKEND_OPENCODE,
    FREEBUFF_PREFERRED_MODEL,
    backend_label,
    find_backend_cli,
    freebuff_model_options,
    invalidate_backend_cache,
    normalize_backend,
    set_freebuff_model,
    stop_opencode_server,
    subprocess_env,
)
from claude_session import _find_claude, _native_bin_dir
from muse_backend import (
    muse_cli_path,
    muse_installed,
    reset_discovery as reset_muse_discovery,
    wsl_exe as muse_wsl_exe,
)
from app_updater import version_tuple
from process_utils import no_window_kwargs

# ---------------------------------------------------------------------------
# Installing Claude Code, and making it visible to every shell.
#
# Both official native installers are documented at code.claude.com/docs/en/setup.
# Neither needs administrator rights or Node.js: they drop a real binary in
# ~/.local/bin and self-update from then on.
# ---------------------------------------------------------------------------

WINDOWS_INSTALL_PS1_URL = "https://claude.ai/install.ps1"
POSIX_INSTALL_SH_URL = "https://claude.ai/install.sh"

# Hermes ships per-platform script installers of the same shape - a PowerShell
# one-liner on native Windows, curl through bash on macOS, Linux and WSL2 -
# and is not on npm. It drops a launcher under the user's home directory and
# self-updates from then on; no administrator rights, no Node.js.
HERMES_INSTALL_PS1_URL = "https://hermes-agent.nousresearch.com/install.ps1"
HERMES_INSTALL_SH_URL = "https://hermes-agent.nousresearch.com/install.sh"


def _powershell_exe() -> Optional[str]:
    """Windows PowerShell first — it ships with Windows 11, pwsh may not."""
    for name in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
        found = process_utils.which_without_cwd(name)
        if found:
            return found
    return None


def _script_installer_argv(powershell_command: str, posix_url: str) -> Optional[List[str]]:
    """The command that runs an official script installer on this platform.

    None when the prerequisites are missing: no PowerShell on Windows, no curl
    or shell on macOS and Linux. macOS ships both curl and bash; a Linux box
    without curl is possible.
    """
    if platform.system() == "Windows":
        shell = _powershell_exe()
        if shell is None:
            return None
        return [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            powershell_command,
        ]
    if process_utils.which_without_cwd("curl") is None:
        return None
    shell = process_utils.which_without_cwd("bash") or process_utils.which_without_cwd("sh")
    if shell is None:
        return None
    return [shell, "-c", f"curl -fsSL {posix_url} | bash"]


def _install_argv() -> Optional[List[str]]:
    """Claude Code's official native installer for this platform."""
    return _script_installer_argv(f"irm {WINDOWS_INSTALL_PS1_URL} | iex", POSIX_INSTALL_SH_URL)


def _hermes_install_argv() -> Optional[List[str]]:
    """Hermes' official installer for this platform."""
    return _script_installer_argv(f"iex (irm {HERMES_INSTALL_PS1_URL})", HERMES_INSTALL_SH_URL)


def _missing_prereq_message(installer: str = "the installer") -> str:
    missing = "PowerShell" if platform.system() == "Windows" else "curl and bash"
    return f"Could not find {missing} on this computer, so {installer} cannot be run automatically."


def _hermes_missing_prereq_message() -> str:
    return _missing_prereq_message("Hermes' installer")


# Where uv -- the tool Hermes' installer bootstraps a Python with -- keeps the
# interpreters it downloads. Measured with ``uv python dir`` on a healthy
# machine: %APPDATA%\\uv\\python, the ROAMING folder. Roaming is also the one
# folder a roaming profile or a cloud-sync client takes over, and Windows
# refuses to walk through a folder it does not trust when an executable is
# about to live under it. uv then dies at "Downloading Python" with
# "Failed to create Python minor version link directory ... (os error 448)" --
# ERROR_TRUSTING_PATH -- while the installer around it still exits 0.
UV_PYTHON_DIR_ENV = "UV_PYTHON_INSTALL_DIR"


def _uv_roaming_python_dir() -> Path:
    """Where uv would put its managed Pythons if nothing were configured."""
    roaming = os.environ.get("APPDATA", "").strip()
    base = Path(roaming) if roaming else Path.home() / "AppData" / "Roaming"
    return base / "uv" / "python"


def _uv_local_python_dir() -> Path:
    """The same folder under local app data, where uv keeps its cache."""
    local = os.environ.get("LOCALAPPDATA", "").strip()
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "uv" / "python"


def _windows_file_attributes(directory: Path) -> Optional[int]:
    """The Windows ``st_file_attributes`` of a folder, or None when it cannot be asked."""
    try:
        return getattr(os.stat(directory), "st_file_attributes", 0)
    except OSError:
        return None


def _folder_is_behind_reparse_point(directory: Path) -> bool:
    """Whether a step on the way to *directory* is a reparse point.

    That is what a cloud-sync placeholder or a roaming redirect looks like to
    Windows, and it is what makes it refuse to place a program underneath.
    Only folders that already exist can be asked, so the walk stops at the
    first one that does not -- a path uv has not created yet is judged by the
    deepest part of it that exists.
    """
    current = directory
    while True:
        attributes = _windows_file_attributes(current)
        if attributes is None:
            return False
        if attributes & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _folder_is_cloud_synced(directory: Path) -> bool:
    """Whether *directory* sits inside a folder a sync client owns.

    Two signals, because each alone misses a machine: the reparse attribute
    Windows puts on the synced folder, and OneDrive's own recorded roots, which
    name the tree even where nothing has been turned into a placeholder yet.
    """
    if _folder_is_behind_reparse_point(directory):
        return True
    try:
        candidate = os.path.normcase(os.path.abspath(str(directory)))
    except OSError:
        return False
    for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        root = os.environ.get(name, "").strip()
        if not root:
            continue
        try:
            prefix = os.path.normcase(os.path.abspath(root)) + os.sep
        except OSError:
            continue
        if candidate.startswith(prefix):
            return True
    return False


def _remember_windows_env_var(name: str, value: str) -> None:
    """Set one user environment variable for every program started later.

    ``HKCU\\Environment`` is the registry twin of ``setx``, read by cmd,
    PowerShell and Windows Terminal when they start. Raises OSError when the
    write fails.
    """
    import winreg  # after the platform check -- the module is Windows-only

    os.environ[name] = value
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Environment",
        0,
        winreg.KEY_READ | winreg.KEY_WRITE,
    ) as key:
        try:
            current, regtype = winreg.QueryValueEx(key, name)
        except OSError:
            current, regtype = None, winreg.REG_EXPAND_SZ
        if current == value:
            return
        if regtype not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
            regtype = winreg.REG_EXPAND_SZ
        winreg.SetValueEx(key, name, 0, regtype, value)
    install_path._broadcast_environment_change()


def _hermes_installer_env(log: Callable[[str], None]) -> Optional[dict[str, str]]:
    """The environment Hermes' installer runs in, or None to change nothing.

    Its Python bootstrap is uv's, and uv's default home for interpreters is the
    roaming folder (see ``UV_PYTHON_DIR_ENV``). On the machines that report
    ``os error 448`` that folder sits behind a cloud-sync placeholder, so the
    interpreters are sent to local app data instead -- where uv already keeps
    its cache, and which no sync client owns. Asked for on every Windows
    install rather than only when the check below agrees, because a synced
    folder is not always recognisable from here and a wrong guess costs a
    folder, while a missed one costs the install.

    A location the user configured themselves is left exactly as it is.
    """
    if platform.system() != "Windows":
        return None
    if os.environ.get(UV_PYTHON_DIR_ENV, "").strip():
        return None
    local = _uv_local_python_dir()
    if _folder_is_cloud_synced(local):
        log(
            "Both of the usual folders for Hermes' Python sit inside a folder that a "
            "cloud-sync program owns, and Windows will not put a program there. Set "
            "UV_PYTHON_INSTALL_DIR to a folder outside every synced folder, then try again."
        )
        return None
    if _folder_is_cloud_synced(_uv_roaming_python_dir()):
        # Remembered, not just handed to this one installer run: the same
        # bootstrap runs again whenever Hermes is updated from a terminal, and
        # an environment variable BlindPilot set for one child process does not
        # reach that one.
        try:
            _remember_windows_env_var(UV_PYTHON_DIR_ENV, str(local))
            log(
                "Your roaming AppData is inside a synced folder, which Windows will not "
                f"put Hermes' Python in. It will go in {local} instead, and Windows has "
                "been told so that Hermes' own updater finds it again."
            )
        except OSError as exc:
            log(
                "Hermes' Python will go in the local app-data folder for this install, "
                f"but Windows could not be told to keep doing so: {exc}"
            )
    return {UV_PYTHON_DIR_ENV: str(local)}


def _hermes_bootstrap_failure(lines: Sequence[str]) -> str:
    """What to hear when Hermes' installer left no Python and no clear reason.

    uv's own sentence for a folder it is not allowed through is localised -- on
    a Polish machine it begins "Nie można przejść do tej ścieżki" -- and the
    installer exits 0 regardless, so the only stable text in the whole log is
    the error number and the name of the step that failed. Both are looked for
    here, because "exit code 0 but hermes was not found" describes nothing a
    user can act on.
    """
    text = "\n".join(lines).lower()
    if "os error 448" not in text and "minor version link" not in text:
        return ""
    return (
        "Windows refused to put the Python Hermes needs inside a folder owned by a "
        "cloud-sync program. BlindPilot uses the local app-data folder for it instead; if "
        "this still fails, set UV_PYTHON_INSTALL_DIR to a folder outside every synced "
        "folder and try again."
    )


def _hermes_binary_after_install() -> Optional[str]:
    """The Hermes launcher after an install, with its install dirs on PATH.

    The installers drop the launcher under the user's home, ``~/.local/bin``
    on POSIX and ``LOCALAPPDATA\\hermes\\bin`` on Windows, so that directory
    is put on this process' PATH before the search.
    """
    from hermes_backend import find_hermes_cli

    local_bin = Path.home() / ".local" / "bin"
    if local_bin.is_dir():
        install_path._add_to_process_path(local_bin)
    if platform.system() == "Windows":
        # Measured on the machine the installer ran on: the launcher lands in
        # %LOCALAPPDATA%\hermes\bin, beside the managed uv, and the
        # installer's own "PATH already configured" only helps shells started
        # afterwards -- not this process, which was running during the
        # install.
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        hermes_bin = local / "hermes" / "bin"
        if hermes_bin.is_dir():
            install_path._add_to_process_path(hermes_bin)
    return find_hermes_cli()


def install_hermes(log: Callable[[str], None]) -> Optional[str]:
    """Run Hermes' official installer for this platform and report the result.

    The same discipline as install_claude: the installer's exit code is
    advisory, and a working `hermes` afterwards is the fact that counts.
    """
    argv = _hermes_install_argv()
    if argv is None:
        log(_hermes_missing_prereq_message())
        return None

    log(
        "Downloading and running the official Hermes installer. This usually takes a minute or two."
    )
    heard: List[str] = []

    def logged(line: str) -> None:
        heard.append(line)
        log(line)

    rc = portable_node._run_logged_process(argv, logged, env=_hermes_installer_env(log))
    if rc is None:
        return None

    binary = _hermes_binary_after_install()
    if binary is None:
        log(f"The installer finished with exit code {rc} but `hermes` was not found afterwards.")
        problem = _hermes_bootstrap_failure(heard)
        if problem:
            log(problem)
        return None

    log(f"Installed: {binary}")
    folder = Path(binary).parent
    try:
        changed = install_path.ensure_on_path(folder)
        if changed:
            log(f"Added {folder} to {changed}.")
    except OSError as exc:
        log(f"Installed, but adding it to PATH failed: {exc}")
    return binary


MUSE_INSTALL_SH_URL = "https://dev.meta.ai/install.sh"

# uv's official installers. BlindPilot installs uv itself when a backend's
# Python tooling is needed on a computer that has neither uv nor a Python:
# a fresh computer has neither, and without a bootstrap the muse.ai install
# could only tell the user to go and fetch the tooling by hand.
UV_INSTALL_PS1_URL = "https://astral.sh/uv/install.ps1"
UV_INSTALL_SH_URL = "https://astral.sh/uv/install.sh"

# Antigravity CLI ships Google's own per-platform script installers (not npm):
# a native agy binary in ~/.local/bin, or %LOCALAPPDATA%\agy\bin on Windows,
# that updates itself from then on.
ANTIGRAVITY_INSTALL_PS1_URL = "https://antigravity.google/cli/install.ps1"
ANTIGRAVITY_INSTALL_SH_URL = "https://antigravity.google/cli/install.sh"


def install_antigravity(log: Callable[[str], None]) -> Optional[str]:
    """Run Google's Antigravity CLI installer and report the result.

    As with the other script installers, a working `agy` afterwards is the
    fact that counts, not the installer's exit code.
    """
    argv = _script_installer_argv(
        f"irm {ANTIGRAVITY_INSTALL_PS1_URL} | iex", ANTIGRAVITY_INSTALL_SH_URL
    )
    if argv is None:
        log(_missing_prereq_message("Antigravity's installer"))
        return None
    log(
        "Downloading and running the official Antigravity CLI installer. This usually takes under a minute."
    )
    rc = portable_node._run_logged_process(argv, log)
    if rc is None:
        return None
    binary = find_backend_cli(BACKEND_ANTIGRAVITY)
    if binary is None:
        log(f"The installer finished with exit code {rc} but `agy` was not found afterwards.")
        return None
    log(f"Installed: {binary}")
    try:
        changed = install_path.ensure_on_path(Path(binary).parent)
        if changed:
            log(f"Added {Path(binary).parent} to {changed}.")
    except OSError as exc:
        log(f"Installed, but adding it to PATH failed: {exc}")
    return binary


def _muse_install_argv() -> Optional[List[str]]:
    """Muse's official installer, run wherever Muse can actually live.

    Muse's CLI ships for macOS and Linux only, so on Windows there is no
    PowerShell one-liner to run natively: the install happens inside WSL, if
    WSL is here, and cannot happen at all if it is not.
    """
    if platform.system() == "Windows":
        # The only prerequisite is WSL itself. This used to also require
        # Muse to be installed already, which made a first install on a
        # fresh computer impossible: the installer was refused exactly
        # when there was nothing installed yet.
        launcher = muse_wsl_exe()
        if launcher is None:
            return None
        # curl inside the distribution, bash inside it: the script is
        # POSIX-only, so running it from Windows is not an option to fall
        # back to.
        return [launcher, "-e", "bash", "-c", f"curl -fsSL {MUSE_INSTALL_SH_URL} | bash"]
    if process_utils.which_without_cwd("curl") is None:
        return None
    shell = process_utils.which_without_cwd("bash") or process_utils.which_without_cwd("sh")
    if shell is None:
        return None
    return [shell, "-c", f"curl -fsSL {MUSE_INSTALL_SH_URL} | bash"]


def _muse_missing_prereq_message() -> str:
    if platform.system() == "Windows":
        return (
            "Muse Code runs on macOS and Linux, so on Windows it is installed "
            "inside WSL -- and WSL with a working distribution was not found on "
            "this computer. Install WSL first, then try again."
        )
    return _missing_prereq_message("Muse's installer")


def install_muse(log: Callable[[str], None]) -> Optional[str]:
    """Run Muse's official installer for this platform and report the result.

    Same discipline as install_hermes: the installer's exit code is advisory,
    and a working `muse` afterwards is the fact that counts. On Windows that
    launcher lives inside WSL, so discovery is reset before the check -- the
    first probe's answer predates the install and must not outlive it.
    """
    argv = _muse_install_argv()
    if argv is None:
        log(_muse_missing_prereq_message())
        return None

    log(
        "Downloading and running the official Muse Code installer. "
        "This usually takes under a minute."
    )
    rc = portable_node._run_logged_process(argv, log)
    if rc is None:
        return None

    reset_muse_discovery()
    if not muse_installed():
        log(f"The installer finished with exit code {rc} but `muse` was not found afterwards.")
        return None
    path = muse_cli_path()
    log(f"Installed: {path}")
    return path


def _path_shells() -> str:
    """The shells worth naming when telling the user to open a new terminal."""
    if platform.system() == "Windows":
        return "cmd, PowerShell, pwsh and Windows Terminal"
    return "Terminal and iTerm"


def install_claude(log: Callable[[str], None]) -> Optional[str]:
    """Run the official native installer for this platform and put it on PATH.

    Streams installer output line by line to *log* (so the caller can show and
    speak progress) and returns the path to the installed binary, or None if
    the install did not produce a working `claude`.
    """
    argv = _install_argv()
    if argv is None:
        log(_missing_prereq_message())
        return None

    log("Downloading and running the Claude Code installer. This usually takes under a minute.")
    rc = portable_node._run_logged_process(argv, log)
    if rc is None:
        return None

    # The installer's own exit code is advisory. What matters is whether a
    # working binary exists afterwards, so look before reporting failure.
    install_path._add_to_process_path(_native_bin_dir())
    binary = _find_claude()
    if binary is None:
        log(f"The installer finished with exit code {rc} but `claude` was not found afterwards.")
        return None

    log(f"Installed: {binary}")
    folder = Path(binary).parent
    try:
        changed = install_path.ensure_on_path(folder)
        if changed:
            log(
                f"Added {folder} to {changed}. Open a new terminal window for "
                f"{_path_shells()} to see it."
            )
        else:
            log(f"Already on your PATH. `claude` will work in {_path_shells()}.")
    except OSError as exc:
        log(f"Installed, but adding it to PATH failed: {exc}")
    return binary


def _install_failure_message(backend: str) -> str:
    """What to hear when an install did not complete: the command, unspliced."""
    info = BACKENDS[normalize_backend(backend)]
    return (
        "The install did not complete. Read the installer output, or install "
        f"{info.label} yourself. {info.install_command} Then click Check Again."
    )


def _install_uv(log: Callable[[str], None]) -> bool:
    """Install uv with its official installer. True when uv exists afterwards.

    The installer's exit code is advisory, like the other script
    installers: a `uv` that discovery can find afterwards is the fact
    that counts.
    """
    from museai_backend import find_uv

    argv = _script_installer_argv(f"irm {UV_INSTALL_PS1_URL} | iex", UV_INSTALL_SH_URL)
    if argv is None:
        return False
    log("Installing uv, the tool manager muse-cli is installed with...")
    if portable_node._run_logged_process(argv, log) is None:
        return False
    return find_uv() is not None


def install_museai(log: Callable[[str], None], upgrade: bool = False) -> Optional[str]:
    """Install or upgrade muse-cli, the client muse.ai is driven through.

    It is a Python package, so uv installs it as a tool (its own environment,
    a launcher on PATH) and pip for this user is the fallback.
    """
    from museai_backend import MUSEAI_MISSING_PREREQ, museai_install_argv

    label = backend_label(BACKEND_MUSEAI)
    argv = museai_install_argv(upgrade=upgrade)
    if argv is None:
        # Neither uv nor a usable Python: a fresh computer. Install uv with
        # its official installer -- the same script-installer pattern the
        # other backends use -- then ask again.
        if _install_uv(log):
            argv = museai_install_argv(upgrade=upgrade)
    if argv is None:
        log(MUSEAI_MISSING_PREREQ)
        return None
    log(f"{'Updating' if upgrade else 'Installing'} {label}'s command line, muse-cli...")
    rc = portable_node._run_logged_process(argv, log)
    binary = find_backend_cli(BACKEND_MUSEAI)
    if binary is None:
        log(f"muse-cli was not found afterwards (exit code {rc}).")
        return None
    log(f"muse-cli is ready at {binary}. Sign in with muse-cli auth export.")
    return binary


# How each backend is installed, in one place. The npm-backed ones share one
# installer; the rest have their own because their official install path is
# their own. A missing entry is a KeyError here rather than npm being tried on
# a backend that never ships there.

Installer = Callable[[Callable[[str], None]], Optional[str]]


def _install_npm_backend(backend: str, log: Callable[[str], None]) -> Optional[str]:
    """Install the backend's npm package and verify its launcher starts."""
    label = backend_label(backend)
    npm = portable_node._find_npm()
    if npm is None:
        npm = portable_node.install_portable_node(log)
    if npm is None:
        log(f"npm could not be installed, so BlindPilot cannot install {label} automatically.")
        return None
    argv = portable_node._npm_install_argv(backend)
    if argv is None:
        log(f"npm could not be installed, so BlindPilot cannot install {label} automatically.")
        return None
    if backend == BACKEND_CODEX:
        _drop_codex_server(log)
    if backend == BACKEND_OPENCODE:
        # Same reason as an update: npm cannot replace a running executable.
        stop_opencode_server()
    log(f"Installing {label} with npm. This can take a minute.")
    rc = portable_node._run_logged_process(argv, log, env=portable_node._npm_environment(npm))
    if rc is None:
        return None
    install_path._add_to_process_path(portable_node._managed_npm_bin_dir())
    binary = portable_node._managed_backend_binary(backend) or find_backend_cli(backend)
    if binary is None:
        log(f"npm finished with exit code {rc}, but {label} was not found afterwards.")
        return None
    try:
        changed = install_path.ensure_on_path(Path(binary).parent)
        if changed:
            log(f"Added {Path(binary).parent} to {changed}.")
    except OSError as exc:
        log(f"Installed, but adding it to PATH failed: {exc}")

    # Freebuff's npm package is only a launcher. Asking every backend for its
    # version both verifies it can start and makes Freebuff download and verify
    # the native binary before the setup wizard advances to sign-in.
    log(f"Verifying the {label} installation...")
    verify_rc = portable_node._run_logged_process(
        [binary, "--version"], log, env=portable_node._npm_environment(npm)
    )
    if verify_rc is None:
        return None
    if verify_rc != 0:
        log(f"{label} was installed but failed its startup check (exit code {verify_rc}).")
        return None
    log(f"Installed and verified: {binary}")
    return binary


INSTALLERS: dict[str, Installer] = {
    # Lambdas rather than the functions themselves: a test (and any future
    # caller) replaces `install_hermes` in this module's namespace, and the
    # table must call what is there now, not what was there at import.
    BACKEND_CLAUDE: lambda log: install_claude(log),
    BACKEND_HERMES: lambda log: install_hermes(log),
    BACKEND_MUSE: lambda log: install_muse(log),
    BACKEND_MUSEAI: lambda log: install_museai(log),
    BACKEND_ANTIGRAVITY: lambda log: install_antigravity(log),
    BACKEND_CODEX: lambda log: _install_npm_backend(BACKEND_CODEX, log),
    BACKEND_FREEBUFF: lambda log: _install_npm_backend(BACKEND_FREEBUFF, log),
    BACKEND_OPENCODE: lambda log: _install_npm_backend(BACKEND_OPENCODE, log),
    BACKEND_COMMANDCODE: lambda log: _install_npm_backend(BACKEND_COMMANDCODE, log),
    BACKEND_GEMINI: lambda log: _install_npm_backend(BACKEND_GEMINI, log),
    BACKEND_COPILOT: lambda log: _install_npm_backend(BACKEND_COPILOT, log),
}


def install_backend(backend: str, log: Callable[[str], None]) -> Optional[str]:
    """Install one selected backend and return its discovered executable."""
    installer = INSTALLERS.get(normalize_backend(backend))
    if installer is None:
        raise KeyError(f"no installer for {backend}")
    return installer(log)


def _executable_version(binary: str) -> str:
    """Return one provider executable's own version text."""
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            stdin=subprocess.DEVNULL,
            env=subprocess_env(binary),
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return ((result.stdout or "") + (result.stderr or "")).strip()


def _repair_claude_native_update(binary: str, log: Callable[[str], None]) -> bool:
    """Make the Windows launcher use the newest downloaded Claude version."""
    if platform.system() != "Windows" or Path(binary).suffix.lower() != ".exe":
        # An npm `claude.cmd` shim is not the native launcher; a PE image
        # copied over it would break it.
        return True
    versions = Path.home() / ".local" / "share" / "claude" / "versions"
    try:
        candidates = [
            path for path in versions.iterdir() if path.is_file() and version_tuple(path.name)
        ]
    except OSError:
        return True
    if not candidates:
        return True
    newest = max(candidates, key=lambda path: version_tuple(path.name))
    current_version = version_tuple(_executable_version(binary))
    newest_version = version_tuple(newest.name)
    if not newest_version or newest_version <= current_version:
        return True
    try:
        shutil.copy2(newest, binary)
    except OSError as exc:
        log(f"Claude downloaded {newest.name}, but its launcher could not be updated: {exc}")
        return False
    verified = version_tuple(_executable_version(binary))
    if verified < newest_version:
        log("Claude's launcher still reports an older version after updating.")
        return False
    log(f"Activated Claude Code {newest.name} in the launcher.")
    return True


def _drop_codex_server(log: Callable[[str], None]) -> None:
    """Let go of every held Codex app-server before npm replaces its executable.

    Windows will not overwrite an executable that is running. Codex may be held
    per ChatGPT-plan Account, so this reaches every Account's process: leaving
    one behind fails the update exactly as one left behind used to.
    """
    log("Stopping Codex's app-server so its executable can be replaced...")
    backend_pool.pool().drop_backend(BACKEND_CODEX)


# How each backend is updated, in one place. The npm-backed ones share one
# path; Hermes and Muse upgrade through their own installers and measure
# success by the launcher afterwards, which is why they are their own flows
# rather than an argv each.

Updater = Callable[[str, Callable[[str], None]], bool]


def _update_claude(binary: str, log: Callable[[str], None]) -> bool:
    label = backend_label(BACKEND_CLAUDE)
    argv = [binary, "update"]
    log(f"Checking for {label} updates...")
    rc = portable_node._run_logged_process(argv, log)
    if rc != 0:
        log(f"{label} update exited with code {rc}.")
        return False
    if not _repair_claude_native_update(binary, log):
        return False
    log(f"{label} is up to date.")
    return True


def _update_npm_backend(backend: str, binary: str, log: Callable[[str], None]) -> bool:
    label = backend_label(backend)
    # Read before the update replaces the launcher: the model list this
    # backend is set to is restored afterwards, because an upgrade can drop it.
    previous_freebuff_model = ""
    if backend == BACKEND_FREEBUFF:
        _models, previous_freebuff_model, _error = freebuff_model_options()
    if portable_node._find_npm() is None and portable_node.install_portable_node(log) is None:
        log(f"npm could not be installed, so BlindPilot cannot update {label} automatically.")
        return False
    argv = portable_node._npm_update_argv(backend)
    if argv is None:
        log(f"npm could not be found, so BlindPilot cannot update {label} automatically.")
        return False
    if backend == BACKEND_CODEX:
        _drop_codex_server(log)
    if backend == BACKEND_OPENCODE:
        # The server BlindPilot has been talking to *is* the executable npm is
        # about to replace, and Windows will not overwrite one that is running.
        # It is started again by the next thing that needs it.
        log("Stopping opencode's server so its executable can be replaced...")
        stop_opencode_server()
    log(f"Checking for {label} updates...")
    npm = portable_node._find_npm()
    rc = portable_node._run_logged_process(
        argv, log, env=portable_node._npm_environment(npm) if npm else None
    )
    if rc != 0:
        log(f"{label} update exited with code {rc}.")
        return False
    install_path._add_to_process_path(portable_node._managed_npm_bin_dir())
    managed_binary = portable_node._managed_backend_binary(backend)
    if managed_binary is None:
        log(f"{label} updated, but its executable was not found afterwards.")
        return False
    try:
        changed = install_path.ensure_on_path(Path(managed_binary).parent)
        if changed:
            log(f"Added {Path(managed_binary).parent} to {changed}.")
    except OSError as exc:
        log(f"{label} updated, but adding it to PATH failed: {exc}")
    verify_rc = portable_node._run_logged_process(
        [managed_binary, "--version"], log, env=portable_node._npm_environment(npm or "")
    )
    if verify_rc is None:
        return False
    if verify_rc != 0:
        log(f"{label} updated but failed its startup check (exit code {verify_rc}).")
        return False
    if backend == BACKEND_FREEBUFF:
        invalidate_backend_cache(BACKEND_FREEBUFF)
        models, _current, _error = freebuff_model_options()
        selected = (
            previous_freebuff_model
            if previous_freebuff_model in models
            else FREEBUFF_PREFERRED_MODEL
            if FREEBUFF_PREFERRED_MODEL in models
            else models[0]
            if models
            else FREEBUFF_PREFERRED_MODEL
        )
        try:
            set_freebuff_model(selected)
        except OSError as exc:
            log(f"{label} updated, but its model selection could not be restored: {exc}")
            return False
    log(f"{label} is up to date.")
    return True


def _update_hermes(binary: str, log: Callable[[str], None]) -> bool:
    # Not on npm: its official installer is the update path too. It upgrades in
    # place, and success is measured by the launcher afterwards, not the exit
    # code.
    label = backend_label(BACKEND_HERMES)
    hermes_argv = _hermes_install_argv()
    if hermes_argv is None:
        log(_hermes_missing_prereq_message())
        return False
    # The iex'd PowerShell installer never exits on its own -- install.ps1 says
    # so deliberately, so it cannot close the user's window -- so the process
    # exit code is whatever the last native command inside the installer
    # returned, and a negative one arrives unsigned: a friend's "exited with
    # code 4294901760" is plain -65536, measured against this machine's
    # PowerShell. The installer's own failure guard (assigning
    # $global:LASTEXITCODE, not exiting) does not reach the process code either.
    # The exit code says nothing about the update; the version the launcher
    # reports afterwards is the only fact that does.
    before = version_tuple(_executable_version(binary))
    log(f"Running the official {label} installer to update...")
    rc = portable_node._run_logged_process(hermes_argv, log, env=_hermes_installer_env(log))
    if rc is None:
        return False
    found = _hermes_binary_after_install()
    if found is None:
        if rc != 0:
            log(f"{label} update exited with code {rc}.")
        log(f"{label} update finished, but `hermes` was not found afterwards.")
        return False
    text = _executable_version(found)
    if version_tuple(text) > before:
        log(f"{label} updated. It now reports {text}.")
    elif rc != 0:
        log(
            f"{label} is still at its current version and the installer did not "
            "finish cleanly, so the update may not have completed. Read the "
            "installer output above, or try the update again -- running it "
            "again is safe."
        )
        return False
    log(f"{label} is up to date.")
    return True


def _update_muse(binary: str, log: Callable[[str], None]) -> bool:
    # Same shape as Hermes: the official script installer upgrades in place,
    # and success is a working launcher afterwards -- inside WSL on Windows.
    label = backend_label(BACKEND_MUSE)
    muse_argv = _muse_install_argv()
    if muse_argv is None:
        log(_muse_missing_prereq_message())
        return False
    log(f"Running the official {label} installer to update...")
    rc = portable_node._run_logged_process(muse_argv, log)
    if rc is None:
        return False
    if not muse_installed():
        log(f"{label} update finished, but `muse` was not found afterwards.")
        return False
    log(f"{label} is up to date.")
    return True


def _update_museai(binary: str, log: Callable[[str], None]) -> bool:
    return install_museai(log, upgrade=True) is not None


UPDATERS: dict[str, Updater] = {
    BACKEND_CLAUDE: _update_claude,
    BACKEND_CODEX: lambda binary, log: _update_npm_backend(BACKEND_CODEX, binary, log),
    BACKEND_FREEBUFF: lambda binary, log: _update_npm_backend(BACKEND_FREEBUFF, binary, log),
    BACKEND_OPENCODE: lambda binary, log: _update_npm_backend(BACKEND_OPENCODE, binary, log),
    BACKEND_COMMANDCODE: lambda binary, log: _update_npm_backend(BACKEND_COMMANDCODE, binary, log),
    BACKEND_GEMINI: lambda binary, log: _update_npm_backend(BACKEND_GEMINI, binary, log),
    BACKEND_COPILOT: lambda binary, log: _update_npm_backend(BACKEND_COPILOT, binary, log),
    BACKEND_HERMES: _update_hermes,
    BACKEND_MUSE: _update_muse,
    BACKEND_MUSEAI: _update_museai,
    BACKEND_ANTIGRAVITY: lambda binary, log: _update_npm_backend(BACKEND_ANTIGRAVITY, binary, log),
}


def update_backend(backend: str, log: Callable[[str], None]) -> bool:
    """Update an installed provider CLI and stream accessible progress."""
    backend = normalize_backend(backend)
    label = backend_label(backend)
    binary = _find_claude() if backend == BACKEND_CLAUDE else find_backend_cli(backend)
    if binary is None:
        log(f"{label} is not installed yet.")
        return False
    updater = UPDATERS.get(backend)
    if updater is None:
        raise KeyError(f"no updater for {backend}")
    return updater(binary, log)
