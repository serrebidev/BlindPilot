"""Show one Chat account's usage, cost and balance report."""

from __future__ import annotations

from threading import Event
from typing import Callable

from accessible_ai.providers.provider_reports import format_report, usage_report

PAD = 8
PAD_DIALOG = 12


def show_usage_dialog(parent, account, credentials, speak: Callable[[str], None]) -> None:
    import wx
    from dialog_tasks import TaskSlot

    class UsageDialog(wx.Dialog):
        def __init__(self):
            super().__init__(parent, title="Usage Report", style=wx.DEFAULT_DIALOG_STYLE)
            self._tasks = TaskSlot(wx.CallAfter)
            self._cancel = Event()
            outer = wx.BoxSizer(wx.VERTICAL)
            pad = self.FromDIP(PAD)
            self.report = wx.StaticText(self, label="Checking…")
            self.report.SetName("Usage report")
            outer.Add(self.report, 0, wx.ALL, pad)
            row = wx.BoxSizer(wx.HORIZONTAL)
            self.refresh = wx.Button(self, label="&Refresh")
            close = wx.Button(self, wx.ID_CANCEL, "&Close")
            row.Add(self.refresh, 0, wx.RIGHT, pad)
            row.Add(close, 0, wx.RIGHT, pad)
            outer.Add(row, 0, wx.ALL, pad)
            self.SetSizer(outer)
            self.refresh.Bind(wx.EVT_BUTTON, lambda _event: self._fetch())
            close.Bind(wx.EVT_BUTTON, self._close)
            self.Bind(wx.EVT_CLOSE, self._close)
            self._fetch()
            self.refresh.SetFocus()
            self.CentreOnParent()

        def _fetch(self) -> None:
            self.report.SetLabel("Checking…")

            def work() -> str:
                return format_report(usage_report(account, credentials))

            self._tasks.start(work, self._shown)

        def _shown(self, text, error) -> None:
            if error:
                self.report.SetLabel(f"Could not check usage: {error}")
                speak(f"Could not check usage: {error}")
                return
            self.report.SetLabel(str(text))
            speak("Usage report ready.")

        def _close(self, _event) -> None:
            self._cancel.set()
            self._tasks.close()
            self.Destroy()

    UsageDialog().ShowModal()
