"""Backend ownership is fixed before a Windows root can run."""

from __future__ import annotations

import ctypes
import subprocess

import pytest

import owned_process


class _Process:
    def __init__(self, events):
        self.events = events
        self.pid = 101

    def kill(self):
        self.events.append("kill exact root")

    def wait(self, timeout=None):
        self.events.append("reap root")


class _Job:
    def __init__(self, events, fail=False):
        self.events = events
        self.fail = fail

    def assign(self, proc):
        self.events.append("assign")
        if self.fail:
            raise OSError("assignment failed")

    def resume(self, proc):
        self.events.append("resume")

    def close(self):
        self.events.append("close job")


def test_windows_backend_is_owned_before_it_can_run(monkeypatch):
    events = []
    monkeypatch.setattr(owned_process.sys, "platform", "win32")
    monkeypatch.setattr(owned_process, "_REAL_POPEN", _Process)

    def spawn(*args, **kwargs):
        assert kwargs["creationflags"] & 4
        events.append("create suspended")
        return _Process(events)

    proc = owned_process.owned_popen(["backend"], _popen=spawn, _job_factory=lambda: _Job(events))
    assert events == ["create suspended", "assign", "resume"]
    assert proc._blindpilot_job is not None


def test_failed_job_assignment_never_runs_an_unowned_backend(monkeypatch):
    events = []
    monkeypatch.setattr(owned_process.sys, "platform", "win32")
    monkeypatch.setattr(owned_process, "_REAL_POPEN", _Process)
    with pytest.raises(OSError, match="assignment failed"):
        owned_process.owned_popen(
            ["backend"],
            _popen=lambda *_a, **_k: (events.append("create suspended"), _Process(events))[1],
            _job_factory=lambda: _Job(events, fail=True),
        )
    assert events == ["create suspended", "assign", "kill exact root", "reap root", "close job"]


@pytest.mark.skipif(owned_process.sys.platform != "win32", reason="Windows Job Object")
def test_real_windows_backend_can_start_and_close_its_job():
    proc = owned_process.owned_popen(
        [owned_process.sys.executable, "-c", "print('ready')"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.communicate(timeout=5)[0].strip() == "ready"
    finally:
        proc._blindpilot_job.close()


@pytest.mark.skipif(owned_process.sys.platform != "win32", reason="Windows Job Object")
def test_job_closes_child_even_after_backend_root_exits():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [
        ctypes.wintypes.DWORD,
        ctypes.wintypes.BOOL,
        ctypes.wintypes.DWORD,
    ]
    kernel.OpenProcess.restype = ctypes.wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
    kernel.WaitForSingleObject.restype = ctypes.wintypes.DWORD
    kernel.TerminateProcess.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.UINT]
    kernel.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    child_code = "import time; time.sleep(20)"
    root_code = (
        "import subprocess,sys; "
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
        "print(child.pid,flush=True)"
    )
    root = owned_process.owned_popen(
        [owned_process.sys.executable, "-c", root_code],
        stdout=subprocess.PIPE,
        text=True,
    )
    child = None
    try:
        child = kernel.OpenProcess(0x00100001, False, int(root.stdout.readline().strip()))
        assert child, "the backend child exited before ownership could be checked"
        assert root.wait(timeout=5) == 0
        root._blindpilot_job.close()
        assert kernel.WaitForSingleObject(child, 5000) == 0
    finally:
        root._blindpilot_job.close()
        root.stdout.close()
        if root.poll() is None:
            root.kill()
        root.wait(timeout=5)
        if child:
            if kernel.WaitForSingleObject(child, 0) != 0:
                kernel.TerminateProcess(child, 1)
            kernel.CloseHandle(child)
