# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for BlindPilot.

One spec for every platform instead of a divergent command line per platform,
so the packaged metadata cannot drift from the workflow that builds it: the
bundle identifier, the version read out of APP_VERSION, the minimum macOS
version, the application icon, and the per-platform hidden imports all live
here. The release workflow builds with:

    python -m PyInstaller --noconfirm --clean --additional-hooks-dir hooks BlindPilot.spec

The spec's own folder (SPECPATH) is where the packaging assets live, so the
build works from any checkout location.
"""

import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

spec_dir = Path(SPECPATH)  # noqa: F821  (PyInstaller global)

app_version_match = re.search(
    r'^APP_VERSION = "([^"]+)"$',
    (spec_dir / "blindpilot_app.py").read_text(encoding="utf-8"),
    re.MULTILINE,
)
if app_version_match is None:
    raise SystemExit("could not read APP_VERSION out of blindpilot_app.py")
APP_VERSION = app_version_match.group(1)

datas = [("EarCons", "EarCons")]
hiddenimports = ["pexpect"]
binaries = []

# websocket's jsonrpc/transport submodules are imported by name at runtime, and
# a hidden import of the package alone leaves them out — the remote Hermes
# backend then reports the package as missing on a machine that has it. Measured
# on a packaged build, not assumed.
_websocket_datas, _websocket_binaries, _websocket_hidden = collect_all("websocket")
datas += _websocket_datas
binaries += _websocket_binaries
hiddenimports += _websocket_hidden

if sys.platform == "win32":
    # pywinpty's ConPTY backend runs the child inside OpenConsole.exe, and a
    # hidden import collects only the module and its DLLs. Without that
    # executable no process can start under a pseudo-terminal, so FreeBuff
    # never runs. accessible_output2 loads its outputs by iterating the
    # package at runtime.
    _winpty_datas, _winpty_binaries, _winpty_hidden = collect_all("winpty")
    datas += _winpty_datas
    binaries += _winpty_binaries
    hiddenimports += _winpty_hidden
    hiddenimports += ["accessible_output2", "accessible_output2.outputs"]
    icon = str(spec_dir / "packaging" / "BlindPilot.ico")
    app_icon = icon
else:
    app_icon = None

a = Analysis(
    [str(spec_dir / "blind_pilot.py")],
    pathex=[str(spec_dir)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # The hooks directory travels inside the spec: PyInstaller refuses the
    # --additional-hooks-dir CLI flag once a .spec file is given, and the
    # workflow builds from the spec.
    hookspath=[str(spec_dir / "hooks")],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BlindPilot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=app_icon,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="BlindPilot",
)

if sys.platform == "darwin":
    # The version and the rest of the bundle metadata come from here, not from
    # PyInstaller defaults, so Finder's Get Info shows a finished product.
    app = BUNDLE(
        coll,
        name="BlindPilot.app",
        icon=str(spec_dir / "packaging" / "BlindPilot.icns"),
        bundle_identifier="com.serrebidev.blindpilot",
        info_plist={
            "CFBundleName": "BlindPilot",
            "CFBundleDisplayName": "BlindPilot",
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "CFBundleHelpBookName": "BlindPilot",
            "CFBundleSignature": "????",
            "LSMinimumSystemVersion": "10.15",
            "LSApplicationCategoryType": "public.app-category.developer-tools",
            "NSHighResolutionCapable": True,
            "NSSupportsAutomaticGraphicsSwitching": True,
            "NSPrincipalClass": "NSApplication",
            "NSHumanReadableCopyright": (
                "Copyright (c) 2026 doubletaponair and BlindPilot contributors"
            ),
        },
    )
