"""Regression tests for the native GTK announcement bridge."""

from __future__ import annotations

import linux_accessibility


class _Function:
    def __init__(self, result=None):
        self.result = result
        self.calls: list[tuple] = []
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


class _Gtk:
    def __init__(self):
        self.gtk_offscreen_window_new = _Function(101)
        self.gtk_label_new = _Function(202)
        self.gtk_container_add = _Function()
        self.gtk_widget_show_all = _Function()
        self.gtk_widget_get_accessible = _Function(303)


class _GObject:
    def __init__(self):
        self.g_signal_emit_by_name = _Function()


def test_linux_announcement_uses_one_realized_native_accessible(monkeypatch):
    gtk = _Gtk()
    gobject = _GObject()
    libraries = {"libgtk.so": gtk, "libgobject.so": gobject}
    monkeypatch.setattr(linux_accessibility.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        linux_accessibility.ctypes.util,
        "find_library",
        lambda name: {"gtk-3": "libgtk.so", "gobject-2.0": "libgobject.so"}[name],
    )
    monkeypatch.setattr(linux_accessibility.ctypes, "CDLL", libraries.__getitem__)
    monkeypatch.setattr(linux_accessibility, "_ANNOUNCER", None)

    assert linux_accessibility.announce("First response") is True
    assert linux_accessibility.announce("café") is True

    assert gtk.gtk_offscreen_window_new.calls == [()]
    assert gtk.gtk_label_new.calls == [(b"BlindPilot announcements",)]
    assert gtk.gtk_container_add.calls == [(101, 202)]
    assert gtk.gtk_widget_show_all.calls == [(101,)]
    assert gtk.gtk_widget_get_accessible.calls == [(202,)]
    assert gobject.g_signal_emit_by_name.calls == [
        (303, b"announcement", b"First response"),
        (303, b"announcement", "café".encode()),
    ]


def test_linux_announcement_failure_is_safe_and_not_retried(monkeypatch):
    attempts: list[str] = []
    monkeypatch.setattr(linux_accessibility.platform, "system", lambda: "Linux")
    monkeypatch.setattr(linux_accessibility, "_ANNOUNCER", None)

    def missing_library(name):
        attempts.append(name)
        raise OSError("GTK unavailable")

    monkeypatch.setattr(linux_accessibility.ctypes, "CDLL", missing_library)

    assert linux_accessibility.announce("One") is False
    assert linux_accessibility.announce("Two") is False
    assert len(attempts) == 1


def test_non_linux_never_loads_gtk(monkeypatch):
    monkeypatch.setattr(linux_accessibility.platform, "system", lambda: "Windows")
    monkeypatch.setattr(linux_accessibility, "_ANNOUNCER", None)
    monkeypatch.setattr(
        linux_accessibility.ctypes,
        "CDLL",
        lambda _name: (_ for _ in ()).throw(AssertionError("GTK was loaded")),
    )

    assert linux_accessibility.announce("Ignored") is False
