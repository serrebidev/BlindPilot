# SPDX-License-Identifier: MIT
"""Recent Conversations has to open at all.

Its pickers sat in a grid declared as two rows; adding Sort made three, and
the installed app's wx refused the third with "too many items (5 > 2*2) in
grid sizer", so the dialog never opened for any backend (0.37.0). The tests
that drive the dialog's keys use a stand-in for it, so nothing built the real
one. This one does, with wx assertions raised as errors.
"""

from __future__ import annotations

import blindpilot_app


def test_recent_conversations_builds_with_assertions_on(frame, monkeypatch):
    import wx

    monkeypatch.setattr(blindpilot_app, "list_history", lambda *_a, **_k: [])
    app = wx.GetApp()
    old = app.GetAssertMode()
    app.SetAssertMode(wx.APP_ASSERT_EXCEPTION)
    try:
        dialog = blindpilot_app.HistoryDialog(frame, backend="museai", cwd=".")
        dialog.Destroy()
    finally:
        app.SetAssertMode(old)
