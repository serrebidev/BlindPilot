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
withheld: a quick task still runs on a free model, but a standard or
deep task keeps the CLI's configured model rather than picking one that
cannot run. This is the same handling the terminal router applies, so
the two routers keep choosing alike.

This is the BlindPilot-side twin of the ``work``/``agent-task`` router in
``~/Documents/Codex/tools``: the tiers, the candidate shortlists and the
usage-cooldown handling match, so a BlindPilot turn and a terminal ``work``
run choose alike. Prices are read from the installed CLI at runtime; the
hand-picked table is the fallback when that cannot be read, and is what
tests exercise directly.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
Based on the original Claude Code Reader application by doubletaponair:
https://github.com/doubletaponair/claude-code-reader
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

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
        "inclusionai/ling-3.1-flash:free",
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

# Current coding-focused free offers, in preference order. The installed CLI's
# price catalog must still confirm zero cost before either can be selected.
_FREE_FALLBACK_CANDIDATES = (
    "inclusionai/ling-3.1-flash:free",
    "poolside/laguna-s-2.1-free",
)

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
# session (every turn re-reads context), so they carry the most weight; this is
# the "typical request" the pricing page quotes.
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

# The task text that makes a tier, ported from the terminal router so the
# two classify alike. A lookup phrased as a question ("how does the
# router pick") reads as a lookup too; the high-risk scan runs first, so
# a question about risky work still lands on deep.
_HIGH_RISK = re.compile(
    r"(?i)\b(security|auth(?:entication|orization)?|permission|privacy|accessibility|"
    r"screen.?reader|migration|architecture|release|deploy|payment|data.?loss|"
    r"race.?condition|concurren\w*|multi.?agent|multiple repos|cross.?repo|refactor)\b"
)
_LOOKUP = re.compile(
    r"(?i)^\s*(explain|summari[sz]e|find|locate|list|show|what|where|check|"
    r"how|why|which|who|whose|is|are|does|do|did|can|could|will|would|"
    r"should|may|might)\b"
)
_SMALL_EDIT = re.compile(
    r"(?i)^\s*(fix|change|rename|update|remove|add)\b.*\b"
    r"(typo|label|wording|text|string|comment|one file|single file|small)\b"
)

# A price catalog parses to at least this many rows, or the read is treated as
# a failure (a format change) rather than trusted.
_MIN_CATALOG = 20

_CATALOG_CACHE: dict[str, Optional[dict]] = {"data": None}


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
        if self.effort:
            parts.append(f"{self.effort} effort")
        if self.priced and self.cost is not None:
            parts.append(f"about ${self.cost:.5f} a request")
        if self.cooldown:
            parts.append("while Command Code cools down")
        return ", ".join(parts) + "."


def classify(task: str) -> str:
    """The capability tier a task belongs to.

    High-risk work is ``deep`` before length is considered; a short lookup or a
    small text/label edit is ``quick``; everything else is ``standard``. The
    order matters: "explain the auth flow" reads as a lookup but names auth, so
    it lands on ``deep``, which is the safer default for a wrong guess.
    """
    if _HIGH_RISK.search(task):
        return DEEP
    if len(task) <= 180 and (_LOOKUP.search(task) or _SMALL_EDIT.search(task)):
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


def model_cost(entry: dict, peak: bool = False) -> float:
    """A model's blended cost over the typical request profile."""
    in_rate = float(entry.get("input", 0.0))
    out_rate = float(entry.get("output", 0.0))
    cache_rate = float(entry.get("cacheRead", 0.0))
    rates = entry.get("peak")
    if peak and isinstance(rates, dict):
        in_rate = float(rates.get("input", in_rate))
        out_rate = float(rates.get("output", out_rate))
    return (
        _COST_PROFILE["input"] * in_rate
        + _COST_PROFILE["output"] * out_rate
        + _COST_PROFILE["cache_read"] * cache_rate
    ) / 1_000_000


# Effort levels strongest first. The CLI validates a level against the
# model's OWN list -- the same labels the catalog prints (measured live:
# kimi-k3 refuses "xhigh" with "Supported: low, high, max") -- so a level is
# only ever sent verbatim from such a list, never translated. An earlier
# build of the CLI had one global vocabulary (low, medium, xhigh); that is
# gone, and translating max or high into xhigh now gets the turn refused.
_EFFORT_STRENGTH = ("max", "xhigh", "high", "medium", "low")


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


def resolve_effort(entry: dict) -> str:
    """The best effort a model actually accepts, or "" to omit it.

    Several cheap models take no effort at all (their catalog column is "—"),
    and an effort sent to one ends the run before it starts, so omission is the
    safe answer rather than a guess. The answer is the strongest name in the
    model's own catalog list, spelled exactly as the catalog spells it.
    """
    return strongest_effort(entry.get("efforts"))


def plan_eligible(min_plan: str, max_plan: str = "go") -> bool:
    """Whether an account at ``max_plan`` may use a model needing ``min_plan``."""
    needed = 1
    text = str(min_plan or "").lower()
    # "goat" is checked before "go" because it contains it.
    if "goat" in text:
        needed = 2
    elif "pro" in text:
        needed = 3
    elif "max" in text:
        needed = 4
    elif "go" in text:
        needed = 1
    return needed <= _PLAN_RANK.get(max_plan, 1)


def select(
    tier: str,
    catalog: dict,
    *,
    max_plan: str = "go",
    include_free: bool = True,
    now: Optional[datetime] = None,
    cooldown: bool = False,
) -> Optional[Route]:
    """The cheapest eligible model for a tier, or None when none qualifies.

    Free models are only ever used for the ``quick`` tier: a standard or deep
    task never lands on one, whatever the price, and a model whose upstream may
    train on the prompt is filtered out entirely. While a recorded rate or
    credit stop is in effect (``cooldown``), paid models are withheld the same
    way, so only a free model can answer a quick task.
    """
    if not catalog:
        return None
    peak = is_peak(now)
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
        if not plan_eligible(entry.get("minPlan", ""), max_plan):
            continue
        cost = model_cost(entry, peak)
        if cost < best_cost:
            best_cost = cost
            best_model = model_id
    if not best_model:
        return None
    entry = catalog[best_model]
    return Route(
        model=best_model,
        effort=resolve_effort(entry),
        tier=tier,
        cost=best_cost,
        priced=True,
        cooldown=cooldown,
    )


def route(
    task: str,
    *,
    catalog: Optional[dict] = None,
    max_plan: str = "go",
    include_free: bool = True,
    now: Optional[datetime] = None,
    static: bool = False,
) -> Route:
    """The model and effort for a task.

    With a price catalog (read from the CLI unless one is passed), the cheapest
    eligible model for the task's tier wins. A catalog with no eligible model
    returns an empty route so the caller keeps its existing choice. Without a
    catalog -- or with ``static`` outside cooldown -- the hand-picked table
    answers. While a recorded rate or credit stop is in effect, paid models
    are withheld. A quick task runs on a free model; a heavier task keeps the
    CLI's configured model.
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
        )
        if chosen is not None:
            return chosen
        return Route(model="", effort="", tier=tier)
    if cooldown:
        return Route(model="", effort="", tier=tier)
    fallback = _STATIC_ROUTES[tier]
    return Route(model=fallback["model"], effort=fallback["effort"], tier=tier)


def best_free_model(
    *, catalog: Optional[dict] = None, exclude_model: str = "", max_plan: str = "go"
) -> Optional[Route]:
    """A coding-focused free fallback confirmed by the installed CLI catalog."""
    active_catalog = catalog if catalog is not None else price_catalog()
    for model_id in _FREE_FALLBACK_CANDIDATES:
        entry = active_catalog.get(model_id)
        if (
            model_id != exclude_model.lower()
            and entry
            and entry.get("free") is True
            and plan_eligible(entry.get("minPlan", ""), max_plan)
        ):
            return Route(model=model_id, effort="", tier="free", cost=0.0, priced=True)
    return None


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


def _read_catalog_text() -> str:
    for path in _catalog_candidates():
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


def price_catalog() -> dict:
    """The installed CLI's price catalog, parsed once and cached.

    A read that yields fewer than ``_MIN_CATALOG`` models is treated as a
    failure (a format change, or no CLI) and not cached, so a later success can
    still take over.
    """
    cached = _CATALOG_CACHE["data"]
    if cached is not None:
        return cached
    text = _read_catalog_text()
    data = parse_prices(text) if text else {}
    if len(data) >= _MIN_CATALOG:
        _CATALOG_CACHE["data"] = data
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

# Where the state lives. The env override is the seam tests use; otherwise
# the file sits beside the app's own per-user data.
_USAGE_STATE_ENV = "COMMANDCODE_USAGE_JSON"
_USAGE_STATE_LOCK = threading.Lock()


def _usage_state_path() -> Path:
    override = os.environ.get(_USAGE_STATE_ENV)
    if override:
        return Path(override)
    try:
        from accessible_ai.storage.paths import app_data_dir

        return app_data_dir() / "commandcode-usage.json"
    except Exception:  # noqa: BLE001 - state is best-effort, never fatal
        return Path.home() / ".blindpilot" / "commandcode-usage.json"


def record_limit_hit(code: int, now: Optional[datetime] = None) -> bool:
    """Note a rate (5) or credit (10) stop so routing avoids paid models.

    Best-effort: a state file that cannot be written or read is the same as
    no state, and routing carries on priced as before.
    """
    minutes = _LIMIT_COOLDOWN_MINUTES.get(code)
    if minutes is None:
        return False
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    path = _usage_state_path()
    deadline = moment + timedelta(minutes=minutes)
    with _USAGE_STATE_LOCK:
        state: dict = {}
        try:
            if path.is_file():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    state = loaded
        except (OSError, ValueError):
            state = {}
        try:
            existing = datetime.fromisoformat(state.get("commandcode", {}).get("cooldownUntil", ""))
            if existing.tzinfo is None:
                existing = existing.replace(tzinfo=timezone.utc)
            if existing > deadline:
                return True
        except (ValueError, AttributeError):
            pass
        state["commandcode"] = {
            "cooldownUntil": deadline.isoformat(),
            "reason": "rate limit" if code == 5 else "credit limit",
            "recordedAt": moment.isoformat(),
        }
        temporary_path: Optional[Path] = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
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
        except OSError:
            return False
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
    return True


def cooling_down(now: Optional[datetime] = None) -> bool:
    """Whether a recorded rate or credit stop is still in effect."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        raw = json.loads(_usage_state_path().read_text(encoding="utf-8"))
        until = raw.get("commandcode", {}).get("cooldownUntil", "")
        deadline = datetime.fromisoformat(until)
    except (OSError, ValueError, AttributeError):
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline > moment.astimezone(timezone.utc)
