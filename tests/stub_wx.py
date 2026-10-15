"""A stand-in for wx, so a settings-only test module can import blindpilot_app.

The stub has to hand back real classes, not instances: the module defines
dialogs and frames that subclass wx types, and a subclass needs a type.
Dunders are protocol, never fabricated — a made-up ``__path__`` class broke
``from wx.lib.wordwrap import wordwrap`` by handing the import system a type
where it wanted a list of search locations.
"""

from __future__ import annotations

import sys
import types


class _Stub:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return _Stub()

    def __call__(self, *args, **kwargs):
        return _Stub()


class _Module(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        # Constant-shaped names are ints in real wx: `wx.ID_HIGHEST + 1` runs
        # at import time, and a fabricated class cannot add.
        if name.startswith("ID_") or name.isupper():
            value: object = 0
        else:
            value = type(name, (_Stub,), {})
        setattr(self, name, value)
        return value


def install() -> None:
    if "wx" in sys.modules:
        return
    sys.modules["wx"] = _Module("wx")
    for name in (
        "wx.adv",
        "wx.lib",
        "wx.lib.newevent",
        "wx.lib.wordwrap",
        "wx.html",
        "wx.richtext",
        "wx.stc",
    ):
        sys.modules[name] = _Module(name)
