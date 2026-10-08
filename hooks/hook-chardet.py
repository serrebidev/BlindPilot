"""Keep chardet's model data and compiled modules under their real names."""

from importlib.util import find_spec

from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from _pyinstaller_hooks_contrib.utils.mypy import find_mypyc_module_for_dist

# The stock hook names nested __mypyc extensions as top-level imports.
hiddenimports = collect_submodules("chardet") + [
    name for name in find_mypyc_module_for_dist("chardet") if find_spec(name) is not None
]
datas = collect_data_files("chardet")
