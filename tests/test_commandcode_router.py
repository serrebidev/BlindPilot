"""The Command Code router without a Command Code install.

Everything here answers from a fixed catalog and fixed clocks -- no CLI, no
network, no account. The price parser is exercised on a catalog snippet in the
CLI's own table shape, so a format change fails a test rather than silently
tying the router to nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import multiprocessing
import os
from pathlib import Path

import commandcode_router as router
import pytest

CATALOG = """| Id (use EXACTLY this) | Name | Context | Efforts | $/1M in/out · cache read | Min plan | Best for |
|---|---|---|---|---|---|---|
| `deepseek/deepseek-v4-pro` | DeepSeek V4 Pro | 1M | off, high, max | $0.66/$1.98 · cache $0.022 | Go and above | reasoning |
| `deepseek/deepseek-v4-flash` | DeepSeek V4 Flash | 1M | off, high, max | $0.15/$0.6 · cache $0.003 | Go and above | fast |
| `inclusionai/ling-3.1-flash:free` | Ling 3.1 Flash | 262K | low, medium, high | $0/$0 · cache $0 | Go and above | cheap |
| `moonshotai/kimi-k3` | Kimi K3 | 1M | low, high, max | $3/$15 · cache $0.3 | Go and above | long |
| `claude-sonnet-5` | Claude Sonnet 5 | 1M | low, medium, high | $2/$10 · cache $0.2 (write $2.5) | Pro and above | smart |
"""


def _record_limit_after_read(state_path: str, read_ready, continue_write) -> None:
    """Write a short cooldown after another process has reached the same state."""
    path = Path(state_path)
    original_read_text = Path.read_text
    paused = False

    def pause_after_read(self, *args, **kwargs):
        nonlocal paused
        result = original_read_text(self, *args, **kwargs)
        if self == path and not paused:
            paused = True
            read_ready.set()
            assert continue_write.wait(10)
        return result

    Path.read_text = pause_after_read
    try:
        assert router.record_limit_hit(5, now=datetime(2026, 10, 14, 2, tzinfo=timezone.utc))
    finally:
        Path.read_text = original_read_text


def _hold_usage_state_lock(state_path: str, acquired, release) -> None:
    with router._usage_state_file_lock(Path(state_path)):
        acquired.set()
        assert release.wait(10)


def _record_limit_in_process(state_path: str, code: int, attempting, completed) -> None:
    os.environ[router._USAGE_STATE_ENV] = state_path
    attempting.set()
    assert router.record_limit_hit(code, now=datetime(2026, 10, 14, 2, tzinfo=timezone.utc))
    completed.set()


def test_parse_prices_reads_the_table_shape():
    catalog = router.parse_prices(CATALOG)
    assert set(catalog) == {
        "deepseek/deepseek-v4-pro",
        "deepseek/deepseek-v4-flash",
        "inclusionai/ling-3.1-flash:free",
        "moonshotai/kimi-k3",
        "claude-sonnet-5",
    }
    flash = catalog["deepseek/deepseek-v4-flash"]
    assert (flash["input"], flash["output"], flash["cacheRead"]) == (0.15, 0.6, 0.003)
    assert flash["free"] is False
    assert flash["efforts"] == ["off", "high", "max"]
    assert "peak" in flash  # DeepSeek bills a peak pair
    assert catalog["inclusionai/ling-3.1-flash:free"]["free"] is True
    assert catalog["claude-sonnet-5"]["minPlan"] == "pro and above"


def test_parse_prices_ignores_prose_and_headings():
    assert router.parse_prices("# Heading\n\nPass the full id, or just the short name.\n") == {}


def test_classify_tiers():
    assert router.classify("Fix the small typo in the label") == router.QUICK
    assert router.classify("Find the setting that controls focus return") == router.QUICK
    assert router.classify("Refactor the authentication flow") == router.DEEP
    assert router.classify("Explain the auth flow") == router.DEEP  # risk beats lookup
    assert router.classify("Fix a concurrency bug") == router.DEEP
    assert router.classify("Add a caching layer to the media index service") == router.STANDARD


def test_classify_question_forms_are_lookups():
    assert router.classify("How does the router pick a model") == router.QUICK
    assert router.classify("why is the export empty") == router.QUICK
    assert router.classify("does the worker retry on its own") == router.QUICK
    assert router.classify("is the markdown renderer slow today") == router.QUICK


def test_classify_questions_about_risky_work_stay_deep():
    # The high-risk scan runs before the lookup match, so a question
    # about risky work still lands on the safer tier.
    assert router.classify("should I refactor the authentication flow") == router.DEEP
    assert router.classify("can you check the screen reader output") == router.DEEP


def test_plan_eligibility():
    assert router.plan_eligible("go and above", "go") is True
    assert router.plan_eligible("goat and above", "go") is False
    assert router.plan_eligible("goat and above", "goat") is True
    assert router.plan_eligible("pro and above", "go") is False
    assert router.plan_eligible("pro and above", "pro") is True
    assert router.plan_eligible("max", "go") is False


def test_select_quick_takes_the_free_model():
    catalog = router.parse_prices(CATALOG)
    chosen = router.select(router.QUICK, catalog)
    assert chosen is not None
    assert chosen.model == "inclusionai/ling-3.1-flash:free"
    assert chosen.cost == 0.0
    assert chosen.priced is True


def test_select_no_free_excludes_free_for_quick():
    catalog = router.parse_prices(CATALOG)
    chosen = router.select(router.QUICK, catalog, include_free=False)
    assert chosen is not None
    assert chosen.model == "deepseek/deepseek-v4-flash"


def test_select_standard_and_deep_never_take_a_free_model():
    catalog = dict(router.parse_prices(CATALOG))
    # A free model eligible on price alone must not be reached by a heavier tier.
    catalog["poolside/laguna-s-2.1-free"] = {
        "input": 0.0,
        "output": 0.0,
        "cacheRead": 0.0,
        "minPlan": "go and above",
        "efforts": [],
        "free": True,
    }
    standard = router.select(router.STANDARD, catalog)
    assert standard is not None and standard.model == "deepseek/deepseek-v4-pro"
    deep = router.select(router.DEEP, catalog)
    assert deep is not None and deep.model == "deepseek/deepseek-v4-pro"


def test_select_filters_plan_restricted_models():
    catalog = router.parse_prices(CATALOG)
    # claude-sonnet-5 is Pro-only and is not a candidate anyway; prove filtering
    # on a candidate: a Pro-only deepseek must be skipped at Go.
    catalog["deepseek/deepseek-v4-pro"] = dict(catalog["deepseek/deepseek-v4-pro"])
    catalog["deepseek/deepseek-v4-pro"]["minPlan"] = "pro and above"
    chosen = router.select(router.STANDARD, catalog)
    assert chosen is None  # no other standard candidate is present


def test_route_falls_back_to_the_static_table():
    chosen = router.route("Fix the small typo", catalog={})
    assert chosen.model == router._STATIC_ROUTES[router.QUICK]["model"]
    assert chosen.effort == "max"


def test_route_declines_a_present_catalog_without_an_eligible_model():
    chosen = router.route("Explain the media index", catalog={"claude-sonnet-5": {}})
    assert chosen.model == ""
    assert chosen.effort == ""
    assert chosen.tier == router.QUICK
    assert chosen.priced is False


def test_route_uses_the_catalog_when_present():
    catalog = router.parse_prices(CATALOG)
    chosen = router.route("Explain the media index", catalog=catalog)
    assert chosen.tier == router.QUICK
    assert chosen.model == "inclusionai/ling-3.1-flash:free"


def test_best_free_model_uses_current_catalog_and_skips_current_model():
    catalog = router.parse_prices(CATALOG)
    catalog["poolside/laguna-s-2.1-free"] = {
        "input": 0.0,
        "output": 0.0,
        "cacheRead": 0.0,
        "minPlan": "go and above",
        "efforts": [],
        "free": True,
    }
    chosen = router.best_free_model(catalog=catalog)
    assert chosen is not None and chosen.model == "inclusionai/ling-3.1-flash:free"
    alternate = router.best_free_model(
        catalog=catalog, exclude_model="inclusionai/ling-3.1-flash:free"
    )
    assert alternate is not None and alternate.model == "poolside/laguna-s-2.1-free"


def test_best_free_model_never_guesses_when_price_or_plan_is_unconfirmed():
    catalog = router.parse_prices(CATALOG)
    catalog["inclusionai/ling-3.1-flash:free"]["free"] = False
    assert router.best_free_model(catalog=catalog) is None
    assert router.best_free_model(catalog={}) is None


def test_catalog_prefers_the_worker_cli_over_path(monkeypatch, tmp_path):
    worker_cli = tmp_path / "managed" / "command-code.cmd"
    worker_cli.parent.mkdir()
    worker_cli.write_text("", encoding="utf-8")
    worker_catalog = (
        worker_cli.parent
        / "node_modules"
        / "command-code"
        / "dist"
        / "bundled"
        / "command-code-knowledge"
        / "reference"
        / "models.md"
    )
    worker_catalog.parent.mkdir(parents=True)
    worker_catalog.write_text("worker", encoding="utf-8")
    path_cli = tmp_path / "path" / "command-code.cmd"
    path_cli.parent.mkdir()
    path_cli.write_text("", encoding="utf-8")
    path_catalog = (
        path_cli.parent
        / "node_modules"
        / "command-code"
        / "dist"
        / "bundled"
        / "command-code-knowledge"
        / "reference"
        / "models.md"
    )
    path_catalog.parent.mkdir(parents=True)
    path_catalog.write_text("path", encoding="utf-8")

    import agent_backends

    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _backend: str(worker_cli))
    monkeypatch.setattr(router.shutil, "which", lambda _name: str(path_cli))
    assert router._load_catalog_file()[0] == "worker"


def test_is_peak_window():
    peak = datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc)  # Wednesday 02:00Z
    off = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    weekend = datetime(2026, 10, 17, 2, 0, tzinfo=timezone.utc)  # Saturday
    assert router.is_peak(peak) is True
    assert router.is_peak(off) is False
    assert router.is_peak(weekend) is False


def test_describe_names_the_model_and_tier():
    chosen = router.Route(model="deepseek/deepseek-v4-flash", effort="xhigh", tier=router.QUICK)
    line = chosen.describe()
    assert "deepseek/deepseek-v4-flash" in line
    assert "quick" in line


def test_select_withholds_paid_models_while_cooling_down():
    catalog = router.parse_prices(CATALOG)
    quick = router.select(router.QUICK, catalog, cooldown=True)
    assert quick is not None
    assert quick.model == "inclusionai/ling-3.1-flash:free"
    assert quick.cooldown is True
    assert router.select(router.QUICK, catalog, cooldown=True, include_free=False) is None
    assert router.select(router.STANDARD, catalog, cooldown=True) is None
    assert router.select(router.DEEP, catalog, cooldown=True) is None


def test_route_runs_quick_on_free_while_cooling_down(monkeypatch, tmp_path):
    state = tmp_path / "commandcode-usage.json"
    monkeypatch.setenv("COMMANDCODE_USAGE_JSON", str(state))
    assert router.cooling_down() is False
    assert router.record_limit_hit(5)
    assert router.cooling_down() is True
    catalog = router.parse_prices(CATALOG)
    quick = router.route("Find the setting that controls focus return", catalog=catalog)
    assert quick.model == "inclusionai/ling-3.1-flash:free"
    assert quick.cooldown is True
    assert "cools down" in quick.describe()
    forced = router.route(
        "Find the setting that controls focus return", catalog=catalog, static=True
    )
    assert forced.model == quick.model
    for task, tier in (
        ("Add a caching layer to the media index service", router.STANDARD),
        ("Refactor the authentication flow", router.DEEP),
    ):
        chosen = router.route(task, catalog=catalog)
        assert chosen.model == "inclusionai/ling-3.1-flash:free"
        assert chosen.tier == tier
        assert chosen.cooldown is True
        assert "free model during cooldown" in chosen.describe()
        assert "cools down" in chosen.describe()
    assert (
        router.route("Refactor the authentication flow", catalog=catalog, include_free=False).model
        == ""
    )
    catalog["inclusionai/ling-3.1-flash:free"]["minPlan"] = "max"
    unavailable = router.route("Refactor the authentication flow", catalog=catalog)
    assert unavailable.model == ""
    assert unavailable.cooldown is True


def test_route_without_catalog_keeps_configured_model_during_cooldown(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMANDCODE_USAGE_JSON", str(tmp_path / "usage.json"))
    moment = datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc)
    assert router.record_limit_hit(5, now=moment)

    for static in (False, True):
        for task in ("Fix the small typo", "Add a caching layer to the media index service"):
            chosen = router.route(task, catalog={}, now=moment, static=static)
            assert chosen.cooldown is True
            assert chosen.model == ""
            assert chosen.effort == ""


def test_recorded_cooldown_expires(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMANDCODE_USAGE_JSON", str(tmp_path / "usage.json"))
    moment = datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc)
    assert router.record_limit_hit(10, now=moment)
    assert router.cooling_down(now=moment) is True
    assert router.cooling_down(now=moment + timedelta(minutes=29)) is True
    assert router.cooling_down(now=moment + timedelta(minutes=31)) is False


def test_malformed_cooldown_value_does_not_block_new_limit(monkeypatch, tmp_path):
    state = tmp_path / "usage.json"
    state.write_text('{"commandcode": {"cooldownUntil": 123}}', encoding="utf-8")
    monkeypatch.setenv("COMMANDCODE_USAGE_JSON", str(state))
    moment = datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc)

    assert router.record_limit_hit(10, now=moment)
    assert router.cooling_down(now=moment + timedelta(minutes=29)) is True


def test_short_rate_limit_does_not_shorten_credit_cooldown(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMANDCODE_USAGE_JSON", str(tmp_path / "usage.json"))
    moment = datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc)

    assert router.record_limit_hit(10, now=moment)
    assert router.record_limit_hit(5, now=moment + timedelta(minutes=1))

    assert router.cooling_down(now=moment + timedelta(minutes=29)) is True


def test_short_cooldown_cannot_overwrite_longer_cooldown_from_another_process(
    monkeypatch, tmp_path
):
    state = tmp_path / "usage.json"
    state.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("COMMANDCODE_USAGE_JSON", str(state))
    context = multiprocessing.get_context("spawn")
    read_ready = context.Event()
    continue_write = context.Event()
    writer = context.Process(
        target=_record_limit_after_read,
        args=(str(state), read_ready, continue_write),
    )
    writer.start()
    try:
        assert read_ready.wait(10)
        moment = datetime(2026, 10, 14, 2, tzinfo=timezone.utc)
        assert router.record_limit_hit(10, now=moment)
        continue_write.set()
        writer.join(10)
        assert writer.exitcode == 0
    finally:
        continue_write.set()
        if writer.is_alive():
            writer.terminate()
            writer.join()

    assert router.cooling_down(now=moment + timedelta(minutes=29)) is True


@pytest.mark.skipif(os.name != "nt", reason="uses Windows msvcrt locking")
def test_windows_usage_state_lock_blocks_a_competing_process(monkeypatch, tmp_path):
    state = tmp_path / "usage.json"
    state.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("COMMANDCODE_USAGE_JSON", str(state))
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    attempting = context.Event()
    completed = context.Event()
    holder = context.Process(target=_hold_usage_state_lock, args=(str(state), acquired, release))
    writer = context.Process(
        target=_record_limit_in_process,
        args=(str(state), 10, attempting, completed),
    )
    holder.start()
    try:
        assert acquired.wait(10)
        writer.start()
        assert attempting.wait(10)
        assert completed.wait(0.25) is False
        release.set()
        holder.join(10)
        writer.join(10)
        assert holder.exitcode == 0
        assert writer.exitcode == 0
    finally:
        release.set()
        for process in (holder, writer):
            if process.is_alive():
                process.terminate()
                process.join()

    moment = datetime(2026, 10, 14, 2, tzinfo=timezone.utc)
    assert router.cooling_down(now=moment + timedelta(minutes=29)) is True


def test_failed_atomic_state_replacement_keeps_original(monkeypatch, tmp_path):
    state = tmp_path / "usage.json"
    state.write_text('{"other": "preserved"}', encoding="utf-8")
    monkeypatch.setenv("COMMANDCODE_USAGE_JSON", str(state))

    def fail_replace(*_args):
        raise OSError("replacement failed")

    monkeypatch.setattr(router.os, "replace", fail_replace)

    assert router.record_limit_hit(5) is False

    assert state.read_text(encoding="utf-8") == '{"other": "preserved"}'
    # The sidecar lock is removed after release, so only the untouched state
    # file and nothing else are left behind.
    assert sorted(path.name for path in tmp_path.iterdir()) == ["usage.json"]


def test_other_exit_codes_are_not_limit_hits(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMANDCODE_USAGE_JSON", str(tmp_path / "usage.json"))
    assert router.record_limit_hit(1) is False
    assert router.cooling_down() is False


def test_a_state_file_that_cannot_be_parsed_is_no_cooldown(monkeypatch, tmp_path):
    state = tmp_path / "usage.json"
    state.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("COMMANDCODE_USAGE_JSON", str(state))
    assert router.cooling_down() is False


def test_describe_names_a_cooldown_pick():
    line = router.Route(
        model="inclusionai/ling-3.1-flash:free", effort="", tier=router.QUICK, cooldown=True
    ).describe()
    assert "cools down" in line


# --------------------------------------------------------------------------
# Tier-scoped effort
# --------------------------------------------------------------------------


def test_tier_effort_prefers_the_tiers_level_from_the_models_own_list():
    entry = {"efforts": ["low", "medium", "high", "max"]}
    assert router.tier_effort(router.QUICK, entry) == "low"
    assert router.tier_effort(router.STANDARD, entry) == "high"
    assert router.tier_effort(router.DEEP, entry) == "max"
    # xhigh counts as strong for a deep task when the model lists no max.
    assert router.tier_effort(router.DEEP, {"efforts": ["low", "xhigh"]}) == "xhigh"
    # No listed level the tier prefers: the strongest own level answers.
    assert router.tier_effort(router.STANDARD, {"efforts": ["low", "max"]}) == "max"
    assert router.tier_effort(router.QUICK, {"efforts": ["off"]}) == ""
    assert router.tier_effort(router.QUICK, {}) == ""
    assert router.tier_effort(router.QUICK, {"efforts": ["LOW", " High "]}) == "low"


def test_select_and_the_static_path_use_the_tiers_effort():
    catalog = router.parse_prices(CATALOG)
    quick = router.select(router.QUICK, catalog)  # ling offers low, medium, high
    assert quick is not None and quick.effort == "low"
    standard = router.select(router.STANDARD, catalog)  # v4-pro offers off, high, max
    assert standard is not None and standard.effort == "high"
    # The static table keeps its own "max" when the catalog cannot name levels.
    assert router.route("Fix the small typo", catalog={}).effort == "max"


# --------------------------------------------------------------------------
# classify: new risky terms, and the small-edit exemption
# --------------------------------------------------------------------------


def test_classify_sends_new_high_risk_domains_to_deep():
    assert router.classify("Add a database index and update the schema") == router.DEEP
    assert router.classify("Review the billing plan picker") == router.DEEP
    assert router.classify("Fix the login password reset flow") == router.DEEP
    assert router.classify("Rotate the encryption secrets in the deploy config") == router.DEEP
    assert router.classify("Restore the backup and drop the stale table") == router.DEEP
    assert router.classify("Find the performance regression in the worker") == router.DEEP
    assert router.classify("Investigate the deadlock in the pool") == router.DEEP


def test_classify_a_small_note_about_a_risky_topic_stays_quick():
    # Naming a risky word in a purely additive note does not make the job
    # deep; the risky work itself still is.
    assert router.classify("Add a note about the payment flow") == router.QUICK
    assert router.classify("Add a label for the password field") == router.QUICK
    assert router.classify("Add a comment text about the token") == router.QUICK
    assert router.classify("add a security note to the README") == router.QUICK
    assert router.classify("Implement the payment flow") == router.DEEP
    assert router.classify("Migrate the billing tables") == router.DEEP


def test_classify_a_destructive_verb_keeps_the_high_risk_tier():
    # The documentation escape is additive-only: a request that removes or
    # reworks stays deep however small the edit or documentation-shaped the
    # object looks.
    assert router.classify("remove the password string") == router.DEEP
    assert router.classify("rotate the encryption secret") == router.DEEP
    assert router.classify("Change the comment text about the token") == router.DEEP
    assert router.classify("delete the auth note") == router.DEEP
    assert router.classify("add a note about auth and delete the backup") == router.DEEP


def test_classify_review_inspect_and_look_are_lookups():
    assert router.classify("review the markdown renderer") == router.QUICK
    assert router.classify("inspect the tab strip layout") == router.QUICK
    assert router.classify("look at the focus order") == router.QUICK


# --------------------------------------------------------------------------
# Defensive parsing
# --------------------------------------------------------------------------


def test_model_cost_skips_a_non_numeric_catalog_price():
    assert router.model_cost({"input": "n/a", "output": None, "cacheRead": ""}) == 0.0
    # A non-numeric peak cell falls back to the base rate rather than crashing.
    peak = router.model_cost({"input": 1, "output": 1, "cacheRead": 0, "peak": {"input": "oops"}})
    assert peak == pytest.approx((800 * 1 + 160 * 1 + 50000 * 0) / 1_000_000)
    priced = router.model_cost({"input": 0.5, "output": 1, "cacheRead": "0.01"})
    assert priced == pytest.approx((800 * 0.5 + 160 * 1 + 50000 * 0.01) / 1_000_000)


def test_plan_matching_is_whole_words_not_substrings():
    assert router.plan_eligible("  GOAT  ", "go") is False
    assert router.plan_eligible("  goat  ", "goat") is True
    assert router.plan_eligible("Pro and above", "pro") is True
    # A plan name inside a longer word is not a plan name.
    assert router.plan_eligible("promax and above", "go") is True
    assert router.plan_eligible("maxplan", "go") is True


def test_model_cost_scales_the_cache_weight_with_the_conversation():
    entry = {"input": 1, "output": 1, "cacheRead": 0.1}
    default = router.model_cost(entry)
    short = router.model_cost(entry, context_tokens=1_000)
    long = router.model_cost(entry, context_tokens=200_000)
    assert short < default < long
    # The default is the fixed typical session: 50000 cache-read tokens.
    assert default == pytest.approx((800 + 160 + 50000 * 0.1) / 1_000_000)


# --------------------------------------------------------------------------
# Capacity cooldowns
# --------------------------------------------------------------------------


def test_capacity_hit_withholds_only_that_model_and_expires(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMANDCODE_USAGE_JSON", str(tmp_path / "usage.json"))
    moment = datetime(2026, 10, 14, 2, tzinfo=timezone.utc)
    model = "deepseek/deepseek-v4-flash"

    assert router.record_capacity_hit(model, now=moment) is True
    assert router.capacity_cooling_down(model, now=moment) is True
    assert router.capacity_cooling_down(model, now=moment + timedelta(minutes=11)) is False
    # Another model, and the account-wide cooldown, are untouched.
    assert router.capacity_cooling_down("deepseek/deepseek-v4-pro", now=moment) is False
    assert router.cooling_down(now=moment) is False

    catalog = router.parse_prices(CATALOG)
    # With the cooled model withheld, no paid quick candidate remains.
    assert router.select(router.QUICK, catalog, include_free=False, now=moment) is None
    assert (
        router.select(router.QUICK, catalog, include_free=False, now=moment + timedelta(minutes=11))
        is not None
    )


def test_capacity_stop_withholds_a_free_model_too(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMANDCODE_USAGE_JSON", str(tmp_path / "usage.json"))
    moment = datetime(2026, 10, 14, 2, tzinfo=timezone.utc)
    free = "inclusionai/ling-3.1-flash:free"
    catalog = router.parse_prices(CATALOG)
    catalog["poolside/laguna-s-2.1-free"] = {
        "input": 0.0,
        "output": 0.0,
        "cacheRead": 0.0,
        "minPlan": "go and above",
        "efforts": [],
        "free": True,
    }

    assert router.record_capacity_hit(free, now=moment) is True
    assert router.record_capacity_hit("poolside/laguna-s-2.1-free", now=moment) is True
    # A capacity-recorded free model is withheld from selection AND the
    # free-fallback walk, the same way a paid one is.
    quick = router.select(router.QUICK, catalog, now=moment)
    assert quick is not None and quick.model == "deepseek/deepseek-v4-flash"
    assert router.best_free_model(catalog=catalog, now=moment) is None
    # The entries expire on their own window, like a paid one.
    later = moment + timedelta(minutes=11)
    quick = router.select(router.QUICK, catalog, now=later)
    assert quick is not None and quick.model == free
    fallback = router.best_free_model(catalog=catalog, now=later)
    assert fallback is not None and fallback.model == free


def test_capacity_record_never_shortens_a_longer_one(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMANDCODE_USAGE_JSON", str(tmp_path / "usage.json"))
    moment = datetime(2026, 10, 14, 2, tzinfo=timezone.utc)
    model = "deepseek/deepseek-v4-flash"

    assert router.record_capacity_hit(model, now=moment)
    assert router.record_capacity_hit(model, now=moment + timedelta(minutes=1))
    assert router.capacity_cooling_down(model, now=moment + timedelta(minutes=9)) is True
    assert router.capacity_cooling_down(model, now=moment + timedelta(minutes=11)) is False


def test_an_empty_model_is_not_recorded_as_at_capacity(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMANDCODE_USAGE_JSON", str(tmp_path / "usage.json"))
    assert router.record_capacity_hit("") is False
    assert router.capacity_cooling_down("") is False


def test_select_excludes_named_models():
    catalog = router.parse_prices(CATALOG)
    assert (
        router.select(
            router.QUICK, catalog, include_free=False, exclude_models={"deepseek/deepseek-v4-flash"}
        )
        is None
    )
    deep = router.select(router.DEEP, catalog, exclude_models={"deepseek/deepseek-v4-pro"})
    assert deep is not None and deep.model == "moonshotai/kimi-k3"


def test_route_excludes_the_model_that_just_failed():
    catalog = router.parse_prices(CATALOG)
    chosen = router.route(
        "Refactor the authentication flow",
        catalog=catalog,
        exclude_models={"deepseek/deepseek-v4-pro"},
    )
    assert chosen.model == "moonshotai/kimi-k3"


# --------------------------------------------------------------------------
# The credit cooldown override
# --------------------------------------------------------------------------


def test_credit_cooldown_env_overrides_the_default(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMANDCODE_USAGE_JSON", str(tmp_path / "usage.json"))
    monkeypatch.setenv("COMMANDCODE_CREDIT_COOLDOWN_MINUTES", "90")
    moment = datetime(2026, 10, 14, 2, tzinfo=timezone.utc)

    assert router.record_limit_hit(10, now=moment)
    assert router.cooling_down(now=moment + timedelta(minutes=89)) is True
    assert router.cooling_down(now=moment + timedelta(minutes=91)) is False


@pytest.mark.parametrize("junk", ["soon", "0", "-5", ""])
def test_an_unusable_credit_cooldown_env_falls_back_to_thirty_minutes(monkeypatch, tmp_path, junk):
    monkeypatch.setenv("COMMANDCODE_USAGE_JSON", str(tmp_path / "usage.json"))
    monkeypatch.setenv("COMMANDCODE_CREDIT_COOLDOWN_MINUTES", junk)
    moment = datetime(2026, 10, 14, 2, tzinfo=timezone.utc)

    assert router.record_limit_hit(10, now=moment)
    assert router.cooling_down(now=moment + timedelta(minutes=29)) is True
    assert router.cooling_down(now=moment + timedelta(minutes=31)) is False


# --------------------------------------------------------------------------
# Cache invalidation
# --------------------------------------------------------------------------


def _catalog_rows(count: int) -> str:
    header = (
        "| Id (use EXACTLY this) | Name | Context | Efforts | "
        "$/1M in/out · cache read | Min plan | Best for |\n"
        "|---|---|---|---|---|---|---|\n"
    )
    rows = "".join(
        f"| `vendor/model-{i}` | M{i} | 1M | low, max | $1/$2 · cache $0.1 | Go and above | x |\n"
        for i in range(count)
    )
    return header + rows


def test_price_catalog_rereads_when_its_source_file_changes(monkeypatch, tmp_path):
    source = tmp_path / "models.md"
    source.write_text(_catalog_rows(router._MIN_CATALOG), encoding="utf-8")
    monkeypatch.setenv("COMMANDCODE_MODELS_MD", str(source))
    monkeypatch.setattr(router, "_CATALOG_CACHE", router._FileCache())

    assert "vendor/model-0" in router.price_catalog()
    source.write_text(
        _catalog_rows(router._MIN_CATALOG)
        + "| `vendor/new-model` | N | 1M | low | $1/$2 · cache $0.1 | Go and above | x |\n",
        encoding="utf-8",
    )
    later = source.stat().st_mtime + 10
    os.utime(source, (later, later))

    assert "vendor/new-model" in router.price_catalog()


def test_usage_state_reads_are_cached_but_a_write_takes_effect(monkeypatch, tmp_path):
    state = tmp_path / "usage.json"
    state.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("COMMANDCODE_USAGE_JSON", str(state))
    reads: list = []
    original = router._read_usage_state

    def counting_read(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(router, "_read_usage_state", counting_read)

    assert router.cooling_down() is False
    before = len(reads)
    assert router.cooling_down() is False
    assert len(reads) == before  # served from the short-lived cache

    assert router.record_limit_hit(5)
    assert router.cooling_down() is True  # the write refreshed the cache
    assert len(reads) == before + 2  # the record's own unlocked check + locked re-read

    monkeypatch.setattr(router, "_STATE_CACHE_TTL_SECONDS", 0.0)
    assert router.cooling_down() is True  # expired TTL re-reads
    assert len(reads) == before + 3


# --------------------------------------------------------------------------
# A lock that never releases
# --------------------------------------------------------------------------


def test_a_lock_held_too_long_fails_the_record_instead_of_hanging(monkeypatch, tmp_path):
    state = tmp_path / "usage.json"
    state.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("COMMANDCODE_USAGE_JSON", str(state))
    monkeypatch.setattr(router, "_LOCK_TIMEOUT_SECONDS", 0.2)
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    holder = context.Process(target=_hold_usage_state_lock, args=(str(state), acquired, release))
    holder.start()
    moment = datetime(2026, 10, 14, 2, tzinfo=timezone.utc)
    try:
        assert acquired.wait(10)
        # The caller must learn the cooldown was NOT recorded.
        assert router.record_limit_hit(5, now=moment) is False
    finally:
        release.set()
        holder.join(10)
        if holder.is_alive():
            holder.terminate()
            holder.join()

    assert router.cooling_down(now=moment + timedelta(minutes=29)) is False
    # The lock sidecar is cleaned up even on the failure path.
    assert sorted(path.name for path in tmp_path.iterdir()) == ["usage.json"]
