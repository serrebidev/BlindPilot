"""What a sighted person gets from the Chat dialogs and the updater.

The screen reader side of these dialogs is covered elsewhere. This is about
the parts that only show: Escape closing a window, a log in a font that keeps
its columns, and every size going through FromDIP so a 150 percent display
does not shrink the dialogs to a quarter of their area.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import wx

from accessible_ai.services.model_service import ModelService
from accessible_ai.storage.credentials import CredentialStore
from accessible_ai.storage.database import Database
from accessible_ai.ui.accounts import AccountsDialog
from accessible_ai.ui.diagnostics import DiagnosticsDialog
from accessible_ai.ui.profiles import ProfilesDialog

ROOT = Path(__file__).resolve().parent.parent
UI_MODULES = [ROOT / "update_dialog.py", *sorted((ROOT / "accessible_ai" / "ui").glob("*.py"))]


def _dialogs(tmp_path: Path) -> list[wx.Dialog]:
    db = Database(tmp_path / "chat.sqlite3")
    credentials = CredentialStore()
    return [
        AccountsDialog(None, db, credentials, ModelService(db, credentials)),
        ProfilesDialog(None, db),
        DiagnosticsDialog(None),
    ]


def test_escape_presses_close_in_each_chat_dialog(tmp_path):
    """A Close button with wx.ID_CLOSE is not what wx.Dialog looks for on
    Escape. Each dialog has to say so, or Escape does nothing and the person
    who pressed it assumes the app has hung."""
    app = wx.GetApp() or wx.App(False)
    dialogs = _dialogs(tmp_path)
    try:
        for dialog in dialogs:
            assert dialog.GetEscapeId() == wx.ID_CLOSE, dialog.GetTitle()
            close = dialog.FindWindow(wx.ID_CLOSE)
            assert close is not None and close.IsEnabled(), dialog.GetTitle()
    finally:
        for dialog in dialogs:
            dialog.Destroy()
        app.ProcessPendingEvents()


def test_the_diagnostics_log_keeps_its_columns():
    """Log lines are aligned by column; a proportional font loses that."""
    app = wx.GetApp() or wx.App(False)
    dialog = DiagnosticsDialog(None)
    try:
        font = dialog.text.GetFont()
        assert font.GetFamily() == wx.FONTFAMILY_TELETYPE or font.IsFixedWidth()
    finally:
        dialog.Destroy()
        app.ProcessPendingEvents()


@pytest.mark.parametrize("module", UI_MODULES, ids=lambda path: path.name)
def test_every_size_border_and_wrap_goes_through_from_dip(module):
    """One rule for the dialog modules: no bare pixel counts.

    A literal 12 is 12 device pixels, which is 12 at 96 DPI and half that at
    200 percent. Sizes and wrap widths are only ever handed to FromDIP, and a
    sizer border is always the module's PAD or PAD_DIALOG after FromDIP.
    """
    text = module.read_text(encoding="utf-8")
    if "wx.Dialog" not in text and "wx.Panel" not in text:
        pytest.skip("no windows built here")
    assert "PAD = 8" in text and "PAD_DIALOG = 12" in text, module.name
    # A wx.Size literal is fine as a module constant; at a call site it has
    # to be wrapped in FromDIP.
    bare_size = re.findall(
        r"^(?!\w).*(?<!FromDIP\()wx\.Size\((?![^)]*FromDIP)[^)]*\d", text, re.MULTILINE
    )
    assert not bare_size, bare_size
    assert not re.search(r"size=\(\s*-?\d", text), "size=(w, h) tuple literal"
    assert not re.search(r"\.Wrap\((?![^)]*FromDIP)", text), "Wrap() with a bare width"
    assert not re.search(r"[vh]gap=\d", text), "FlexGridSizer gap with a bare count"
    border = re.search(r"wx\.(?:ALL|LEFT|RIGHT|TOP|BOTTOM)[^,\n]*,\s*\d+\s*,?\s*\)", text)
    assert border is None, f"bare sizer border: {border.group(0)!r}"
