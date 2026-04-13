"""
Business intelligence — cross-stream hypothesis engine.

Correlates revenue, labour, product mix, and operational data to produce
standing beliefs about how the business actually works.

Each hypothesis has:
    hypothesis_key   — stable identifier
    category         — revenue_pattern | labour_efficiency | product_mix | cost_trend
    title            — short heading
    statement        — plain-English belief statement
    implication      — what it means operationally
    confidence       — 0.0–1.0
    confidence_label — "strong" | "moderate" | "weak"
    evidence         — dict of supporting numbers

These feed into:
    - Chat context  (Claude reasons from hypotheses, not raw numbers)
    - Opportunities page  (surfaced as business profile cards)
    - Curiosity agenda  (weak hypotheses become knowledge-gap questions)
"""

from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timedelta

from sqlalchemy import text

from config.database import engine
from data.storage import get_daily_profitability, get_profitability_correlations

logger = logging.getLogger("autopilot.business_intelligence")

_TZ = "Australia/Brisbane"

_ALT_MILK_TERMS = {"oat", "almond", "soy", "coconut", "macadamia", "oat milk",
                   "almond milk", "soy milk", "coconut milk", "alternative milk",
                   "alt milk", "dairy free", "dairy-free"}
_EXTRA_SHOT_TERMS = {"extra shot", "double shot", "triple shot", "add shot"}


# ── Maths helpers ──────────────────────────────────────────────────────────

def _text(sql: str):
    return text(sql)


def _linear_trend(values: list[float]) -> tuple[float, float]:
    """Return (slope_per_unit, r_squared).  slope > 0 → rising."""
    n = len(values)
    if n < 3:
        return 0.0, 0.0
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(values) / n
    ss_xy = sum((xs[i] - x_mean) * (values[i] - y_mean) for i in range(n))
    ss_xx = sum((xs[i] - x_mean) ** 2 for i in range(n))
    ss_yy = sum((values[i] - y_mean) ** 2 for i in range(n))
    if ss_xx == 0:
        return 0.0, 0.0
    slope = ss_xy / ss_xx
    r_sq = (ss_xy ** 2 / (ss_xx * ss_yy)) if ss_yy > 0 else 0.0
    return round(slope, 4), round(r_sq, 3)


def _cv(values: list[float]) -> float:
    """Coefficient of variation (std / mean).  Lower → more consistent."""
    n = len(values)
    if n < 2:
        return 1.0
    mean = sum(values) / n
    if mean == 0:
        return 1.0
    var = sum((v - mean) ** 2 for v in values) / n
    return math.sqrt(var) / mean


def _confidence(n_days: int, cv: float = 0.3) -> tuple[float, str]:
    sample = min(n_days / 21, 1.0)          # full weight after 21 days
    consistency = max(0.0, 1.0 - min(cv, 1.5) / 1.5)
    score = round(sample * 0.55 + consistency * 0.45, 2)
    label = "strong" if score >= 0.65 else "moderate" if score >= 0.40 else "weak"
    return score, label


def _hour_label(h: int) -> str:
    if h == 0:
        return "12am"
    if h < 12:
        return f"{h}am"
    if h == 12:
        return "12pm"
    return f"{h - 12}pm"


# ── Data fetchers ──────────────────────────────────────────────────────────

def _hourly_revenue(site_id: str, days: int) -> dict[int, dict]:
    """Return {hour: {revenue_cents, order_count, day_count}} across N days."""
    cutoff = date.today() - timedelta(days=days)
    with engine.connect() as conn:
        rows = conn.execute(
            _text(
                f"SELECT "
                f"  EXTRACT(HOUR FROM closed_at AT TIME ZONE '{_TZ}')::int AS hour, "
                f"  COUNT(DISTINCT DATE(closed_at AT TIME ZONE '{_TZ}')) AS day_count, "
                f"  COUNT(*) AS order_count, "
                f"  COALESCE(SUM(total_money_cents), 0) AS revenue_cents "
                "FROM orders_raw "
                "WHERE site_id = :sid "
                "  AND DATE(closed_at AT TIME ZONE :tz) >= :cutoff "
                "  AND state = 'COMPLETED' "
                "GROUP BY hour "
                "ORDER BY hour"
            ),
            {"sid": site_id, "tz": _TZ, "cutoff": cutoff},
        ).mappings().all()

    return {
        int(r["hour"]): {
            "revenue_cents": int(r["revenue_cents"]),
            "order_count": int(r["order_count"]),
            "day_count": int(r["day_count"]),
        }
        for r in rows
    }


def _daily_revenue_by_date(site_id: str, days: int) -> list[dict]:
    """Return [{date, revenue_cents, order_count}] ordered by date."""
    cutoff = date.today() - timedelta(days=days)
    with engine.connect() as conn:
        rows = conn.execute(
            _text(
                f"SELECT "
                f"  DATE(closed_at AT TIME ZONE '{_TZ}') AS trade_date, "
                f"  COALESCE(SUM(total_money_cents), 0) AS revenue_cents, "
                f"  COUNT(*) AS order_count "
                "FROM orders_raw "
                "WHERE site_id = :sid "
                "  AND DATE(closed_at AT TIME ZONE :tz) >= :cutoff "
                "  AND state = 'COMPLETED' "
                "GROUP BY trade_date "
                "ORDER BY trade_date"
            ),
            {"sid": site_id, "tz": _TZ, "cutoff": cutoff},
        ).mappings().all()

    return [
        {
            "date": str(r["trade_date"]),
            "revenue_cents": int(r["revenue_cents"]),
            "order_count": int(r["order_count"]),
        }
        for r in rows
    ]


def _modifier_counts(site_id: str, days: int) -> tuple[int, int, int]:
    """Return (total_drinks, alt_milk_count, extra_shot_count)."""
    cutoff = date.today() - timedelta(days=days)
    with engine.connect() as conn:
        rows = conn.execute(
            _text(
                "SELECT modifiers, COUNT(*) AS cnt "
                "FROM order_items "
                "WHERE site_id = :sid "
                "  AND created_at >= :cutoff "
                "  AND modifiers IS NOT NULL "
                "GROUP BY modifiers"
            ),
            {"sid": site_id, "cutoff": cutoff},
        ).mappings().all()

        total_row = conn.execute(
            _text(
                "SELECT COUNT(*) AS cnt "
                "FROM order_items "
                "WHERE site_id = :sid AND created_at >= :cutoff"
            ),
            {"sid": site_id, "cutoff": cutoff},
        ).scalar()

    total_drinks = int(total_row or 0)
    alt_milk = 0
    extra_shot = 0

    for row in rows:
        cnt = int(row["cnt"])
        raw = row["modifiers"]
        if not raw:
            continue
        try:
            mods = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(mods, list):
                continue
            names = " ".join((m.get("name") or "").lower() for m in mods)
            if any(t in names for t in _ALT_MILK_TERMS):
                alt_milk += cnt
            if any(t in names for t in _EXTRA_SHOT_TERMS):
                extra_shot += cnt
        except Exception:
            continue

    return total_drinks, alt_milk, extra_shot


def _score_key_mix(site_id: str, days: int) -> dict[str, dict]:
    """
    Return {score_key: {recent_qty, prior_qty, item_name}} comparing
    most-recent half-window vs prior half-window.
    """
    from data.processing import resolve_item_key

    half = max(days // 2, 7)
    cutoff_full = date.today() - timedelta(days=days)
    cutoff_recent = date.today() - timedelta(days=half)

    with engine.connect() as conn:
        rows = conn.execute(
            _text(
                "SELECT item_name, "
                "  SUM(CASE WHEN created_at >= :recent THEN 1 ELSE 0 END) AS recent_qty, "
                "  SUM(CASE WHEN created_at < :recent THEN 1 ELSE 0 END) AS prior_qty "
                "FROM order_items "
                "WHERE site_id = :sid AND created_at >= :full "
                "GROUP BY item_name "
                "HAVING COUNT(*) >= 10"
            ),
            {"sid": site_id, "full": cutoff_full, "recent": cutoff_recent},
        ).mappings().all()

    # Roll up to score_key
    result: dict[str, dict] = {}
    for row in rows:
        item_name = row["item_name"] or ""
        try:
            sk, _ = resolve_item_key(item_name)
        except Exception:
            sk = item_name.lower()
        if sk not in result:
            result[sk] = {"recent_qty": 0, "prior_qty": 0, "item_name": item_name}
        result[sk]["recent_qty"] += int(row["recent_qty"] or 0)
        result[sk]["prior_qty"] += int(row["prior_qty"] or 0)

    return result


# ── Individual hypothesis builders ────────────────────────────────────────

def _h_revenue_concentration(hourly: dict[int, dict]) -> dict | None:
    """What % of revenue lands in the peak 2-hour window?"""
    if not hourly:
        return None

    total_rev = sum(v["revenue_cents"] for v in hourly.values())
    if total_rev == 0:
        return None

    # Find the best consecutive 2-hour window
    hours = sorted(hourly.keys())
    best_start = hours[0]
    best_rev = 0
    for h in hours:
        window_rev = hourly.get(h, {}).get("revenue_cents", 0) + \
                     hourly.get(h + 1, {}).get("revenue_cents", 0)
        if window_rev > best_rev:
            best_rev = window_rev
            best_start = h

    share_pct = round(best_rev / total_rev * 100, 1)
    if share_pct < 25:          # not concentrated enough to be meaningful
        return None

    n_days = max(v["day_count"] for v in hourly.values())
    conf, label = _confidence(n_days, cv=0.2)

    return {
        "hypothesis_key": "revenue_concentration_peak",
        "category": "revenue_pattern",
        "title": f"Revenue concentrates in a 2-hour window",
        "statement": (
            f"{share_pct}% of daily revenue typically falls between "
            f"{_hour_label(best_start)} and {_hour_label(best_start + 2)}."
        ),
        "implication": (
            "Almost everything that matters operationally happens in 2 hours. "
            "Any staffing shortfall or prep failure in this window has outsized impact on the day."
        ),
        "confidence": conf,
        "confidence_label": label,
        "evidence": {
            "peak_window_start": _hour_label(best_start),
            "peak_window_end": _hour_label(best_start + 2),
            "revenue_share_pct": share_pct,
            "days_sampled": n_days,
        },
    }


def _h_quiet_window(hourly: dict[int, dict]) -> dict | None:
    """Most consistent low-traffic period — frame as prep opportunity."""
    if not hourly:
        return None

    total_rev = sum(v["revenue_cents"] for v in hourly.values())
    if total_rev == 0:
        return None

    # Only consider afternoon hours (12pm–5pm) as prep opportunities
    afternoon = {h: v for h, v in hourly.items() if 12 <= h <= 17}
    if not afternoon:
        return None

    quietest_h = min(afternoon, key=lambda h: afternoon[h]["revenue_cents"])
    quiet_share = round(afternoon[quietest_h]["revenue_cents"] / total_rev * 100, 1)

    if quiet_share > 10:       # too busy to call it a quiet window
        return None

    n_days = max(v["day_count"] for v in hourly.values())
    conf, label = _confidence(n_days, cv=0.3)

    return {
        "hypothesis_key": "quiet_prep_window",
        "category": "revenue_pattern",
        "title": "Reliable quiet window for prep and admin",
        "statement": (
            f"The {_hour_label(quietest_h)}–{_hour_label(quietest_h + 1)} window is your "
            f"most consistent low-traffic period, averaging only {quiet_share}% of daily revenue."
        ),
        "implication": (
            "This is a predictable window for prep, cleaning, stock counts, and "
            "admin tasks without impacting service."
        ),
        "confidence": conf,
        "confidence_label": label,
        "evidence": {
            "quiet_hour_start": _hour_label(quietest_h),
            "quiet_hour_end": _hour_label(quietest_h + 1),
            "revenue_share_pct": quiet_share,
            "days_sampled": n_days,
        },
    }


def _h_dow_efficiency(correlations: dict) -> dict | None:
    """Which day of week has the worst revenue per labour dollar?"""
    by_dow = correlations.get("by_dow") or []
    valid = [
        d for d in by_dow
        if d.get("rev_per_labor_dollar") and float(d["rev_per_labor_dollar"]) > 0
    ]
    if len(valid) < 3:
        return None

    valid.sort(key=lambda d: float(d["rev_per_labor_dollar"]))
    worst = valid[0]
    best = valid[-1]

    worst_rate = float(worst["rev_per_labor_dollar"])
    best_rate = float(best["rev_per_labor_dollar"])
    gap_pct = round((best_rate - worst_rate) / best_rate * 100, 1)

    if gap_pct < 10:            # not a meaningful gap
        return None

    conf, label = _confidence(n_days=28, cv=0.25)

    return {
        "hypothesis_key": "dow_efficiency_gap",
        "category": "labour_efficiency",
        "title": f"{worst['day_name']}s are the least efficient trading day",
        "statement": (
            f"{worst['day_name']}s generate ${worst_rate:.2f} revenue per labour dollar — "
            f"{gap_pct}% less than {best['day_name']}s (${best_rate:.2f})."
        ),
        "implication": (
            f"Review the staffing configuration on {worst['day_name']}s. "
            "The same labour spend is producing significantly less revenue than your best day."
        ),
        "confidence": conf,
        "confidence_label": label,
        "evidence": {
            "worst_day": worst["day_name"],
            "worst_rev_per_labor_dollar": round(worst_rate, 2),
            "best_day": best["day_name"],
            "best_rev_per_labor_dollar": round(best_rate, 2),
            "gap_pct": gap_pct,
        },
    }


def _h_labour_trend(daily_rows: list[dict]) -> dict | None:
    """Is labour % trending up or down?"""
    rows = [r for r in daily_rows if r.get("labor_pct") is not None
            and r.get("labor_data_quality") != "missing"]
    if len(rows) < 7:
        return None

    values = [float(r["labor_pct"]) for r in rows]
    slope, r_sq = _linear_trend(values)

    if abs(slope) < 0.05 or r_sq < 0.15:   # trend too flat or noisy
        return None

    start_pct = round(sum(values[:5]) / 5, 1)
    end_pct = round(sum(values[-5:]) / 5, 1)
    direction = "risen" if slope > 0 else "fallen"
    conf, label = _confidence(len(rows), cv=_cv(values))

    # Weekly cost implication (rough)
    avg_rev = sum(float(r.get("revenue_cents", 0)) for r in rows[-7:]) / 7 if rows else 0
    weekly_impact_cents = int(abs(end_pct - start_pct) / 100 * avg_rev * 7)

    return {
        "hypothesis_key": "labour_trend",
        "category": "labour_efficiency",
        "title": f"Labour % has {direction} over the last {len(rows)} days",
        "statement": (
            f"Labour as a % of revenue has {direction} from {start_pct}% to {end_pct}% "
            f"over the last {len(rows)} days."
        ),
        "implication": (
            f"If the trend continues, this represents approximately "
            f"${weekly_impact_cents / 100:,.0f}/week in {'extra cost' if slope > 0 else 'savings'} "
            "over the next month."
        ),
        "confidence": conf,
        "confidence_label": label,
        "evidence": {
            "start_labor_pct": start_pct,
            "end_labor_pct": end_pct,
            "direction": direction,
            "slope_per_day": round(slope, 3),
            "r_squared": r_sq,
            "days_sampled": len(rows),
            "weekly_impact_cents": weekly_impact_cents,
        },
    }


def _h_avg_ticket_trend(daily_by_date: list[dict]) -> dict | None:
    """Is average ticket size moving?"""
    rows = [r for r in daily_by_date
            if r.get("order_count", 0) > 5 and r.get("revenue_cents", 0) > 0]
    if len(rows) < 7:
        return None

    tickets = [r["revenue_cents"] / r["order_count"] for r in rows]
    slope, r_sq = _linear_trend(tickets)

    if abs(slope) < 5 or r_sq < 0.15:      # less than 5 cents/day drift or too noisy
        return None

    start_cents = round(sum(tickets[:5]) / 5)
    end_cents = round(sum(tickets[-5:]) / 5)
    delta_cents = end_cents - start_cents
    direction = "increased" if delta_cents > 0 else "decreased"

    # Weekly uplift/loss across typical order volume
    avg_orders_per_day = sum(r["order_count"] for r in rows) / len(rows)
    weekly_impact_cents = int(abs(delta_cents) * avg_orders_per_day * 7)

    conf, label = _confidence(len(rows), cv=_cv(tickets))

    return {
        "hypothesis_key": "avg_ticket_trend",
        "category": "revenue_pattern",
        "title": f"Average ticket size has {direction}",
        "statement": (
            f"Average transaction value has {direction} from "
            f"${start_cents / 100:.2f} to ${end_cents / 100:.2f} "
            f"over {len(rows)} days ({'+' if delta_cents > 0 else ''}"
            f"${delta_cents / 100:.2f} per order)."
        ),
        "implication": (
            f"Across your typical order volume this {direction.replace('d', 's')} "
            f"approximately ${weekly_impact_cents / 100:,.0f}/week in revenue."
        ),
        "confidence": conf,
        "confidence_label": label,
        "evidence": {
            "start_avg_ticket_cents": start_cents,
            "end_avg_ticket_cents": end_cents,
            "delta_cents": delta_cents,
            "direction": direction,
            "days_sampled": len(rows),
            "weekly_impact_cents": weekly_impact_cents,
        },
    }


def _h_product_mix_shift(mix: dict[str, dict]) -> list[dict]:
    """Surface the biggest drink category movers between recent and prior window."""
    recent_total = sum(v["recent_qty"] for v in mix.values())
    prior_total = sum(v["prior_qty"] for v in mix.values())

    if recent_total < 50 or prior_total < 50:
        return []

    movers: list[dict] = []
    for sk, v in mix.items():
        if v["prior_qty"] < 10 or v["recent_qty"] < 10:
            continue
        prior_share = v["prior_qty"] / prior_total * 100
        recent_share = v["recent_qty"] / recent_total * 100
        delta = recent_share - prior_share

        if abs(delta) < 2.5:    # less than 2.5pp shift — not notable
            continue

        direction = "grown" if delta > 0 else "fallen"
        item_label = (v["item_name"] or sk).title()

        movers.append({
            "hypothesis_key": f"mix_shift_{sk}",
            "category": "product_mix",
            "title": f"{item_label} share is {direction}",
            "statement": (
                f"{item_label} has {direction} from {prior_share:.1f}% to "
                f"{recent_share:.1f}% of drink orders over the measurement window "
                f"({'+'if delta > 0 else ''}{delta:.1f} percentage points)."
            ),
            "implication": (
                "Rising alt-milk or high-modifier items quietly increase your blended COGS "
                "without a price change. Falling high-margin items reduce overall profitability."
                if delta > 0 else
                "Falling volume on a key item may warrant a promotion or menu placement review."
            ),
            "confidence": 0.70,
            "confidence_label": "moderate",
            "evidence": {
                "score_key": sk,
                "item_name": v["item_name"],
                "prior_share_pct": round(prior_share, 1),
                "recent_share_pct": round(recent_share, 1),
                "delta_pct_points": round(delta, 1),
                "direction": direction,
            },
        })

    movers.sort(key=lambda h: abs(h["evidence"]["delta_pct_points"]), reverse=True)
    return movers[:2]           # surface the two biggest movers


def _h_modifier_penetration(total: int, alt_milk: int, extra_shot: int) -> dict | None:
    """What % of drinks use alt milk?"""
    if total < 30:
        return None

    alt_pct = round(alt_milk / total * 100, 1)
    shot_pct = round(extra_shot / total * 100, 1)

    if alt_pct < 10:
        return None

    conf, label = _confidence(n_days=21, cv=0.2)

    # Assume ~35 cents per alt milk serve
    alt_milk_cost_cents_per_serve = 35
    daily_orders = total / 21
    weekly_alt_milk_cost = int(alt_pct / 100 * daily_orders * 7 * alt_milk_cost_cents_per_serve)

    return {
        "hypothesis_key": "modifier_alt_milk_penetration",
        "category": "cost_trend",
        "title": f"{alt_pct}% of drinks include alt milk",
        "statement": (
            f"{alt_pct}% of all drinks are made with alt milk"
            + (f", and {shot_pct}% include an extra shot." if shot_pct > 5 else ".")
        ),
        "implication": (
            f"Alt milk is your single largest per-drink cost modifier. At {alt_pct}% penetration "
            f"across typical weekly volume, it adds approximately ${weekly_alt_milk_cost / 100:,.0f}/week "
            "to COGS — worth watching if pricing hasn't been reviewed recently."
        ),
        "confidence": conf,
        "confidence_label": label,
        "evidence": {
            "alt_milk_pct": alt_pct,
            "extra_shot_pct": shot_pct,
            "drinks_sampled": total,
            "estimated_weekly_alt_milk_cost_cents": weekly_alt_milk_cost,
        },
    }


# ── Main entry point ───────────────────────────────────────────────────────

def compute_business_intelligence(site_id: str, days: int = 28) -> dict:
    """
    Compute standing business hypotheses for a site.

    Args:
        site_id: venue identifier
        days:    lookback window (default 28)

    Returns:
        {generated_at, window_days, data_days, hypotheses, summary}
    """
    hypotheses: list[dict] = []

    # Hourly revenue distribution
    try:
        hourly = _hourly_revenue(site_id, days)
        h = _h_revenue_concentration(hourly)
        if h:
            hypotheses.append(h)
        h = _h_quiet_window(hourly)
        if h:
            hypotheses.append(h)
    except Exception as exc:
        logger.info("hourly revenue analysis failed: %s", exc)
        hourly = {}

    data_days = max((v["day_count"] for v in hourly.values()), default=0)

    # Day-of-week efficiency
    try:
        corr = get_profitability_correlations(site_id, days=days)
        h = _h_dow_efficiency(corr)
        if h:
            hypotheses.append(h)
    except Exception as exc:
        logger.info("DOW efficiency analysis failed: %s", exc)

    # Labour % trend
    try:
        start = date.today() - timedelta(days=days)
        daily_prof = get_daily_profitability(site_id, start, date.today())
        h = _h_labour_trend(daily_prof)
        if h:
            hypotheses.append(h)
    except Exception as exc:
        logger.info("labour trend analysis failed: %s", exc)
        daily_prof = []

    # Average ticket trend
    try:
        daily_dates = _daily_revenue_by_date(site_id, days)
        h = _h_avg_ticket_trend(daily_dates)
        if h:
            hypotheses.append(h)
    except Exception as exc:
        logger.info("avg ticket trend failed: %s", exc)

    # Product mix shift
    try:
        mix = _score_key_mix(site_id, days)
        hypotheses.extend(_h_product_mix_shift(mix))
    except Exception as exc:
        logger.info("product mix analysis failed: %s", exc)

    # Modifier penetration
    try:
        total, alt_milk, extra_shot = _modifier_counts(site_id, days)
        h = _h_modifier_penetration(total, alt_milk, extra_shot)
        if h:
            hypotheses.append(h)
    except Exception as exc:
        logger.info("modifier penetration analysis failed: %s", exc)

    # Sort: strong first, then moderate, then weak; within tier alphabetical by key
    _rank = {"strong": 0, "moderate": 1, "weak": 2}
    hypotheses.sort(key=lambda h: (_rank.get(h["confidence_label"], 9), h["hypothesis_key"]))

    summary = {
        "strong": sum(1 for h in hypotheses if h["confidence_label"] == "strong"),
        "moderate": sum(1 for h in hypotheses if h["confidence_label"] == "moderate"),
        "weak": sum(1 for h in hypotheses if h["confidence_label"] == "weak"),
        "total": len(hypotheses),
    }

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "window_days": days,
        "data_days": data_days,
        "hypotheses": hypotheses,
        "summary": summary,
    }


def format_hypotheses_for_chat(hypotheses: list[dict], max_items: int = 6) -> str:
    """
    Return a compact plain-language summary for injection into the chat system prompt.
    Only includes strong and moderate hypotheses.
    """
    lines = []
    for h in hypotheses:
        if h["confidence_label"] not in ("strong", "moderate"):
            continue
        lines.append(f"- {h['statement']}")
        if len(lines) >= max_items:
            break
    return "\n".join(lines)
