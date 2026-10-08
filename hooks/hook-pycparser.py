"""pycparser 3 uses a handwritten parser and no longer ships PLY tables."""

from importlib.util import find_spec

hiddenimports = [
    name for name in ("pycparser.lextab", "pycparser.yacctab") if find_spec(name) is not None
]
