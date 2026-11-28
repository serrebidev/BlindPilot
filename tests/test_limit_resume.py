"""Banked Codex resets and wait-and-resume when a usage limit stops a turn."""

from __future__ import annotations

import time

import pytest

import agent_backends
import limit_resume


def _result(available: int, rows=None, windows=None) -> dict:
    return {
        "rateLimits": windows,
        "rateLimitResetCredits": {
            "availableCount": available,
            "credits": rows if rows is not None else [],
        },
    }


def test_banked_count_and_rows_come_from_the_answer():
    rows = [
        {
            "id": "RateLimitResetCredit_1",
            "status": "available",
            "grantedAt": 1781654400,
            "expiresAt": 1784246400,
            "title": "Rate-limit reset",
        }
    ]
    result = _result(2, rows=rows)
    assert agent_backends._codex_banked_count(result) == 2
    assert agent_backends._codex_banked_credits(result) == rows


def test_no_banked_credits_means_no_line_in_the_report():
    assert agent_backends._codex_banked_lines(_result(0)) == []
    assert agent_backends._codex_banked_lines({}) == []
    assert agent_backends._codex_banked_lines(None) == []


def test_banked_lines_name_the_count_and_expiry():
    result = _result(
        1,
        rows=[
            {
                "id": "x",
                "grantedAt": 1781654400,
                "expiresAt": 1784246400,
                "title": "Rate-limit reset",
            }
        ],
    )
    lines = agent_backends._codex_banked_lines(result)
    assert lines[0].startswith("Banked resets: 1 available")
    assert "/usage reset" in lines[0]
    assert any("Rate-limit reset" in line for line in lines[1:])


def test_usage_lines_carry_windows_then_banked(monkeypatch):
    monkeypatch.setattr(
        agent_backends,
        "_codex_usage_result",
        lambda _binary, _timeout: _result(
            1,
            windows={
                "limitName": None,
                "primary": {"usedPercent": 5, "windowDurationMins": 300, "resetsAt": 1788858356},
                "secondary": None,
            },
        ),
    )
    lines = agent_backends._codex_usage_lines("codex", 20)
    assert lines[0].startswith("Five-hour limit")
    assert lines[1].startswith("Banked resets: 1 available")


def test_consume_asks_the_app_server_and_returns_the_outcome(monkeypatch):
    seen = {}

    def fake_call(_binary, _timeout, method, params):
        seen["method"] = method
        seen["params"] = params
        return {"outcome": "reset"}

    monkeypatch.setattr(agent_backends, "_codex_call", fake_call)
    outcome = agent_backends.codex_consume_banked_reset("codex", 20, idempotency_key="k-1")
    assert outcome == "reset"
    assert seen["method"] == "account/rateLimitResetCredit/consume"
    assert seen["params"]["idempotencyKey"] == "k-1"
    assert "creditId" not in seen["params"]


def test_consume_passes_an_explicit_credit_and_makes_a_key_when_none_given(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        agent_backends,
        "_codex_call",
        lambda _b, _t, _m, params: seen.update(params) or {"outcome": "noCredit"},
    )
    agent_backends.codex_consume_banked_reset("codex", 20, credit_id="c-9", idempotency_key="")
    assert seen["creditId"] == "c-9"
    assert seen["idempotencyKey"]


def test_consume_says_nothing_when_the_server_does_not_answer(monkeypatch):
    monkeypatch.setattr(agent_backends, "_codex_call", lambda *_a, **_k: None)
    assert agent_backends.codex_consume_banked_reset("codex", 20) == ""


def test_backend_limit_state_only_claude_and_codex(monkeypatch):
    monkeypatch.setattr(
        agent_backends,
        "_codex_usage_result",
        lambda *_a: _result(3, windows={"limitName": None, "primary": None, "secondary": None}),
    )
    state = agent_backends.backend_limit_state("codex", "codex")
    assert state["banked_count"] == 3
    assert agent_backends.backend_limit_state("opencode", "cli")["windows"] == []


def test_a_limit_failure_is_recognised_and_a_plain_error_is_not():
    assert limit_resume.is_usage_limit_failure("You've hit your usage limit. Try again at 8 PM")
    assert limit_resume.is_usage_limit_failure("rate limit reached")
    assert limit_resume.is_usage_limit_failure("Rate limit reached. Upgrade to Pro")
    assert not limit_resume.is_usage_limit_failure("Request failed with status 500")
    assert not limit_resume.is_usage_limit_failure("the limit on retries is 3")


def test_wait_waits_for_the_reset_plus_grace_and_never_guesses():
    now = 1000.0
    assert limit_resume.wait_seconds(None, now) is None
    assert limit_resume.wait_seconds(now + 60, now) == pytest.approx(65.0)
    assert limit_resume.wait_seconds(now - 60, now) == pytest.approx(5.0)


def test_earliest_reset_is_the_next_one():
    now = time.time()
    windows = [
        agent_backends.UsageWindow("Five-hour", 100.0, now + 3600, 300.0),
        agent_backends.UsageWindow("Weekly", 100.0, now + 86400, 10080.0),
        agent_backends.UsageWindow("Past", 0.0, now - 10, 300.0),
    ]
    assert limit_resume.earliest_reset_at(windows, now) == pytest.approx(now + 3600)


def test_auto_spend_needs_a_credit_and_an_allowance_at_ten_percent():
    full = agent_backends.UsageWindow("Five-hour", 100.0, time.time() + 3600, 300.0)
    nearly = agent_backends.UsageWindow("Weekly", 91.0, time.time() + 3600, 10080.0)
    grazed = agent_backends.UsageWindow("Weekly", 20.0, time.time() + 3600, 10080.0)
    balance = agent_backends.UsageWindow("Credits", None, None, detail="left")
    assert limit_resume.auto_spend_allowed([full], 1)
    assert limit_resume.auto_spend_allowed([nearly], 1)
    assert not limit_resume.auto_spend_allowed([grazed], 1)
    assert not limit_resume.auto_spend_allowed([full], 0)
    assert not limit_resume.auto_spend_allowed([balance], 1)


def test_consume_outcomes_are_said_plainly():
    assert "open again" in limit_resume.consume_outcome_line("reset")
    assert "already spent" in limit_resume.consume_outcome_line("alreadyRedeemed")
    assert limit_resume.consume_succeeded("reset")
    assert limit_resume.consume_succeeded("alreadyRedeemed")
    assert not limit_resume.consume_succeeded("noCredit")
    assert not limit_resume.consume_succeeded("")


def test_the_settings_carry_the_two_choices():
    import blindpilot_app

    settings = blindpilot_app.SETTINGS
    assert isinstance(settings.auto_continue_at_limit, bool)
    assert isinstance(settings.spend_codex_resets, bool)
