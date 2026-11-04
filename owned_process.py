"""Create backend roots with a kernel-owned lifetime on Windows."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import subprocess
import sys


class _BasicLimit(ctypes.Structure):
    _fields_ = [
        ("per_process_time", ctypes.c_longlong),
        ("per_job_time", ctypes.c_longlong),
        ("flags", ctypes.wintypes.DWORD),
        ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t),
        ("active_processes", ctypes.wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", ctypes.wintypes.DWORD),
        ("scheduling", ctypes.wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "read_operations",
            "write_operations",
            "other_operations",
            "read_bytes",
            "write_bytes",
            "other_bytes",
        )
    ]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimit),
        ("io", _IoCounters),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        (name, ctypes.wintypes.DWORD)
        for name in (
            "size",
            "usage",
            "thread_id",
            "owner_pid",
            "base_priority",
            "delta_priority",
            "flags",
        )
    ]


class _WindowsJob:
    """A private Job Object; closing its last handle ends owned descendants."""

    def __init__(self) -> None:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = ctypes.wintypes.HANDLE
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = handle
        kernel.SetInformationJobObject.argtypes = [
            handle,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.wintypes.DWORD,
        ]
        kernel.SetInformationJobObject.restype = ctypes.wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [handle, handle]
        kernel.AssignProcessToJobObject.restype = ctypes.wintypes.BOOL
        kernel.CloseHandle.argtypes = [handle]
        kernel.CloseHandle.restype = ctypes.wintypes.BOOL
        kernel.CreateToolhelp32Snapshot.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = handle
        kernel.Thread32First.argtypes = [handle, ctypes.POINTER(_ThreadEntry)]
        kernel.Thread32First.restype = ctypes.wintypes.BOOL
        kernel.Thread32Next.argtypes = [handle, ctypes.POINTER(_ThreadEntry)]
        kernel.Thread32Next.restype = ctypes.wintypes.BOOL
        kernel.OpenThread.argtypes = [
            ctypes.wintypes.DWORD,
            ctypes.wintypes.BOOL,
            ctypes.wintypes.DWORD,
        ]
        kernel.OpenThread.restype = handle
        kernel.ResumeThread.argtypes = [handle]
        kernel.ResumeThread.restype = ctypes.wintypes.DWORD
        self._kernel = kernel
        self._handle = kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimit()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(
            self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def assign(self, proc: subprocess.Popen) -> None:
        # _handle is the Windows-only Popen attribute; typeshed does not
        # declare it on Popen[Any], and this class is Windows-only too.
        self.assign_handle(int(proc._handle))  # type: ignore[attr-defined]

    def assign_handle(self, handle: int) -> None:
        if not self._kernel.AssignProcessToJobObject(self._handle, handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def resume(self, proc: subprocess.Popen) -> None:
        kernel = self._kernel
        snapshot = kernel.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = _ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            found = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.owner_pid == proc.pid:
                    thread = kernel.OpenThread(0x0002, False, entry.thread_id)
                    if not thread:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        if kernel.ResumeThread(thread) == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                    finally:
                        kernel.CloseHandle(thread)
                    return
                found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            raise OSError("The suspended backend's initial thread was not found")
        finally:
            kernel.CloseHandle(snapshot)

    def close(self) -> None:
        handle = self._handle
        if handle:
            self._handle = None
            self._kernel.CloseHandle(handle)

    def __del__(self) -> None:
        self.close()


_REAL_POPEN = subprocess.Popen


def owned_popen(*args, _popen=None, _job_factory=None, **kwargs):
    """Start one backend root; no child can run before its Job assignment."""
    popen = _popen or subprocess.Popen
    if sys.platform != "win32":
        return popen(*args, **kwargs)
    job = (_job_factory or _WindowsJob)()
    kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x00000004  # CREATE_SUSPENDED
    try:
        proc = popen(*args, **kwargs)
    except BaseException:
        job.close()
        raise
    if not isinstance(proc, _REAL_POPEN):
        # Test doubles cannot own kernel handles; production Popen always can.
        job.close()
        return proc
    try:
        job.assign(proc)
        job.resume(proc)
    except BaseException:
        try:
            proc.kill()  # Exact process handle; never a PID lookup.
            proc.wait(timeout=5)
        finally:
            job.close()
        raise
    proc._blindpilot_job = job
    return proc
