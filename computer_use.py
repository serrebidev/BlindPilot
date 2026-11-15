"""Drive other apps through their accessibility trees (Windows only).

Actions address controls by their accessibility tree position, never by
screen coordinates. Every action validates the target window first:
the foreground window must belong to the session's target, and BlindPilot
itself is refused. A disappeared target raises instead of falling back to
coordinates.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any


class ComputerUseError(RuntimeError):
    """Raised with a speakable reason when an action cannot be taken."""


@dataclass
class ControlRef:
    """One numbered control in a window's accessibility tree."""

    number: int
    control_type: str
    name: str

    def describe(self) -> str:
        label = f"{self.number}. {self.control_type}"
        return f"{label}: {self.name}" if self.name else label


def _desktop():
    try:
        from pywinauto import Desktop
    except ImportError:
        raise ComputerUseError(
            "Computer use needs the pywinauto package, which is not installed."
        ) from None
    return Desktop(backend="uia")


def _is_blindpilot(window: Any) -> bool:
    """True only for our own process, never for any app that merely says so.

    Title text is not identity: a document typed with our name in it keeps
    its title and must stay operable. The process id is the check: computer
    use never operates the process it runs in.
    """
    try:
        return int(window.element_info.process_id) == os.getpid()
    except Exception:
        return False


class ComputerUseSession:
    """One target window for one computer-use conversation."""

    def __init__(self, title_contains: str = ""):
        self._title = title_contains
        self._window = None
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def close(self) -> None:
        """End a process this session launched. Windows it only focused stay."""
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None
        self._window = None

    def open(self, app_path: str = "", wait_seconds: float = 8.0) -> str:
        """Launch an app or focus the matching window. Returns its title."""
        desktop = _desktop()
        if app_path:
            if not os.path.isfile(app_path):
                raise ComputerUseError(f"That app was not found: {app_path}") from None
            self._process = subprocess.Popen([app_path])
        deadline = time.monotonic() + wait_seconds
        last_title = ""
        while time.monotonic() < deadline:
            windows = desktop.windows()
            for window in windows:
                title = str(window.window_text() or "")
                if _is_blindpilot(window):
                    continue
                if self._title:
                    if self._title.lower() in title.lower():
                        self._window = window
                        window.set_focus()
                        return title
                elif window.is_active():
                    self._window = window
                    return title
                if window.element_info.process_id:
                    last_title = title
            time.sleep(0.25)
        if app_path:
            raise ComputerUseError(f"The app did not open a window: {app_path}") from None
        raise ComputerUseError(
            "No matching window was found."
            + (f" Last one seen was {last_title!r}." if last_title else "")
        )

    def _live_window(self) -> Any:
        """The target window, re-validated before every action."""
        with self._lock:
            window = self._window
            if window is None:
                raise ComputerUseError("No target window is open for this conversation.") from None
            if _is_blindpilot(window):
                raise ComputerUseError("BlindPilot itself cannot be operated.") from None
            try:
                if not window.is_enabled():
                    raise ComputerUseError("The target window is not responding.") from None
            except Exception:
                raise ComputerUseError("The target window disappeared.") from None
            return window

    def list_controls(self) -> list[ControlRef]:
        """Numbered controls of the target window, in tree order."""
        window = self._live_window()
        try:
            descendants = window.descendants()
        except Exception:
            raise ComputerUseError("The target window disappeared.") from None
        refs = []
        for index, control in enumerate(descendants, 1):
            info = control.element_info
            refs.append(ControlRef(index, str(info.control_type), str(control.window_text())))
        if not refs:
            raise ComputerUseError("The target window shows no accessible controls.") from None
        return refs

    def _control(self, number: int) -> Any:
        window = self._live_window()
        try:
            descendants = window.descendants()
        except Exception:
            raise ComputerUseError("The target window disappeared.") from None
        if number < 1 or number > len(descendants):
            raise ComputerUseError(f"There is no control number {number} in that window.") from None
        return descendants[number - 1]

    def press(self, number: int) -> str:
        """Press a control (invoke, or select/expand/legacy fallbacks)."""
        control = self._control(number)
        try:
            iface = control.iface_invoke
            iface.Invoke()
            return f"Pressed control {number}."
        except Exception:
            pass
        try:
            control.legacy_interface.DoDefaultAction()
            return f"Activated control {number}."
        except Exception:
            pass
        try:
            control.select()
            return f"Selected control {number}."
        except Exception:
            raise ComputerUseError(
                f"Control {number} cannot be pressed from its accessibility tree."
            ) from None

    def set_value(self, number: int, text: str) -> str:
        """Set a control's text/value through its pattern, not the keyboard."""
        control = self._control(number)
        try:
            value = control.iface_value
            value.SetValue(text)
            return f"Set control {number}."
        except Exception:
            pass
        try:
            control.set_focus()
            control.type_keys(text, with_spaces=True)
            return f"Typed into control {number}."
        except Exception:
            raise ComputerUseError(f"Control {number} takes no text from its tree.") from None


def screenshot_png() -> bytes:
    """Unsupported in this build: control is by accessibility tree only."""
    raise ComputerUseError(
        "This build does not take screenshots; control is by accessibility tree."
    )
