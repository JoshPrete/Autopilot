"""
Clubhouse Autopilot - Chat Engine
Context gathering, system prompt building, and Claude API streaming.

Gathers relevant data from the database based on the user's question,
builds a system prompt with the cafe's current state, and streams
Claude's response back via SSE.
"""

import json
import logging
from datetime import date, datetime, timedelta
from typing import AsyncGenerator

import anthropic

from config.database import engine
from config.settings import settings
from data.storage import (
    get_daily_efficiency_snapshot,
    get_daily_profitability,
    get_data_freshness,
    get_document,
    get_dow_pattern,
    get_events_range,
    get_intelligence_summary,
    get_item_costs,
    get_learned_patterns,
    get_prediction,
    get_recent_documents,
    get_recent_insights,
    get_rosters_for_date,
    get_roster_summary,
    get_site,
    get_staffing_vs_workload,
    has_real_cogs,
)
from analysis.accuracy import get_rolling_accuracy

logger = logging.getLogger("autopilot.chat")

CLAUDE_MODEL = "claude-sonnet-4-5-20250929"
MAX_TOKENS = 1500


# ============================================================
# Context Gathering Helpers
# ============================================================


def _keyword_match(question: str, keywords: list[str]) -> bool:
    q = question.lower()
    return any(kw in q for kw in keywords)


def _safe_json(val):
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val
    return val


def _fmt_prediction(pred: dict) -> dict:
    fd = _safe_json(pred.get("forecast_data", {}))
    rw = fd.get("rush_windows", [])
    if isinstance(rw, str):
        rw = _safe_json(rw) or []
    # Also check the top-level rush_windows field
    if not rw and pred.get("rush_windows"):
        rw = _safe_json(pred.get("rush_windows", [])) or []

    return {
        "date": str(pred.get("forecast_date", "")),
        "predicted_drinks": fd.get("total_predicted_drinks"),
        "predicted_workload": fd.get("total_predicted_workload"),
        "staffing_mode": fd.get("staffing_mode"),
        "confidence_label": fd.get("confidence_label"),
        "rush_count": fd.get("rush_count", 0),
        "rush_windows": rw,
        "weather": fd.get("weather"),
        "event_multiplier": pred.get("event_factor"),
        "actual_accuracy": pred.get("actual_accuracy"),
        "hourly": fd.get("forecast", {}).get("hourly") if isinstance(fd.get("forecast"), dict) else None,
    }


def _get_predictions_range(site_id: str, start: date, end: date) -> list[dict]:
    from sqlalchemy import text

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT forecast_date, forecast_data, confidence_score, "
                "event_factor, actual_accuracy, rush_windows "
                "FROM predictions "
                "WHERE site_id = :sid AND forecast_date BETWEEN :s AND :e "
                "ORDER BY forecast_date"
            ),
            {"sid": site_id, "s": start, "e": end},
        ).mappings().all()

    return [_fmt_prediction(dict(r)) for r in rows]


def _get_upcoming_events(site_id: str, days_ahead: int = 14) -> list[dict]:
    from sqlalchemy import text

    today = date.today()
    end = today + timedelta(days=days_ahead)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT event_name, event_date, historical_impact "
                "FROM special_events "
                "WHERE site_id = :sid AND event_date BETWEEN :s AND :e "
                "ORDER BY event_date"
            ),
            {"sid": site_id, "s": today, "e": end},
        ).mappings().all()

    return [
        {
            "name": r["event_name"],
            "date": str(r["event_date"]),
            "impact_multiplier": float(r["historical_impact"]) if r["historical_impact"] else 1.0,
        }
        for r in rows
    ]


def _get_revenue_from_orders(site_id: str, days: int = 30) -> list[dict]:
    """Build daily revenue summary from orders_raw (since daily_sales_history may not exist)."""
    from sqlalchemy import text

    cutoff = date.today() - timedelta(days=days)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT DATE(closed_at) AS sale_date, "
                "COUNT(*) AS order_count, "
                "SUM(total_money_cents) AS total_cents "
                "FROM orders_raw "
                "WHERE site_id = :sid AND closed_at >= :cutoff "
                "AND state = 'COMPLETED' "
                "GROUP BY DATE(closed_at) "
                "ORDER BY sale_date DESC"
            ),
            {"sid": site_id, "cutoff": cutoff},
        ).mappings().all()

    return [
        {
            "date": str(r["sale_date"]),
            "day_name": date.fromisoformat(str(r["sale_date"])).strftime("%A"),
            "revenue": round(int(r["total_cents"] or 0) / 100, 2),
            "orders": int(r["order_count"]),
        }
        for r in rows
    ]


def _get_daily_items_summary(site_id: str, days: int = 14) -> list[dict]:
    """Daily drink/item counts from order_items."""
    from sqlalchemy import text

    cutoff = date.today() - timedelta(days=days)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT DATE(oi.created_at) AS sale_date, "
                "COUNT(*) AS total_items, "
                "SUM(oi.workload_units) AS total_workload "
                "FROM order_items oi "
                "WHERE oi.site_id = :sid AND oi.created_at >= :cutoff "
                "GROUP BY DATE(oi.created_at) "
                "ORDER BY sale_date DESC"
            ),
            {"sid": site_id, "cutoff": cutoff},
        ).mappings().all()

    return [
        {
            "date": str(r["sale_date"]),
            "items": int(r["total_items"]),
            "workload": round(float(r["total_workload"] or 0), 1),
        }
        for r in rows
    ]


def _get_top_items(site_id: str, days: int = 14, limit: int = 10) -> list[dict]:
    """Most popular items recently."""
    from sqlalchemy import text

    cutoff = date.today() - timedelta(days=days)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT item_name, COUNT(*) AS cnt, "
                "ROUND(AVG(workload_units)::numeric, 1) AS avg_wu "
                "FROM order_items "
                "WHERE site_id = :sid AND created_at >= :cutoff "
                "GROUP BY item_name "
                "ORDER BY cnt DESC LIMIT :lim"
            ),
            {"sid": site_id, "cutoff": cutoff, "lim": limit},
        ).mappings().all()

    return [
        {"item": r["item_name"], "count": int(r["cnt"]), "avg_workload": float(r["avg_wu"] or 0)}
        for r in rows
    ]


def _get_item_counts_by_day(site_id: str, days: int = 7) -> list[dict]:
    """Per-item daily counts — answers 'how many X did we sell'."""
    from sqlalchemy import text

    cutoff = date.today() - timedelta(days=days)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT DATE(oi.created_at) AS sale_date, "
                "oi.item_name, "
                "COUNT(*) AS qty "
                "FROM order_items oi "
                "WHERE oi.site_id = :sid AND oi.created_at >= :cutoff "
                "GROUP BY DATE(oi.created_at), oi.item_name "
                "ORDER BY sale_date DESC, qty DESC"
            ),
            {"sid": site_id, "cutoff": cutoff},
        ).mappings().all()

    return [
        {
            "date": str(r["sale_date"]),
            "item": r["item_name"],
            "qty": int(r["qty"]),
        }
        for r in rows
    ]


def _get_item_variations(site_id: str, days: int = 7) -> list[dict]:
    """Pull item variations and detailed modifiers from raw order payloads."""
    from sqlalchemy import text

    cutoff = date.today() - timedelta(days=days)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT DATE(o.closed_at) AS sale_date, o.payload "
                "FROM orders_raw o "
                "WHERE o.site_id = :sid AND o.closed_at >= :cutoff "
                "AND o.state = 'COMPLETED'"
            ),
            {"sid": site_id, "cutoff": cutoff},
        ).all()

    # Build per-item, per-variation, per-day breakdown
    item_data = {}  # item_name -> {variations: {name: count}, modifiers: {name: count}, daily: {date: count}, total: int}
    skip_mods = {"N/A", "Default Espresso"}

    for row in rows:
        sale_date = str(row[0])
        payload = _safe_json(row[1]) if isinstance(row[1], str) else row[1]
        if not isinstance(payload, dict):
            continue

        for li in payload.get("line_items", []):
            name = li.get("name", "Unknown")
            variation = li.get("variation_name", "")

            if name not in item_data:
                item_data[name] = {"total": 0, "variations": {}, "modifiers": {}, "daily": {}}

            item_data[name]["total"] += 1

            if sale_date not in item_data[name]["daily"]:
                item_data[name]["daily"][sale_date] = 0
            item_data[name]["daily"][sale_date] += 1

            if variation and variation != "Regular":
                item_data[name]["variations"][variation] = item_data[name]["variations"].get(variation, 0) + 1

            for mod in li.get("modifiers", []):
                mod_name = mod.get("name", "?")
                if mod_name not in skip_mods:
                    item_data[name]["modifiers"][mod_name] = item_data[name]["modifiers"].get(mod_name, 0) + 1

    # Convert to sorted list
    result = []
    for name, data in sorted(item_data.items(), key=lambda x: -x[1]["total"]):
        entry = {"item": name, "total": data["total"]}
        if data["variations"]:
            entry["variations"] = sorted(data["variations"].items(), key=lambda x: -x[1])
        if data["modifiers"]:
            entry["modifiers"] = sorted(data["modifiers"].items(), key=lambda x: -x[1])
        entry["daily"] = sorted(data["daily"].items())
        result.append(entry)

    return result


def _get_modifier_stats(site_id: str, days: int = 7) -> dict:
    """Full modifier detail report pulled from raw order payloads."""
    from sqlalchemy import text

    cutoff = date.today() - timedelta(days=days)
    with engine.connect() as conn:
        # Pull detailed modifier names from raw payloads
        raw_rows = conn.execute(
            text(
                "SELECT DATE(o.closed_at) AS sale_date, o.payload "
                "FROM orders_raw o "
                "WHERE o.site_id = :sid AND o.closed_at >= :cutoff "
                "AND o.state = 'COMPLETED'"
            ),
            {"sid": site_id, "cutoff": cutoff},
        ).all()

        # Size/item breakdown from order_items
        sizes = conn.execute(
            text(
                "SELECT item_name, COUNT(*) AS cnt "
                "FROM order_items "
                "WHERE site_id = :sid AND created_at >= :cutoff "
                "GROUP BY item_name ORDER BY cnt DESC"
            ),
            {"sid": site_id, "cutoff": cutoff},
        ).all()

        total_items = conn.execute(
            text("SELECT COUNT(*) FROM order_items WHERE site_id = :sid AND created_at >= :cutoff"),
            {"sid": site_id, "cutoff": cutoff},
        ).scalar() or 1

    # Parse raw payloads for detailed modifier names
    # Categorise modifiers
    milk_names = {"Oat", "Almond", "Soy", "Coconut", "Full Cream", "Skim", "Lactose Free"}
    syrup_names = {"Vanilla", "Caramel", "Chocolate", "No Syrup"}
    drink_types = {"Flat White", "Latte", "Cappuccino", "Long Black", "Espresso",
                   "Chai", "Mocha", "Iced Latte", "Iced Mocha", "Iced Chai",
                   "Iced Matcha", "Strawberry Matcha", "Piccolo"}
    extras = {"EXTRA HOT", "STEVIA", "SUGAR", "DINE IN", "2 Extra Shot",
              "1 Extra Shot", "2", "3"}

    modifier_counts = {}  # name -> count
    modifier_daily = {}   # date -> {name -> count}
    milk_counts = {}
    syrup_counts = {}
    drink_type_counts = {}
    extras_counts = {}
    total_drinks = 0

    for row in raw_rows:
        sale_date = str(row[0])
        payload = _safe_json(row[1]) if isinstance(row[1], str) else row[1]
        if not isinstance(payload, dict):
            continue

        for li in payload.get("line_items", []):
            total_drinks += 1
            for mod in li.get("modifiers", []):
                name = mod.get("name", "Unknown")
                if name == "N/A" or name == "Default Espresso":
                    continue

                modifier_counts[name] = modifier_counts.get(name, 0) + 1

                if sale_date not in modifier_daily:
                    modifier_daily[sale_date] = {}
                modifier_daily[sale_date][name] = modifier_daily[sale_date].get(name, 0) + 1

                if name in milk_names:
                    milk_counts[name] = milk_counts.get(name, 0) + 1
                elif name in syrup_names:
                    syrup_counts[name] = syrup_counts.get(name, 0) + 1
                elif name in drink_types:
                    drink_type_counts[name] = drink_type_counts.get(name, 0) + 1
                elif name in extras or name.startswith("2") or name.startswith("3"):
                    extras_counts[name] = extras_counts.get(name, 0) + 1

    total_drinks = max(total_drinks, 1)

    def _sorted_counts(d):
        return sorted(d.items(), key=lambda x: x[1], reverse=True)

    size_breakdown = [{"size": r[0], "count": int(r[1]), "pct": round(int(r[1]) / total_items * 100, 1)} for r in sizes]

    return {
        "total_drinks": total_drinks,
        "total_items": total_items,
        "milk_breakdown": [{"name": k, "count": v, "pct": round(v / total_drinks * 100, 1)} for k, v in _sorted_counts(milk_counts)],
        "syrup_breakdown": [{"name": k, "count": v, "pct": round(v / total_drinks * 100, 1)} for k, v in _sorted_counts(syrup_counts)],
        "drink_types": [{"name": k, "count": v, "pct": round(v / total_drinks * 100, 1)} for k, v in _sorted_counts(drink_type_counts)],
        "extras": [{"name": k, "count": v, "pct": round(v / total_drinks * 100, 1)} for k, v in _sorted_counts(extras_counts)],
        "all_modifiers": [{"name": k, "count": v, "pct": round(v / total_drinks * 100, 1)} for k, v in _sorted_counts(modifier_counts)],
        "daily": {d: _sorted_counts(mods) for d, mods in sorted(modifier_daily.items(), reverse=True)},
        "size_breakdown": size_breakdown,
    }


def _get_profitability_context(site_id: str, days: int = 14) -> list[dict]:
    """Fetch daily P&L records for the last N days."""
    try:
        today = date.today()
        start = today - timedelta(days=days)
        return get_daily_profitability(site_id, start, today)
    except Exception:
        return []


def _get_item_margins_context(site_id: str, days: int = 14) -> list[dict]:
    """Compute item-level margin analysis."""
    try:
        from analysis.profitability import compute_item_margins
        return compute_item_margins(site_id, days=days)
    except Exception:
        return []


def _get_workload_timeline_recent(site_id: str, limit: int = 48) -> list[dict]:
    from sqlalchemy import text

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT interval_start, workload_units, orders_count, items_count "
                "FROM workload_timeline "
                "WHERE site_id = :sid "
                "ORDER BY interval_start DESC LIMIT :lim"
            ),
            {"sid": site_id, "lim": limit},
        ).mappings().all()

    return [
        {
            "time": str(r["interval_start"]),
            "workload_units": float(r["workload_units"]) if r["workload_units"] else 0,
            "orders": r["orders_count"],
            "items": r["items_count"],
        }
        for r in rows
    ]


def _get_hourly_averages(site_id: str, weeks_back: int = 4) -> list[dict]:
    """Average workload by hour of day from recent timeline data."""
    from sqlalchemy import text

    cutoff = datetime.utcnow() - timedelta(weeks=weeks_back)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT EXTRACT(HOUR FROM interval_start)::int AS hour, "
                "ROUND(AVG(workload_units)::numeric, 1) AS avg_wu, "
                "ROUND(AVG(orders_count)::numeric, 1) AS avg_orders, "
                "ROUND(AVG(items_count)::numeric, 1) AS avg_items "
                "FROM workload_timeline "
                "WHERE site_id = :sid AND interval_start >= :cutoff "
                "GROUP BY EXTRACT(HOUR FROM interval_start) "
                "ORDER BY hour"
            ),
            {"sid": site_id, "cutoff": cutoff},
        ).mappings().all()

    return [
        {
            "hour": f"{int(r['hour'])}:00",
            "avg_workload": float(r["avg_wu"]),
            "avg_orders": float(r["avg_orders"]),
            "avg_items": float(r["avg_items"]),
        }
        for r in rows
    ]


# ============================================================
# Roster & Staffing Helpers
# ============================================================


def _get_roster_for_date(site_id: str, target_date: date) -> list[dict]:
    """Get formatted roster for a specific date."""
    try:
        rosters = get_rosters_for_date(site_id, target_date)
        return [
            {
                "name": r.get("employee_name") or "TBC",
                "start": str(r["start_time"]),
                "end": str(r["end_time"]),
                "hours": float(r["total_hours"]) if r.get("total_hours") else None,
                "is_open": r.get("is_open", False),
            }
            for r in rosters
        ]
    except Exception:
        return []


def _has_roster_data(site_id: str) -> bool:
    """Quick check if any roster data exists for this site."""
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM deputy_rosters WHERE site_id = :sid LIMIT 1"),
                {"sid": site_id},
            ).scalar()
            return (count or 0) > 0
    except Exception:
        return False


# ============================================================
# Knowledge Layer Helpers (derived from existing data)
# ============================================================


def _get_operational_benchmarks(site_id: str, weeks: int = 4) -> dict:
    """
    Operational benchmarks derived from historical data:
    - Average drinks per hour at different times
    - Peak hour per day-of-week
    - Busiest/quietest days ranking
    """
    from sqlalchemy import text

    cutoff = datetime.utcnow() - timedelta(weeks=weeks)

    try:
        with engine.connect() as conn:
            # Avg items per hour by day-of-week
            peak_hours = conn.execute(
                text("""
                    SELECT
                        TRIM(TO_CHAR(interval_start, 'Day')) AS day_name,
                        EXTRACT(HOUR FROM interval_start)::int AS hour,
                        ROUND(AVG(items_count)::numeric, 1) AS avg_items,
                        ROUND(AVG(workload_units)::numeric, 1) AS avg_wu
                    FROM workload_timeline
                    WHERE site_id = :sid AND interval_start >= :cutoff
                    GROUP BY TRIM(TO_CHAR(interval_start, 'Day')),
                             EXTRACT(HOUR FROM interval_start)
                    ORDER BY avg_items DESC
                    LIMIT 20
                """),
                {"sid": site_id, "cutoff": cutoff},
            ).mappings().all()

            # Daily volume ranking
            daily_ranking = conn.execute(
                text("""
                    SELECT
                        TRIM(TO_CHAR(interval_start, 'Day')) AS day_name,
                        ROUND(AVG(daily_total)::numeric, 0) AS avg_daily_items
                    FROM (
                        SELECT DATE(interval_start) AS d,
                               TRIM(TO_CHAR(interval_start, 'Day')) AS day_name_inner,
                               SUM(items_count) AS daily_total
                        FROM workload_timeline
                        WHERE site_id = :sid AND interval_start >= :cutoff
                        GROUP BY DATE(interval_start), TRIM(TO_CHAR(interval_start, 'Day'))
                    ) sub
                    CROSS JOIN LATERAL (SELECT sub.day_name_inner AS day_name) naming
                    GROUP BY TRIM(TO_CHAR(interval_start, 'Day'))
                    ORDER BY avg_daily_items DESC
                """),
                {"sid": site_id, "cutoff": cutoff},
            ).mappings().all()

        return {
            "peak_hours": [
                {"day": r["day_name"], "hour": f"{r['hour']}:00",
                 "avg_items": float(r["avg_items"]), "avg_workload": float(r["avg_wu"])}
                for r in peak_hours
            ],
            "daily_ranking": [
                {"day": r["day_name"], "avg_items": float(r["avg_daily_items"])}
                for r in daily_ranking
            ],
        }
    except Exception:
        return {}


def _get_trending_items(site_id: str) -> list[dict]:
    """
    Compare item counts this week vs last week to identify trends.
    Returns items with growth/decline percentages.
    """
    from sqlalchemy import text

    today = date.today()
    this_week_start = today - timedelta(days=7)
    last_week_start = today - timedelta(days=14)

    try:
        with engine.connect() as conn:
            this_week = conn.execute(
                text("""
                    SELECT item_name, COUNT(*) AS cnt
                    FROM order_items
                    WHERE site_id = :sid
                    AND created_at >= :start AND created_at < :end
                    GROUP BY item_name
                """),
                {"sid": site_id, "start": this_week_start, "end": today},
            ).mappings().all()

            last_week = conn.execute(
                text("""
                    SELECT item_name, COUNT(*) AS cnt
                    FROM order_items
                    WHERE site_id = :sid
                    AND created_at >= :start AND created_at < :end
                    GROUP BY item_name
                """),
                {"sid": site_id, "start": last_week_start, "end": this_week_start},
            ).mappings().all()

        this_counts = {r["item_name"]: int(r["cnt"]) for r in this_week}
        last_counts = {r["item_name"]: int(r["cnt"]) for r in last_week}

        trends = []
        all_items = set(this_counts.keys()) | set(last_counts.keys())
        for item in all_items:
            this_cnt = this_counts.get(item, 0)
            last_cnt = last_counts.get(item, 0)
            if last_cnt == 0 and this_cnt > 0:
                change_pct = 100.0
            elif last_cnt > 0:
                change_pct = round((this_cnt - last_cnt) / last_cnt * 100, 1)
            else:
                continue

            if abs(change_pct) >= 10 and (this_cnt + last_cnt) >= 5:
                trends.append({
                    "item": item,
                    "this_week": this_cnt,
                    "last_week": last_cnt,
                    "change_pct": change_pct,
                    "direction": "up" if change_pct > 0 else "down",
                })

        trends.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
        return trends[:15]

    except Exception:
        return []


def _get_weather_context(site_id: str) -> dict | None:
    """Extract weather data from tomorrow's prediction if available."""
    try:
        tomorrow = date.today() + timedelta(days=1)
        pred = get_prediction(site_id, tomorrow)
        if pred:
            fd = _safe_json(pred.get("forecast_data", {}))
            weather = fd.get("weather")
            if weather and isinstance(weather, dict):
                return weather
    except Exception:
        pass
    return None


def _get_cogs_snapshot(site_id: str) -> dict:
    """Summarize available COGS coverage and recency for chat grounding."""
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT
                        COUNT(*) AS total_items,
                        COUNT(*) FILTER (WHERE source IN ('xero', 'document')) AS real_items,
                        COUNT(*) FILTER (WHERE source = 'xero') AS xero_items,
                        COUNT(*) FILTER (WHERE source = 'document') AS document_items,
                        MAX(updated_at) AS last_updated_at
                    FROM item_costs
                    WHERE site_id = :sid
                    """
                ),
                {"sid": site_id},
            ).mappings().first()
    except Exception:
        return {}

    if not row:
        return {}
    return {
        "total_items": int(row.get("total_items") or 0),
        "real_items": int(row.get("real_items") or 0),
        "xero_items": int(row.get("xero_items") or 0),
        "document_items": int(row.get("document_items") or 0),
        "last_updated_at": str(row.get("last_updated_at")) if row.get("last_updated_at") else None,
    }


def _get_recent_efficiency_context(site_id: str, lookback_days: int = 3) -> dict | None:
    """
    Return the latest daily efficiency snapshot with usable trade/staffing signals.
    Falls back to the most recent checked date.
    """
    today = date.today()
    fallback = None

    for offset in range(0, max(1, lookback_days)):
        d = today - timedelta(days=offset)
        snap = get_daily_efficiency_snapshot(site_id, d)
        if not fallback:
            fallback = snap
        summary = snap.get("summary", {})
        has_signal = (
            int(summary.get("intervals_analyzed") or 0) > 0
            or int(summary.get("total_revenue_cents") or 0) > 0
        )
        if has_signal:
            return snap

    return fallback


def _get_recent_recommendations(site_id: str, days: int = 14, limit: int = 8) -> list[dict]:
    """Fetch recent persisted recommendations for grounded follow-up."""
    from sqlalchemy import text

    cutoff = date.today() - timedelta(days=days)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT rec_id, action_type, action_timing, action_details, adopted, outcome_data
                    FROM recommendations
                    WHERE site_id = :sid
                      AND DATE(action_timing) >= :cutoff
                    ORDER BY action_timing DESC
                    LIMIT :lim
                    """
                ),
                {"sid": site_id, "cutoff": cutoff, "lim": limit},
            ).mappings().all()
    except Exception:
        return []

    result = []
    for r in rows:
        details = _safe_json(r.get("action_details") or {})
        outcome = _safe_json(r.get("outcome_data") or {})
        if not isinstance(details, dict):
            details = {}
        if not isinstance(outcome, dict):
            outcome = {}
        realized = outcome.get("realized") if isinstance(outcome.get("realized"), dict) else {}
        result.append(
            {
                "rec_id": str(r.get("rec_id")),
                "action_type": r.get("action_type"),
                "action_timing": str(r.get("action_timing")),
                "title": details.get("title"),
                "expected_weekly_profit_uplift_cents": details.get("expected_weekly_profit_uplift_cents"),
                "ranking_score_cents": details.get("ranking_score_cents"),
                "confidence": details.get("confidence"),
                "adopted": bool(r.get("adopted")),
                "realized_weekly_delta_cents": realized.get("weekly_net_profit_delta_cents"),
            }
        )
    return result


# ============================================================
# Main Context Gatherer
# ============================================================


def gather_chat_context(site_id: str, question: str) -> dict:
    today = date.today()
    context = {}

    # --- Always: data freshness ---
    try:
        freshness = get_data_freshness(site_id)
        if freshness:
            context["data_freshness"] = freshness
    except Exception:
        pass

    # --- Always: today's prediction ---
    today_pred = get_prediction(site_id, today)
    if today_pred:
        context["today_prediction"] = _fmt_prediction(today_pred)
    else:
        tomorrow_pred = get_prediction(site_id, today + timedelta(days=1))
        if tomorrow_pred:
            context["tomorrow_prediction"] = _fmt_prediction(tomorrow_pred)

    # --- Always: recent accuracy ---
    try:
        acc = get_rolling_accuracy(site_id, days_back=7)
        if acc.get("days_measured", 0) > 0:
            context["accuracy"] = acc
    except Exception:
        pass

    # --- Always: upcoming events ---
    try:
        events = _get_upcoming_events(site_id, days_ahead=14)
        if events:
            context["upcoming_events"] = events
    except Exception:
        pass

    # --- Always: revenue (full history available) ---
    try:
        revenue = _get_revenue_from_orders(site_id, days=90)
        if revenue:
            context["recent_revenue"] = revenue
    except Exception:
        pass

    # --- Always: rolling 7-day roster window (past 3 + today + future 3) ---
    try:
        roster_summary = get_roster_summary(
            site_id, today - timedelta(days=3), today + timedelta(days=4)
        )
        if roster_summary:
            context["rolling_roster"] = roster_summary
        # Detailed today + tomorrow rosters
        today_roster = _get_roster_for_date(site_id, today)
        if today_roster:
            context["today_roster"] = today_roster
        tomorrow_roster = _get_roster_for_date(site_id, today + timedelta(days=1))
        if tomorrow_roster:
            context["tomorrow_roster"] = tomorrow_roster
    except Exception:
        pass

    # --- Always: COGS source status ---
    try:
        context["has_real_cogs"] = has_real_cogs(site_id)
    except Exception:
        context["has_real_cogs"] = False

    # --- Always: Xero connection status ---
    try:
        from data.xero import is_xero_configured
        context["xero_connected"] = is_xero_configured(site_id)
    except Exception:
        context["xero_connected"] = False

    # --- Always: COGS coverage summary ---
    try:
        cogs_snapshot = _get_cogs_snapshot(site_id)
        if cogs_snapshot:
            context["cogs_snapshot"] = cogs_snapshot
    except Exception:
        pass

    # --- Always: recent documents ---
    try:
        recent_docs = get_recent_documents(site_id, limit=5)
        if recent_docs:
            context["recent_documents"] = recent_docs
    except Exception:
        pass

    # --- Always: intelligence summary ---
    try:
        intel_summary = get_intelligence_summary(site_id)
        if intel_summary and intel_summary.get("active_insights", 0) > 0:
            context["intelligence_summary"] = intel_summary
    except Exception:
        pass

    # --- Always: recent active insights ---
    try:
        recent_insights = get_recent_insights(site_id, days=7)
        if recent_insights:
            context["active_insights"] = recent_insights
    except Exception:
        pass

    # --- Conditional: learned patterns (keyword-triggered) ---
    if _keyword_match(question, ["insight", "learn", "pattern", "intelligence",
                                  "recommendation", "suggest", "advice",
                                  "improve", "optimize", "optimise"]):
        try:
            patterns = get_learned_patterns(site_id, min_confidence=0.5)
            if patterns:
                context["learned_patterns"] = patterns
        except Exception:
            pass

    # --- Conditional: events / closures / holidays ---
    if _keyword_match(question, ["closed", "closure", "holiday", "event",
                                  "public holiday", "market", "festival"]):
        try:
            past_events = get_events_range(
                site_id, today - timedelta(days=30), today + timedelta(days=30)
            )
            if past_events:
                context["events_calendar"] = [
                    {
                        "name": e["event_name"],
                        "date": str(e["event_date"]),
                        "type": e.get("event_type"),
                        "impact": float(e["historical_impact"]) if e.get("historical_impact") else None,
                    }
                    for e in past_events
                ]
        except Exception:
            pass

    # --- Conditional: staffing / roster / schedule ---
    staffing_keywords = ["staff", "roster", "schedule", "shift", "deputy",
                         "understaffed", "overstaffed", "working", "who's on",
                         "whos on", "who is on", "who is working"]
    if _keyword_match(question, staffing_keywords):
        has_rosters = _has_roster_data(site_id)
        if has_rosters:
            try:
                # Next 14 days summary
                roster_summary = get_roster_summary(site_id, today, today + timedelta(days=14))
                if roster_summary:
                    context["roster_summary"] = roster_summary

                # Historical staffing vs workload (last 30 days)
                staffing_data = get_staffing_vs_workload(
                    site_id, today - timedelta(days=30), today
                )
                if staffing_data:
                    context["staffing_vs_workload"] = staffing_data
            except Exception:
                pass
        else:
            context["deputy_status"] = "not_connected"

    # --- Conditional: operator efficiency snapshot ---
    if _keyword_match(question, ["efficiency", "profit", "labor", "labour", "staff", "roster",
                                 "schedule", "optimize", "optimise", "recommend", "action"]):
        try:
            efficiency = _get_recent_efficiency_context(site_id, lookback_days=3)
            if efficiency:
                context["daily_efficiency"] = efficiency
        except Exception:
            pass

    # --- Conditional: latest generated actions (grounding for "what should I do") ---
    if _keyword_match(question, ["recommend", "suggest", "what should", "next action",
                                 "what do i do", "improve", "optimize", "optimise",
                                 "profit", "efficiency", "staff"]):
        try:
            from analysis.next_actions import generate_next_actions
            context["next_actions_live"] = generate_next_actions(
                site_id=site_id,
                target_date=today,
                max_actions=5,
            )
        except Exception:
            pass
        try:
            recent_recs = _get_recent_recommendations(site_id, days=14, limit=8)
            if recent_recs:
                context["recent_recommendations"] = recent_recs
        except Exception:
            pass

    # --- Conditional: 2-4 week roster planning ---
    if _keyword_match(question, ["2 weeks", "3 weeks", "4 weeks", "next week", "roster ahead",
                                 "schedule ahead", "advance roster", "shift plan", "templates"]):
        try:
            from analysis.shift_optimizer import optimize_shifts_range
            context["optimized_shift_range"] = optimize_shifts_range(
                site_id=site_id,
                start_date=today,
                days=28,
            )
        except Exception:
            pass

    # --- Conditional: staffing / forecast range ---
    if _keyword_match(question, ["staff", "roster", "schedule", "next week",
                                  "next 2 weeks", "coming days", "forecast",
                                  "tomorrow", "week ahead", "predict"]):
        try:
            predictions = _get_predictions_range(site_id, today - timedelta(days=1), today + timedelta(days=14))
            if predictions:
                context["predictions_range"] = predictions
        except Exception:
            pass

    # --- Conditional: revenue / sales / money ---
    if _keyword_match(question, ["revenue", "sales", "money", "income",
                                  "takings", "how much", "dollars", "earn"]):
        try:
            context["revenue_30d"] = _get_revenue_from_orders(site_id, days=30)
        except Exception:
            pass

    # --- Conditional: rush / busy / peak ---
    if _keyword_match(question, ["rush", "busy", "peak", "busiest", "quietest",
                                  "slow", "quiet"]):
        try:
            context["dow_pattern"] = get_dow_pattern(site_id)
            context["hourly_averages"] = _get_hourly_averages(site_id)
        except Exception:
            pass

    # --- Conditional: history / trend / compare ---
    if _keyword_match(question, ["history", "trend", "compare", "last week",
                                  "week before", "month", "versus", "vs",
                                  "this week", "yesterday"]):
        try:
            context["revenue_30d"] = _get_revenue_from_orders(site_id, days=30)
            context["dow_pattern"] = get_dow_pattern(site_id)
            context["items_summary"] = _get_daily_items_summary(site_id, days=14)
        except Exception:
            pass

    # --- Conditional: menu / items / popular / "how many X" ---
    if _keyword_match(question, ["menu", "item", "popular", "top", "drink",
                                  "best seller", "selling", "sell", "sold",
                                  "how many", "toastie", "coffee", "latte",
                                  "flat white", "cappuccino", "mocha", "chai",
                                  "muffin", "croissant", "wrap", "pastry",
                                  "cookie", "juice", "smoothie", "matcha",
                                  "batch brew", "iced", "cold brew",
                                  "food", "pastry", "wrap", "butterboy",
                                  "croissant", "muffin", "bean"]):
        try:
            context["top_items"] = _get_top_items(site_id, days=14, limit=15)
            context["item_counts_by_day"] = _get_item_counts_by_day(site_id, days=7)
            context["item_variations"] = _get_item_variations(site_id, days=30)
        except Exception:
            pass

    # --- Conditional: modifiers ---
    if _keyword_match(question, ["modifier", "oat", "almond", "soy", "alt milk",
                                  "alternative milk", "iced", "extra shot",
                                  "decaf", "large", "syrup", "milk type",
                                  "upgrade", "add-on", "add on", "milk",
                                  "breakdown", "customis", "customiz"]):
        try:
            context["modifier_stats"] = _get_modifier_stats(site_id, days=90)
        except Exception:
            pass

    # --- Conditional: workload / current / now ---
    if _keyword_match(question, ["workload", "timeline", "actual", "real-time",
                                  "right now", "current", "today so far"]):
        try:
            context["workload_timeline"] = _get_workload_timeline_recent(site_id, limit=48)
        except Exception:
            pass

    # --- Conditional: weekly review ---
    if _keyword_match(question, ["review", "weekly", "report", "summary"]):
        try:
            site = get_site(site_id)
            site_name = site["name"] if site else "Clubhouse"
            from analysis.reporting import generate_weekly_review
            review = generate_weekly_review(site_id, site_name)
            context["weekly_review"] = {
                "week_start": review.get("week_start"),
                "week_end": review.get("week_end"),
                "accuracy": review.get("accuracy"),
                "adoption": review.get("adoption"),
                "insights": review.get("insights"),
                "daily_details": review.get("daily_details"),
            }
        except Exception:
            pass

    # --- Conditional: hourly patterns (any time-of-day question) ---
    if _keyword_match(question, ["hour", "morning", "afternoon", "lunch",
                                  "open", "close", "pattern"]):
        try:
            context["hourly_averages"] = _get_hourly_averages(site_id)
        except Exception:
            pass

    # --- Conditional: trending items ---
    if _keyword_match(question, ["trend", "trending", "growing", "declining",
                                  "popular", "compared to last week",
                                  "week over week", "change"]):
        try:
            trends = _get_trending_items(site_id)
            if trends:
                context["trending_items"] = trends
        except Exception:
            pass

    # --- Conditional: operational benchmarks ---
    if _keyword_match(question, ["benchmark", "average", "efficiency",
                                  "drinks per hour", "peak", "busiest",
                                  "quietest", "ranking", "best day",
                                  "worst day", "compare days"]):
        try:
            benchmarks = _get_operational_benchmarks(site_id)
            if benchmarks:
                context["benchmarks"] = benchmarks
        except Exception:
            pass

    # --- Conditional: profitability / P&L / margins ---
    if _keyword_match(question, ["profit", "p&l", "margin", "cogs", "cost of goods",
                                  "profitable", "bottom line"]):
        try:
            pnl = _get_profitability_context(site_id, days=14)
            if pnl:
                context["daily_profitability"] = pnl
            # Only include item margins if real COGS data exists
            if context.get("has_real_cogs"):
                margins = _get_item_margins_context(site_id, days=14)
                if margins:
                    context["item_margins"] = margins
        except Exception:
            pass

    # --- Conditional: labor efficiency ---
    if _keyword_match(question, ["efficiency", "revenue per hour", "cost per drink",
                                  "labor cost", "labour cost", "wage", "labor %",
                                  "labour %"]):
        try:
            pnl = _get_profitability_context(site_id, days=14)
            if pnl:
                context["daily_profitability"] = pnl
        except Exception:
            pass

    # --- Always: weather (if available in tomorrow's prediction) ---
    try:
        weather = _get_weather_context(site_id)
        if weather:
            context["tomorrow_weather"] = weather
    except Exception:
        pass

    return context


# ============================================================
# System Prompt
# ============================================================


def build_system_prompt(site_name: str, context: dict) -> str:
    # --- Data freshness header ---
    freshness = context.get("data_freshness")
    today = date.today()
    freshness_line = ""
    stale_warning = ""
    if freshness:
        try:
            fresh_date = date.fromisoformat(freshness)
            days_old = (today - fresh_date).days
            freshness_line = f"Data current through: {fresh_date.strftime('%d/%m/%Y')}"
            if days_old == 0:
                freshness_line += " (today)"
            elif days_old == 1:
                freshness_line += " (yesterday)"
            else:
                freshness_line += f" ({days_old} days ago)"
                stale_warning = f"WARNING: Data is {days_old} days stale. Warn the user about this in your responses."
        except Exception:
            freshness_line = f"Data freshness: {freshness}"
    else:
        freshness_line = "Data freshness: unknown"
        stale_warning = "WARNING: Could not determine data freshness. Note this uncertainty in responses."

    sections = [
        f"You are the Clubhouse Autopilot assistant for **{site_name}** — a specialty coffee cafe in Nundah, Brisbane.",
        "",
        f"**{freshness_line}**",
    ]
    if stale_warning:
        sections.append(f"**{stale_warning}**")

    sections.extend([
        "",
        "You're the cafe's AI analyst. You have access to real operational data: sales, predictions, workload patterns, "
        "rush windows, events, and menu analytics. Managers ask you questions to plan their day, week, and staffing.",
        "",
        "Response Format:",
        "1. Lead with data freshness indicator: 'Data through: DD/MM' or similar",
        "2. Then the key answer/number",
        "3. Then supporting detail",
        "4. Then caveats if data is stale (>1 day old)",
        "",
        "Personality & Style:",
        "- Friendly, sharp, and data-driven — like a really smart shift supervisor who knows the numbers",
        "- Lead with the key insight or number, then provide supporting detail",
        "- Use markdown tables when comparing multiple days or items — managers love quick-scan tables",
        "- Bold the most important numbers",
        "- Give actionable takeaways when relevant (e.g. 'You might want an extra hand Thursday')",
        "- Australian English, casual-professional tone",
        "- Dates in DD/MM format, currency in AUD ($)",
        "- If you don't have data for something, say so — don't guess",
        "- For recommendations, cite the specific metric(s) used (for example labor %, rev/labor-hour, understaffed intervals, margin %)",
        "- If data is missing/empty, say that explicitly and provide the next check to run",
        "- Keep responses focused. Don't pad with generic advice unless asked",
        "",
    ])

    # --- COGS status ---
    has_real = context.get("has_real_cogs", False)
    xero_connected = context.get("xero_connected", False)
    if has_real and xero_connected:
        sections.append("**COGS STATUS:** Real cost data available (source: Xero, auto-synced daily).")
        sections.append("Full P&L analysis is available with real COGS.")
        sections.append("")
    elif has_real:
        sections.append("**COGS STATUS:** Real cost data available (source: uploaded documents).")
        sections.append("Full P&L analysis is available. Consider connecting Xero at /xero/setup for automatic updates.")
        sections.append("")
    elif xero_connected:
        sections.append("**COGS STATUS:** Xero is connected but no costs have synced yet. A sync will run automatically at 5:25pm AEST, or the user can trigger one at /xero/setup.")
        sections.append("When asked about profitability, show revenue + labor (real data) but note that COGS sync is pending.")
        sections.append("")
    else:
        sections.append("**COGS STATUS:** No real cost data available. Only estimated/default COGS exist.")
        sections.append("When asked about profitability, show revenue + labor (real data) but note that COGS are NOT available.")
        sections.append("Tell the user: 'Connect Xero at /xero/setup for automatic COGS, or upload supplier invoices.'")
        sections.append("")

    # --- COGS coverage details ---
    if "cogs_snapshot" in context:
        c = context["cogs_snapshot"]
        sections.append("### COGS Coverage")
        sections.append(
            f"- Items with costs: {c.get('total_items', 0)} total "
            f"({c.get('real_items', 0)} real, {c.get('xero_items', 0)} from Xero, {c.get('document_items', 0)} from documents)"
        )
        if c.get("last_updated_at"):
            sections.append(f"- Last cost update: {c['last_updated_at']}")
        sections.append("")

    # --- Intelligence Engine ---
    if "active_insights" in context or "intelligence_summary" in context:
        sections.append("## Intelligence Engine")
        sections.append(
            "The system runs a daily intelligence cycle that analyzes patterns and learns from outcomes."
        )
        sections.append("")

        if "active_insights" in context:
            sections.append("### Active Insights (last 7 days)")
            for ins in context["active_insights"][:5]:
                icon = "!" if ins.get("severity") == "warning" else (
                    ">" if ins.get("severity") == "opportunity" else "-"
                )
                conf = float(ins.get("confidence", 0))
                sections.append(
                    f"{icon} **{ins.get('title', '')}** (confidence: {conf:.0%})"
                )
                body = ins.get("body", "")
                if body:
                    sections.append(f"  {body}")
            sections.append("")

        if "intelligence_summary" in context:
            summary = context["intelligence_summary"]
            if summary.get("top_patterns"):
                sections.append("### What the System Has Learned")
                for p in summary["top_patterns"]:
                    conf = float(p.get("confidence", 0))
                    impact = int(p.get("total_impact_cents") or 0)
                    impact_str = f", measured impact: ${impact / 100:+,.0f}/week" if impact != 0 else ""
                    sections.append(
                        f"- {p.get('description', p.get('pattern_key', '?'))} "
                        f"(confidence: {conf:.0%}{impact_str})"
                    )
                sections.append("")

            stats = summary.get("learning_stats", {})
            if stats.get("total_patterns", 0) > 0:
                sections.append(
                    f"Learning stats: {stats['total_patterns']} patterns tracked, "
                    f"{stats.get('high_confidence', 0)} high-confidence, "
                    f"{stats.get('suppressed', 0)} suppressed"
                )
                sections.append("")

        sections.append(
            "When discussing insights, mention confidence levels. High confidence (>70%) "
            "patterns with positive measured impact are proven recommendations. Low confidence "
            "patterns are observations worth monitoring."
        )
        sections.append("")

    if "learned_patterns" in context:
        sections.append("### Detailed Learned Patterns")
        for p in context["learned_patterns"][:10]:
            conf = float(p.get("confidence", 0))
            samples = int(p.get("sample_size", 0))
            impact = int(p.get("total_impact_cents") or 0)
            sections.append(
                f"- **{p.get('pattern_key', '?')}**: {p.get('description', '')} "
                f"(confidence: {conf:.0%}, samples: {samples}, "
                f"cumulative impact: ${impact / 100:+,.0f})"
            )
        sections.append("")

    sections.append("=== LIVE DATA ===")

    # --- Today/Tomorrow prediction ---
    for key, label in [("today_prediction", "Today"), ("tomorrow_prediction", "Tomorrow")]:
        if key in context:
            p = context[key]
            sections.append(f"\n## {label}'s Prediction ({p['date']})")
            sections.append(f"- **Predicted drinks: {p.get('predicted_drinks', 'N/A')}**")
            sections.append(f"- Staffing mode: {p.get('staffing_mode', 'N/A')}")
            sections.append(f"- Confidence: {p.get('confidence_label', 'N/A')}")
            sections.append(f"- Predicted workload: {p.get('predicted_workload', 'N/A')} units")
            if p.get("rush_count", 0) > 0:
                sections.append(f"- **Rush windows: {p['rush_count']}**")
                for i, rw in enumerate(p.get("rush_windows", []), 1):
                    if isinstance(rw, dict):
                        start = rw.get("start", rw.get("start_time", "?"))
                        end = rw.get("end", rw.get("end_time", "?"))
                        drinks = rw.get("predicted_drinks", rw.get("drinks", "?"))
                        dur = rw.get("duration_minutes", "?")
                        sections.append(f"  Rush {i}: {start} – {end} ({drinks} drinks, {dur} min)")
                        if rw.get("switch_3p_time"):
                            sections.append(f"    → Switch to 3-person at {rw['switch_3p_time']}")
                        if rw.get("wally_start_time"):
                            sections.append(f"    → Start Wally at {rw['wally_start_time']}")
            else:
                sections.append("- No rush windows predicted")
            if p.get("weather") and isinstance(p["weather"], dict):
                w = p["weather"]
                sections.append(f"- Weather: {w.get('temp_c', '?')}°C, {w.get('description', '?')}, "
                              f"rain {round((w.get('rain_probability', 0)) * 100)}%")
            if p.get("event_multiplier") and p["event_multiplier"] != 1.0:
                sections.append(f"- **Event multiplier active: {p['event_multiplier']}x**")
            if p.get("actual_accuracy") is not None:
                sections.append(f"- Yesterday's actual accuracy: {round(p['actual_accuracy'] * 100, 1)}%")

    # --- Predictions range ---
    if "predictions_range" in context:
        sections.append(f"\n## Predictions ({len(context['predictions_range'])} days)")
        for p in context["predictions_range"]:
            day_name = ""
            try:
                day_name = date.fromisoformat(p["date"]).strftime("%a %d/%m")
            except:
                day_name = p["date"]
            event_note = ""
            if p.get("event_multiplier") and p["event_multiplier"] != 1.0:
                event_note = f" EVENT({p['event_multiplier']}x)"
            acc_note = ""
            if p.get("actual_accuracy") is not None:
                acc_note = f" acc={round(p['actual_accuracy'] * 100)}%"
            sections.append(
                f"- {day_name}: {p.get('predicted_drinks', '?')} drinks, "
                f"{p.get('staffing_mode', '?')} mode, "
                f"{p.get('rush_count', 0)} rushes, "
                f"conf={p.get('confidence_label', '?')}"
                f"{event_note}{acc_note}"
            )

    # --- Accuracy ---
    if "accuracy" in context:
        a = context["accuracy"]
        sections.append(f"\n## Prediction Accuracy (7-day rolling)")
        sections.append(f"- **Average: {a.get('avg_accuracy', 'N/A')}%**")
        sections.append(f"- Days measured: {a.get('days_measured', 0)}")
        sections.append(f"- Trend: {a.get('trend', 'N/A')}")
        if a.get("daily_accuracies"):
            sections.append("- Daily breakdown:")
            for d in a["daily_accuracies"]:
                sections.append(f"  {d['date']}: {d['accuracy']}%")
        if a.get("alert"):
            sections.append(f"- **ALERT: {a.get('alert_reason')}**")

    # --- Revenue ---
    for key, label in [("recent_revenue", "Revenue History (last 90 days)"), ("revenue_30d", "Revenue (last 30 days)")]:
        if key in context and context[key]:
            rev = context[key]
            total = sum(r["revenue"] for r in rev)
            avg = total / len(rev) if rev else 0
            sections.append(f"\n## {label}")
            sections.append(f"- Total: **${total:,.2f}** across {len(rev)} days")
            sections.append(f"- Daily average: **${avg:,.2f}**")
            sections.append(f"- Total orders: {sum(r['orders'] for r in rev)}")
            best = max(rev, key=lambda r: r["revenue"])
            worst = min(rev, key=lambda r: r["revenue"])
            sections.append(f"- Best day: {best['date']} ({best['day_name']}) — ${best['revenue']:,.2f} ({best['orders']} orders)")
            sections.append(f"- Slowest day: {worst['date']} ({worst['day_name']}) — ${worst['revenue']:,.2f} ({worst['orders']} orders)")
            sections.append("- Day-by-day:")
            for r in rev[:14]:
                sections.append(f"  {r['date']} ({r['day_name']}): ${r['revenue']:,.2f}, {r['orders']} orders")

    # --- Staffing & Rosters ---
    if "deputy_status" in context and context["deputy_status"] == "not_connected":
        sections.append("\n## Staffing & Rosters")
        sections.append("- Deputy roster integration is not connected yet. No shift data available.")
        sections.append("- If asked about rosters/staffing, let the manager know Deputy isn't synced yet.")

    # --- Rolling Roster (7-day window) ---
    if "rolling_roster" in context:
        sections.append("\n## Rolling Roster (past 3 days + next 4 days)")
        sections.append("| Day | Staff | Hours | Labor Cost | Open |")
        sections.append("|-----|-------|-------|------------|------|")
        for day in context["rolling_roster"]:
            try:
                day_name = date.fromisoformat(day["date"]).strftime("%a %d/%m")
            except Exception:
                day_name = day["date"]
            open_note = f"{day['open_shifts']}" if day.get("open_shifts") else "0"
            sections.append(
                f"| {day_name} | {day['staff_count']} | {day['total_hours']:.1f}h | "
                f"${day['total_cost']:.0f} | {open_note} |"
            )

    if "today_roster" in context:
        sections.append("\n## Today's Roster (detail)")
        for shift in context["today_roster"]:
            open_tag = " (OPEN/UNFILLED)" if shift.get("is_open") else ""
            hours = f" ({shift['hours']}h)" if shift.get("hours") else ""
            sections.append(f"- {shift['name']}: {shift['start']} – {shift['end']}{hours}{open_tag}")

    if "tomorrow_roster" in context:
        sections.append("\n## Tomorrow's Roster (detail)")
        for shift in context["tomorrow_roster"]:
            open_tag = " (OPEN/UNFILLED)" if shift.get("is_open") else ""
            hours = f" ({shift['hours']}h)" if shift.get("hours") else ""
            sections.append(f"- {shift['name']}: {shift['start']} – {shift['end']}{hours}{open_tag}")

    if "roster_summary" in context:
        sections.append(f"\n## Roster Summary (next {len(context['roster_summary'])} days)")
        for day in context["roster_summary"]:
            try:
                day_name = date.fromisoformat(day["date"]).strftime("%a %d/%m")
            except Exception:
                day_name = day["date"]
            open_note = f", {day['open_shifts']} OPEN" if day.get("open_shifts") else ""
            sections.append(
                f"- {day_name}: {day['staff_count']} staff, "
                f"{day['total_hours']}h total, ${day['total_cost']:.0f} cost{open_note}"
            )

    if "staffing_vs_workload" in context:
        data = context["staffing_vs_workload"]
        sections.append(f"\n## Staffing vs Workload (last {len(data)} days)")
        sections.append("Use this to assess if staffing matched demand:")
        for d in data:
            drinks = d["total_drinks"] or "N/A"
            dps = d["drinks_per_staff"] or "N/A"
            sections.append(
                f"- {d['date']}: {d['staff_on']} staff, {drinks} drinks, "
                f"drinks/staff={dps}, {d['staff_hours']}h, ${d['labour_cost']:.0f}"
            )
        # Calculate average drinks-per-staff for threshold insight
        valid = [d for d in data if d.get("drinks_per_staff")]
        if valid:
            avg_dps = sum(d["drinks_per_staff"] for d in valid) / len(valid)
            sections.append(f"- **Average drinks per staff member: {avg_dps:.1f}**")
            sections.append(f"- Days above average suggest understaffing; below suggest overstaffing")

    # --- Daily Profitability (P&L) ---
    if "daily_profitability" in context:
        pnl = context["daily_profitability"]
        real_cogs = context.get("has_real_cogs", False)

        if real_cogs:
            # Full P&L with real COGS
            sections.append(f"\n## Daily P&L ({len(pnl)} days)")
            sections.append("Days with $0 labor may be missing Deputy data.")
            sections.append("")
            sections.append("| Date | Revenue | Labor | COGS | Net Profit | Labor % | Rev/Hr |")
            sections.append("|------|---------|-------|------|------------|---------|--------|")
            for d in pnl:
                rev = d["revenue_cents"] / 100
                labor = d["labor_cost_cents"] / 100
                cogs = d["cogs_cents"] / 100 if d.get("cogs_cents") else 0
                net = d["net_profit_cents"] / 100 if d.get("net_profit_cents") else 0
                labor_pct = f"{d['labor_pct']:.1f}%" if d.get("labor_pct") is not None else "N/A"
                rev_hr = f"${d['revenue_per_labor_hour'] / 100:.0f}" if d.get("revenue_per_labor_hour") else "N/A"
                no_labor_flag = " *" if d["labor_cost_cents"] == 0 else ""
                try:
                    day_name = date.fromisoformat(d["date"]).strftime("%a %d/%m")
                except Exception:
                    day_name = d["date"]
                sections.append(
                    f"| {day_name} | ${rev:,.0f} | ${labor:,.0f}{no_labor_flag} | "
                    f"${cogs:,.0f} | ${net:,.0f} | {labor_pct} | {rev_hr} |"
                )
            total_rev = sum(d["revenue_cents"] for d in pnl)
            total_labor = sum(d["labor_cost_cents"] for d in pnl)
            total_cogs = sum(d.get("cogs_cents", 0) or 0 for d in pnl)
            total_net = sum(d.get("net_profit_cents", 0) or 0 for d in pnl)
            days_with_labor = sum(1 for d in pnl if d["labor_cost_cents"] > 0)
            avg_labor_pct = (
                sum(d["labor_pct"] for d in pnl if d.get("labor_pct") and d["labor_cost_cents"] > 0)
                / days_with_labor
            ) if days_with_labor > 0 else 0
            sections.append(f"\n**Totals:** Rev ${total_rev / 100:,.0f} | Labor ${total_labor / 100:,.0f} | "
                           f"COGS ${total_cogs / 100:,.0f} | Net ${total_net / 100:,.0f}")
            sections.append(f"**Avg labor % (days with data): {avg_labor_pct:.1f}%** "
                           f"(industry benchmark: 25-35% for specialty cafes)")
            if days_with_labor < len(pnl):
                sections.append(f"* = {len(pnl) - days_with_labor} day(s) missing Deputy labor data")
        else:
            # Revenue + Labor only (no estimated COGS)
            sections.append(f"\n## Revenue & Labor ({len(pnl)} days)")
            sections.append("**COGS not available** — upload supplier invoices to enable full P&L.")
            sections.append("Showing revenue and labor only (real data).")
            sections.append("")
            sections.append("| Date | Revenue | Orders | Labor | Labor % | Rev/Hr |")
            sections.append("|------|---------|--------|-------|---------|--------|")
            for d in pnl:
                rev = d["revenue_cents"] / 100
                labor = d["labor_cost_cents"] / 100
                orders = d.get("order_count") or "—"
                labor_pct = f"{d['labor_pct']:.1f}%" if d.get("labor_pct") is not None else "N/A"
                rev_hr = f"${d['revenue_per_labor_hour'] / 100:.0f}" if d.get("revenue_per_labor_hour") else "N/A"
                no_labor_flag = " *" if d["labor_cost_cents"] == 0 else ""
                try:
                    day_name = date.fromisoformat(d["date"]).strftime("%a %d/%m")
                except Exception:
                    day_name = d["date"]
                sections.append(
                    f"| {day_name} | ${rev:,.0f} | {orders} | "
                    f"${labor:,.0f}{no_labor_flag} | {labor_pct} | {rev_hr} |"
                )
            total_rev = sum(d["revenue_cents"] for d in pnl)
            total_labor = sum(d["labor_cost_cents"] for d in pnl)
            days_with_labor = sum(1 for d in pnl if d["labor_cost_cents"] > 0)
            avg_labor_pct = (
                sum(d["labor_pct"] for d in pnl if d.get("labor_pct") and d["labor_cost_cents"] > 0)
                / days_with_labor
            ) if days_with_labor > 0 else 0
            sections.append(f"\n**Totals:** Rev ${total_rev / 100:,.0f} | Labor ${total_labor / 100:,.0f}")
            sections.append(f"**Avg labor %: {avg_labor_pct:.1f}%** (benchmark: 25-35%)")
            if days_with_labor < len(pnl):
                sections.append(f"* = {len(pnl) - days_with_labor} day(s) missing Deputy labor data")

    # --- Daily Efficiency (latest available day) ---
    if "daily_efficiency" in context:
        eff = context["daily_efficiency"]
        s = eff.get("summary", {})
        vs = eff.get("variance_summary", {})
        sections.append("\n## Daily Efficiency Snapshot")
        sections.append(
            f"- Date: {eff.get('date')} | Intervals analyzed: {s.get('intervals_analyzed', 0)} | "
            f"Revenue: ${((s.get('total_revenue_cents') or 0) / 100):,.0f}"
        )
        sections.append(
            f"- Labor: ${((s.get('deputy_labor_cost_cents') or 0) / 100):,.0f} | "
            f"Labor %: {s.get('labor_pct') if s.get('labor_pct') is not None else 'N/A'} | "
            f"Rev/labor-hour: "
            f"{('$' + str(round((s.get('revenue_per_labor_hour_cents') or 0) / 100))) if s.get('revenue_per_labor_hour_cents') else 'N/A'}"
        )
        sections.append(
            f"- Variance intervals: understaffed={vs.get('understaffed_intervals', 0)}, "
            f"overstaffed={vs.get('overstaffed_intervals', 0)}, no_staff={vs.get('no_staff_intervals', 0)}"
        )
        top_mismatch = (eff.get("peaks") or {}).get("mismatch") or []
        if top_mismatch:
            sections.append("- Top mismatch intervals:")
            for row in top_mismatch[:5]:
                rev = int(row.get("revenue_cents") or 0)
                sections.append(
                    f"  - {row.get('interval_start')}: {row.get('status')} "
                    f"(staff_on={row.get('staff_on')}, expected={row.get('expected_staff')}, revenue=${rev / 100:,.0f})"
                )

    # --- Live Next Actions ---
    if "next_actions_live" in context:
        payload = context["next_actions_live"]
        actions = payload.get("actions") or []
        if actions:
            sections.append("\n## Recommended Next Actions (live)")
            for a in actions[:5]:
                expected = int(a.get("expected_weekly_profit_uplift_cents") or 0)
                proven = a.get("proven_weekly_impact_cents")
                proven_text = f", proven {int(proven) / 100:+,.0f}/wk" if proven is not None else ""
                conf = a.get("confidence")
                conf_text = f", conf {round(float(conf) * 100)}%" if conf is not None else ""
                sections.append(
                    f"- {a.get('title', a.get('action_type'))}: est {expected / 100:+,.0f}/wk{proven_text}{conf_text}"
                )

    # --- Recently persisted recommendations ---
    if "recent_recommendations" in context:
        sections.append("\n## Recent Recommendation Memory")
        for r in context["recent_recommendations"][:8]:
            expected = int(r.get("expected_weekly_profit_uplift_cents") or 0)
            realized = r.get("realized_weekly_delta_cents")
            realized_text = f", realized {int(realized) / 100:+,.0f}/wk" if realized is not None else ""
            adopted = "adopted" if r.get("adopted") else "not adopted"
            sections.append(
                f"- {r.get('title') or r.get('action_type')}: est {expected / 100:+,.0f}/wk{realized_text}, {adopted}"
            )

    # --- 2-4 week optimization templates ---
    if "optimized_shift_range" in context:
        opt = context["optimized_shift_range"]
        summary = opt.get("summary", {})
        sections.append("\n## 28-Day Shift Optimization")
        sections.append(
            f"- Days with predictions: {summary.get('days_with_predictions', 0)} / {opt.get('days', 0)}"
        )
        templates = opt.get("weekly_templates") or []
        for t in templates:
            if t.get("status") != "ok":
                continue
            shift_count = len(t.get("template_shifts") or [])
            delta = t.get("avg_estimated_labor_delta_cents")
            delta_text = f"{int(delta) / 100:+,.0f}/day" if delta is not None else "N/A"
            sections.append(
                f"- {t.get('day_of_week')}: {shift_count} template shifts, avg labor delta {delta_text}"
            )

    # --- Item Margins ---
    if "item_margins" in context:
        margins = context["item_margins"]
        sections.append(f"\n## Item Margin Analysis (last 14 days, estimated COGS)")
        sections.append("| Item | Qty | Avg Price | COGS | Margin % | Total Profit |")
        sections.append("|------|-----|-----------|------|----------|-------------|")
        for m in margins[:20]:
            sections.append(
                f"| {m['item']} | {m['qty']} | ${m['avg_price_cents'] / 100:.2f} | "
                f"${m['cogs_cents'] / 100:.2f} | {m['margin_pct']}% | "
                f"${m['total_profit_cents'] / 100:,.0f} |"
            )
        total_profit = sum(m["total_profit_cents"] for m in margins)
        sections.append(f"\n**Total estimated product profit: ${total_profit / 100:,.0f}**")

    # --- Tomorrow's Weather ---
    if "tomorrow_weather" in context:
        w = context["tomorrow_weather"]
        sections.append(f"\n## Tomorrow's Weather Forecast")
        sections.append(f"- Temperature: {w.get('temp_c', '?')}°C")
        sections.append(f"- Conditions: {w.get('description', '?')}")
        rain_pct = round((w.get('rain_probability', 0)) * 100)
        sections.append(f"- Rain probability: {rain_pct}%")
        if rain_pct > 50:
            sections.append("- **High rain chance — historically reduces foot traffic**")

    # --- Trending Items ---
    if "trending_items" in context:
        sections.append("\n## Trending Items (this week vs last week)")
        growers = [t for t in context["trending_items"] if t["direction"] == "up"]
        decliners = [t for t in context["trending_items"] if t["direction"] == "down"]
        if growers:
            sections.append("**Growing:**")
            for t in growers[:8]:
                sections.append(f"- {t['item']}: {t['last_week']} → {t['this_week']} (+{t['change_pct']}%)")
        if decliners:
            sections.append("**Declining:**")
            for t in decliners[:8]:
                sections.append(f"- {t['item']}: {t['last_week']} → {t['this_week']} ({t['change_pct']}%)")

    # --- Operational Benchmarks ---
    if "benchmarks" in context:
        bm = context["benchmarks"]
        if bm.get("peak_hours"):
            sections.append("\n## Peak Hours (busiest time slots by day)")
            for ph in bm["peak_hours"][:10]:
                sections.append(f"- {ph['day']} {ph['hour']}: avg {ph['avg_items']} items, {ph['avg_workload']} WU")
        if bm.get("daily_ranking"):
            sections.append("\n## Daily Volume Ranking")
            for dr in bm["daily_ranking"]:
                sections.append(f"- {dr['day']}: avg {dr['avg_items']} items/day")

    # --- Items summary ---
    if "items_summary" in context:
        sections.append("\n## Daily Items & Workload (last 14 days)")
        for d in context["items_summary"]:
            sections.append(f"- {d['date']}: {d['items']} items, {d['workload']} WU")

    # --- Top items ---
    if "top_items" in context:
        sections.append("\n## Top Items (last 14 days)")
        for item in context["top_items"]:
            sections.append(f"- {item['item']}: {item['count']} sold (avg {item['avg_workload']} WU)")

    # --- Modifier stats ---
    if "modifier_stats" in context:
        ms = context["modifier_stats"]
        sections.append(f"\n## Modifier & Size Detail Report (last 7 days)")
        sections.append(f"Total items: {ms['total_items']} | Total drinks: {ms['total_drinks']}")

        sections.append("\n### Cup Size Breakdown")
        for s in ms["size_breakdown"]:
            sections.append(f"- {s['size']}: {s['count']} ({s['pct']}%)")

        if ms.get("drink_types"):
            sections.append("\n### Drink Types")
            for d in ms["drink_types"]:
                sections.append(f"- **{d['name']}**: {d['count']} ({d['pct']}%)")

        if ms.get("milk_breakdown"):
            sections.append("\n### Milk Breakdown")
            total_milks = sum(m["count"] for m in ms["milk_breakdown"])
            sections.append(f"Total alt/specified milk drinks: {total_milks}")
            for m in ms["milk_breakdown"]:
                pct_of_milks = round(m["count"] / max(total_milks, 1) * 100, 1)
                sections.append(f"- **{m['name']}**: {m['count']} ({m['pct']}% of all drinks, {pct_of_milks}% of milk selections)")

        if ms.get("syrup_breakdown"):
            sections.append("\n### Syrup Breakdown")
            for s in ms["syrup_breakdown"]:
                sections.append(f"- **{s['name']}**: {s['count']} ({s['pct']}%)")

        if ms.get("extras"):
            sections.append("\n### Extras & Add-ons")
            for e in ms["extras"]:
                sections.append(f"- **{e['name']}**: {e['count']} ({e['pct']}%)")

        if ms.get("daily"):
            sections.append("\n### Daily Modifier Detail")
            for d, mods in list(ms["daily"].items())[:7]:
                mod_str = ", ".join(f"{name}={count}" for name, count in mods[:10])
                sections.append(f"- {d}: {mod_str}")

    # --- Item variations (detailed from raw Square data) ---
    if "item_variations" in context:
        items = context["item_variations"][:40]
        total_sold = sum(i["total"] for i in items)
        sections.append(f"\n## Detailed Item Breakdown (Square POS, recent window — {total_sold} total items)")
        for item in items:
            sections.append(f"\n### {item['item']} — {item['total']} sold")
            if item.get("variations"):
                sections.append("  Variations:")
                for name, count in item["variations"]:
                    sections.append(f"  - {name}: {count}")
            if item.get("modifiers"):
                top_mods = item["modifiers"][:10]
                sections.append("  Top modifiers:")
                for name, count in top_mods:
                    sections.append(f"  - {name}: {count}")
            if item.get("daily"):
                # Show weekly aggregates instead of every day
                daily = dict(item["daily"])
                sections.append(f"  Daily range: {item['daily'][0][0]} to {item['daily'][-1][0]} ({len(daily)} days)")

    # --- Item counts by day ---
    if "item_counts_by_day" in context:
        sections.append("\n## Item Sales by Day (last 7 days)")
        # Group by date
        by_date = {}
        for row in context["item_counts_by_day"]:
            d = row["date"]
            if d not in by_date:
                by_date[d] = []
            by_date[d].append({"item": row["item"], "qty": row["qty"]})
        for d in sorted(by_date.keys(), reverse=True):
            items = by_date[d]
            total = sum(i["qty"] for i in items)
            sections.append(f"\n### {d} ({total} total items)")
            for item in items[:20]:
                sections.append(f"  - {item['item']}: {item['qty']}")

    # --- Upcoming events ---
    if "upcoming_events" in context:
        sections.append(f"\n## Upcoming Events (next 14 days)")
        for ev in context["upcoming_events"]:
            try:
                day_name = date.fromisoformat(ev["date"]).strftime("%A %d/%m")
            except:
                day_name = ev["date"]
            impact = ev["impact_multiplier"]
            impact_label = ""
            if impact > 1.0:
                impact_label = f" — expect **+{round((impact - 1) * 100)}%** volume"
            elif impact < 1.0:
                impact_label = f" — expect **{round((impact - 1) * 100)}%** volume"
            sections.append(f"- {day_name}: **{ev['name']}**{impact_label}")

    # --- Events Calendar (past + future) ---
    if "events_calendar" in context:
        sections.append(f"\n## Events & Closures Calendar")
        for ev in context["events_calendar"]:
            try:
                day_name = date.fromisoformat(ev["date"]).strftime("%A %d/%m")
            except Exception:
                day_name = ev["date"]
            impact_note = ""
            if ev.get("impact") and ev["impact"] != 1.0:
                if ev["impact"] < 1.0:
                    impact_note = f" — {round((1 - ev['impact']) * 100)}% volume reduction"
                else:
                    impact_note = f" — +{round((ev['impact'] - 1) * 100)}% volume"
            type_tag = f" [{ev['type']}]" if ev.get("type") else ""
            sections.append(f"- {day_name}: **{ev['name']}**{type_tag}{impact_note}")
        sections.append("Use this to flag anomalous days in historical data.")

    # --- Recent Documents ---
    if "recent_documents" in context:
        docs = context["recent_documents"]
        sections.append(f"\n## Recent Document Uploads")
        for doc in docs:
            summary = doc.get("extraction_summary") or "pending processing"
            sections.append(f"- {doc['filename']} ({doc.get('document_type', 'unknown')}): {summary}")

    # --- DOW pattern ---
    if "dow_pattern" in context:
        sections.append(f"\n## Day-of-Week Pattern (workload vs weekly average)")
        ranked = sorted(context["dow_pattern"].items(), key=lambda x: x[1], reverse=True)
        for day, factor in ranked:
            bar = "+" if factor > 1.0 else ("-" if factor < 1.0 else "=")
            pct = round((factor - 1) * 100)
            sections.append(f"- {day}: {factor}x ({bar}{abs(pct)}% vs avg)")

    # --- Hourly averages ---
    if "hourly_averages" in context:
        sections.append(f"\n## Hourly Patterns (average workload by hour)")
        for h in context["hourly_averages"]:
            sections.append(f"- {h['hour']}: {h['avg_workload']} WU avg, {h['avg_orders']} orders, {h['avg_items']} items")

    # --- Weekly review ---
    if "weekly_review" in context:
        wr = context["weekly_review"]
        sections.append(f"\n## Weekly Review ({wr.get('week_start')} to {wr.get('week_end')})")
        if wr.get("accuracy"):
            sections.append(f"- Accuracy: {wr['accuracy'].get('avg_accuracy', 'N/A')}%")
        if wr.get("adoption"):
            a = wr["adoption"]
            rate = a.get("adoption_rate")
            if rate is not None:
                sections.append(f"- Adoption: {round(rate * 100)}%")
        if wr.get("daily_details"):
            sections.append("- Daily:")
            for d in wr["daily_details"]:
                acc = f"{d['accuracy']}%" if d.get("accuracy") else "no data"
                sections.append(f"  {d.get('day_name', d['date'])}: {d['predicted_drinks']} predicted, {acc}")
        if wr.get("insights"):
            sections.append("- Insights:")
            for insight in wr["insights"]:
                sections.append(f"  - {insight}")

    # --- Workload timeline ---
    if "workload_timeline" in context:
        wt = context["workload_timeline"]
        sections.append(f"\n## Recent Workload ({len(wt)} intervals)")
        for entry in wt[:16]:
            sections.append(f"- {entry['time']}: {entry['workload_units']:.1f} WU, {entry['orders']} orders, {entry['items']} items")

    return "\n".join(sections)


# ============================================================
# Streaming Response
# ============================================================


async def stream_chat_response(
    site_id: str,
    site_name: str,
    messages: list[dict],
    document_ids: list[str] = None,
) -> AsyncGenerator[str, None]:
    if not settings.ANTHROPIC_API_KEY:
        yield 'data: {"error": "ANTHROPIC_API_KEY not configured"}\n\n'
        yield 'data: {"done": true}\n\n'
        return

    last_user_msg = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            last_user_msg = msg.get("content", "")
            break

    # --- Process uploaded documents ---
    extraction_results = []
    if document_ids:
        from app.extraction import extract_document, build_extraction_content_blocks

        for doc_id in document_ids:
            try:
                yield f'data: {json.dumps({"extraction_status": "processing", "document_id": doc_id})}\n\n'
                result = extract_document(doc_id, str(site_id))
                extraction_results.append({"document_id": doc_id, "result": result})

                # Send extraction event
                event_data = {
                    "extraction": {
                        "document_id": doc_id,
                        "document_type": result.get("document_type"),
                        "summary": result.get("summary"),
                        "items_count": len(result.get("items", [])),
                        "events_count": len(result.get("events", [])),
                        "is_cogs_document": result.get("is_cogs_document", False),
                    }
                }
                yield f"data: {json.dumps(event_data)}\n\n"
            except Exception as e:
                logger.error("Document extraction failed for %s: %s", doc_id, e)
                yield f'data: {json.dumps({"extraction_error": str(e), "document_id": doc_id})}\n\n'

    context = gather_chat_context(site_id, last_user_msg)

    # Add extraction results to context
    if extraction_results:
        context["extraction_results"] = extraction_results

    system_prompt = build_system_prompt(site_name, context)

    # Build API messages — include document content blocks for conversational response
    api_messages = list(messages)
    if extraction_results:
        extraction_text_parts = []
        for er in extraction_results:
            r = er["result"]
            extraction_text_parts.append(
                f"Document extracted: {r.get('summary', 'Unknown document')}. "
                f"Type: {r.get('document_type')}. "
                f"Items found: {len(r.get('items', []))}. "
                f"Events found: {len(r.get('events', []))}."
            )
        # Append extraction context to the last user message
        if api_messages and api_messages[-1].get("role") == "user":
            api_messages[-1] = {
                "role": "user",
                "content": api_messages[-1]["content"] + "\n\n[Document extraction results: "
                + " | ".join(extraction_text_parts) + "]",
            }

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    try:
        with client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=api_messages,
        ) as stream:
            for text in stream.text_stream:
                chunk = json.dumps({"content": text})
                yield f"data: {chunk}\n\n"

    except anthropic.APIError as e:
        logger.error("Claude API error: %s", e)
        error_chunk = json.dumps({"error": f"AI service error: {str(e)}"})
        yield f"data: {error_chunk}\n\n"

    yield 'data: {"done": true}\n\n'
