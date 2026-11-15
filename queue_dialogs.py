"""Review and manage the current session's queued messages."""

from __future__ import annotations

from typing import Callable

PAD = 8
PAD_DIALOG = 12


def show_queue_dialog(parent, panel, speak: Callable[[str], None]) -> None:
    import wx

    class QueueDialog(wx.Dialog):
        def __init__(self):
            super().__init__(
                parent, title="Queued Messages", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
            )
            self.SetSize(self.FromDIP(wx.Size(640, 380)))
            self._panel = panel
            outer = wx.BoxSizer(wx.VERTICAL)
            pad = self.FromDIP(PAD)
            outer.Add(
                wx.StaticText(self, label="&Messages waiting for this session:"), 0, wx.ALL, pad
            )
            self.list = wx.ListBox(self, style=wx.LB_EXTENDED)
            self.list.SetName("Queued messages")
            outer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
            self.status = wx.StaticText(self, label="")
            outer.Add(self.status, 0, wx.ALL, pad)
            row = wx.BoxSizer(wx.HORIZONTAL)
            self.remove = wx.Button(self, label="&Remove selected")
            self.clear = wx.Button(self, label="&Clear all")
            self.pause = wx.Button(self, label="&Pause queue")
            close = wx.Button(self, wx.ID_CANCEL, "&Close")
            for button in (self.remove, self.clear, self.pause):
                row.Add(button, 0, wx.RIGHT, pad)
            row.Add(close, 0, wx.RIGHT, pad)
            outer.Add(row, 0, wx.ALL, pad)
            self.SetSizer(outer)
            self.remove.Bind(wx.EVT_BUTTON, self._remove)
            self.clear.Bind(wx.EVT_BUTTON, self._clear)
            self.pause.Bind(wx.EVT_BUTTON, self._pause)
            close.Bind(wx.EVT_BUTTON, self._close)
            self.Bind(wx.EVT_CLOSE, self._close)
            self._refresh()
            self.list.SetFocus()
            self.CentreOnParent()

        def _pending(self) -> list:
            return list(getattr(self._panel, "_pending_messages", []))

        def _refresh(self) -> None:
            pending = self._pending()
            self.list.Clear()
            for text, _files, _backend in pending:
                self.list.Append(text if text and text.strip() else "Attached files")
            paused = bool(getattr(self._panel, "_queue_paused", False))
            self.pause.SetLabel("&Resume queue" if paused else "&Pause queue")
            self.remove.Enable(bool(pending))
            self.clear.Enable(bool(pending))
            self.status.SetLabel(
                f"{len(pending)} waiting." + (" The queue is paused." if paused else "")
            )

        def _remove(self, _event) -> None:
            selections = self.list.GetSelections()
            if not selections:
                speak("Select a queued message to remove.")
                return
            pending = list(getattr(self._panel, "_pending_messages", []))
            kept = [item for index, item in enumerate(pending) if index not in selections]
            self._panel._pending_messages = kept
            speak(f"Removed {len(selections)} queued messages.")
            self._refresh()

        def _clear(self, _event) -> None:
            self._panel._pending_messages = []
            self._panel._steering_context = ""
            self._panel._queue_paused = False
            speak("Queued messages cleared.")
            self._refresh()

        def _pause(self, _event) -> None:
            paused = bool(getattr(self._panel, "_queue_paused", False))
            self._panel._queue_paused = not paused
            if not paused:
                speak("Queue paused.")
            else:
                speak("Queue resumed.")
                self._panel._send_queued_message()
            self._refresh()

        def _close(self, _event) -> None:
            self.Destroy()

    QueueDialog().ShowModal()
