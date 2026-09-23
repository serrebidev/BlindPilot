"""Each tab keeps its own backend, and says which one once tabs differ.

The backend used to be one app-wide setting. Choosing Codex for a new tab
moved every other tab to Codex as well, so the next message typed into a
Claude tab went to Codex, and nothing on screen said so beforehand.

Run from the project root:

    python -m pytest tests/test_tabs_keep_their_backend.py -q
"""

from __future__ import annotations

import wave

import wx

import blindpilot_app
from test_tab_strip_focus import _frame, _running_app


def test_tabs_keep_their_backend_and_say_it_once_mixed(monkeypatch, tmp_path):
    with _running_app():
        frame = _frame(monkeypatch, tmp_path)
        try:
            monkeypatch.setattr(frame, "_announce_setting", lambda text: None)
            frame._set_backend(blindpilot_app.BACKEND_CLAUDE)
            first = frame.notebook.GetPage(0)
            # One backend: no tag, the prompt keeps its plain name.
            assert not frame.notebook.GetPageText(0).startswith("Claude")
            assert first.prompt.GetName() == "Prompt"
            assert frame.earcons.per_backend_send is False

            second = frame._add_session(str(tmp_path))
            frame._set_backend(blindpilot_app.BACKEND_CODEX)

            assert first.selected_backend() == blindpilot_app.BACKEND_CLAUDE
            assert second.selected_backend() == blindpilot_app.BACKEND_CODEX
            codex = blindpilot_app.backend_label(blindpilot_app.BACKEND_CODEX)
            claude = blindpilot_app.backend_label(blindpilot_app.BACKEND_CLAUDE)
            assert frame.notebook.GetPageText(0).startswith(f"{claude}: ")
            assert frame.notebook.GetPageText(1).startswith(f"{codex}: ")
            assert first.prompt.GetName() == f"{claude} prompt"
            assert second.prompt.GetName() == f"{codex} prompt"
            assert frame.earcons.per_backend_send is True

            # Switching back says the tab's backend, and the menu follows it.
            wx.Yield()
            spoken: list[str] = []
            monkeypatch.setattr(blindpilot_app, "announce", lambda text: spoken.append(text))
            monkeypatch.setattr(
                type(frame), "_focus_is_within", staticmethod(lambda focus, control: False)
            )
            first.focus_prompt = lambda: None
            frame._cycle_tab(+1)
            wx.Yield()
            assert spoken and spoken[0].startswith(f"Session 1 of 2, {claude}: ")
            assert frame.current_backend() == blindpilot_app.BACKEND_CLAUDE
            assert frame._backend_items[blindpilot_app.BACKEND_CLAUDE].IsChecked()
        finally:
            frame.Destroy()


def test_send_cue_is_pitched_per_backend(tmp_path):
    source = tmp_path / "send.wav"
    with wave.open(str(source), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(44100)
        out.writeframes(b"\0\0" * 100)
    cues = blindpilot_app.Earcons(str(tmp_path))
    assert cues._send_for(blindpilot_app.BACKEND_CLAUDE) == str(source)
    codex = cues._send_for(blindpilot_app.BACKEND_CODEX)
    with wave.open(codex, "rb") as pitched:
        assert pitched.getframerate() > 44100
        assert pitched.getnframes() == 100
