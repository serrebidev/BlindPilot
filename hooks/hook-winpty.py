"""PyInstaller policy for WinPTY's Windows API-set dependencies.

OpenConsole links against UI Automation API-set contracts. Windows resolves
those virtual contracts through its API-set schema; they are intentionally not
physical DLLs that an application should distribute. PyInstaller already
suppresses this warning for ``api-ms-win-*`` contracts, but not the equivalent
``ext-ms-win-uiacore-*`` names.
"""

from PyInstaller.depend import dylib


dylib.missing_lib_warning_suppression_list = dylib.MatchList(
    [
        r"api-ms-win-.*\.dll",
        r"ext-ms-win-uiacore-.*\.dll",
    ]
)
