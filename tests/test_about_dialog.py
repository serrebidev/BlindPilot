"""About dialog: every link is a tab stop; Enter/Space open it, Tab does not."""

import wx

import about_dialog


def _key(link, code):
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(code)
    event.SetEventObject(link)
    link.GetEventHandler().ProcessEvent(event)


def test_links_are_tab_stops_and_open_on_enter(monkeypatch):
    app = wx.GetApp() or wx.App(False)
    opened = []
    monkeypatch.setattr(about_dialog, "_open", opened.append)
    links = about_dialog.serrebi_links("serrebidev/x", "serrebi/x")
    dlg = about_dialog.AboutDialog(None, "About X", "X 1.0", "Text.", links)
    try:
        assert [link.GetLabel() for link in dlg.links] == [label for label, _ in links]
        assert all(link.AcceptsFocus() for link in dlg.links)
        _key(dlg.links[0], wx.WXK_TAB)
        assert opened == []
        _key(dlg.links[0], wx.WXK_RETURN)
        _key(dlg.links[3], wx.WXK_SPACE)
        assert opened == ["https://github.com/serrebidev/x", "https://t.me/SerrebiProjects"]
        assert links[-1][1] == "https://git.serrebiradio.com/serrebi/x/src/branch/main/CREDITS.md"
    finally:
        dlg.Destroy()
    assert app
