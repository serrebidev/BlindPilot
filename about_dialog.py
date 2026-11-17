"""Keyboard-first About dialog: plain text, then every link as its own tab stop.

    show_about(parent, title, heading, description, links)

``links`` is a list of ``(label, url)``. Each becomes a HyperlinkCtrl that Tab
reaches and Enter, Space or a click opens in the default browser.
"""

import wx
import wx.adv


def _open(url):
    wx.LaunchDefaultBrowser(url)


class AboutDialog(wx.Dialog):
    def __init__(self, parent, title, heading, description, links):
        super().__init__(parent, title=title, style=wx.DEFAULT_DIALOG_STYLE)
        panel = wx.Panel(self)
        layout = wx.BoxSizer(wx.VERTICAL)

        head = wx.StaticText(panel, label=heading)
        head.SetFont(head.GetFont().Bold())
        layout.Add(head, 0, wx.ALL, 12)

        text = wx.StaticText(panel, label=description)
        text.Wrap(500)
        layout.Add(text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.links = []
        for label, url in links:
            link = wx.adv.HyperlinkCtrl(panel, label=label, url=url)
            link.SetName(label)

            # wxMSW's HyperlinkCtrl has no key handling of its own: Enter would
            # press the default OK button instead. Open on Enter/Space only, so
            # Tab and arrows still just move.
            def on_key(event, _url=url):
                if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER, wx.WXK_SPACE):
                    _open(_url)
                    return
                event.Skip()

            link.Bind(wx.EVT_CHAR_HOOK, on_key)
            link.Bind(wx.adv.EVT_HYPERLINK, lambda _e, _url=url: _open(_url))
            layout.Add(link, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
            self.links.append(link)

        # Buttons are children of the panel; dialog-owned buttons in a panel
        # sizer leave wxMSW without a command route to close the dialog.
        buttons = wx.StdDialogButtonSizer()
        self.ok_btn = wx.Button(panel, id=wx.ID_OK, label="OK")
        buttons.AddButton(self.ok_btn)
        buttons.Realize()
        layout.Add(buttons, 0, wx.ALIGN_RIGHT | wx.ALL, 12)
        panel.SetSizerAndFit(layout)
        self.SetMinSize((540, -1))
        self.Fit()
        self.CentreOnParent()
        self.ok_btn.Bind(wx.EVT_BUTTON, lambda _e: self._finish(wx.ID_OK))
        self.Bind(wx.EVT_CLOSE, lambda _e: self._finish(wx.ID_CANCEL))
        self.SetEscapeId(wx.ID_OK)
        self.ok_btn.SetDefault()
        self.ok_btn.SetFocus()

    def _finish(self, result):
        if self.IsModal():
            self.EndModal(result)
        else:
            self.Destroy()


def serrebi_links(github_repo, forgejo_repo, branch="main"):
    """The standard link set: GitHub, Forgejo, projects site, Telegram, credits."""
    forgejo = f"https://git.serrebiradio.com/{forgejo_repo}"
    return [
        (f"GitHub: {github_repo}", f"https://github.com/{github_repo}"),
        (f"Forgejo: {forgejo_repo}", forgejo),
        ("Projects: projects.serrebiradio.com", "https://projects.serrebiradio.com"),
        ("Telegram: SerrebiProjects", "https://t.me/SerrebiProjects"),
        ("Full credits (CREDITS.md)", f"{forgejo}/src/branch/{branch}/CREDITS.md"),
    ]


def show_about(parent, title, heading, description, links):
    dlg = AboutDialog(parent, title, heading, description, links)
    try:
        dlg.ShowModal()
    finally:
        if dlg:
            dlg.Destroy()
