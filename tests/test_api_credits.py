"""Claude Code tabs on API credits (The Chat Place 0.6.2, #658).

A reply costs what its run added to the session's running total; the key
goes only to Anthropic; the overall limit stops sending; and a key that
would quietly bill a subscription tab is found.
"""

from __future__ import annotations

import json

import pytest

import api_credits
import claude_session as cs


@pytest.fixture(autouse=True)
def _own_state(tmp_path, monkeypatch):
    monkeypatch.setattr(api_credits, "app_data_dir", lambda: tmp_path)


def test_a_reply_costs_what_its_run_added_to_the_session_total():
    assert api_credits.record("s1", 0.10) == pytest.approx(0.10)
    # Resumed: the CLI reports the earlier run plus this one.
    assert api_credits.record("s1", 0.25) == pytest.approx(0.15)
    assert api_credits.record("s2", 0.05) == pytest.approx(0.05)
    assert api_credits.spent() == pytest.approx(0.30)
    assert api_credits.record("s1", None) is None


def test_the_overall_limit_stops_sending_until_raised_or_reset():
    api_credits.set_overall_limit(0.2)
    api_credits.record("s1", 0.25)
    assert api_credits.limit_reached()
    api_credits.reset_total()
    assert not api_credits.limit_reached()
    assert api_credits.overall_limit() == pytest.approx(0.2)


def test_costs_are_said_in_words():
    assert api_credits.money(0.06) == "6 cents"
    assert api_credits.money(0.01) == "1 cent"
    assert api_credits.money(0.001) == "less than a cent"
    assert api_credits.money(1.4249) == "$1.42"


def test_the_key_goes_only_to_anthropic():
    env = {"PATH": "x", "ANTHROPIC_BASE_URL": "https://proxy", "ANTHROPIC_AUTH_TOKEN": "t"}
    charged = api_credits.claude_env(env, "sk-ant-1")
    assert charged == {"PATH": "x", "ANTHROPIC_API_KEY": "sk-ant-1"}
    wants = cs.Wants(cwd=".", permission_mode="", api_key="sk-ant-1")
    # Claude Code turns Claude in Chrome off with an API key itself.
    assert "--no-chrome" in cs.build_command("claude", wants, "")


def test_a_key_that_would_bill_a_subscription_tab_is_found(tmp_path, monkeypatch):
    monkeypatch.setattr(api_credits.Path, "home", lambda: tmp_path / "home")
    assert api_credits.subscription_charged_elsewhere({}, str(tmp_path)) == ""
    assert "ANTHROPIC_API_KEY" in api_credits.subscription_charged_elsewhere(
        {"ANTHROPIC_API_KEY": "k"}
    )
    # A setup-token sign-in belongs to the subscription.
    assert api_credits.subscription_charged_elsewhere({"CLAUDE_CODE_OAUTH_TOKEN": "t"}) == ""
    settings = tmp_path / ".claude" / "settings.local.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"apiKeyHelper": "get-key.sh"}), encoding="utf-8")
    assert "apiKeyHelper" in api_credits.subscription_charged_elsewhere({}, str(tmp_path))
