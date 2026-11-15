"""Style rules for the built web UI and site dashboards."""

import sys
import threading

import pytest

from computer_tools import ComputerUseExecutor, TOOL_DEFINITIONS

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


class FakeInvoke:
    def __init__(self, log):
        self._log = log

    def Invoke(self):
        self._log.append("invoke")


class FakeControl:
    def __init__(self, text, target):
        self._text = text
        self._target = target
        self.iface_invoke = FakeInvoke([])

    def window_text(self):
        return self._text

    @property
    def element_info(self):
        return type(
            "Info", (), {"control_type": "Document", "name": self._text, "process_id": 4242}
        )()


class FakeWindow:
    def __init__(self, title="Untitled - Notepad"):
        self._title = title
        self.enabled_typed = ""
        self.is_active_flag = True

    def window_text(self):
        return self._title

    def is_enabled(self):
        return True

    def is_active(self):
        return self.is_active_flag

    def process_name(self):
        return "notepad.exe"

    def descendants(self):
        if not hasattr(self, "_controls"):
            self._controls = [FakeControl("Document", None), FakeControl("Menu", None)]
        return self._controls

    def set_focus(self):
        self.is_active_flag = True


def _bind_session(monkeypatch):
    import computer_use

    session = computer_use.ComputerUseSession()
    window = FakeWindow()
    session._window = window
    monkeypatch.setattr(
        computer_use, "_desktop", lambda: type("D", (), {"windows": lambda self: [window]})()
    )
    return session


def test_list_controls_numbers_the_tree(monkeypatch):
    session = _bind_session(monkeypatch)
    refs = session.list_controls()
    assert [ref.number for ref in refs] == [1, 2]
    assert "1. Document" in refs[0].describe()


def test_press_uses_invoke_pattern(monkeypatch):
    session = _bind_session(monkeypatch)
    window = session._window
    log: list[str] = []
    window.descendants()[0].iface_invoke = FakeInvoke(log)
    assert session.press(1).startswith("Pressed")
    assert log == ["invoke"]


def test_executor_binds_tools_to_session(monkeypatch, tmp_path):
    session = _bind_session(monkeypatch)
    cancel = threading.Event()
    executor = ComputerUseExecutor(session, cancel)
    assert isinstance(TOOL_DEFINITIONS, list) and len(TOOL_DEFINITIONS) == 3
    result = executor.run("list_controls", {})
    assert "1. Document" in result


def test_executor_refuses_when_stopped(monkeypatch):
    session = _bind_session(monkeypatch)
    cancel = threading.Event()
    executor = ComputerUseExecutor(session, cancel)
    cancel.set()
    assert "Stopped" in executor.run("list_controls", {})


def test_tools_have_definitions():
    names = [tool["function"]["name"] for tool in TOOL_DEFINITIONS]
    assert names == ["list_controls", "press_control", "set_control_text"]


def _in_service_session() -> bool:
    """Session 0 (a service, such as the CI runner) has no desktop to open windows on."""
    if sys.platform != "win32":
        return False
    import ctypes
    import os

    session = ctypes.c_ulong()
    ok = ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session))
    return bool(ok) and session.value == 0


@pytest.mark.skipif(_in_service_session(), reason="needs an interactive desktop")
@pytest.mark.filterwarnings("ignore:Revert to STA COM threading mode:UserWarning")
def test_real_notepad_round_trip():
    """Drive a real Notepad through UIA: list, type, verify, stop."""
    import computer_use

    session = computer_use.ComputerUseSession(title_contains="Untitled - Notepad")
    session.open(app_path="C:\\Windows\\System32\\notepad.exe")
    refs = session.list_controls()
    assert len(refs) >= 1
    # Type into the document control through its value pattern.
    typed = False
    for ref in refs:
        if "document" in ref.control_type.lower():
            try:
                session.set_value(ref.number, "BlindPilot computer use probe")
                typed = True
            except Exception:
                pass
            break
    assert typed
    # The text now lives in the document control's text.
    window = session._live_window()
    found = any(
        "BlindPilot computer use probe" in str(control.window_text())
        for control in window.descendants()
    )
    assert found
    # Stop discipline: once cancelled, the executor refuses further actions.
    cancel = threading.Event()
    executor = ComputerUseExecutor(session, cancel)
    cancel.set()
    assert "Stopped" in executor.run("list_controls", {})
    session.close()
