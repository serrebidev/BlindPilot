"""Linux computer use: numbering and target checks, without a desktop.

The AT-SPI calls themselves were driven live against a zenity dialog under
WSLg (typed into its entry, pressed OK, zenity printed the text). These
tests pin what must not drift: list_controls and the actions number the
same walk, and BlindPilot's own process or a vanished target is refused.
"""

from __future__ import annotations

import os

import pytest

import computer_use_linux as linux
from computer_use import ComputerUseError


class FakeTree:
    """Desktop > app (pid 77) > window > [button OK > label, text]."""

    def __init__(self, pid: int = 77):
        self.children_of = {"desk": ["app"], "app": ["win"], "win": ["ok", "entry"], "ok": ["lbl"]}
        self.roles = {"ok": "push button", "lbl": "label", "entry": "text"}
        self.names = {"ok": "OK", "lbl": "OK", "entry": "", "app": "zenity", "win": "BPTest"}
        self.app_pid = pid
        self.released = False

    def desktop(self):
        return "desk"

    def children(self, handle):
        return self.children_of.get(handle, [])

    def role(self, handle):
        return self.roles.get(handle, "")

    def name(self, handle):
        return self.names.get(handle, "")

    def pid(self, handle):
        return self.app_pid if handle == "app" else 0

    def active(self, handle):
        return handle == "win"

    def release(self):
        self.released = True


def _session(monkeypatch, tree: FakeTree) -> linux.LinuxAtspiSession:
    monkeypatch.setattr(linux, "_Tree", lambda: tree)
    session = linux.LinuxAtspiSession()
    session._pid = 77
    return session


def test_controls_are_numbered_depth_first_and_the_tree_released(monkeypatch):
    tree = FakeTree()
    refs = _session(monkeypatch, tree).list_controls()
    assert [ref.describe() for ref in refs] == ["1. push button: OK", "2. label: OK", "3. text"]
    assert tree.released


def test_an_action_reaches_the_control_list_controls_numbered(monkeypatch):
    tree = FakeTree()
    seen: list[str] = []
    session = _session(monkeypatch, tree)
    session._act(3, lambda _tree, handle: seen.append(handle) or "done")
    assert seen == ["entry"]


def test_a_target_that_closed_is_refused(monkeypatch):
    session = _session(monkeypatch, FakeTree(pid=99))
    with pytest.raises(ComputerUseError, match="disappeared"):
        session.list_controls()


def test_blindpilot_itself_is_refused(monkeypatch):
    session = _session(monkeypatch, FakeTree(pid=os.getpid()))
    session._pid = os.getpid()
    with pytest.raises(ComputerUseError, match="BlindPilot itself"):
        session.list_controls()
