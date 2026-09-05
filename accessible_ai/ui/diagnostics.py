from __future__ import annotations

import wx

from accessible_ai.logging_setup import log_path

# Sizer borders in device independent pixels. Every one goes through FromDIP.
PAD = 8
PAD_DIALOG = 12

DIALOG_SIZE = wx.Size(760, 560)


class DiagnosticsDialog(wx.Dialog):
    def __init__(self, parent: wx.Window):
        super().__init__(
            parent,
            title="Diagnostics",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.SetSize(self.FromDIP(DIALOG_SIZE))
        panel = wx.Panel(self)
        pad_dialog = panel.FromDIP(PAD_DIALOG)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(
            wx.StaticText(panel, label=f"Log file: {log_path()}"),
            0,
            wx.EXPAND | wx.ALL,
            pad_dialog,
        )
        self.text = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        self.text.SetName("Diagnostic log")
        # Log lines are column aligned. This is the one control in the
        # application that wants a fixed width font; everything else keeps
        # the system font.
        point_size = self.text.GetFont().GetPointSize()
        self.text.SetFont(wx.Font(wx.FontInfo(point_size).Family(wx.FONTFAMILY_TELETYPE)))
        outer.Add(self.text, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, pad_dialog)
        row = wx.BoxSizer(wx.HORIZONTAL)
        refresh = wx.Button(panel, label="&Refresh")
        row.Add(refresh, 0, wx.ALIGN_CENTER_VERTICAL)
        row.AddStretchSpacer()
        close = wx.Button(panel, wx.ID_CLOSE, "Close")
        buttons = wx.StdDialogButtonSizer()
        buttons.AddButton(close)
        buttons.Realize()
        row.Add(buttons, 0, wx.ALIGN_CENTER_VERTICAL)
        outer.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, pad_dialog)
        panel.SetSizer(outer)
        refresh.Bind(wx.EVT_BUTTON, self.on_refresh)
        close.Bind(wx.EVT_BUTTON, lambda evt: self.EndModal(wx.ID_CLOSE))
        # Escape presses the Close button, as it does in every other dialog.
        self.SetEscapeId(wx.ID_CLOSE)
        self.on_refresh(None)
        self.CentreOnParent()

    def on_refresh(self, event) -> None:
        path = log_path()
        try:
            value = path.read_text(encoding="utf-8") if path.exists() else "No log entries yet."
        except OSError as exc:
            value = f"Could not read log: {exc}"
        self.text.SetValue(value)
        self.text.SetInsertionPointEnd()
