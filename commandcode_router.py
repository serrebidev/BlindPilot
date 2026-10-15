"""Task-based model routing for Command Code, inside BlindPilot.

A Command Code tab with Auto routing selected routes the turn by the task
itself: a short lookup or a small
text edit runs on the cheapest eligible model, and anything touching security,
accessibility, migration, a release or a refactor runs on a stronger one. The
choice is two layers, both in this file:

1. A capability tier -- a text heuristic on the task string (quick, standard,
   deep).
2. A model within the tier -- the cheapest eligible Command Code model, priced
   from the CLI's own bundled catalog (``models.md``, the same source the
   pricing page is generated from). Cost is blended over a typical request
   (see ``_COST_PROFILE``), so cache-read price dominates, as it does in a real
   long session.

A rate or credit stop (the CLI's exit codes 5 and 10) is recorded to a
small state file, and while the stop is in effect paid models are
withheld. Any task uses a catalog-confirmed free model when available and
eligible for the account plan. A provider-capacity stop (exit 1 with the
server's own "at capacity" text) is recorded per model the same way, so the
next turn picks a different model rather than the same full one.

The file is split in two. The top is pure policy: plain data in, a decision
out, no filesystem or network. The bottom (from "Price catalog I/O" down)
touches the disk: catalog discovery and the usage-state sidecar with its
cross-process lock. Everything stays exported from this module, so callers
and tests import one name either way.

This is the BlindPilot-side twin of the ``work``/``agent-task`` router in
``~/Documents/Codex/tools``: the tiers, the candidate shortlists and the
normal routing match. During a cooldown BlindPilot may run heavier work on
free, so a terminal ``work`` run can choose differently. Prices are read from
the installed CLI at runtime; the hand-picked table is the fallback when that
cannot be read, and is what tests exercise directly.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
Based on the original Claude Code Reader application by doubletaponair:
https://github.com/doubletaponair/claude-code-reader
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

# The capability tiers, cheapest first. A tier is a human judgement; only the
# price ranking inside it is automatic.
QUICK = "quick"
STANDARD = "standard"
DEEP = "deep"

# The candidate models per tier: a capability shortlist, not a price list. The
# picker below only decides price among these, so a new deal or a rate change
# never promotes a model that cannot do the job. Ids are lowercase because the
# CLI prints and accepts them lowercased.
_TIER_CANDIDATES: dict[str, tuple[str, ...]] = {
    QUICK: (
        "poolside/laguna-s-2.1-free",
        "meta/muse-spark-1.3-contributor",
        "xiaomi/mimo-v2.5",
        "qwen/qwen3.7-flash",
        "qwen/qwen3.8-flash",
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4.1-flash",
        "z-ai/glm-5.3-flash",
    ),
    STANDARD: (
        "deepseek/deepseek-v4-pro",
        "minimaxai/minimax-m3",
        "moonshotai/kimi-k2.7-code",
        "zai-org/glm-5.3",
        "xai/grok-4.5",
    ),
    DEEP: (
        "moonshotai/kimi-k3",
        "qwen/qwen3.8-max",
        "zai-org/glm-5.3",
        "minimaxai/minimax-m3",
        "xai/grok-4.5",
        "deepseek/deepseek-v4-pro",
    ),
}

# Current coding-focused free offers. The installed CLI's price catalog must
# still confirm zero cost. Ling's bundled free entry is retired server-side.
_FREE_FALLBACK_CANDIDATES = ("poolside/laguna-s-2.1-free",)

# The hand-picked model per tier, used when the price catalog cannot be read.
# It matches the terminal router's static table so the two never disagree.
_STATIC_ROUTES: dict[str, dict[str, str]] = {
    QUICK: {"model": "deepseek/deepseek-v4-flash", "effort": "max"},
    STANDARD: {"model": "deepseek/deepseek-v4-pro", "effort": "max"},
    DEEP: {"model": "deepseek/deepseek-v4-pro", "effort": "max"},
}

# Upstreams whose terms allow training on the prompt. Never chosen, whatever
# the price.
_TRAINING_MODELS = frozenset({"glyph-cluster:free"})

# Per-request token profile used to compare models. Cache reads dominate a real
# session (every turn re-reads context), so they carry the most weight; 50000
# is the typical session a caller that cannot supply context_tokens is priced
# at.
_COST_PROFILE = {"input": 800, "output": 160, "cache_read": 50000}

# DeepSeek V4 bills peak rates Mon-Fri 01-04 & 06-10 UTC; the catalog lists the
# off-peak number, so the peak pair is recorded here (matching the terminal
# router and its price file).
_DEEPSEEK_PEAK: dict[str, dict[str, float]] = {
    "deepseek/deepseek-v4-pro": {"input": 1.32, "output": 3.96},
    "deepseek/deepseek-v4-flash": {"input": 0.30, "output": 1.20},
    "deepseek/deepseek-v4-flash-vision-exp": {"input": 0.30, "output": 1.20},
    "deepseek/deepseek-v4.1-flash": {"input": 0.30, "output": 1.20},
    "deepseek/deepseek-v4.1-flash-fast": {"input": 0.32, "output": 1.16},
}

# Plan ranking. A model whose ``minPlan`` needs more than the account has is
# filtered out, so a Go-tier account never picks a Pro/GOAT/Max-only model.
_PLAN_RANK = {"go": 1, "goat": 2, "pro": 3, "max": 4}

# The environment variable that names the account plan, when it can't be read
# from the CLI headlessly. Defaults to the lowest tier.
_PLAN_ENV = "COMMANDCODE_PLAN"


def _resolve_max_plan(max_plan) -> str:
    """The plan to route against, falling back to the env, then to ``go``."""
    if isinstance(max_plan, str) and max_plan.strip():
        return max_plan.strip().lower()
    raw = os.environ.get(_PLAN_ENV, "")
    candidate = raw.strip().lower()
    if candidate in _PLAN_RANK:
        return candidate
    return "go"

# The task text that makes a tier, ported from the terminal router so the
# two classify alike. The high-risk scan runs first, so a question about
# risky work lands on deep even when it reads as a lookup ("explain the
# auth flow"), and a small edit naming a risky word stays deep too ("remove
# the password string"). The one exception is a purely additive
# documentation request that only NAMES a risky word, which
# _documentation_only lets through: naming the word in a note is still a
# note.
_HIGH_RISK = re.compile(
    r"(?i)\b(security|auth(?:entication|orization)?|permission|privacy|accessibility|"
    r"screen.?reader|migration|architecture|release|deploy|payment|billing|data.?loss|"
    r"race.?condition|concurren\w*|deadlocks?|multi.?agent|multiple repos|cross.?repo|refactor|"
    r"databases?|schemas?|logins?|passwords?|encrypt(?:ion|ed|ing)?|secrets?|tokens?|"
    r"backups?|restor(?:e|ed|ing|ations?)|delet(?:e|ed|ing)|drops?|dropp\w*|performance)\b"
)
_LOOKUP = re.compile(
    r"(?i)^\s*(explain|summari[sz]e|find|locate|list|show|what|where|check|"
    r"review|inspect|look|how|why|which|who|whose|is|are|does|do|did|can|could|will|would|"
    r"should|may|might)\b"
)
_SMALL_EDIT = re.compile(
    r"(?i)^\s*(fix|change|rename|update|remove|add)\b.*\b"
    r"(typo|label|wording|text|string|comment|note|one file|single file|small)\b"
)

# The documentation escape from the high-risk scan, deliberately narrow: an
# ADDITIVE verb, a documentation noun, and every risky word sitting inside a
# documentation phrase ("a security note", "a note about the payment flow",
# "text in the README"). A verb that edits, removes or implements never takes
# it -- "remove the password string" is deep however small the edit looks.
_DOC_VERBS = re.compile(r"(?i)^\s*(add|write|draft|note|mention)\b")
_DOC_NOUNS = re.compile(
    r"(?i)\b(note|comment|text|label|wording|string|readme|docs?|documentation)\b"
)
# One documentation phrase: optionally a word before the noun ("security
# note"), the noun, an optional link ("about", "for", "in"), an optional
# determiner, then the few words it names -- stopping at a clause boundary,
# so "a note about auth and delete the backup" is not read as one phrase.
_DOC_PHRASE = re.compile(
    r"(?i)(?:\b\w+\s+)?\b(?:note|comment|text|label|wording|string|readme|docs?|documentation)\b"
    r"(?:\s+(?:about|on|for|titled|called|entitled|in|to|regarding))?"
    r"(?:\s+(?:the|a|an|this|that|these|those|my|our|your))?"
    r"(?:\s+(?!(?:and|or|but|then|so)\b)[\w./-]+){0,4}"
)

# A price catalog parses to at least this many rows, or the read is treated as
# a failure (a format change) rather than trusted.
_MIN_CATALOG = 20


@dataclass(frozen=True)
class _FileCache:
    """One cached file read: its source, the source's mtime, and when it was read."""

    source: str = ""
    mtime: Optional[float] = None
    cached_at: float = 0.0
    data: dict = field(default_factory=dict)


def _cache_fresh(cache: _FileCache, source: str, mtime: Optional[float], ttl: float) -> bool:
    """Whether a cached read still matches its source and its TTL has not run out."""
    return (
        time.monotonic() - cache.cached_at < ttl and cache.source == source and cache.mtime == mtime
    )


_CATALOG_CACHE = _FileCache()


@dataclass(frozen=True)
class Route:
    """What the router decided: the model to run and why."""

    model: str
    effort: str
    tier: str
    cost: Optional[float] = None
    priced: bool = False
    cooldown: bool = False

    def describe(self) -> str:
        """One line, for reading back: what the next turn will run as."""
        parts = [f"Auto model {self.model}", f"{self.tier} task"]
        if self.cooldown and self.cost == 0:
            parts.append("free model during cooldown")
        if self.effort:
            parts.append(f"{self.effort} effort")
        if self.priced and self.cost is not None:
            parts.append(f"about ${self.cost:.5f} a request")
        if self.cooldown:
            parts.append("while Command Code cools down")
        return ", ".join(parts) + "."


def _documentation_only(task: str) -> bool:
    """Whether a task only adds documentation that NAMES a risky word.

    Every risky word must sit inside a documentation phrase and the request
    must open with an additive verb; anything else keeps the high-risk
    classification, so "add a security note to the README" is a note while
    "remove the password string" is still deep work.
    """
    if not _DOC_VERBS.search(task) or not _DOC_NOUNS.search(task):
        return False
    return _HIGH_RISK.search(_DOC_PHRASE.sub(" ", task)) is None


def classify(task: str) -> str:
    """The capability tier a task belongs to.

    The high-risk scan runs first, so anything touching security, a
    database, billing or a release is ``deep`` -- except a purely additive
    documentation request that only names a risky word
    (``_documentation_only``), which falls through to the small-edit and
    lookup rules rather than the deep tier. A pure small text/label/note
    edit is ``quick``; a short lookup is ``quick``; everything else is
    ``standard``. The order matters: "explain the auth flow" reads as a
    lookup but names auth, so it lands on ``deep``, and "remove the
    password string" stays deep however small the edit looks.
    """
    if _HIGH_RISK.search(task) and not _documentation_only(task):
        return DEEP
    if len(task) <= 180 and _SMALL_EDIT.search(task):
        return QUICK
    if len(task) <= 180 and _LOOKUP.search(task):
        return QUICK
    return STANDARD


def is_peak(now: Optional[datetime] = None) -> bool:
    """Whether DeepSeek's peak window is in effect (Mon-Fri 01-04 & 06-10 UTC)."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    if moment.weekday() >= 5:  # Saturday, Sunday
        return False
    hour = moment.hour
    return (1 <= hour < 4) or (6 <= hour < 10)


def _rate(value: object, default: float = 0.0) -> float:
    """A catalog price cell as a number, or the default when it is not one.

    A format change that puts text in a price column costs that model its
    ranking, not a crash on every Auto turn.
    """
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def model_cost(entry: dict, peak: bool = False, context_tokens: Optional[int] = None) -> float:
    """A model's blended cost over the typical request profile.

    ``context_tokens`` scales the cache-read weight to a conversation of that
    size -- one turn re-reads the whole context, so cache reads grow with it.
    Left unset, the fixed typical-session profile answers (50000 cache-read
    tokens; see ``_COST_PROFILE``).
    """
    in_rate = _rate(entry.get("input"))
    out_rate = _rate(entry.get("output"))
    cache_rate = _rate(entry.get("cacheRead"))
    rates = entry.get("peak")
    if peak and isinstance(rates, dict):
        in_rate = _rate(rates.get("input"), in_rate)
        out_rate = _rate(rates.get("output"), out_rate)
    cache_weight = (
        float(context_tokens)
        if context_tokens is not None and context_tokens > 0
        else _COST_PROFILE["cache_read"]
    )
    return (
        _COST_PROFILE["input"] * in_rate
        + _COST_PROFILE["output"] * out_rate
        + cache_weight * cache_rate
    ) / 1_000_000


# Effort levels strongest first. The CLI validates a level against the
# model's OWN list -- the same labels the catalog prints (measured live:
# kimi-k3 refuses "xhigh" with "Supported: low, high, max") -- so a level is
# only ever sent verbatim from such a list, never translated. An earlier
# build of the CLI had one global vocabulary (low, medium, xhigh); that is
# gone, and translating max or high into xhigh now gets the turn refused.
_EFFORT_STRENGTH = ("max", "xhigh", "high", "medium", "low")

# The effort each tier prefers, checked in this order against the model's own
# listed levels: a quick task wants the cheap levels, a standard task the
# workhorse, a deep task the strongest.
_TIER_EFFORT_PREFS: dict[str, tuple[str, ...]] = {
    QUICK: ("low", "medium"),
    STANDARD: ("high",),
    DEEP: ("max", "xhigh"),
}


def strongest_effort(efforts) -> str:
    """The strongest level in a model's own list, or "" when there is none.

    "off" is a level only in the sense that the model takes none, so it never
    wins; a list of nothing but "off" (or nothing) yields "".
    """
    if not isinstance(efforts, (list, tuple)) or not efforts:
        return ""
    lowered = [str(e).strip().lower() for e in efforts]
    for want in _EFFORT_STRENGTH:
        if want in lowered:
            return want
    return ""


def tier_effort(tier: str, model_entry: dict) -> str:
    """The effort a tier prefers among a model's own listed levels.

    A preferred level is only ever one taken verbatim from the model's catalog
    list, never invented; a model that offers none of the tier's preferences
    falls back to its strongest own level (``strongest_effort``).
    """
    efforts = model_entry.get("efforts") if isinstance(model_entry, dict) else None
    if isinstance(efforts, (list, tuple)) and efforts:
        lowered = [str(e).strip().lower() for e in efforts]
        for want in _TIER_EFFORT_PREFS.get(tier, ()):
            if want in lowered:
                return want
    return strongest_effort(efforts)


def plan_eligible(min_plan: str, max_plan: str = "go") -> bool:
    """Whether an account at ``max_plan`` may use a model needing ``min_plan``."""
    needed = 1
    # Whole words only: "goat" is not "go", and a plan name inside a longer
    # word ("promax") is not a plan name.
    tokens = set(re.findall(r"[a-z]+", str(min_plan or "").lower()))
    if "goat" in tokens:
        needed = 2
    elif "pro" in tokens:
        needed = 3
    elif "max" in tokens:
        needed = 4
    return needed <= _PLAN_RANK.get(max_plan, 1)


def select(
    tier: str,
    catalog: dict,
    *,
    max_plan: Optional[str] = None,
    include_free: bool = True,
    now: Optional[datetime] = None,
    cooldown: bool = False,
    exclude_models: Optional[set[str]] = None,
    context_tokens: Optional[int] = None,
) -> Optional[Route]:
    """The cheapest eligible model for a tier, or None when none qualifies.

    Free models are only ever used for the ``quick`` tier: a standard or deep
    task never lands on one, whatever the price, and a model whose upstream may
    train on the prompt is filtered out entirely. While a recorded rate or
    credit stop is in effect (``cooldown``), paid models are withheld the same
    way, so only a free model can answer a quick task. A model whose providers
    were just recorded at capacity is withheld the same way, free or paid, and
    ``exclude_models`` withholds the models the caller names -- the one that
    just failed a turn, for a retry.
    """
    if not catalog:
        return None
    peak = is_peak(now)
    excluded = {str(m).casefold() for m in exclude_models or ()}
    best_model = ""
    best_cost = float("inf")
    for model_id in _TIER_CANDIDATES.get(tier, ()):
        entry = catalog.get(model_id)
        if not entry:
            continue
        is_free = bool(entry.get("free"))
        if is_free and tier != QUICK:
            continue
        if is_free and not include_free:
            continue
        if cooldown and not is_free:
            continue
        if model_id in _TRAINING_MODELS:
            continue
        if model_id.casefold() in excluded:
            continue
        if capacity_cooling_down(model_id, now):
            continue
        if not plan_eligible(entry.get("minPlan", ""), _resolve_max_plan(max_plan)):
            continue
        cost = model_cost(entry, peak, context_tokens)
        if cost < best_cost:
            best_cost = cost
            best_model = model_id
    if not best_model:
        return None
    entry = catalog[best_model]
    return Route(
        model=best_model,
        effort=tier_effort(tier, entry),
        tier=tier,
        cost=best_cost,
        priced=True,
        cooldown=cooldown,
    )


def route(
    task: str,
    *,
    catalog: Optional[dict] = None,
    max_plan: Optional[str] = None,
    include_free: bool = True,
    now: Optional[datetime] = None,
    static: bool = False,
    exclude_models: Optional[set[str]] = None,
    context_tokens: Optional[int] = None,
) -> Route:
    """The model and effort for a task.

    With a price catalog (read from the CLI unless one is passed), the cheapest
    eligible model for the task's tier wins. A catalog with no eligible model
    returns an empty route so the caller keeps its existing choice. Without a
    catalog -- or with ``static`` outside cooldown -- the hand-picked table
    answers. While a recorded rate or credit stop is in effect, paid models
    are withheld and a catalog-confirmed free model handles any tier.
    ``exclude_models`` and ``context_tokens`` are passed through to
    ``select``/``model_cost``.
    """
    tier = classify(task)
    active_catalog = catalog if catalog is not None else price_catalog()
    cooldown = cooling_down(now)
    if active_catalog and (not static or cooldown):
        chosen = select(
            tier,
            active_catalog,
            max_plan=max_plan,
            include_free=include_free,
            now=now,
            cooldown=cooldown,
            exclude_models=exclude_models,
            context_tokens=context_tokens,
        )
        if chosen is not None:
            return chosen
        if cooldown and include_free:
            free = best_free_model(catalog=active_catalog, max_plan=max_plan, now=now)
            if free is not None:
                return Route(free.model, free.effort, tier, free.cost, free.priced, True)
        return Route(model="", effort="", tier=tier, cooldown=cooldown)
    if cooldown:
        return Route(model="", effort="", tier=tier, cooldown=True)
    fallback = _STATIC_ROUTES[tier]
    effort = fallback["effort"]
    entry = (active_catalog or {}).get(fallback["model"])
    if entry:
        # The static model's own catalog levels, when the catalog knows it.
        effort = tier_effort(tier, entry) or effort
    return Route(model=fallback["model"], effort=effort, tier=tier)


def best_free_model(
    *,
    catalog: Optional[dict] = None,
    exclude_model: str = "",
    max_plan: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[Route]:
    """A coding-focused free fallback confirmed by the installed CLI catalog.

    A free model whose providers were just recorded at capacity is skipped
    the same way ``select`` skips one, so the fallback never lands on the
    model that just refused the turn.
    """
    active_catalog = catalog if catalog is not None else price_catalog()
    # Capability-gated, not a bare free scan: only models the router already
    # considers fit for work qualify, so a free model the catalog just
    # started offering never outranks the curated shortlist. The declared
    # fallbacks go first, then the quick tier's candidates (what a QUICK task
    # may already pick), then any free catalog entry whose id matches a tier
    # candidate -- this keeps the fallback working across CLI versions whose
    # exact model ids changed.
    candidate_ids: list[str] = []
    for model_id in _FREE_FALLBACK_CANDIDATES + _TIER_CANDIDATES.get(QUICK, ()):
        if model_id not in candidate_ids:
            candidate_ids.append(model_id)
    # Also consider any tier candidate (any tier) the catalog marks free, so
    # a free upstream that appears in the installed CLI's catalog is not
    # silently ignored just because it was paid in the hand-written table.
    for tier_ids in _TIER_CANDIDATES.values():
        for model_id in tier_ids:
            if model_id not in candidate_ids:
                candidate_ids.append(model_id)
    max_plan_resolved = _resolve_max_plan(max_plan)
    for model_id in candidate_ids:
        entry = active_catalog.get(model_id)
        if (
            model_id != exclude_model.lower()
            and entry
            and entry.get("free") is True
            and model_id not in _TRAINING_MODELS
            and not capacity_cooling_down(model_id, now)
            and plan_eligible(entry.get("minPlan", ""), max_plan_resolved)
        ):
            return Route(model=model_id, effort="", tier="free", cost=0.0, priced=True)
    return None


def catalog_missing_candidates(catalog: Optional[dict] = None) -> dict:
    """Tier candidates the catalog does not currently offer.

    A renamed or retired model is not a crash -- ``select`` already skips an
    id that is absent -- but leaving it in the shortlist silently weakens the
    tier. This surfaces the gap so a release check (or a developer) can take
    the model out or add its replacement. Returns a mapping of tier to the
    missing ids; an empty dict means full coverage.
    """
    active = catalog if catalog is not None else price_catalog()
    missing: dict = {}
    for tier, ids in _TIER_CANDIDATES.items():
        absent = [model_id for model_id in ids if model_id not in active]
        if absent:
            missing[tier] = absent
    return missing


def catalog_is_complete(catalog: Optional[dict] = None) -> bool:
    """Whether every tier candidate appears in the catalog."""
    return not catalog_missing_candidates(catalog)


def parse_prices(text: str) -> dict:
    """Model entries out of the CLI's bundled ``models.md`` table.

    The table's rows are ``| `id` | name | context | efforts | $in/$out · cache
    $c | min plan | ... |``. Only the fields the router ranks on are read; a row
    without an id or a price is skipped, so a prose or heading line cannot be
    mistaken for a model.
    """
    models: dict[str, dict] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 7:
            continue
        id_match = re.search(r"`([^`]+)`", cells[0])
        if not id_match:
            continue
        price = re.search(r"\$([\d.]+)/\$([\d.]+)(?:\s*·\s*cache\s*\$([\d.]+))?", cells[4])
        if not price:
            continue
        model_id = id_match.group(1).lower()
        in_rate = float(price.group(1))
        out_rate = float(price.group(2))
        cache_rate = float(price.group(3)) if price.group(3) else 0.0
        efforts = [
            part.strip().lower()
            for part in cells[3].split(",")
            if part.strip() and part.strip() not in ("—", "-")
        ]
        entry = {
            "input": in_rate,
            "output": out_rate,
            "cacheRead": cache_rate,
            "minPlan": cells[5].lower(),
            "efforts": efforts,
            "free": in_rate == 0 and out_rate == 0,
        }
        if model_id in _DEEPSEEK_PEAK:
            entry["peak"] = _DEEPSEEK_PEAK[model_id]
        models[model_id] = entry
    return models


# --------------------------------------------------------------------------
# Price catalog I/O
#
# Discovery and reading of the CLI's bundled models.md. The parsing itself
# (parse_prices) is pure and stays above; everything here touches the disk.
# --------------------------------------------------------------------------

# A cached catalog is re-read once its source file's mtime changes, and after
# this long regardless, so a CLI upgrade is picked up without a restart.
_CATALOG_TTL_SECONDS = 300.0


def _catalog_candidates():
    """Paths the bundled model catalog may live at, best guess first.

    Prefer the binary selected by the backend so routing reads the same CLI's
    catalog as the worker. Other installed layouts are fallbacks.
    """
    override = os.environ.get("COMMANDCODE_MODELS_MD")
    if override:
        yield Path(override)
    rels = (
        "dist/bundled/command-code-knowledge/reference/models.md",
        "node_modules/command-code/dist/bundled/command-code-knowledge/reference/models.md",
    )
    roots: list[Path] = []
    try:
        from agent_backends import BACKEND_COMMANDCODE, find_backend_cli

        found = find_backend_cli(BACKEND_COMMANDCODE)
        if found:
            roots.append(Path(found))
    except Exception:  # noqa: BLE001 - a missing CLI is just no catalog
        pass
    for name in ("command-code", "cmdc", "commandcode"):
        found = shutil.which(name)
        if found:
            roots.append(Path(found))
    for base in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")):
        if base:
            roots.append(Path(base) / "npm")
            roots.append(Path(base) / "BlindPilot" / "npm")
    seen: set[str] = set()
    for root in roots:
        try:
            here = root.resolve()
        except OSError:
            continue
        for parent in [here, *here.parents][:7]:
            for rel in rels:
                candidate = parent / rel
                key = str(candidate)
                if key not in seen:
                    seen.add(key)
                    yield candidate


def _load_catalog_file() -> tuple[str, Optional[Path], Optional[float]]:
    """The first readable catalog's text, its path and its mtime."""
    for path in _catalog_candidates():
        try:
            if path.is_file():
                return (
                    path.read_text(encoding="utf-8", errors="replace"),
                    path,
                    path.stat().st_mtime,
                )
        except OSError:
            continue
    return "", None, None


def _catalog_cache_fresh() -> bool:
    """Keep the parsed catalog until its source changes or the TTL runs out."""
    cache = _CATALOG_CACHE
    if not cache.source:
        return False
    try:
        mtime = Path(cache.source).stat().st_mtime
    except OSError:
        return False
    return _cache_fresh(cache, cache.source, mtime, _CATALOG_TTL_SECONDS)


def price_catalog() -> dict:
    """The installed CLI's price catalog, parsed once and cached.

    A read that yields fewer than ``_MIN_CATALOG`` models is treated as a
    failure (a format change, or no CLI) and not cached, so a later success can
    still take over. A cached catalog is re-read when its source file's mtime
    changes, and after ``_CATALOG_TTL_SECONDS`` regardless.
    """
    global _CATALOG_CACHE
    if _CATALOG_CACHE.data and _catalog_cache_fresh():
        return _CATALOG_CACHE.data
    text, path, mtime = _load_catalog_file()
    data = parse_prices(text) if text else {}
    if len(data) >= _MIN_CATALOG:
        _CATALOG_CACHE = _FileCache(str(path) if path else "", mtime, time.monotonic(), data)
        return data
    return {}


# --------------------------------------------------------------------------
# Usage state
#
# Command Code credits CANNOT be read headlessly: /usage is interactive-only
# and the plan is not readable from disk. The only automatic signal is the
# CLI's own limit exit codes, so when a turn hits one its reset window is
# recorded here and paid models are withheld until it passes. Read the live
# number with /usage in a session, or https://commandcode.ai/usage.
# --------------------------------------------------------------------------

# The CLI's exit codes for a spent window, and how long paid models stay
# withheld afterwards. Code 5 is a transient rate limit ("wait a moment");
# code 10 is the credit window, whose real 5-hour reset cannot be read
# headlessly, so the recorded wait is a conservative bet and the worker's
# per-turn free retry covers the tail.
_LIMIT_COOLDOWN_MINUTES: dict[int, int] = {5: 2, 10: 30}

# What a limit exit code means, said in the recorded reason and in the
# notice that announces a retry. Both live here so the router and the
# worker never phrase the same stop two ways.
LIMIT_LABELS = {5: "rate limit", 10: "credit limit"}

# The code-10 window above is the default; this env var overrides it (parsed
# defensively -- anything unusable falls back to the default).
_CREDIT_COOLDOWN_ENV = "COMMANDCODE_CREDIT_COOLDOWN_MINUTES"
_DEFAULT_CREDIT_COOLDOWN_MINUTES = 30

# How long a provider-capacity stop ("at capacity") withholds that one model.
# Shorter than a credit stop -- the upstream recovers on its own -- but long
# enough that the next turn does not re-pick the same full model.
_CAPACITY_COOLDOWN_MINUTES = 10

# Both platforms wait this long for the sidecar lock before the update fails.
_LOCK_TIMEOUT_SECONDS = 5.0

# cooling_down() and capacity_cooling_down() may re-read the state file at
# most this often; the write path never uses this cache.
_STATE_CACHE_TTL_SECONDS = 5.0

# Where the state lives. The env override is the seam tests use; otherwise
# the file sits beside the app's own per-user data.
_USAGE_STATE_ENV = "COMMANDCODE_USAGE_JSON"
_USAGE_STATE_LOCK = threading.Lock()

_USAGE_STATE_CACHE = _FileCache()


def _remember_usage_state(path: Path, mtime: Optional[float], data: dict) -> None:
    """Refresh the read cache, so a recorded stop is read straight back."""
    global _USAGE_STATE_CACHE
    _USAGE_STATE_CACHE = _FileCache(str(path), mtime, time.monotonic(), data)


def _usage_state_path() -> Path:
    override = os.environ.get(_USAGE_STATE_ENV)
    if override:
        return Path(override)
    try:
        from accessible_ai.storage.paths import app_data_dir

        return app_data_dir() / "commandcode-usage.json"
    except Exception:  # noqa: BLE001 - state is best-effort, never fatal
        return Path.home() / ".blindpilot" / "commandcode-usage.json"


@contextmanager
def _usage_state_file_lock(path: Path):
    """Serialize usage-state updates across router processes.

    The wait is bounded on both platforms: a holder that never releases fails
    the update rather than hanging the turn.

    shortcut: the sidecar is unlinked after release so long-lived runs do not
    litter it; a waiter that already opened the same file keeps locking the
    removed inode, so two writers can overlap in that rare window -- the write
    itself is still an atomic replace.
    """
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock_path.open("a+b") as lock_file:
            lock_file.seek(0)
            if sys.platform == "win32":
                import msvcrt

                timeout = time.monotonic() + _LOCK_TIMEOUT_SECONDS
                while True:
                    try:
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= timeout:
                            raise
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                timeout = time.monotonic() + _LOCK_TIMEOUT_SECONDS
                while True:
                    try:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() >= timeout:
                            raise
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        try:
            lock_path.unlink()
        except OSError:
            # Busy on Windows while another process still holds the file, or
            # already gone because someone else cleaned up first.
            pass


def _read_usage_state(path: Path) -> dict:
    try:
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return loaded
    except (OSError, ValueError):
        pass
    return {}


def _read_usage_state_cached() -> dict:
    """The state, re-read at most once every few seconds.

    route() asks once a turn; the write path never uses this cache, and each
    write refreshes it, so a recorded stop is read straight back.
    """
    path = _usage_state_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    cache = _USAGE_STATE_CACHE
    if _cache_fresh(cache, str(path), mtime, _STATE_CACHE_TTL_SECONDS):
        return cache.data
    data = _read_usage_state(path)
    _remember_usage_state(path, mtime, data)
    return data


def _parse_deadline(raw: object) -> Optional[datetime]:
    try:
        deadline = datetime.fromisoformat(raw)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline


def _has_longer_stop(state: dict, deadline: datetime, *path: str) -> bool:
    """Whether the stop recorded at this state path outlasts the new deadline.

    A section that is there but is not an object has nothing to compare, so
    the new stop may take its place.
    """
    raw: object = state
    for key in path:
        if not isinstance(raw, dict):
            return False
        raw = raw.get(key)
    existing = _parse_deadline(raw)
    return existing is not None and existing > deadline


def _write_usage_state(path: Path, state: dict) -> None:
    """Replace the state file atomically, then refresh the read cache."""
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(state, temporary, indent=2)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    _remember_usage_state(path, mtime, state)


def _update_usage_state(check: Callable[[dict], bool], mutate: Callable[[dict], None]) -> bool:
    """Record a stop under the file lock; False when it could not be recorded.

    The state is re-read inside the lock, so a shorter stop never overwrites
    a longer one another process recorded. A lock that cannot be taken in time
    -- or any other I/O failure -- reports False rather than pretending the
    stop was saved.
    """
    path = _usage_state_path()
    with _USAGE_STATE_LOCK:
        try:
            if check(_read_usage_state(path)):
                return True
            with _usage_state_file_lock(path):
                state = _read_usage_state(path)
                if check(state):
                    return True
                mutate(state)
                _write_usage_state(path, state)
        except OSError:
            return False
    return True


def _cooldown_minutes(code: int) -> Optional[int]:
    """How long a limit exit code withholds paid models."""
    if code == 10:
        raw = os.environ.get(_CREDIT_COOLDOWN_ENV, "")
        try:
            minutes = int(raw)
        except ValueError:
            return _DEFAULT_CREDIT_COOLDOWN_MINUTES
        return minutes if minutes > 0 else _DEFAULT_CREDIT_COOLDOWN_MINUTES
    return _LIMIT_COOLDOWN_MINUTES.get(code)


def record_limit_hit(code: int, now: Optional[datetime] = None) -> bool:
    """Note a rate (5) or credit (10) stop so routing avoids paid models.

    Best-effort but honest: False means the stop could NOT be recorded (the
    lock timed out, or the state file could not be written), and the caller
    should say so rather than assume paid models are withheld.
    """
    minutes = _cooldown_minutes(code)
    if minutes is None:
        return False
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    deadline = moment + timedelta(minutes=minutes)
    reason = LIMIT_LABELS[code]

    def mutate(state: dict) -> None:
        state["commandcode"] = {
            "cooldownUntil": deadline.isoformat(),
            "reason": reason,
            "recordedAt": moment.isoformat(),
        }

    return _update_usage_state(
        lambda state: _has_longer_stop(state, deadline, "commandcode", "cooldownUntil"), mutate
    )


def record_capacity_hit(model: str, now: Optional[datetime] = None) -> bool:
    """Note that a model's providers reported themselves at capacity.

    Keyed by model id in the same state file as a rate or credit stop, and
    withheld from selection the same way. A capacity failure is the upstream's
    rather than the account's, so it never withholds paid models as a whole.
    """
    if not model:
        return False
    # The id can arrive in any case from user config; one casefolded key
    # matches what capacity_cooling_down looks up, whatever case arrives.
    key = model.casefold()
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    deadline = moment + timedelta(minutes=_CAPACITY_COOLDOWN_MINUTES)
    entry = {"until": deadline.isoformat(), "recordedAt": moment.isoformat()}

    def mutate(state: dict) -> None:
        capacity = state.get("capacity")
        if not isinstance(capacity, dict):
            capacity = {}
        capacity[key] = entry
        state["capacity"] = capacity

    return _update_usage_state(
        lambda state: _has_longer_stop(state, deadline, "capacity", key, "until"), mutate
    )


def cooling_down(now: Optional[datetime] = None) -> bool:
    """Whether a recorded rate or credit stop is still in effect."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    raw = _read_usage_state_cached()
    try:
        until = raw.get("commandcode", {}).get("cooldownUntil", "")
    except AttributeError:
        return False
    deadline = _parse_deadline(until)
    if deadline is None:
        return False
    return deadline > moment.astimezone(timezone.utc)


def capacity_cooling_down(model: str, now: Optional[datetime] = None) -> bool:
    """Whether a model's providers are still recorded as at capacity."""
    if not model:
        return False
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    raw = _read_usage_state_cached()
    try:
        until = raw.get("capacity", {}).get(model.casefold(), {}).get("until", "")
    except AttributeError:
        return False
    deadline = _parse_deadline(until)
    if deadline is None:
        return False
    return deadline > moment.astimezone(timezone.utc)
