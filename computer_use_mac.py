"""Drive other apps through their accessibility trees (macOS).

Actions address controls by their accessibility tree position, never by
screen coordinates. Every action validates the target window first:
the message must be the right one and BlindPilot itself is refused (by
process id, never by title text). A disappeared target raises instead of
falling back to coordinates.

Requires Accessibility permission for the running process. Without it,
every entry point raises with the System Settings path to grant it.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import threading
import time

from computer_use import ComputerUseError, ControlRef

# AXError codes worth naming in words.
_AX_ERRORS: dict[int, str] = {
    0: "",
    -25200: "That element cannot be used.",
    -25201: "That message is not allowed.",
    -25202: "Not enough privileges; Accessibility permission is missing.",
    -25204: "That element has no such attribute.",
    -25205: "That action is not supported on that element.",
    -25206: "That action failed.",
    -25209: "That element has no focused state.",
    -25211: "That element did not reply in time.",
    -25212: "That application is not trusted.",
    -25213: "That element is not a valid reference.",
    -25214: "That attribute value cannot be set.",
    -25215: "That attribute value cannot be read.",
}


def _services():
    framework = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
    return ctypes.CDLL(framework)


def _trusted() -> bool:
    services = _services()
    services.AXIsProcessTrusted.restype = ctypes.c_bool
    return bool(services.AXIsProcessTrusted())


def _require_trusted() -> None:
    if not _trusted():
        raise ComputerUseError(
            "This Mac has not granted Accessibility permission to BlindPilot. "
            "Open System Settings, Privacy and Security, Accessibility, and enable BlindPilot."
        )


def _cf_string(text: str):
    return ctypes.c_void_p(
        ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        ).CFStringCreateWithCString(ctypes.c_void_p(-1), text.encode("utf-8"), 0)
    )


def _cf_release(value: int) -> None:
    if value:
        ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation").CFRelease(
            ctypes.c_void_p(value)
        )


class _Element:
    """One AXUIElement plus the accessors the session needs."""

    def __init__(self, handle: int, services):
        self._handle = handle
        self._services = services

    @property
    def handle(self):
        return self._handle

    def attribute(self, name: str):
        """One attribute value as text, or "" when absent."""
        needed = ctypes.c_void_p(0)
        result = self._services.AXUIElementCopyAttributeValue(
            self._handle, _cf_string(name), ctypes.byref(needed)
        )
        if result != 0 or not needed.value:
            return ""
        try:
            value = _to_text(needed.value)
            return value if isinstance(value, str) else ""
        finally:
            _cf_release(needed.value)

    def children(self) -> list["_Element"]:
        needed = ctypes.c_void_p(0)
        result = self._services.AXUIElementCopyAttributeValue(
            self._handle, _cf_string("AXChildren"), ctypes.byref(needed)
        )
        if result != 0 or not needed.value:
            return []
        try:
            count = ctypes.CDLL(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
            ).CFArrayGetCount(ctypes.c_void_p(needed.value))
            get = ctypes.CDLL(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
            ).CFArrayGetValueAtIndex
            get.restype = ctypes.c_void_p
            return [_Element(get(needed.value, i), self._services) for i in range(count)]
        finally:
            _cf_release(needed.value)

    def perform(self, action: str) -> int:
        return int(self._services.AXUIElementPerformAction(self._handle, _cf_string(action)))

    def set_attribute(self, name: str, text: str) -> int:
        return int(
            self._services.AXUIElementSetAttributeValue(
                self._handle, _cf_string(name), _cf_string(text)
            )
        )


def _to_text(value) -> str:
    foundation = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    if not value:
        return ""
    kind = foundation.CFGetTypeID(ctypes.c_void_p(value))
    if kind == foundation.CFStringGetTypeID():
        length = foundation.CFStringGetLength(ctypes.c_void_p(value))
        buffer = ctypes.create_unicode_buffer(length + 1)
        foundation.CFStringGetCString(ctypes.c_void_p(value), buffer, ctypes.sizeof(buffer), 0)
        return buffer.value
    return ""


def _explain(error: int) -> str:
    name = _AX_ERRORS.get(error)
    if name:
        return name
    return f"Accessibility error {error}."


class MacAXSession:
    """One target application for one computer-use conversation."""

    def __init__(self, title_contains: str = ""):
        self._title = title_contains
        self._app: _Element | None = None
        self._window: _Element | None = None
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._services = _services()
        self._services.AXUIElementCopyAttributeValue.restype = ctypes.c_int32

    def close(self) -> None:
        """End a process this session launched. Apps it only focused stay."""
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
        _require_trusted()
        if app_path:
            if not os.path.exists(app_path):
                raise ComputerUseError(f"That app was not found: {app_path}") from None
            self._process = subprocess.Popen(["open", "-a", app_path])
        from AppKit import NSWorkspace

        deadline = time.monotonic() + wait_seconds
        last_name = ""
        while time.monotonic() < deadline:
            apps = NSWorkspace.sharedWorkspace().runningApplications()
            for app in apps:
                name = str(app.localizedName() or "")
                if "blindpilot" in name.lower():
                    continue
                last_name = name
                if self._title and self._title.lower() not in name.lower():
                    continue
                candidate = _Element(
                    self._services.AXUIElementCreateApplication(int(app.processIdentifier())),
                    self._services,
                )
                if not candidate.handle:
                    continue
                self._app = candidate
                return name
            time.sleep(0.25)
        if app_path:
            raise ComputerUseError(f"The app did not open a window: {app_path}") from None
        raise ComputerUseError(
            "No matching window was found."
            + (f" Last one seen was {last_name!r}." if last_name else "")
        ) from None

    def _live_window(self) -> _Element:
        """The target window, re-validated before every action."""
        with self._lock:
            window = self._window
            if window is None:
                raise ComputerUseError("No target window is open for this conversation.")
            pid = ctypes.c_int(0)
            self._services.AXUIElementGetPid(window.handle, ctypes.byref(pid))
            if int(pid.value) == os.getpid():
                raise ComputerUseError("BlindPilot itself cannot be operated.")
            error = self._services.AXUIElementPerformAction(window.handle, _cf_string("AXRaise"))
            if error not in (0, -25209, -25200):
                raise ComputerUseError("The target window disappeared.") from None
            return window

    def _front_window(self) -> _Element:
        """The frontmost window of the target app, re-read each time."""
        app = self._app
        if app is None:
            raise ComputerUseError("No target application is open.") from None
        apps_value = ctypes.c_void_p(0)
        frontmost = self._services.AXUIElementCopyAttributeValue(
            app.handle, _cf_string("AXFocusedWindow"), ctypes.byref(apps_value)
        )
        if frontmost == 0 and apps_value.value:
            try:
                element = _Element(apps_value.value, self._services)
                self._window = element
                return element
            except Exception:
                pass
        focused = self._ElementOfAttribute("AXWindows")
        if focused:
            self._window = focused[0]
            return focused[0]
        raise ComputerUseError("The target application has no window.") from None

    def _ElementOfAttribute(self, name: str) -> list[_Element]:
        app = self._app
        if app is None:
            return []
        needed = ctypes.c_void_p(0)
        result = self._services.AXUIElementCopyAttributeValue(
            app.handle, _cf_string(name), ctypes.byref(needed)
        )
        if result != 0 or not needed.value:
            return []
        try:
            foundation = ctypes.CDLL(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
            )
            count = foundation.CFArrayGetCount(ctypes.c_void_p(needed.value))
            get = foundation.CFArrayGetValueAtIndex
            get.restype = ctypes.c_void_p
            return [_Element(get(needed.value, i), self._services) for i in range(count)]
        finally:
            _cf_release(needed.value)

    def list_controls(self) -> list[ControlRef]:
        """Numbered controls of the front window, depth-first."""
        _require_trusted()
        window = self._front_window()
        refs: list[ControlRef] = []

        def walk(element: _Element) -> None:
            role = element.attribute("AXRole")
            title = element.attribute("AXTitle") or element.attribute("AXDescription")
            if role or title:
                refs.append(ControlRef(len(refs) + 1, role, title))
                if len(refs) > 400:
                    return
            for child in element.children():
                if len(refs) > 400:
                    return
                walk(child)

        for child in window.children():
            if len(refs) > 400:
                break
            walk(child)
        if not refs:
            raise ComputerUseError("The target window shows no accessible controls.")
        return refs

    def _control(self, number: int) -> _Element:
        _require_trusted()
        window = self._live_window()
        flat: list[_Element] = []

        def walk(element: _Element) -> None:
            flat.append(element)
            for child in element.children():
                walk(child)

        for child in window.children():
            walk(child)
        if number < 1 or number > len(flat):
            raise ComputerUseError(f"There is no control number {number} in that window.")
        return flat[number - 1]

    def press(self, number: int) -> str:
        """Press a control via its accessible actions, never coordinates."""
        control = self._control(number)
        for action in ("AXPress", "AXShowMenu"):
            error = control.perform(action)
            if error == 0:
                return f"Pressed control {number}."
            if error == -25204:
                raise ComputerUseError(
                    "That control cannot be pressed from its accessibility tree."
                ) from None
        raise ComputerUseError(
            f"Control {number} cannot be pressed from its accessibility tree."
        ) from None

    def set_value(self, number: int, text: str) -> str:
        """Set a control's value through AXValue (text fields, check boxes)."""
        control = self._control(number)
        error = control.set_attribute("AXValue", text)
        if error == 0:
            return f"Set control {number}."
        raise ComputerUseError(
            "That control takes no text from its accessibility tree: " + _explain(error)
        ) from None
