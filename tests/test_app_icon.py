"""The window icon is found from source and from a PyInstaller bundle.

Every window used to carry the toolkit's default glyph: the icon file was
only ever given to the packaged exe's resource, never to the frame. The frame
now loads packaging/BlindPilot.ico, which lives next to the code from source
and is unpacked into the bundle by PyInstaller.
"""

from __future__ import annotations

import os
from pathlib import Path

import blindpilot_app as app


def test_from_source_the_icon_is_next_to_the_application(monkeypatch):
    monkeypatch.delattr(app.sys, "_MEIPASS", raising=False)

    expected = Path(os.path.abspath(app.__file__)).parent / "packaging" / "BlindPilot.ico"

    assert app._app_icon_path() == expected
    assert expected.exists()


def test_frozen_the_icon_is_in_the_unpacked_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(app.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert app._app_icon_path() == tmp_path / "packaging" / "BlindPilot.ico"
