"""The Command Code router without a Command Code install.

Everything here answers from a fixed catalog and fixed clocks -- no CLI, no
network, no account. The price parser is exercised on a catalog snippet in the
CLI's own table shape, so a format change fails a test rather than silently
tying the router to nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone

import commandcode_router as router

CATALOG = """| Id (use EXACTLY this) | Name | Context | Efforts | $/1M in/out · cache read | Min plan | Best for |
|---|---|---|---|---|---|---|
| `deepseek/deepseek-v4-pro` | DeepSeek V4 Pro | 1M | off, high, max | $0.66/$1.98 · cache $0.022 | Go and above | reasoning |
| `deepseek/deepseek-v4-flash` | DeepSeek V4 Flash | 1M | off, high, max | $0.15/$0.6 · cache $0.003 | Go and above | fast |
| `inclusionai/ling-3.1-flash:free` | Ling 3.1 Flash | 262K | low, medium, high | $0/$0 · cache $0 | Go and above | cheap |
| `moonshotai/kimi-k3` | Kimi K3 | 1M | low, high, max | $3/$15 · cache $0.3 | Go and above | long |
| `claude-sonnet-5` | Claude Sonnet 5 | 1M | low, medium, high | $2/$10 · cache $0.2 (write $2.5) | Pro and above | smart |
"""


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
    assert router.classify("Add a caching layer to the media index service") == router.STANDARD


def test_plan_eligibility():
    assert router.plan_eligible("go and above", "go") is True
    assert router.plan_eligible("goat and above", "go") is False
    assert router.plan_eligible("goat and above", "goat") is True
    assert router.plan_eligible("pro and above", "go") is False
    assert router.plan_eligible("pro and above", "pro") is True
    assert router.plan_eligible("max", "go") is False


def test_resolve_effort_uses_only_cli_effort_names():
    assert router.resolve_effort({"efforts": ["low", "medium", "high"]}) == "xhigh"
    assert router.resolve_effort({"efforts": ["max"]}) == "xhigh"
    assert router.resolve_effort({"efforts": ["xhigh"]}) == "xhigh"
    assert router.resolve_effort({"efforts": ["low", "medium"]}) == "medium"
    assert router.resolve_effort({"efforts": []}) == ""
    assert router.resolve_effort({}) == ""


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
    assert chosen.effort == "xhigh"


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
    assert router._read_catalog_text() == "worker"


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
