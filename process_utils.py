"""Shared process helpers for BlindPilot.

Process-group, pipe-cleanup, and console-window helpers used by more than
one caller. Windows Job Object ownership and owned process launch stay in
``owned_process.py``; this module owns only the generic helpers, and process
cleanup here preserves ADR 0001's ownership and fail-closed rules.
"""

from __future__ import annotations

import os
import platform
import signal
import subprocess
from typing import Optional


# A windowed app owns no console, so every child process Windows considers a
# console program is given a brand new one - a terminal that pops up on screen,
# takes focus away from the screen reader, and in the case of a long-running
# agent CLI stays there for the whole turn. CREATE_NO_WINDOW suppresses it.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def no_window_kwargs() -> dict:
    """``subprocess`` keyword arguments that keep children off the screen."""
    return {"creationflags": CREATE_NO_WINDOW} if CREATE_NO_WINDOW else {}


def own_group_kwargs() -> dict:
    """``subprocess`` keyword arguments that give a child its own process group.

    Half the provider CLIs are launchers rather than programs: npm's
    ``@openai/codex`` is a Node script that runs the real Codex as a child of
    its own, and FreeBuff's is the same shape. Killing the launcher leaves that
    child running — still holding its lock, still waiting on a sign-in nobody
    is completing. A child in a group of its own can be stopped as a group,
    which is the only way to stop what it started too.
    """
    return {} if platform.system() == "Windows" else {"start_new_session": True}


def end_process_group(proc: object, timeout: float = 0.0) -> None:
    """Stop a child started with :func:`own_group_kwargs`, and its own children.

    The kill signals go out immediately; the wait for the child to be gone is
    bounded by *timeout*, or five seconds when it is not given. Stop Task and a
    closing wizard both call this from the window's own thread, so an unbounded
    wait there is a frozen application -- but no wait at all leaves a zombie on
    POSIX and an unreleased handle on Windows, which is why the default path
    gained the short bounded wait.

    The group is only signalled while the root is live and demonstrably the
    leader, which is exactly what ``start_new_session`` made it. Anything else, a
    child started without its own group or a stand-in in a test, is stopped on
    its own. That check is not a formality: a process still sitting in
    BlindPilot's group would otherwise have BlindPilot signal itself, and take
    every other backend down with it.

    On Windows, only a Job Object attached at creation can end descendants.
    On POSIX, an exited root cannot prove a group still belongs to us, so
    children left behind by that root are not signalled by a reused group ID.
    """
    job = getattr(proc, "_blindpilot_job", None)
    if job is not None:
        job.close()
    poll = getattr(proc, "poll", None)
    if poll is None or poll() is None:
        pid = getattr(proc, "pid", None)
        if isinstance(pid, int) and pid > 0:
            if platform.system() != "Windows":
                try:
                    # POSIX only, and the platform test above is a runtime
                    # condition the checker cannot read, so against a Windows
                    # target it reports all three of these as missing.
                    if os.getpgid(pid) == pid:  # type: ignore[attr-defined]
                        os.killpg(pid, signal.SIGKILL)  # type: ignore[attr-defined]
                except OSError:
                    pass
        # Also stop the exact root if group or Job cleanup did not reach it.
        kill = getattr(proc, "kill", None)
        if kill is not None:
            try:
                kill()
            except OSError:
                pass
    # Reaped whether the kill just happened or the child had already exited:
    # a finished child that is never waited on stays a zombie (POSIX) or keeps
    # its process handle (Windows). An already-exited child answers at once.
    wait = getattr(proc, "wait", None)
    if wait is not None:
        try:
            wait(timeout=timeout or 5)
        except Exception:
            pass


def _close_process_pipes(proc: object) -> None:
    """Close a child's pipe handles, whatever state the child is in.

    Ending a process does not hand its pipes back: a server stopped after it
    had already exited still holds one file handle per pipe until something
    closes them, and an application that stays open all day leaks one server's
    worth per restart. Best effort, and tolerant of the stand-ins tests use --
    a pipe without a ``close`` method is left alone rather than raising.
    """
    for name in ("stdin", "stdout", "stderr"):
        pipe = getattr(proc, name, None)
        close = getattr(pipe, "close", None)
        if close is None:
            continue
        try:
            close()
        except (OSError, ValueError, AttributeError):
            pass


_CONSOLE_WINDOW_CLASSES = ("ConsoleWindowClass", "PseudoConsoleWindow")


def _banish_window(handle: int) -> None:
    """Make a console unable to take focus, then hide it off-screen.

    A console created for a pseudo-terminal is owned by BlindPilot, so Windows
    gives it the executable path for a title and treats it as an ordinary app
    window. Hiding alone is insufficient: Windows can make it visible again
    while ConPTY is starting or stopping, at which point it can take focus.
    Turn it into a disabled, no-activate tool window first, then hide and park
    it. The watcher reapplies these properties if a child creates another
    console later.
    """
    import ctypes

    user32 = ctypes.windll.user32
    try:
        # GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_NOACTIVATE.
        # Tool windows do not appear on the taskbar; no-activate prevents an
        # accidental ShowWindow from moving keyboard or screen-reader focus.
        ex_style = user32.GetWindowLongW(handle, -20)
        ex_style = (ex_style | 0x00000080 | 0x08000000) & ~0x00040000
        user32.SetWindowLongW(handle, -20, ex_style)
        user32.EnableWindow(handle, False)
        user32.ShowWindow(handle, 0)  # SW_HIDE
        # SWP_NOSENDCHANGING | SWP_NOACTIVATE | SWP_NOZORDER |
        # SWP_NOOWNERZORDER | SWP_HIDEWINDOW.
        user32.SetWindowPos(
            handle,
            0,
            -32000,
            -32000,
            0,
            0,
            0x0400 | 0x0010 | 0x0004 | 0x0200 | 0x0080,
        )
    except OSError:
        pass


def _descendant_pids(roots: set[int]) -> set[int]:
    """Every process descended from ``roots``, so only our own are touched."""
    if platform.system() != "Windows":
        try:
            result = subprocess.run(
                ["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=5
            )
            posix_parents = {
                int(pid): int(parent)
                for pid, parent in (line.split() for line in result.stdout.splitlines())
            }
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return set(roots)
        family = set(roots)
        while (
            additions := {pid for pid, parent in posix_parents.items() if parent in family} - family
        ):
            family.update(additions)
        return family
    import ctypes
    import ctypes.wintypes as wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == -1:
        return set(roots)
    parents: dict[int, int] = {}
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        ok = kernel32.Process32First(snapshot, ctypes.byref(entry))
        while ok:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            ok = kernel32.Process32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)

    family = set(roots)
    for pid in parents:
        seen: set[int] = set()
        walker = pid
        while walker and walker not in seen:
            seen.add(walker)
            if walker in family:
                family.update(seen)
                break
            walker = parents.get(walker, 0)
    return family


def hide_console_windows(roots: Optional[set[int]] = None) -> int:
    """Hide the console this process was given, and any its children raise.

    Creating the pseudo-terminal attaches a console to BlindPilot itself, which
    is why the window belongs to us rather than to the program being run. That
    one is found directly; the enumeration is the safety net for a child that
    raises its own, and never touches a console outside this process tree.
    """
    if platform.system() != "Windows":
        return 0
    import ctypes
    import ctypes.wintypes as wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    hidden = 0
    try:
        own = kernel32.GetConsoleWindow()
        if own and user32.IsWindowVisible(own):
            _banish_window(own)
            hidden += 1
    except OSError:
        pass

    candidates: list[tuple[int, int]] = []

    def visit(handle, _param):
        try:
            if not user32.IsWindowVisible(handle):
                return True
            name = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(handle, name, 64)
            if name.value in _CONSOLE_WINDOW_CLASSES:
                owner = wintypes.DWORD()
                user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
                candidates.append((handle, int(owner.value)))
        except Exception:
            pass
        return True

    try:
        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
        user32.EnumWindows(callback, 0)
    except Exception:
        return hidden
    # Walking the process table costs milliseconds, and this runs in a tight
    # loop to catch the window in the frame it appears. Pay for it only when
    # there is actually a console window on screen to account for.
    if not candidates:
        return hidden
    try:
        family = _descendant_pids(set(roots or ()) | {os.getpid()})
    except Exception:
        family = set(roots or ()) | {os.getpid()}
    for handle, owner in candidates:
        if owner in family:
            try:
                _banish_window(handle)
                hidden += 1
            except Exception:
                pass
    return hidden
