"""Putting a CLI's folder on the persistent PATH, on every platform.

Windows appends to ``HKCU\\Environment``; macOS and Linux append a stanza to the
login shell's startup file. Both are paths a *new* terminal will read, which is
not necessarily the PATH this process inherited, so the checks here ask the
registry and the user's own login shell rather than ``os.environ``.

Split out of ``blindpilot_app.py`` (issue #34) so the window calls the PATH
registration instead of defining it. The window reaches these as
``install_path.ensure_on_path`` and the like, so it keeps no re-export. The
registry keys, the profile lines and the PATH strings are unchanged by the
move.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from claude_session import _login_shell


def _same_dir(a: str, b: str) -> bool:
    """Compare two directory strings the way the platform resolves them.

    ``normcase`` folds case and slashes on Windows and is a no-op on POSIX,
    which is what we want: PATH is case-insensitive on one and not the other.
    ``$HOME`` / ``%USERPROFILE%`` style references are expanded, since PATH
    entries are routinely written that way and comparing them literally would
    append a duplicate entry on every launch.
    """

    def norm(p: str) -> str:
        p = os.path.expandvars(os.path.expanduser(p.strip().strip('"')))
        return os.path.normcase(os.path.normpath(p))

    return bool(a.strip()) and norm(a) == norm(b)


def _bundle_dir() -> Optional[str]:
    """The folder a packaged build keeps its own libraries in, if this is one."""
    if not getattr(sys, "frozen", False):
        return None
    return getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(sys.executable))


def path_without_bundle_entries(current: str, bundle: str) -> str:
    """*current* PATH with every entry inside the packaged folder removed."""

    def inside(entry: str) -> bool:
        try:
            candidate = os.path.normcase(
                os.path.normpath(os.path.abspath(entry.strip().strip('"')))
            )
        except (OSError, ValueError):
            return False
        root = os.path.normcase(os.path.normpath(os.path.abspath(bundle)))
        return candidate == root or candidate.startswith(root + os.sep)

    kept = [entry for entry in current.split(os.pathsep) if entry.strip() and not inside(entry)]
    return os.pathsep.join(kept)


def keep_bundle_off_child_path() -> None:
    """Stop BlindPilot's private DLL folder from reaching child processes.

    PyInstaller's pywin32 hook puts ``_internal\\pywin32_system32`` on this
    process's PATH. It registers the same folder with ``os.add_dll_directory``,
    which is what actually makes pywin32 load; the PATH entry is only a
    fallback for Anaconda builds where that call does nothing. Unlike the DLL
    directory, PATH is inherited — by the agent CLI, by the terminal, and by
    everything those start in turn, for as long as any of them live.

    Those processes then resolve ordinary libraries (the Visual C++ runtime,
    pythoncom) out of BlindPilot's install folder and hold them open. The next
    update finds its own files in use by programs it has no business closing,
    and the installer gives up rather than replace them — the silent "installer
    exited with code 5" this used to end in.
    """
    bundle = _bundle_dir()
    if not bundle:
        return
    current = os.environ.get("PATH", "")
    cleaned = path_without_bundle_entries(current, bundle)
    if cleaned != current:
        os.environ["PATH"] = cleaned


def _windows_persistent_path_dirs() -> List[str]:
    """Every directory on the *persistent* PATH — user PATH plus system PATH.

    This is what a freshly opened cmd, PowerShell 5, pwsh or Windows Terminal
    tab composes its PATH from, which is not necessarily what this process
    inherited. Checking the registry rather than ``os.environ`` is the only way
    to know whether `claude` will actually resolve in a new terminal.
    """
    import winreg

    dirs: List[str] = []
    for root, subkey in (
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    ):
        try:
            with winreg.OpenKey(root, subkey) as key:
                value, _type = winreg.QueryValueEx(key, "Path")
        except OSError:
            continue
        if isinstance(value, str):
            dirs.extend(p for p in value.split(os.pathsep) if p.strip())
    return dirs


def _posix_persistent_path_dirs() -> List[str]:
    """The PATH a fresh Terminal window would have.

    Asks the user's own login shell, so whatever their .zprofile / .zshrc /
    .bash_profile / fish config builds up is what we see — the same PATH they
    would get by opening Terminal or iTerm and typing `claude`. Printed one per
    line because in fish ``$PATH`` is a list, not a colon-joined string.
    """
    shell = _login_shell()
    if shell is None:
        return []
    if os.path.basename(shell) == "fish":
        # fish's $PATH is a real list, so this is already space-safe.
        script = "for p in $PATH; echo $p; end"
    else:
        # Split on the colon with tr rather than by word-splitting: PATH
        # entries containing spaces are normal on macOS (/Applications/...)
        # and word-splitting would shred them into fragments.
        script = 'printf \'%s\\n\' "$PATH" | tr ":" "\\n"'
    try:
        result = subprocess.run(
            [shell, "-l", "-c", script],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return [p for p in os.environ.get("PATH", "").split(":") if p.strip()]
    if result.returncode != 0:
        # A configured shell can be temporarily unusable (for example a stale
        # WSL launcher on Windows while exercising the macOS code path).  The
        # inherited POSIX PATH is still better evidence than an empty list.
        return [p for p in os.environ.get("PATH", "").split(":") if p.strip()]
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _is_on_persistent_path(directory: Path) -> bool:
    """Would a newly opened terminal find things in *directory*?

    Deliberately not a check of ``os.environ``: this process may have inherited
    a PATH that a fresh terminal will not have, or vice versa.
    """
    try:
        if platform.system() == "Windows":
            dirs = _windows_persistent_path_dirs()
        else:
            dirs = _posix_persistent_path_dirs()
            if not dirs:
                return True  # No usable login shell to ask — don't cry wolf.
        return any(_same_dir(p, str(directory)) for p in dirs)
    except Exception:
        return True  # Never block the user on a check we couldn't run.


def _broadcast_environment_change() -> None:
    """Tell Explorer (and everything else) that the environment changed.

    Without this broadcast a newly opened terminal still inherits Explorer's
    stale copy of the environment, so a PATH edit appears to do nothing until
    the user signs out and back in.
    """
    try:
        import ctypes
        from ctypes import wintypes

        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002

        send = ctypes.windll.user32.SendMessageTimeoutW
        send.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            ctypes.c_wchar_p,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(wintypes.DWORD),
        ]
        send.restype = wintypes.LPARAM
        result = wintypes.DWORD()
        send(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            "Environment",
            SMTO_ABORTIFHUNG,
            5000,
            ctypes.byref(result),
        )
    except Exception:
        pass


def _add_to_process_path(directory: Path) -> None:
    """Make the directory usable in *this* process without a restart."""
    entry = str(directory)
    current = os.environ.get("PATH", "")
    if not any(_same_dir(p, entry) for p in current.split(os.pathsep) if p.strip()):
        os.environ["PATH"] = entry + os.pathsep + current


def _path_with_entry(current: str, directory: str) -> Optional[str]:
    """The PATH string *current* with *directory* appended, or None if present.

    Kept separate from the registry write so the string surgery — the part that
    can wreck someone's PATH — is testable on its own.
    """
    entries = [p for p in current.split(os.pathsep) if p.strip()]
    if any(_same_dir(p, directory) for p in entries):
        return None
    entries.append(directory)
    return os.pathsep.join(entries)


def _shell_profile_file() -> Path:
    """The startup file to extend for the user's login shell.

    zsh is the default on macOS since Catalina; ``.zshrc`` is read by both
    interactive login and non-login shells, so it is the one place that covers
    Terminal, iTerm and a shell opened inside an editor. Bash on macOS reads
    ``.bash_profile`` for login shells (which is what Terminal opens) while
    Linux desktops open non-login shells that read ``.bashrc``.
    """
    home = Path.home()
    shell = os.path.basename(_login_shell() or "")
    if shell == "zsh":
        return home / ".zshrc"
    if shell == "fish":
        return home / ".config" / "fish" / "config.fish"
    if shell == "bash":
        if platform.system() == "Darwin":
            return home / ".bash_profile"
        return home / ".bashrc"
    return home / ".profile"


PATH_STANZA_MARKER = "# Added by BlindPilot"


def _path_export_line(directory: Path, shell: str) -> str:
    """The one line that puts *directory* on PATH for the given shell.

    Separators are forced to POSIX form — the line we are composing is shell
    script, not a host path, so it must read the same whatever built it.
    """
    # Written against $HOME rather than the expanded path, so the profile stays
    # portable and reads the way a person would have written it by hand.
    home = Path.home().as_posix()
    text = directory.as_posix()
    if text == home or text.startswith(home + "/"):
        text = "$HOME" + text[len(home) :]
    if shell == "fish":
        return f'fish_add_path "{text}"'
    return f'export PATH="{text}:$PATH"'


def ensure_on_posix_path(directory: Path) -> Optional[str]:
    """Add *directory* to PATH for future terminal sessions on macOS / Linux.

    Appends an export line to the login shell's startup file — the equivalent
    of the registry write on Windows, and the only way to affect terminals the
    user opens later. Returns the file that was changed, or None if nothing
    needed changing. Raises OSError if the write fails.
    """
    if _is_on_persistent_path(directory):
        return None

    profile = _shell_profile_file()
    line = _path_export_line(directory, os.path.basename(_login_shell() or ""))
    try:
        existing = profile.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    # Appending twice would leave a duplicate stanza in a file the user owns.
    if line in existing:
        return None

    profile.parent.mkdir(parents=True, exist_ok=True)
    with open(profile, "a", encoding="utf-8") as fh:
        # Lead with a newline: the file may not end with one, and appending to
        # a half-finished last line would corrupt it.
        fh.write(f"\n{PATH_STANZA_MARKER}\n{line}\n")
    return str(profile)


def ensure_on_path(directory: Path) -> Optional[str]:
    """Make *directory* reachable from a terminal, persistently.

    Returns a description of what was changed, or None if nothing needed
    changing. Raises OSError if the change could not be written.
    """
    _add_to_process_path(directory)
    if platform.system() == "Windows":
        return "your user PATH" if ensure_on_windows_path(directory) else None
    return ensure_on_posix_path(directory)


def ensure_on_windows_path(directory: Path) -> bool:
    """Append *directory* to the user's persistent PATH if it isn't there.

    Writes ``HKCU\\Environment``, which cmd, PowerShell 5.1, pwsh 7 and Windows
    Terminal all read when they start, so one entry covers every shell. `setx`
    is deliberately not used — it silently truncates PATH at 1024 characters.

    Returns True when an entry was added, False when it was already present.
    Raises OSError if the registry write fails.
    """
    if platform.system() != "Windows":
        return False

    import winreg  # after the platform check — the module is Windows-only

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Environment",
        0,
        winreg.KEY_READ | winreg.KEY_WRITE,
    ) as key:
        try:
            current, regtype = winreg.QueryValueEx(key, "Path")
        except OSError:
            current, regtype = "", winreg.REG_EXPAND_SZ
        if not isinstance(current, str):
            current, regtype = "", winreg.REG_EXPAND_SZ
        updated = _path_with_entry(current, str(directory))
        if updated is None:
            return False
        # Preserve REG_EXPAND_SZ when that's what's there: existing entries may
        # contain %USERPROFILE% and rewriting the value as REG_SZ would leave
        # those literal, breaking the rest of the user's PATH.
        if regtype not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
            regtype = winreg.REG_EXPAND_SZ
        winreg.SetValueEx(key, "Path", 0, regtype, updated)

    _broadcast_environment_change()
    return True
