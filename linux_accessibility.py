"""Optional native GTK bridge for non-focus-stealing Orca announcements."""

from __future__ import annotations

import ctypes
import ctypes.util
import platform


# None until the first announcement, False once GTK has been found
# wanting and there is no point asking again, otherwise the handles.
_ANNOUNCER: object | tuple = None


def announce(text: str) -> bool:
    """Emit an ATK announcement from a realized, invisible GTK label.

    wxPython already owns GTK's initialization. Loading PyGObject alongside it
    makes the two bindings both configure GTK and produces initialization
    warnings. Calling GTK's public C API avoids that collision while still
    creating a genuine accessible object in BlindPilot's AT-SPI tree.
    """
    global _ANNOUNCER

    if platform.system() != "Linux" or _ANNOUNCER is False:
        return False
    try:
        if _ANNOUNCER is None:
            gtk_name = ctypes.util.find_library("gtk-3") or "libgtk-3.so.0"
            gobject_name = ctypes.util.find_library("gobject-2.0") or "libgobject-2.0.so.0"
            gtk = ctypes.CDLL(gtk_name)
            gobject = ctypes.CDLL(gobject_name)

            gtk.gtk_offscreen_window_new.argtypes = []
            gtk.gtk_offscreen_window_new.restype = ctypes.c_void_p
            gtk.gtk_label_new.argtypes = [ctypes.c_char_p]
            gtk.gtk_label_new.restype = ctypes.c_void_p
            gtk.gtk_container_add.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            gtk.gtk_container_add.restype = None
            gtk.gtk_widget_show_all.argtypes = [ctypes.c_void_p]
            gtk.gtk_widget_show_all.restype = None
            gtk.gtk_widget_get_accessible.argtypes = [ctypes.c_void_p]
            gtk.gtk_widget_get_accessible.restype = ctypes.c_void_p
            gobject.g_signal_emit_by_name.argtypes = [
                ctypes.c_void_p,
                ctypes.c_char_p,
                ctypes.c_char_p,
            ]
            gobject.g_signal_emit_by_name.restype = None

            window = gtk.gtk_offscreen_window_new()
            label = gtk.gtk_label_new(b"BlindPilot announcements")
            if not window or not label:
                raise RuntimeError("GTK could not create an announcement source")
            gtk.gtk_container_add(window, label)
            gtk.gtk_widget_show_all(window)
            source = gtk.gtk_widget_get_accessible(label)
            if not source:
                raise RuntimeError("GTK did not expose the announcement source to ATK")
            _ANNOUNCER = (gtk, gobject, window, label, source)

        assert isinstance(_ANNOUNCER, tuple)  # set just above, or on a past call
        _gtk, gobject, _window, _label, source = _ANNOUNCER
        gobject.g_signal_emit_by_name(
            source,
            b"announcement",
            text.encode("utf-8", "replace"),
        )
        return True
    except Exception:
        # Accessibility must never make BlindPilot itself unstable. All callers
        # also mirror the message to status text that the review cursor can read.
        _ANNOUNCER = False
        return False
