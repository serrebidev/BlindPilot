from __future__ import annotations

import ast
import socket
import subprocess
import urllib.request
from pathlib import Path

import blindpilot_app as app
from blocking_contract import BlockedTheGuiThread, _GUARDED, gui_thread_may_not_block


def test_every_forbidden_primitive_is_blocked_without_handler_exceptions():
    with gui_thread_may_not_block():
        for owner, name, message, _offends in _GUARDED:
            entry = getattr(owner, name)
            if owner in (subprocess, socket, urllib.request):
                args = ()
            else:
                args = (object(),)
            try:
                entry(*args)
            except BlockedTheGuiThread as exc:
                assert str(exc) == message
            else:
                owner_name = getattr(owner, "__name__", type(owner).__name__)
                raise AssertionError(f"{owner_name}.{name} escaped the GUI guard")


def test_every_menu_and_timer_binding_uses_the_budget_wrapper():
    source = Path(app.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    uncovered: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "Bind" or len(node.args) < 2:
            continue
        event = node.args[0]
        if not isinstance(event, ast.Attribute) or event.attr not in {"EVT_MENU", "EVT_TIMER"}:
            continue
        handler = node.args[1]
        wrapped = (
            isinstance(handler, ast.Call)
            and isinstance(handler.func, ast.Name)
            and handler.func.id == "_gui_work"
        )
        scope = parents.get(node)
        while scope is not None and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = parents.get(scope)
        centralized = isinstance(scope, ast.FunctionDef) and scope.name in {
            "_menu_item",
            "_bind_shortcut",
        }
        if not wrapped and not centralized:
            uncovered.append(f"line {node.lineno}: {ast.unparse(handler)}")

    assert not uncovered, "Unmeasured menu/timer callbacks:\n" + "\n".join(uncovered)


def test_callafter_and_calllater_are_measured(monkeypatch):
    recorded: list[tuple[str, object]] = []
    scheduled: list[object] = []

    def measured(name, action):
        recorded.append((name, action))
        return action

    monkeypatch.setattr(app, "_gui_work", measured)
    monkeypatch.setattr(app, "_ORIGINAL_CALL_AFTER", lambda callback: scheduled.append(callback))
    monkeypatch.setattr(
        app,
        "_ORIGINAL_CALL_LATER",
        lambda _milliseconds, callback: scheduled.append(callback) or object(),
    )
    app.wx.CallAfter(lambda: None)
    app.wx.CallLater(10, lambda: None)

    assert [name.split(":", 1)[0] for name, _ in recorded] == ["CallAfter", "CallLater"]
    assert len(scheduled) == 2
