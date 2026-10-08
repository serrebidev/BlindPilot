"""Codex asks before using a tool in Default mode, like Claude Code does.

Outside Bypass permissions, Codex approval requests were answered from the
permission mode without asking anyone, so Default mode could only ever
refuse. In Default mode the request now opens the permission dialog; the
other modes keep their automatic answers.
"""

from __future__ import annotations

import agent_backends as ab


def _worker(answer):
    worker = ab.CodexWorker.__new__(ab.CodexWorker)
    worker._on_permission = lambda tool, payload, suggestions: answer
    return worker


def test_allow_becomes_accept():
    worker = _worker({"behavior": "allow"})
    assert (
        worker._ask_codex_permission({"params": {"command": "rm -rf /"}}, "command")
        == "accept"
    )


def test_deny_becomes_decline():
    worker = _worker({"behavior": "deny", "message": "no"})
    assert (
        worker._ask_codex_permission({"params": {"command": "rm -rf /"}}, "command")
        == "decline"
    )


def test_a_failed_ask_denies():
    def boom(tool, payload, suggestions):
        raise RuntimeError("tab gone")

    worker = ab.CodexWorker.__new__(ab.CodexWorker)
    worker._on_permission = boom
    assert (
        worker._ask_codex_permission({"params": {"command": "rm -rf /"}}, "command")
        == "decline"
    )


def test_the_dialog_sees_the_command():
    seen = {}

    def capture(tool, payload, suggestions):
        seen["tool"] = tool
        seen["payload"] = payload
        return {"behavior": "deny"}

    worker = ab.CodexWorker.__new__(ab.CodexWorker)
    worker._on_permission = capture
    worker._ask_codex_permission(
        {"params": {"command": "git push", "cwd": "/repo", "noise": True}}, "command"
    )
    assert seen["tool"] == "git push"
    assert seen["payload"] == {"command": "git push", "cwd": "/repo"}
