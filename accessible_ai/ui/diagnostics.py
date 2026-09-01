from __future__ import annotations

import wx

from accessible_ai.logging_setup import LOG_PATH


class DiagnosticsDialog(wx.Dialog):
    def __init__(self, parent: wx.Window):
        super().__init__(
            parent,
            title="Diagnostics",
            size=(760, 560),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(wx.StaticText(panel, label=f"Log file: {LOG_PATH}"), 0, wx.EXPAND | wx.ALL, 12)
        self.text = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        self.text.SetName("Diagnostic log")
        outer.Add(self.text, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        row = wx.BoxSizer(wx.HORIZONTAL)
        refresh = wx.Button(panel, label="&Refresh")
        close = wx.Button(panel, wx.ID_CLOSE, "Close")
        row.Add(refresh, 0, wx.RIGHT, 8)
        row.Add(close, 0)
        outer.Add(row, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        panel.SetSizer(outer)
        refresh.Bind(wx.EVT_BUTTON, self.on_refresh)
        close.Bind(wx.EVT_BUTTON, lambda evt: self.EndModal(wx.ID_CLOSE))
        self.on_refresh(None)

    def on_refresh(self, event) -> None:
        try:
            value = (
                LOG_PATH.read_text(encoding="utf-8") if LOG_PATH.exists() else "No log entries yet."
            )
        except OSError as exc:
            value = f"Could not read log: {exc}"
        self.text.SetValue(value)
        self.text.SetInsertionPointEnd()
