"""Approve, don't start: Claude's plan is right, but not to be built yet."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import blindpilot_app as app

Real = app.PermissionDialog


def _answer(monkeypatch: pytest.MonkeyPatch, choice: int, note: str = "") -> tuple[dict, list]:
    class Dialog:
        APPROVE_EDITS, APPROVE_ASK, HOLD = Real.APPROVE_EDITS, Real.APPROVE_ASK, Real.HOLD
        ALLOW, ALLOW_SESSION, DENY = Real.ALLOW, Real.ALLOW_SESSION, Real.DENY

        def __init__(self, *_args: object) -> None:
            pass

        def ShowModal(self) -> int:
            return choice

        def reason(self) -> str:
            return note

        def Destroy(self) -> None:
            pass

    monkeypatch.setattr(app, "PermissionDialog", Dialog)
    monkeypatch.setattr(app, "_notify_if_away", lambda *_args: None)
    said: list[str] = []
    modes: list[str] = []
    tab = SimpleNamespace(
        _announce=lambda text, **_kw: said.append(text),
        _earcons=SimpleNamespace(stop_progress=lambda: None, start_progress=lambda: None),
        _hide_working=lambda: None,
        _show_working=lambda: None,
        _worker=None,
        _set_mode=modes.append,
    )
    answer = app.SessionPanel._show_permission_dialog(
        tab, app.BACKEND_CLAUDE, "ExitPlanMode", {"plan": "Do it"}, []
    )
    return answer, (said + modes)[1:]  # [0] is "has a plan for you to approve"


def test_dont_start_is_a_deny_that_says_the_plan_is_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer, said = _answer(monkeypatch, Real.HOLD, "after lunch")
    assert answer["behavior"] == "deny"
    assert answer["message"].startswith(app.PLAN_HELD_MESSAGE)
    assert answer["message"].endswith("They said: after lunch")
    # No mode change: the tab stays in plan mode.
    assert said == ["Plan approved. Claude won't start on it."]


def test_keep_planning_still_says_keep_planning(monkeypatch: pytest.MonkeyPatch) -> None:
    answer, said = _answer(monkeypatch, Real.DENY)
    assert answer == {"behavior": "deny", "message": "The user wants to keep planning."}
    assert said == ["Kept planning"]
