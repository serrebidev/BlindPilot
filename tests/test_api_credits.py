"""Claude Code tabs on API credits (The Chat Place 0.6.2, #658).

A reply costs what its run added to the session's running total; the key
goes only to Anthropic; the overall limit stops sending; and a key that
would quietly bill a subscription tab is found.
"""

from __future__ import annotations

import json
import time

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


def test_claude_and_codex_keep_their_own_limits_and_totals():
    api_credits.record("s1", 0.5)
    api_credits.add(0.25, "codex")
    api_credits.set_message_limit(1.5, "codex")
    assert api_credits.spent("claude") == pytest.approx(0.5)
    assert api_credits.spent("codex") == pytest.approx(0.25)
    assert api_credits.message_limit("codex") == pytest.approx(1.5)
    assert api_credits.message_limit("claude") == api_credits.DEFAULT_MESSAGE_LIMIT
    api_credits.reset_total("codex")
    assert api_credits.spent("codex") == 0
    assert api_credits.spent("claude") == pytest.approx(0.5)


def test_a_total_kept_by_0_61_is_still_claudes(tmp_path):
    (tmp_path / "api_credits.json").write_text(
        json.dumps({"spent": 3.0, "overall_limit": 5.0, "session_totals": {"s1": 3.0}}),
        encoding="utf-8",
    )
    assert api_credits.spent("claude") == pytest.approx(3.0)
    assert api_credits.overall_limit("claude") == pytest.approx(5.0)
    assert api_credits.record("s1", 3.5) == pytest.approx(0.5)
    assert api_credits.spent("codex") == 0


def test_costs_are_worked_out_from_each_models_prices():
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_read_input_tokens": 1_000_000,
        "cache_creation_input_tokens": 1_000_000,
    }
    assert api_credits.claude_cost("claude-opus-5-5", usage) == pytest.approx(4 + 20 + 0.2 + 5)
    assert api_credits.claude_cost("claude-opus-5-5[1m]", usage) == pytest.approx(29.2)
    assert api_credits.claude_cost("claude-opus-4-8", usage) == pytest.approx(5 + 25 + 0.5 + 6.25)
    assert api_credits.claude_cost("claude-new-thing", usage) is None
    codex = {"inputTokens": 1_000_000, "cachedInputTokens": 400_000, "outputTokens": 100_000}
    # 600k fresh at $4, 400k cached at $0.40, 100k out at $20.
    assert api_credits.codex_cost("gpt-5.6-sol", codex) == pytest.approx(2.4 + 0.16 + 2.0)
    assert api_credits.codex_cost("gpt-5-mini", codex) == pytest.approx(0.15 + 0.01 + 0.2)
    # A later model is unknown, not priced as the family it starts like.
    assert api_credits.codex_cost("gpt-5.7", codex) is None


def test_codex_on_api_credits_sends_the_key_only_to_openai():
    args = api_credits.codex_args()
    pairs = dict(arg.split("=", 1) for arg in args if arg != "-c")
    provider = f"model_providers.{api_credits.CODEX_PROVIDER_ID}"
    assert pairs["model_provider"] == json.dumps(api_credits.CODEX_PROVIDER_ID)
    assert pairs[f"{provider}.base_url"] == '"https://api.openai.com/v1"'
    assert pairs[f"{provider}.env_key"] == json.dumps(api_credits.CODEX_KEY_ENV)
    env = api_credits.codex_env({"PATH": "x"}, "sk-1")
    assert env == {"PATH": "x", api_credits.CODEX_KEY_ENV: "sk-1"}


def _claude_turn(limit: float, monkeypatch):
    import claude_worker

    api_credits.set_message_limit(limit, "claude")
    worker = object.__new__(claude_worker.ClaudeWorker)
    worker._api_key = "sk-ant-1"
    worker._turn_spend = {}
    worker._limit_said = False
    said: list[str] = []
    worker._on_activity = lambda kind, text: said.append(text)
    stopped: list[bool] = []
    monkeypatch.setattr(worker, "cancel", lambda: stopped.append(True), raising=False)
    return worker, said, stopped


def test_a_claude_reply_stops_at_the_per_message_limit(monkeypatch):
    worker, said, stopped = _claude_turn(1.0, monkeypatch)
    message = {"id": "m1", "model": "claude-opus-5-5", "usage": {"output_tokens": 30_000}}
    worker._count_spend(message)  # 60 cents
    worker._count_spend(message)  # the same response again, block by block
    assert not said
    worker._count_spend({**message, "id": "m2"})  # $1.20 in all
    deadline = time.monotonic() + 5
    while not stopped and time.monotonic() < deadline:
        time.sleep(0.01)
    assert stopped
    assert said == ["Stopped at the $1.00 per-message limit for API credits."]


def _codex_turn(model: str, monkeypatch):
    from backend_workers import codex_worker

    worker = object.__new__(codex_worker.CodexWorker)
    worker._api_key = "sk-1"
    worker._turn_id = "t1"
    worker._codex_model = model
    worker._turn_cost = 0.0
    worker._turn_tokens = 0
    worker._priced = True
    worker._charged = False
    worker._limit_said = False
    said: list[str] = []
    worker._on_activity = lambda kind, text: said.append(text)
    monkeypatch.setattr(worker, "cancel", lambda: None, raising=False)
    return worker, said


def test_a_codex_reply_says_its_estimated_cost(monkeypatch):
    worker, said = _codex_turn("gpt-5.6-sol", monkeypatch)
    last = {
        "inputTokens": 10_000,
        "cachedInputTokens": 0,
        "outputTokens": 1_000,
        "totalTokens": 11_000,
    }
    worker._count_cost({"turnId": "t1"}, last)  # 4 cents + 2 cents
    worker._count_cost({"turnId": "older"}, last)  # a resume repeating the last turn's
    worker._charge_turn()
    worker._charge_turn()
    assert said == ["Cost: an estimated 6 cents"]
    assert api_credits.spent("codex") == pytest.approx(0.06)


def test_a_price_unknown_codex_reply_uses_up_the_per_message_limit(monkeypatch):
    api_credits.set_message_limit(2.0, "codex")
    worker, said = _codex_turn("gpt-9-future", monkeypatch)
    worker._count_cost({}, {"totalTokens": 12_400})
    worker._charge_turn()
    assert said == ["Cost: unknown, 12,400 tokens"]
    assert api_credits.spent("codex") == pytest.approx(2.0)
