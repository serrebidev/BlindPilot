"""Start a computer-use conversation, and the global stop key.

Starting asks for the target app (or just the window), whether to ask before
each action first, and registers the stop key before any automatic action
can run. The stop key works while another app has focus; the same
cancellation object the turn uses is what in-window Stop sets.
"""

from __future__ import annotations

import ctypes
import threading
from typing import Callable


STOP_KEY_LABEL = "Ctrl+Shift+F12"
HOTKEY_ID = 48101


def register_stop_key(callback: Callable[[], None]) -> Callable[[], None]:
    """Register the global stop key. Returns an unregister function.

    Must succeed before automatic actions start; failure raises so the
    caller can speak the reason and offer Ask mode instead.
    """
    if not hasattr(ctypes, "windll"):
        raise RuntimeError("Computer use needs Windows.")
    user32 = ctypes.windll.user32
    MOD_SHIFT = 0x0004
    MOD_CONTROL = 0x0002
    VK_F12 = 0x7B
    if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_SHIFT | MOD_CONTROL, VK_F12):
        raise RuntimeError("The stop key is already in use by another app.")
    state = {"alive": True, "tid": 0}

    def pump() -> None:
        import ctypes as _ct

        state["tid"] = _ct.windll.kernel32.GetCurrentThreadId()

        class Message(_ct.Structure):
            _fields_ = [
                ("hwnd", _ct.c_void_p),
                ("message", _ct.c_uint),
                ("wparam", _ct.c_ulonglong),
                ("lparam", _ct.c_longlong),
                ("time", _ct.c_ulonglong),
                ("pt_x", _ct.c_long),
                ("pt_y", _ct.c_long),
            ]

        message = Message()
        while state["alive"]:
            if user32.GetMessageW(_ct.byref(message), None, 0, 0) <= 0:
                break
            if message.message == 0x0312 and message.wparam == HOTKEY_ID:
                callback()
            user32.TranslateMessage(_ct.byref(message))
            user32.DispatchMessageW(_ct.byref(message))

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()

    def unregister() -> None:
        state["alive"] = False
        if state["tid"]:
            user32.PostThreadMessageW(state["tid"], 0x0012, 0, 0)
        user32.UnregisterHotKey(None, HOTKEY_ID)

    return unregister


def show_computer_use_dialog(parent, speak: Callable[[str], None]) -> tuple | None:
    """Opt-in dialog. Returns (app_path, ask) or None when cancelled."""
    import wx

    class StartDialog(wx.Dialog):
        def __init__(self):
            super().__init__(parent, title="Computer Use", style=wx.DEFAULT_DIALOG_STYLE)
            outer = wx.BoxSizer(wx.VERTICAL)
            pad = self.FromDIP(8)
            outer.Add(
                wx.StaticText(
                    self,
                    label="The model will operate another app through its accessibility "
                    "tree. BlindPilot itself cannot be operated.",
                ),
                0,
                wx.ALL,
                pad,
            )
            outer.Add(
                wx.StaticText(
                    self, label="&App to open (or leave empty to use the window in front):"
                ),
                0,
                wx.LEFT | wx.RIGHT,
                pad,
            )
            self.app = wx.TextCtrl(self)
            self.app.SetName("App to open")
            row = wx.BoxSizer(wx.HORIZONTAL)
            row.Add(self.app, 1, wx.RIGHT, pad)
            browse = wx.Button(self, label="&Browse…")
            row.Add(browse, 0, 0, 0)
            outer.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
            browse.Bind(wx.EVT_BUTTON, self._browse)
            self.ask = wx.CheckBox(self, label="Ask before each action")
            self.ask.SetName("Ask before each action")
            outer.Add(self.ask, 0, wx.ALL, pad)
            outer.Add(
                wx.StaticText(
                    self,
                    label=f"The stop key is {STOP_KEY_LABEL}. It works with another app "
                    "in front. An in-window Stop does the same thing.",
                ),
                0,
                wx.LEFT | wx.RIGHT | wx.BOTTOM,
                pad,
            )
            row2 = wx.BoxSizer(wx.HORIZONTAL)
            start = wx.Button(self, label="&Start computer use")
            cancel = wx.Button(self, wx.ID_CANCEL, "&Cancel")
            row2.Add(start, 0, wx.RIGHT, pad)
            row2.Add(cancel, 0, wx.RIGHT, pad)
            outer.Add(row2, 0, wx.ALL, pad)
            self.SetSizer(outer)
            start.Bind(wx.EVT_BUTTON, self._start)
            cancel.Bind(wx.EVT_BUTTON, lambda _e: self.Destroy())
            self.Bind(wx.EVT_CLOSE, lambda _e: self.Destroy())
            self.app.SetFocus()
            self.CentreOnParent()
            self._result: tuple | None = None

        def _browse(self, _event) -> None:
            dialog = wx.FileDialog(
                self,
                "Choose the app to open",
                defaultDir="C:\\Windows\\System32",
                style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
                wildcard="Applications (*.exe)|*.exe",
            )
            try:
                if dialog.ShowModal() == wx.ID_OK and dialog.GetPath():
                    self.app.SetValue(dialog.GetPath())
            finally:
                dialog.Destroy()

        def _start(self, _event) -> None:
            self._result = (self.app.GetValue().strip(), bool(self.ask.GetValue()))
            self.Destroy()

    dialog = StartDialog()
    dialog.ShowModal()
    result = dialog._result
    dialog.Destroy()
    return result
