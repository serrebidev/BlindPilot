"""What the packaged Windows build has to carry.

These read BlindPilot.spec and the installer script as text, and the manifest
as XML, rather than running PyInstaller or Inno Setup. They cannot show that a
build works, only that the pieces a finished Windows program is judged by are
still asked for: the icon the running app loads, the version resource Explorer
and SmartScreen read, the DPI manifest, and the installer's own icon.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "BlindPilot.spec"
MANIFEST = ROOT / "packaging" / "BlindPilot.manifest"
INSTALLER = ROOT / "installer" / "BlindPilot.iss"

WINDOWS_SETTINGS_2005 = "{http://schemas.microsoft.com/SMI/2005/WindowsSettings}"
WINDOWS_SETTINGS_2016 = "{http://schemas.microsoft.com/SMI/2016/WindowsSettings}"


def test_the_window_icon_ships_beside_the_sounds():
    """The frame loads sys._MEIPASS/packaging/BlindPilot.ico when frozen. An
    icon that is only compiled into the EXE is not on disk to be loaded."""
    text = SPEC.read_text(encoding="utf-8")
    assert '("packaging/BlindPilot.ico", "packaging")' in text
    assert (ROOT / "packaging" / "BlindPilot.ico").is_file()


def test_the_windows_exe_carries_a_version_resource_built_from_app_version():
    text = SPEC.read_text(encoding="utf-8")
    assert "VSVersionInfo(" in text
    assert re.search(r"^\s*version=version_info,\s*$", text, re.MULTILINE), (
        "EXE() is not handed the version resource"
    )
    assert 'StringStruct("ProductName", "BlindPilot")' in text
    assert 'StringStruct("ProductVersion", APP_VERSION)' in text
    assert 'StringStruct("FileVersion", APP_VERSION)' in text


def test_the_windows_exe_is_handed_the_dpi_manifest():
    text = SPEC.read_text(encoding="utf-8")
    assert "BlindPilot.manifest" in text
    assert re.search(r"^\s*manifest=manifest,\s*$", text, re.MULTILINE), (
        "EXE() is not handed the manifest"
    )
    assert MANIFEST.is_file()


def test_the_manifest_declares_per_monitor_dpi_awareness_with_a_fallback():
    """Windows 10 1703 and later read dpiAwareness; everything before reads
    dpiAware. Without both, one generation of Windows or the other draws the
    app at 96 DPI and stretches the result."""
    root = ET.parse(MANIFEST).getroot()
    awareness = root.find(f".//{WINDOWS_SETTINGS_2016}dpiAwareness")
    assert awareness is not None and awareness.text is not None
    assert awareness.text.split(",")[0].strip() == "PerMonitorV2"
    aware = root.find(f".//{WINDOWS_SETTINGS_2005}dpiAware")
    assert aware is not None and aware.text is not None
    assert aware.text.strip().lower().startswith("true")


def test_the_installer_shows_the_app_icon():
    """Otherwise the downloaded setup.exe wears Inno Setup's stock picture."""
    text = INSTALLER.read_text(encoding="utf-8")
    match = re.search(r"^SetupIconFile=(.+)$", text, re.MULTILINE)
    assert match, "no SetupIconFile under [Setup]"
    relative = match.group(1).strip().replace("\\", "/")
    # Paths in the script are relative to the script, as LicenseFile already is.
    assert (INSTALLER.parent / relative).resolve().is_file()
