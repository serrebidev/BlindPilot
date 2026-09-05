# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for BlindPilot.

One spec for every platform instead of a divergent command line per platform,
so the packaged metadata cannot drift from the workflow that builds it: the
bundle identifier, the version read out of APP_VERSION, the minimum macOS
version, the application icon, and the per-platform hidden imports all live
here. The release workflow builds with:

    python -m PyInstaller --noconfirm --clean BlindPilot.spec

The hooks directory is named in the spec itself (hookspath below).

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

datas = [
    ("EarCons", "EarCons"),
    # The window icon. The app looks for it under sys._MEIPASS/packaging when
    # frozen, so it ships beside the sounds rather than only inside the EXE.
    ("packaging/BlindPilot.ico", "packaging"),
]
hiddenimports = []
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

    # Explorer's Properties tab and the SmartScreen prompt read the version
    # resource. Without one both show a nameless program with no version.
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    # A Windows file version is four numbers. Take what APP_VERSION has and
    # pad with zeros, so a pre-release tag does not break the build.
    _version_numbers = [int(part) for part in re.findall(r"\d+", APP_VERSION)][:4]
    _version_numbers += [0] * (4 - len(_version_numbers))
    _version_tuple = tuple(_version_numbers)
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=_version_tuple,
            prodvers=_version_tuple,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct("CompanyName", "serrebidev"),
                            StringStruct("FileDescription", "BlindPilot"),
                            StringStruct("FileVersion", APP_VERSION),
                            StringStruct("InternalName", "BlindPilot"),
                            StringStruct(
                                "LegalCopyright",
                                "Copyright (c) 2026 doubletaponair and BlindPilot contributors",
                            ),
                            StringStruct("OriginalFilename", "BlindPilot.exe"),
                            StringStruct("ProductName", "BlindPilot"),
                            StringStruct("ProductVersion", APP_VERSION),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )

    # PyInstaller's stock manifest says nothing about DPI, so Windows would
    # draw the frozen app at 96 DPI and stretch the bitmap on a high DPI
    # display. This one declares per-monitor awareness, which wxWidgets
    # 3.3 handles, with the older system-DPI flag as the fallback.
    manifest = str(spec_dir / "packaging" / "BlindPilot.manifest")
else:
    # pexpect drives the pseudo-terminal on macOS and Linux only; it is not
    # installed on Windows, where naming it made PyInstaller warn every build.
    hiddenimports += ["pexpect"]
    app_icon = None
    version_info = None
    manifest = None

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
    version=version_info,
    manifest=manifest,
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
