"""Drive other apps through their accessibility trees (Linux, AT-SPI).

The same contract as the Windows and macOS sessions: controls are addressed
by their position in the target window's accessibility tree, never by screen
coordinates; every action finds the target again first and refuses
BlindPilot's own process (by process id, never by name); a target that has
gone raises instead of guessing.

libatspi is called through ctypes, like the GTK announcer in
``linux_accessibility``: loading PyGObject next to wxPython would make both
bindings configure GTK. Calls are blocking D-Bus requests, made from the
generation worker thread, never the GUI thread (BlindPilot's own GTK bridge
answers on the GUI thread, so asking it from there would deadlock).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import subprocess
import threading
import time
from typing import Any

from computer_use import ComputerUseError, ControlRef

# Controls listed per window; a huge document tree is cut off here.
CONTROL_LIMIT = 400
ATSPI_STATE_ACTIVE = 1


class _GError(ctypes.Structure):
    _fields_ = [("domain", ctypes.c_uint32), ("code", ctypes.c_int), ("message", ctypes.c_char_p)]


_P = ctypes.c_void_p
_E = ctypes.POINTER(ctypes.POINTER(_GError))
_SIGNATURES: dict[str, tuple[list[Any], Any]] = {
    "atspi_init": ([], ctypes.c_int),
    "atspi_get_desktop": ([ctypes.c_int], _P),
    "atspi_accessible_get_child_count": ([_P, _E], ctypes.c_int),
    "atspi_accessible_get_child_at_index": ([_P, ctypes.c_int, _E], _P),
    "atspi_accessible_get_name": ([_P, _E], _P),
    "atspi_accessible_get_role_name": ([_P, _E], _P),
    "atspi_accessible_get_process_id": ([_P, _E], ctypes.c_uint),
    "atspi_accessible_get_state_set": ([_P], _P),
    "atspi_state_set_contains": ([_P, ctypes.c_int], ctypes.c_int),
    "atspi_accessible_get_action_iface": ([_P], _P),
    "atspi_action_get_n_actions": ([_P, _E], ctypes.c_int),
    "atspi_action_do_action": ([_P, ctypes.c_int, _E], ctypes.c_int),
    "atspi_accessible_get_editable_text_iface": ([_P], _P),
    "atspi_editable_text_set_text_contents": ([_P, ctypes.c_char_p, _E], ctypes.c_int),
    "atspi_accessible_get_component_iface": ([_P], _P),
    "atspi_component_grab_focus": ([_P, _E], ctypes.c_int),
}
_libraries: tuple[Any, Any, Any] | None = None
_load_lock = threading.Lock()


def _load() -> tuple[Any, Any, Any]:
    """(libatspi, libgobject, libglib), typed and initialised once."""
    global _libraries
    with _load_lock:
        if _libraries is None:
            try:
                atspi = ctypes.CDLL(ctypes.util.find_library("atspi") or "libatspi.so.0")
                gobject = ctypes.CDLL(
                    ctypes.util.find_library("gobject-2.0") or "libgobject-2.0.so.0"
                )
                glib = ctypes.CDLL(ctypes.util.find_library("glib-2.0") or "libglib-2.0.so.0")
            except OSError:
                raise ComputerUseError(
                    "Computer use on Linux needs AT-SPI (the libatspi2.0-0 package)."
                ) from None
            for name, (args, result) in _SIGNATURES.items():
                function = getattr(atspi, name)
                function.argtypes = args
                function.restype = result
            gobject.g_object_unref.argtypes = [_P]
            glib.g_free.argtypes = [_P]
            glib.g_error_free.argtypes = [ctypes.POINTER(_GError)]
            # 1 means already initialised (GTK's own bridge did it): fine.
            atspi.atspi_init()
            _libraries = (atspi, gobject, glib)
        return _libraries


class _Tree:
    """One pass over the desktop tree. Every object it hands out is released
    together by ``release``, so no reference outlives the action."""

    def __init__(self) -> None:
        self.atspi, self._gobject, self._glib = _load()
        self._held: list[int] = []

    def _hold(self, handle: int | None) -> int | None:
        if handle:
            self._held.append(handle)
        return handle

    def release(self) -> None:
        for handle in reversed(self._held):
            self._gobject.g_object_unref(handle)
        self._held.clear()

    def _call(self, function, *args) -> Any:
        """Call with a trailing GError**; None when the call failed."""
        error = ctypes.POINTER(_GError)()
        result = function(*args, ctypes.byref(error))
        if error:
            self._glib.g_error_free(error)
            return None
        return result

    def _string(self, function, handle: int) -> str:
        pointer = self._call(function, handle)
        if not pointer:
            return ""
        try:
            return (ctypes.string_at(pointer) or b"").decode("utf-8", "replace")
        finally:
            self._glib.g_free(pointer)

    def desktop(self) -> int:
        handle = self._hold(self.atspi.atspi_get_desktop(0))
        if not handle:
            raise ComputerUseError("The accessibility bus is not running on this desktop.")
        return handle

    def children(self, handle: int) -> list[int]:
        count = self._call(self.atspi.atspi_accessible_get_child_count, handle) or 0
        found: list[int] = []
        for index in range(max(0, min(int(count), 10_000))):
            child = self._call(self.atspi.atspi_accessible_get_child_at_index, handle, index)
            if child:
                self._held.append(child)
                found.append(child)
        return found

    def name(self, handle: int) -> str:
        return self._string(self.atspi.atspi_accessible_get_name, handle)

    def role(self, handle: int) -> str:
        return self._string(self.atspi.atspi_accessible_get_role_name, handle)

    def pid(self, handle: int) -> int:
        return int(self._call(self.atspi.atspi_accessible_get_process_id, handle) or 0)

    def active(self, handle: int) -> bool:
        states = self._hold(self.atspi.atspi_accessible_get_state_set(handle))
        return bool(states and self.atspi.atspi_state_set_contains(states, ATSPI_STATE_ACTIVE))

    def iface(self, getter: str, handle: int) -> int | None:
        return self._hold(getattr(self.atspi, getter)(handle))


def _window_of(tree: _Tree, app: int) -> int | None:
    """The app's active window, else its first one."""
    windows = tree.children(app)
    for window in windows:
        if tree.active(window):
            return window
    return windows[0] if windows else None


def _walk(tree: _Tree, window: int) -> list[tuple[int, str, str]]:
    """(handle, role, name) for each control under the window, depth first.

    ``list_controls`` and the actions share this one walk, so control number
    N is the same control in both.
    """
    found: list[tuple[int, str, str]] = []

    def visit(handle: int) -> None:
        for child in tree.children(handle):
            if len(found) >= CONTROL_LIMIT:
                return
            found.append((child, tree.role(child), tree.name(child)))
            visit(child)

    visit(window)
    return found


class LinuxAtspiSession:
    """One target application for one computer-use conversation."""

    def __init__(self, title_contains: str = ""):
        self._title = title_contains
        self._pid = 0
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def close(self) -> None:
        """End a process this session launched. Apps it only found stay."""
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None
        self._pid = 0

    def open(self, app_path: str = "", wait_seconds: float = 8.0) -> str:
        """Launch an app or find the matching one. Returns its name."""
        if app_path:
            if not os.path.exists(app_path):
                raise ComputerUseError(f"That app was not found: {app_path}")
            self._process = subprocess.Popen([app_path])
        deadline = time.monotonic() + wait_seconds
        last_name = ""
        while time.monotonic() < deadline:
            tree = _Tree()
            try:
                for app in tree.children(tree.desktop()):
                    pid = tree.pid(app)
                    if not pid or pid == os.getpid():
                        continue
                    name = tree.name(app)
                    last_name = name or last_name
                    window = _window_of(tree, app)
                    if window is None:
                        continue
                    if self._process is not None:
                        matched = pid == self._process.pid
                    elif self._title:
                        wanted = self._title.lower()
                        matched = wanted in name.lower() or wanted in tree.name(window).lower()
                    else:
                        matched = tree.active(window)
                    if matched:
                        self._pid = pid
                        return tree.name(window) or name
            finally:
                tree.release()
            time.sleep(0.25)
        if app_path:
            self.close()
            raise ComputerUseError(f"The app did not open a window: {app_path}")
        raise ComputerUseError(
            "No matching window was found."
            + (f" Last one seen was {last_name!r}." if last_name else "")
        )

    def _controls(self, tree: _Tree) -> list[tuple[int, str, str]]:
        """The target's controls, found again for this action."""
        with self._lock:
            pid = self._pid
        if not pid:
            raise ComputerUseError("No target window is open for this conversation.")
        if pid == os.getpid():
            raise ComputerUseError("BlindPilot itself cannot be operated.")
        for app in tree.children(tree.desktop()):
            if tree.pid(app) == pid:
                window = _window_of(tree, app)
                if window is None:
                    break
                return _walk(tree, window)
        raise ComputerUseError("The target window disappeared.")

    def list_controls(self) -> list[ControlRef]:
        tree = _Tree()
        try:
            refs = [
                ControlRef(number, role, name)
                for number, (_handle, role, name) in enumerate(self._controls(tree), 1)
            ]
        finally:
            tree.release()
        if not refs:
            raise ComputerUseError("The target window shows no accessible controls.")
        return refs

    def _act(self, number: int, action) -> str:
        tree = _Tree()
        try:
            controls = self._controls(tree)
            if number < 1 or number > len(controls):
                raise ComputerUseError(f"There is no control number {number} in that window.")
            return action(tree, controls[number - 1][0])
        finally:
            tree.release()

    def press(self, number: int) -> str:
        """The control's first accessible action, else give it focus."""

        def press(tree: _Tree, control: int) -> str:
            action = tree.iface("atspi_accessible_get_action_iface", control)
            if action and (tree._call(tree.atspi.atspi_action_get_n_actions, action) or 0) > 0:
                if tree._call(tree.atspi.atspi_action_do_action, action, 0):
                    return f"Pressed control {number}."
            component = tree.iface("atspi_accessible_get_component_iface", control)
            if component and tree._call(tree.atspi.atspi_component_grab_focus, component):
                return f"Focused control {number}."
            raise ComputerUseError(
                f"Control {number} cannot be pressed from its accessibility tree."
            )

        return self._act(number, press)

    def set_value(self, number: int, text: str) -> str:
        """Replace an editable control's text through its EditableText interface."""

        def set_text(tree: _Tree, control: int) -> str:
            editable = tree.iface("atspi_accessible_get_editable_text_iface", control)
            if editable and tree._call(
                tree.atspi.atspi_editable_text_set_text_contents, editable, text.encode("utf-8")
            ):
                return f"Set control {number}."
            raise ComputerUseError(f"Control {number} takes no text from its accessibility tree.")

        return self._act(number, set_text)
