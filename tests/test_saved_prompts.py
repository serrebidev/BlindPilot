"""Saved Prompts (Ctrl+Shift+P): kept in BlindPilot's config, in the user's order.

The dialog is built for real, because the list, the text box and the button
state are all set up in __init__. The name and text boxes are stubbed, since
they are modal.
"""

from __future__ import annotations

import pytest

wx = pytest.importorskip("wx")

import blindpilot_app as app  # noqa: E402


@pytest.fixture
def config(monkeypatch):
    saved: dict = {}
    monkeypatch.setattr(app, "_load_config", lambda: dict(saved))
    monkeypatch.setattr(app, "_save_config", lambda cfg: saved.update(cfg) or True)
    monkeypatch.setattr(app, "announce", lambda *a, **k: None)
    return saved


@pytest.fixture
def dialog(wx_app, config):
    config["saved_prompts"] = [
        {"name": "Review", "text": "Review the diff."},
        {"name": "Ship", "text": "Commit and release."},
        {"name": "broken"},
    ]
    window = wx.Frame(None)
    dlg = app.SavedPromptsDialog(window, draft="Fix the flaky test please now")
    dlg.EndModal = lambda code: setattr(dlg, "ended", code)
    try:
        yield dlg
    finally:
        dlg.Destroy()
        window.Destroy()


def test_an_entry_without_text_is_left_out(dialog):
    assert [p["name"] for p in dialog.prompts] == ["Review", "Ship"]
    assert dialog.text.GetValue() == "Review the diff."


def test_use_hands_back_the_selected_text(dialog):
    dialog.list_box.SetSelection(1)
    dialog._use()
    assert dialog.chosen == "Commit and release."
    assert dialog.ended == wx.ID_OK


def test_moving_is_saved_and_follows_the_prompt(dialog, config):
    dialog._move(1)
    assert [p["name"] for p in config["saved_prompts"]] == ["Ship", "Review"]
    assert dialog.list_box.GetSelection() == 1
    dialog._move(1)  # already last: nothing moves
    assert [p["name"] for p in config["saved_prompts"]] == ["Ship", "Review"]


def test_new_starts_from_the_prompt_box(dialog, config, monkeypatch):
    seen = []

    def ask(name, text):
        seen.append((name, text))
        return {"name": name, "text": text}

    monkeypatch.setattr(dialog, "_ask", ask)
    dialog._new()
    assert seen == [("Fix the flaky test please", "Fix the flaky test please now")]
    assert config["saved_prompts"][-1]["name"] == "Fix the flaky test please"
    assert dialog.list_box.GetSelection() == 2


def test_delete_asks_first(dialog, config, monkeypatch):
    monkeypatch.setattr(app.wx, "MessageBox", lambda *a, **k: wx.NO)
    dialog._delete()
    assert "saved_prompts" not in config or len(config["saved_prompts"]) == 3
    monkeypatch.setattr(app.wx, "MessageBox", lambda *a, **k: wx.YES)
    dialog._delete()
    assert [p["name"] for p in config["saved_prompts"]] == ["Ship"]
