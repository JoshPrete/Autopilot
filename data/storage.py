"""
Clubhouse Autopilot v1.2 - Database Storage
Database operations for all schema tables (Spec Section 6.1)

Handles writing and reading from PostgreSQL using raw SQL via
psycopg2 through SQLAlchemy's engine. All operations are
site-scoped per the multi-site default principle.
"""

import json
import logging
import math
import re
import uuid as _uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from config.database import engine
from config.settings import settings
from security.crypto import (
    TokenEncryptionError,
    decrypt_secret,
    encrypt_secret,
    is_encrypted_secret,
    token_encryption_ready,
)

logger = logging.getLogger("autopilot.storage")


# ============================================================
# Sites
# ============================================================


def get_site_by_location_id(square_location_id: str) -> Optional[dict]:
    """Look up a site by its Square location ID."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "SELECT site_id, name, square_location_id, timezone "
                "FROM sites WHERE square_location_id = :loc_id AND active = TRUE"
            ),
            {"loc_id": square_location_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None


def get_site(site_id: str) -> Optional[dict]:
    """Look up a site by its UUID."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "SELECT site_id, name, square_location_id, timezone "
                "FROM sites WHERE site_id = :sid"
            ),
            {"sid": site_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None


def create_site(name: str, square_location_id: str, timezone: str = None) -> str:
    """Create a new site. Returns the site_id."""
    tz = timezone or settings.SITE_TIMEZONE
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "INSERT INTO sites (name, square_location_id, timezone) "
                "VALUES (:name, :loc_id, :tz) RETURNING site_id"
            ),
            {"name": name, "loc_id": square_location_id, "tz": tz},
        )
        site_id = str(result.scalar())
        conn.commit()
        logger.info("Created site '%s' with id %s", name, site_id)
        return site_id


# ============================================================
# Orders (Raw from Square)
# ============================================================


def store_orders_raw(site_id: str, parsed_orders: list[dict]) -> int:
    """
    Store raw parsed orders into orders_raw table.
    Skips duplicates via ON CONFLICT DO NOTHING.
    Returns count of newly inserted orders.
    """
    if not parsed_orders:
        return 0

    inserted = 0
    with engine.connect() as conn:
        for order in parsed_orders:
            result = conn.execute(
                _text(
                    "INSERT INTO orders_raw "
                    "(order_id, site_id, created_at, closed_at, total_money_cents, state, payload) "
                    "VALUES (:oid, :sid, :created, :closed, :total, :state, :payload) "
                    "ON CONFLICT (order_id) DO NOTHING"
                ),
                {
                    "oid": order["order_id"],
                    "sid": site_id,
                    "created": order["created_at"],
                    "closed": order["closed_at"],
                    "total": order["total_money_cents"],
                    "state": order["state"],
                    "payload": json.dumps(order["payload"]),
                },
            )
            inserted += result.rowcount

        conn.commit()

    logger.info(
        "Stored %d/%d orders (skipped %d duplicates)",
        inserted,
        len(parsed_orders),
        len(parsed_orders) - inserted,
    )
    return inserted


# ============================================================
# Order Items (with workload scores)
# ============================================================


def store_order_items(site_id: str, processed_orders: list[dict]) -> int:
    """
    Store processed order items with workload scores.
    Each line item (expanded by quantity) gets its own row.
    """
    if not processed_orders:
        return 0

    inserted = 0
    with engine.connect() as conn:
        for order in processed_orders:
            for li in order.get("line_items", []):
                workload = li.get("workload", {})
                conn.execute(
                    _text(
                        "INSERT INTO order_items "
                        "(order_id, site_id, catalog_item_id, item_name, quantity, "
                        "position_in_order, workload_units, modifiers, created_at, "
                        "prep_time_seconds) "
                        "VALUES (:oid, :sid, :cat_id, :name, :qty, :pos, :wu, "
                        ":mods, :created, :prep)"
                    ),
                    {
                        "oid": order["order_id"],
                        "sid": site_id,
                        "cat_id": li.get("catalog_item_id"),
                        "name": li.get("item_name"),
                        "qty": 1,  # Already expanded by quantity
                        "pos": li.get("effective_position"),
                        "wu": workload.get("workload_units", 0),
                        "mods": json.dumps(workload.get("applied_modifiers", [])),
                        "created": order.get("closed_at"),
                        "prep": workload.get("prep_time_seconds"),
                    },
                )
                inserted += 1

        conn.commit()

    logger.info("Stored %d order items", inserted)
    return inserted


# ============================================================
# Workload Timeline (15-min aggregations)
# ============================================================


def store_timeline(site_id: str, timeline: list[dict]) -> int:
    """
    Store 15-minute workload timeline entries.
    Uses ON CONFLICT to update existing intervals (re-processing safe).
    """
    if not timeline:
        return 0

    stored = 0
    with engine.connect() as conn:
        for entry in timeline:
            conn.execute(
                _text(
                    "INSERT INTO workload_timeline "
                    "(site_id, interval_start, workload_units, orders_count, "
                    "items_count, avg_prep_seconds) "
                    "VALUES (:sid, :start, :wu, :oc, :ic, :avg_prep) "
                    "ON CONFLICT (site_id, interval_start) DO UPDATE SET "
                    "workload_units = EXCLUDED.workload_units, "
                    "orders_count = EXCLUDED.orders_count, "
                    "items_count = EXCLUDED.items_count, "
                    "avg_prep_seconds = EXCLUDED.avg_prep_seconds, "
                    "calculated_at = NOW()"
                ),
                {
                    "sid": site_id,
                    "start": entry["interval_start"],
                    "wu": entry["workload_units"],
                    "oc": entry["orders_count"],
                    "ic": entry["items_count"],
                    "avg_prep": entry["avg_prep_seconds"],
                },
            )
            stored += 1

        conn.commit()

    logger.info("Stored %d timeline intervals", stored)
    return stored


# ============================================================
# Historical patterns (for prediction engine)
# ============================================================


def get_recent_pattern(
    site_id: str,
    day_of_week: int,
    hour: int,
    weeks_back: int = None,
) -> list[float]:
    """
    Get recent workload values for same weekday + hour.

    Spec Section 5.5 Layer 1:
        Last 6-8 weeks, same day_of_week, same time window.

    Args:
        site_id: Site UUID
        day_of_week: 0=Monday ... 6=Sunday (Python convention)
        hour: Hour of day (0-23)
        weeks_back: How many weeks to look back (default: from settings)

    Returns:
        List of workload_units values (one per matching interval)
    """
    if weeks_back is None:
        weeks_back = settings.RECENT_PATTERN_WEEKS

    cutoff = datetime.utcnow() - timedelta(weeks=weeks_back)

    with engine.connect() as conn:
        has_flags = bool(
            conn.execute(
                _text("SELECT to_regclass('public.data_quality_flags') IS NOT NULL")
            ).scalar()
        )
        where_clause = (
            "WHERE site_id = :sid "
            "AND EXTRACT(DOW FROM interval_start) = :dow "
            "AND EXTRACT(HOUR FROM interval_start) = :hr "
            "AND interval_start >= :cutoff "
        )
        if has_flags:
            where_clause += (
                "AND DATE(interval_start) NOT IN ("
                "  SELECT flag_date FROM data_quality_flags "
                "  WHERE site_id = :sid AND active = TRUE "
                "  AND flag_type IN ('partial_ingest', 'manual_exclude_forecast')"
                ") "
            )
        result = conn.execute(
            _text(
                "SELECT workload_units FROM workload_timeline "
                f"{where_clause}"
                "ORDER BY interval_start"
            ),
            {"sid": site_id, "dow": day_of_week, "hr": hour, "cutoff": cutoff},
        )
        values = [float(row[0]) for row in result]

    logger.debug("Recent pattern: dow=%d hour=%d -> %d values", day_of_week, hour, len(values))
    return values


def get_yoy_pattern(
    site_id: str,
    target_date: date,
    years_back: int = None,
) -> list[float]:
    """
    Get year-over-year workload values for same week number.

    Spec Section 5.5 Layer 2:
        Same date range from previous year(s).

    Args:
        site_id: Site UUID
        target_date: The date we're predicting for
        years_back: How many years to look back (default: from settings)

    Returns:
        List of workload_units values from matching historical weeks
    """
    if years_back is None:
        years_back = settings.YOY_YEARS_BACK

    week_number = target_date.isocalendar()[1]
    target_year = target_date.year
    past_years = [target_year - i for i in range(1, years_back + 1)]

    if not past_years:
        return []

    with engine.connect() as conn:
        has_flags = bool(
            conn.execute(
                _text("SELECT to_regclass('public.data_quality_flags') IS NOT NULL")
            ).scalar()
        )
        where_clause = (
            "WHERE site_id = :sid "
            "AND EXTRACT(WEEK FROM interval_start) = :week "
            "AND EXTRACT(YEAR FROM interval_start) = ANY(:years) "
        )
        if has_flags:
            where_clause += (
                "AND DATE(interval_start) NOT IN ("
                "  SELECT flag_date FROM data_quality_flags "
                "  WHERE site_id = :sid AND active = TRUE "
                "  AND flag_type IN ('partial_ingest', 'manual_exclude_forecast')"
                ") "
            )
        result = conn.execute(
            _text(
                "SELECT workload_units FROM workload_timeline "
                f"{where_clause}"
                "ORDER BY interval_start"
            ),
            {"sid": site_id, "week": week_number, "years": past_years},
        )
        values = [float(row[0]) for row in result]

    logger.debug("YoY pattern: week=%d years=%s -> %d values", week_number, past_years, len(values))
    return values


def get_dow_pattern(site_id: str, weeks_back: int = 12) -> dict[str, float]:
    """
    Calculate day-of-week multipliers from recent data.

    Spec Section 5.5 Layer 4:
        Each weekday as % of weekly average.

    Returns:
        Dict mapping day name -> multiplier (e.g. {"Monday": 0.85, ...})
    """
    cutoff = datetime.utcnow() - timedelta(weeks=weeks_back)

    day_names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

    with engine.connect() as conn:
        has_flags = bool(
            conn.execute(
                _text("SELECT to_regclass('public.data_quality_flags') IS NOT NULL")
            ).scalar()
        )
        where_clause = "WHERE site_id = :sid " "AND interval_start >= :cutoff "
        if has_flags:
            where_clause += (
                "AND DATE(interval_start) NOT IN ("
                "  SELECT flag_date FROM data_quality_flags "
                "  WHERE site_id = :sid AND active = TRUE "
                "  AND flag_type IN ('partial_ingest', 'manual_exclude_forecast')"
                ") "
            )
        result = conn.execute(
            _text(
                "SELECT "
                "EXTRACT(DOW FROM interval_start) as day_of_week, "
                "AVG(workload_units) as avg_workload "
                "FROM workload_timeline "
                f"{where_clause}"
                "GROUP BY EXTRACT(DOW FROM interval_start)"
            ),
            {"sid": site_id, "cutoff": cutoff},
        )
        rows = {int(row[0]): float(row[1]) for row in result}

    if not rows:
        logger.info("No DoW data, returning defaults")
        from config.constants import DOW_PATTERN_DEFAULT

        return DOW_PATTERN_DEFAULT

    # Calculate weekly average across all days
    weekly_avg = sum(rows.values()) / len(rows) if rows else 1.0

    pattern = {}
    for dow_num, day_name in enumerate(day_names):
        if dow_num in rows and weekly_avg > 0:
            pattern[day_name] = round(rows[dow_num] / weekly_avg, 2)
        else:
            from config.constants import DOW_PATTERN_DEFAULT

            pattern[day_name] = DOW_PATTERN_DEFAULT.get(day_name, 1.0)

    logger.debug("DoW pattern: %s", pattern)
    return pattern


def check_special_events(site_id: str, target_date: date) -> float:
    """
    Check if special events affect a given date.

    Spec Section 5.5 Layer 3:
        Returns event multiplier (1.0 = no impact, 1.18 = +18%).
        If multiple events, multiplies them together.
    """
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "SELECT event_name, historical_impact "
                "FROM special_events "
                "WHERE site_id = :sid AND event_date = :d"
            ),
            {"sid": site_id, "d": target_date},
        )
        events = list(result.mappings())

    if not events:
        return 1.0

    combined = 1.0
    for event in events:
        impact = event["historical_impact"] or 1.0
        logger.info("Event '%s' on %s: %.2fx impact", event["event_name"], target_date, impact)
        combined *= impact

    return combined


# ============================================================
# Predictions
# ============================================================


def store_prediction(site_id: str, forecast_date: date, prediction: dict) -> str:
    """
    Store a prediction record with all pattern components.
    Returns the prediction_id.
    """
    forecast_data = {
        k: v
        for k, v in prediction.items()
        if k
        not in (
            "recent_avg",
            "yoy_avg",
            "dow_factor",
            "event_multiplier",
            "base_prediction",
            "confidence",
        )
    }

    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "INSERT INTO predictions "
                "(site_id, forecast_date, model_version, "
                "recent_baseline, yoy_baseline, dow_factor, event_factor, "
                "composite_baseline, forecast_data, confidence_score) "
                "VALUES (:sid, :fd, :mv, :rb, :yb, :df, :ef, :cb, :fdata, :cs) "
                "ON CONFLICT (site_id, forecast_date) DO UPDATE SET "
                "model_version = EXCLUDED.model_version, "
                "recent_baseline = EXCLUDED.recent_baseline, "
                "yoy_baseline = EXCLUDED.yoy_baseline, "
                "dow_factor = EXCLUDED.dow_factor, "
                "event_factor = EXCLUDED.event_factor, "
                "composite_baseline = EXCLUDED.composite_baseline, "
                "forecast_data = EXCLUDED.forecast_data, "
                "confidence_score = EXCLUDED.confidence_score, "
                "generated_at = NOW() "
                "RETURNING prediction_id"
            ),
            {
                "sid": site_id,
                "fd": forecast_date,
                "mv": "v1.2",
                "rb": prediction.get("recent_avg"),
                "yb": prediction.get("yoy_avg"),
                "df": prediction.get("dow_factor"),
                "ef": prediction.get("event_multiplier"),
                "cb": prediction.get("base_prediction"),
                "fdata": _json_dumps(forecast_data),
                "cs": prediction.get("confidence"),
            },
        )
        prediction_id = str(result.scalar())
        conn.commit()

    logger.info("Stored prediction %s for %s", prediction_id, forecast_date)
    return prediction_id


def get_prediction(site_id: str, forecast_date: date) -> Optional[dict]:
    """Retrieve a prediction for a given site and date."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "SELECT prediction_id, forecast_date, generated_at, "
                "recent_baseline, yoy_baseline, dow_factor, event_factor, "
                "composite_baseline, forecast_data, rush_windows, "
                "confidence_score, actual_accuracy "
                "FROM predictions "
                "WHERE site_id = :sid AND forecast_date = :fd"
            ),
            {"sid": site_id, "fd": forecast_date},
        )
        row = result.mappings().first()
        return dict(row) if row else None


def get_prediction_by_id(site_id: str, prediction_id: str) -> Optional[dict]:
    """Retrieve a prediction by prediction_id scoped to site."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "SELECT prediction_id, forecast_date, generated_at, "
                "recent_baseline, yoy_baseline, dow_factor, event_factor, "
                "composite_baseline, forecast_data, rush_windows, "
                "confidence_score, actual_accuracy "
                "FROM predictions "
                "WHERE site_id = :sid AND prediction_id = :pid"
            ),
            {"sid": site_id, "pid": prediction_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None


def store_prediction_plan_snapshot(site_id: str, prediction_id: str, plan_text: str) -> bool:
    """
    Persist an exact rendered tomorrow plan string onto the prediction row.

    This enables deterministic regeneration by prediction_id without depending
    on any live data lookups during re-render.
    """
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                """
                UPDATE predictions
                SET forecast_data = COALESCE(forecast_data, '{}'::jsonb)
                    || jsonb_build_object('plan_snapshot_text', :plan_text)
                WHERE site_id = :sid
                  AND prediction_id = :pid
                """
            ),
            {"sid": site_id, "pid": prediction_id, "plan_text": plan_text},
        )
        conn.commit()
        return bool(result.rowcount)


# ============================================================
# Recommendations
# ============================================================


def store_recommendation(
    prediction_id: str | None,
    site_id: str,
    action_type: str,
    action_timing: datetime,
    owner_role: str,
    action_details: dict,
) -> str:
    """Store a recommendation. Returns the rec_id."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "INSERT INTO recommendations "
                "(prediction_id, site_id, action_type, action_timing, "
                "owner_role, action_details) "
                "VALUES (:pid, :sid, :atype, :atiming, :role, :details) "
                "RETURNING rec_id"
            ),
            {
                "pid": prediction_id,
                "sid": site_id,
                "atype": action_type,
                "atiming": action_timing,
                "role": owner_role,
                "details": json.dumps(action_details),
            },
        )
        rec_id = str(result.scalar())
        conn.commit()

    return rec_id


def get_recommendation(site_id: str, rec_id: str) -> Optional[dict]:
    """Get a recommendation by rec_id scoped to site."""
    with engine.connect() as conn:
        row = (
            conn.execute(
                _text(
                    "SELECT rec_id, prediction_id, site_id, action_type, action_timing, "
                    "owner_role, action_details, adopted, outcome_data, created_at "
                    "FROM recommendations "
                    "WHERE site_id = :sid AND rec_id = :rid"
                ),
                {"sid": site_id, "rid": rec_id},
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None


def _profitability_window_metrics(site_id: str, start_date: date, end_date: date) -> dict:
    """Aggregate KPI metrics from daily_profitability for a date window."""
    with engine.connect() as conn:
        row = (
            conn.execute(
                _text(
                    """
                SELECT
                    COUNT(*) AS days_count,
                    AVG(labor_pct) AS avg_labor_pct,
                    AVG(revenue_per_labor_hour) AS avg_rev_per_labor_hour,
                    AVG(net_profit_cents) AS avg_net_profit_cents,
                    SUM(net_profit_cents) AS total_net_profit_cents
                FROM daily_profitability
                WHERE site_id = :sid
                  AND profit_date BETWEEN :s AND :e
                """
                ),
                {"sid": site_id, "s": start_date, "e": end_date},
            )
            .mappings()
            .first()
        )

    return {
        "days_count": int((row or {}).get("days_count") or 0),
        "avg_labor_pct": (
            float(row["avg_labor_pct"]) if row and row.get("avg_labor_pct") is not None else None
        ),
        "avg_rev_per_labor_hour": (
            float(row["avg_rev_per_labor_hour"])
            if row and row.get("avg_rev_per_labor_hour") is not None
            else None
        ),
        "avg_net_profit_cents": (
            float(row["avg_net_profit_cents"])
            if row and row.get("avg_net_profit_cents") is not None
            else None
        ),
        "total_net_profit_cents": int(row["total_net_profit_cents"] or 0) if row else 0,
    }


def compute_recommendation_realized_impact(
    site_id: str,
    rec_id: str,
    window_days: int = 7,
) -> Optional[dict]:
    """
    Compare KPI change 7 days before vs after adoption date and persist outcome_data.
    """
    with engine.connect() as conn:
        rec = (
            conn.execute(
                _text(
                    """
                SELECT rec_id, action_type, action_timing, outcome_data
                FROM recommendations
                WHERE site_id = :sid AND rec_id = :rid
                """
                ),
                {"sid": site_id, "rid": rec_id},
            )
            .mappings()
            .first()
        )
        if not rec:
            return None

        adoption = (
            conn.execute(
                _text(
                    """
                SELECT log_date
                FROM adoption_logs
                WHERE site_id = :sid
                  AND rec_id = :rid
                  AND adopted = TRUE
                ORDER BY log_date DESC
                LIMIT 1
                """
                ),
                {"sid": site_id, "rid": rec_id},
            )
            .mappings()
            .first()
        )
        if not adoption:
            return None

    anchor = adoption["log_date"]
    before_start = anchor - timedelta(days=window_days)
    before_end = anchor - timedelta(days=1)
    after_start = anchor + timedelta(days=1)
    after_end = anchor + timedelta(days=window_days)

    before = _profitability_window_metrics(site_id, before_start, before_end)
    after = _profitability_window_metrics(site_id, after_start, after_end)

    labor_pct_delta_pp = None
    if before["avg_labor_pct"] is not None and after["avg_labor_pct"] is not None:
        labor_pct_delta_pp = round(after["avg_labor_pct"] - before["avg_labor_pct"], 2)

    rev_per_labor_hour_delta_pct = None
    if before["avg_rev_per_labor_hour"] and after["avg_rev_per_labor_hour"]:
        if before["avg_rev_per_labor_hour"] > 0:
            rev_per_labor_hour_delta_pct = round(
                (
                    (after["avg_rev_per_labor_hour"] - before["avg_rev_per_labor_hour"])
                    / before["avg_rev_per_labor_hour"]
                )
                * 100,
                2,
            )

    avg_daily_net_profit_delta_cents = None
    if before["avg_net_profit_cents"] is not None and after["avg_net_profit_cents"] is not None:
        avg_daily_net_profit_delta_cents = round(
            after["avg_net_profit_cents"] - before["avg_net_profit_cents"]
        )

    weekly_net_profit_delta_cents = (
        after["total_net_profit_cents"] - before["total_net_profit_cents"]
    )

    realized = {
        "anchor_date": anchor.isoformat(),
        "window_days": window_days,
        "before_days": before["days_count"],
        "after_days": after["days_count"],
        "labor_pct_delta_pp": labor_pct_delta_pp,
        "rev_per_labor_hour_delta_pct": rev_per_labor_hour_delta_pct,
        "avg_daily_net_profit_delta_cents": avg_daily_net_profit_delta_cents,
        "weekly_net_profit_delta_cents": weekly_net_profit_delta_cents,
        "computed_at": datetime.utcnow().isoformat(),
    }

    with engine.connect() as conn:
        current_outcome = conn.execute(
            _text(
                "SELECT outcome_data FROM recommendations WHERE site_id = :sid AND rec_id = :rid"
            ),
            {"sid": site_id, "rid": rec_id},
        ).scalar()
        if isinstance(current_outcome, str):
            try:
                current_outcome = json.loads(current_outcome)
            except json.JSONDecodeError:
                current_outcome = {}
        if not isinstance(current_outcome, dict):
            current_outcome = {}
        current_outcome["realized"] = realized

        conn.execute(
            _text(
                """
                UPDATE recommendations
                SET outcome_data = :outcome,
                    adopted = TRUE
                WHERE site_id = :sid AND rec_id = :rid
                """
            ),
            {"sid": site_id, "rid": rec_id, "outcome": _json_dumps(current_outcome)},
        )
        conn.commit()

    return realized


def backfill_realized_impacts(
    site_id: str,
    lookback_days: int = 120,
    window_days: int = 7,
    limit: int = 50,
) -> dict:
    """Compute/persist realized impact for adopted recommendations missing it."""
    with engine.connect() as conn:
        rows = (
            conn.execute(
                _text(
                    """
                SELECT
                    r.rec_id,
                    MAX(r.created_at) AS latest_created_at
                FROM recommendations r
                JOIN adoption_logs al ON al.rec_id = r.rec_id
                WHERE r.site_id = :sid
                  AND al.adopted = TRUE
                  AND al.log_date >= (CURRENT_DATE - (:days * INTERVAL '1 day'))
                  AND (
                    r.outcome_data IS NULL
                    OR (r.outcome_data->'realized') IS NULL
                  )
                GROUP BY r.rec_id
                ORDER BY latest_created_at DESC
                LIMIT :lim
                """
                ),
                {"sid": site_id, "days": lookback_days, "lim": limit},
            )
            .mappings()
            .all()
        )

    updated = 0
    for row in rows:
        if compute_recommendation_realized_impact(site_id, row["rec_id"], window_days=window_days):
            updated += 1

    return {"candidates": len(rows), "updated": updated}


def recommendation_exists_for_action_key(
    site_id: str,
    action_type: str,
    action_key: str,
    target_date: date,
) -> bool:
    """Idempotency guard: has this action key already been stored for date?"""
    with engine.connect() as conn:
        exists = conn.execute(
            _text(
                """
                SELECT 1
                FROM recommendations
                WHERE site_id = :sid
                  AND action_type = :atype
                  AND DATE(action_timing) = :d
                  AND action_details->>'action_key' = :akey
                LIMIT 1
                """
            ),
            {"sid": site_id, "atype": action_type, "d": target_date, "akey": action_key},
        ).first()
        return exists is not None


def get_action_type_outcome_summary(site_id: str, action_type: str, days: int = 90) -> dict:
    """Summarize adoption outcomes for a recommendation action_type."""
    with engine.connect() as conn:
        row = (
            conn.execute(
                _text(
                    """
                SELECT
                    COUNT(*) FILTER (WHERE al.adopted = TRUE) AS adopted_count,
                    COUNT(*) AS total_count,
                    COUNT(DISTINCT al.rec_id) FILTER (
                        WHERE r.outcome_data->'realized' IS NOT NULL
                    ) AS realized_count,
                    AVG(al.helpfulness_rating) AS helpfulness_avg,
                    AVG(
                        NULLIF(r.outcome_data->'realized'->>'weekly_net_profit_delta_cents', '')::numeric
                    ) AS avg_realized_weekly_profit_delta_cents,
                    AVG(
                        NULLIF(r.outcome_data->'realized'->>'avg_daily_net_profit_delta_cents', '')::numeric
                    ) AS avg_realized_daily_net_profit_delta_cents
                FROM adoption_logs al
                JOIN recommendations r ON r.rec_id = al.rec_id
                WHERE r.site_id = :sid
                  AND r.action_type = :atype
                  AND al.log_date >= (CURRENT_DATE - (:days * INTERVAL '1 day'))
                """
                ),
                {"sid": site_id, "atype": action_type, "days": days},
            )
            .mappings()
            .first()
        )

    adopted = int((row or {}).get("adopted_count") or 0)
    total = int((row or {}).get("total_count") or 0)
    realized_count = int((row or {}).get("realized_count") or 0)
    helpfulness = (row or {}).get("helpfulness_avg")
    avg_realized_weekly = (row or {}).get("avg_realized_weekly_profit_delta_cents")
    avg_realized_daily = (row or {}).get("avg_realized_daily_net_profit_delta_cents")
    return {
        "adopted_count": adopted,
        "total_count": total,
        "realized_count": realized_count,
        "adoption_rate": round(adopted / total, 3) if total > 0 else None,
        "helpfulness_avg": round(float(helpfulness), 2) if helpfulness is not None else None,
        "avg_realized_weekly_profit_delta_cents": (
            round(float(avg_realized_weekly)) if avg_realized_weekly is not None else None
        ),
        "avg_realized_daily_net_profit_delta_cents": (
            round(float(avg_realized_daily)) if avg_realized_daily is not None else None
        ),
    }


# ============================================================
# Adoption Logs
# ============================================================


def store_adoption_log(
    site_id: str,
    log_date: date,
    rec_id: str,
    manager_name: str,
    adopted: bool,
    rush_timing_rating: int = None,
    helpfulness_rating: int = None,
    notes: str = None,
) -> str:
    """Store an adoption feedback log. Returns the log_id."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "INSERT INTO adoption_logs "
                "(site_id, log_date, rec_id, manager_name, adopted, "
                "rush_timing_rating, helpfulness_rating, notes) "
                "VALUES (:sid, :ld, :rid, :mn, :adopted, :rtr, :hr, :notes) "
                "RETURNING log_id"
            ),
            {
                "sid": site_id,
                "ld": log_date,
                "rid": rec_id,
                "mn": manager_name,
                "adopted": adopted,
                "rtr": rush_timing_rating,
                "hr": helpfulness_rating,
                "notes": notes,
            },
        )
        log_id = str(result.scalar())
        conn.commit()

    return log_id


# ============================================================
# Menu Items & Modifiers
# ============================================================


def upsert_menu_item(
    site_id: str,
    catalog_item_id: str,
    item_name: str,
    base_workload_score: float,
    category: str = None,
    avg_prep_seconds: int = None,
) -> None:
    """Insert or update a menu item's workload mapping."""
    with engine.connect() as conn:
        conn.execute(
            _text(
                "INSERT INTO menu_items "
                "(site_id, catalog_item_id, item_name, category, "
                "base_workload_score, avg_prep_seconds) "
                "VALUES (:sid, :cat_id, :name, :cat, :score, :prep) "
                "ON CONFLICT (site_id, catalog_item_id) DO UPDATE SET "
                "item_name = EXCLUDED.item_name, "
                "category = EXCLUDED.category, "
                "base_workload_score = EXCLUDED.base_workload_score, "
                "avg_prep_seconds = EXCLUDED.avg_prep_seconds"
            ),
            {
                "sid": site_id,
                "cat_id": catalog_item_id,
                "name": item_name,
                "cat": category,
                "score": base_workload_score,
                "prep": avg_prep_seconds,
            },
        )
        conn.commit()


def upsert_modifier(
    site_id: str,
    catalog_modifier_id: str,
    modifier_name: str,
    workload_add: float,
) -> None:
    """Insert or update a modifier's workload adjustment."""
    with engine.connect() as conn:
        conn.execute(
            _text(
                "INSERT INTO modifiers "
                "(site_id, catalog_modifier_id, modifier_name, workload_add) "
                "VALUES (:sid, :mod_id, :name, :add) "
                "ON CONFLICT (site_id, catalog_modifier_id) DO UPDATE SET "
                "modifier_name = EXCLUDED.modifier_name, "
                "workload_add = EXCLUDED.workload_add"
            ),
            {
                "sid": site_id,
                "mod_id": catalog_modifier_id,
                "name": modifier_name,
                "add": workload_add,
            },
        )
        conn.commit()


# ============================================================
# Contacts
# ============================================================


def get_contacts_by_role(site_id: str, role_label: str) -> list[dict]:
    """Get active contacts for a site filtered by role (P1/P2/P3/MANAGER)."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "SELECT contact_id, full_name, phone_e164, role_label "
                "FROM contacts "
                "WHERE site_id = :sid AND role_label = :role AND is_active = TRUE"
            ),
            {"sid": site_id, "role": role_label},
        )
        return [dict(row) for row in result.mappings()]


def get_contact_by_phone(phone_e164: str) -> Optional[dict]:
    """Look up an active contact by E.164 phone number (across all sites)."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "SELECT contact_id, site_id, full_name, phone_e164, role_label, pin_hash "
                "FROM contacts "
                "WHERE phone_e164 = :phone AND is_active = TRUE "
                "LIMIT 1"
            ),
            {"phone": phone_e164},
        )
        row = result.mappings().first()
        return dict(row) if row else None


def update_contact_pin(contact_id: str, pin_hash: str) -> None:
    """Set or update the pin_hash for a contact."""
    with engine.connect() as conn:
        conn.execute(
            _text("UPDATE contacts SET pin_hash = :hash WHERE contact_id = :cid"),
            {"hash": pin_hash, "cid": contact_id},
        )
        conn.commit()


# ============================================================
# Manual Signals (fallback toggles)
# ============================================================


def store_manual_signal(site_id: str, signal_type: str, value: str = None) -> None:
    """Store a manual signal (BAR2_OPEN, DELIVERY_STACKING, MILK_QUEUE_HIGH)."""
    with engine.connect() as conn:
        conn.execute(
            _text(
                "INSERT INTO manual_signals (site_id, signal_type, value) "
                "VALUES (:sid, :stype, :val)"
            ),
            {"sid": site_id, "stype": signal_type, "val": value},
        )
        conn.commit()


# ============================================================
# Weekly Reviews
# ============================================================


def get_weekly_stats(site_id: str, week_start: date, week_end: date) -> dict:
    """
    Compute weekly statistics for the review report.
    Aggregates prediction accuracy and adoption rates.
    """
    with engine.connect() as conn:
        # Prediction accuracy
        acc_result = conn.execute(
            _text(
                "SELECT AVG(actual_accuracy), COUNT(*) "
                "FROM predictions "
                "WHERE site_id = :sid "
                "AND forecast_date BETWEEN :ws AND :we "
                "AND actual_accuracy IS NOT NULL"
            ),
            {"sid": site_id, "ws": week_start, "we": week_end},
        )
        acc_row = acc_result.first()
        avg_accuracy = float(acc_row[0]) if acc_row and acc_row[0] else None
        prediction_count = int(acc_row[1]) if acc_row else 0

        # Adoption rate
        adopt_result = conn.execute(
            _text(
                "SELECT "
                "COUNT(*) FILTER (WHERE adopted = TRUE) as adopted_count, "
                "COUNT(*) as total_count "
                "FROM adoption_logs "
                "WHERE site_id = :sid "
                "AND log_date BETWEEN :ws AND :we"
            ),
            {"sid": site_id, "ws": week_start, "we": week_end},
        )
        adopt_row = adopt_result.first()
        adopted = int(adopt_row[0]) if adopt_row else 0
        total = int(adopt_row[1]) if adopt_row else 0
        adoption_rate = adopted / total if total > 0 else None

    return {
        "avg_prediction_accuracy": round(avg_accuracy, 2) if avg_accuracy else None,
        "prediction_days": prediction_count,
        "adoption_rate": round(adoption_rate, 2) if adoption_rate else None,
        "recommendations_total": total,
        "recommendations_adopted": adopted,
    }


# ============================================================
# Daily Sales History (rolling storage)
# ============================================================


def store_daily_sales(site_id: str, sale_date: date, summary: dict, source: str = "api") -> None:
    """
    Store a daily sales summary row into daily_sales_history.

    Called by the daily pipeline after ingestion to maintain rolling
    continuity with the CSV-imported historical data.

    Args:
        site_id: Site UUID
        sale_date: The business date
        summary: Pipeline summary dict with keys like orders_count,
                 items_count, total_revenue_cents, etc.
        source: 'api' for daily pipeline, 'csv' for CSV imports
    """
    total_revenue = summary.get("total_revenue_cents", 0)
    items_count = summary.get("items_count", 0)

    with engine.connect() as conn:
        conn.execute(
            _text(
                """
                INSERT INTO daily_sales_history
                    (site_id, sale_date, gross_sales_cents, net_sales_cents,
                     product_sales_cents, total_collected_cents,
                     orders_estimated, items_estimated, source)
                VALUES
                    (:sid, :sale_date, :gross, :net, :product, :total,
                     :orders_est, :items_est, :source)
                ON CONFLICT (site_id, sale_date) DO UPDATE SET
                    gross_sales_cents = EXCLUDED.gross_sales_cents,
                    net_sales_cents = EXCLUDED.net_sales_cents,
                    product_sales_cents = EXCLUDED.product_sales_cents,
                    total_collected_cents = EXCLUDED.total_collected_cents,
                    orders_estimated = EXCLUDED.orders_estimated,
                    items_estimated = EXCLUDED.items_estimated,
                    source = EXCLUDED.source,
                    imported_at = NOW()
            """
            ),
            {
                "sid": site_id,
                "sale_date": sale_date,
                "gross": total_revenue,
                "net": total_revenue,  # Approximate; API doesn't split net/gross easily
                "product": total_revenue,
                "total": total_revenue,
                "orders_est": summary.get("orders_count", 0),
                "items_est": items_count,
                "source": source,
            },
        )
        conn.commit()

    logger.info(
        "Stored daily sales for %s: $%.2f, %d items (source=%s)",
        sale_date,
        total_revenue / 100 if total_revenue else 0,
        items_count,
        source,
    )


def _ensure_daily_sales_xero_columns(conn) -> None:
    """Backwards-safe migration: add Xero revenue cross-check columns."""
    conn.execute(
        _text("ALTER TABLE daily_sales_history " "ADD COLUMN IF NOT EXISTS xero_revenue_cents INT")
    )
    conn.execute(
        _text(
            "ALTER TABLE daily_sales_history " "ADD COLUMN IF NOT EXISTS xero_synced_at TIMESTAMP"
        )
    )


def store_xero_daily_revenue(site_id: str, sale_date: date, xero_revenue_cents: int) -> None:
    """
    Store Xero-sourced revenue for a specific day.

    Updates only the xero_revenue_cents and xero_synced_at columns,
    preserving the existing Square-sourced gross_sales_cents for comparison.
    If no daily_sales_history row exists for this day, creates one with
    source='xero'.
    """
    with engine.connect() as conn:
        _ensure_daily_sales_xero_columns(conn)
        conn.execute(
            _text(
                """
                INSERT INTO daily_sales_history
                    (site_id, sale_date, xero_revenue_cents, xero_synced_at, source)
                VALUES
                    (:sid, :sale_date, :xero_rev, NOW(), 'xero')
                ON CONFLICT (site_id, sale_date) DO UPDATE SET
                    xero_revenue_cents = :xero_rev,
                    xero_synced_at = NOW()
            """
            ),
            {"sid": site_id, "sale_date": sale_date, "xero_rev": xero_revenue_cents},
        )
        conn.commit()

    logger.info("Stored Xero revenue for %s: $%.2f", sale_date, xero_revenue_cents / 100)


# ============================================================
# Full daily pipeline storage
# ============================================================


def store_daily_pipeline(site_id: str, pipeline_result: dict) -> dict:
    """
    Store all outputs from a daily processing pipeline run.

    Takes the output of processing.process_orders_batch() and writes:
    1. Raw orders -> orders_raw
    2. Order items with workload -> order_items
    3. Timeline aggregation -> workload_timeline

    Returns counts of stored records.
    """
    orders = pipeline_result.get("orders", [])
    timeline = pipeline_result.get("timeline", [])

    orders_stored = store_orders_raw(site_id, orders)
    items_stored = store_order_items(site_id, orders)
    timeline_stored = store_timeline(site_id, timeline)

    result = {
        "orders_stored": orders_stored,
        "items_stored": items_stored,
        "timeline_stored": timeline_stored,
    }

    logger.info(
        "Daily pipeline storage complete: %d orders, %d items, %d intervals",
        orders_stored,
        items_stored,
        timeline_stored,
    )
    return result


# ============================================================
# Helper
# ============================================================


class _JSONEncoder(json.JSONEncoder):
    """Handle UUID and date/datetime objects in JSON serialization."""

    def default(self, o):
        if isinstance(o, _uuid.UUID):
            return str(o)
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return super().default(o)


def get_avg_workload_per_drink(site_id: str, weeks_back: int = 6) -> float:
    """
    Compute average total workload per drink from recent data.

    Returns the ratio: (total workload for all items) / (drink count).
    Used by the forecast to convert predicted workload units to drink count.
    Falls back to 3.5 if insufficient data.
    """
    cutoff = datetime.utcnow() - timedelta(weeks=weeks_back)

    with engine.connect() as conn:
        result = (
            conn.execute(
                _text(
                    """
                SELECT
                    SUM(wt.workload_units) AS total_wu,
                    SUM(wt.items_count) AS total_items
                FROM workload_timeline wt
                WHERE wt.site_id = :sid
                AND wt.interval_start >= :cutoff
            """
                ),
                {"sid": site_id, "cutoff": cutoff},
            )
            .mappings()
            .first()
        )

        drink_result = (
            conn.execute(
                _text(
                    """
                SELECT COUNT(*) AS drink_count
                FROM order_items oi
                JOIN orders_raw o ON oi.order_id = o.order_id
                WHERE oi.site_id = :sid
                AND o.closed_at >= :cutoff
                AND oi.item_name NOT IN (
                    'Sweet Pastry', 'TOASTIE', 'A Sweet Muffin', 'A Savoury Muffin',
                    'Breakfast Wrap', 'Ham And Cheese Croissant', 'Plain Croissant',
                    'BUTTERBOY', 'A Cookie',
                    'Portugese Tart/Friand/Caramel Slice',
                    'Fiji Water', 'Fruit Juice', 'Kombucha', 'Famous Soda', 'Black Mass',
                    '500g Beans', '1kg Beans', '250g Beans',
                    'Candle', 'eGift Card', 'Mary Clothes'
                )
            """
                ),
                {"sid": site_id, "cutoff": cutoff},
            )
            .mappings()
            .first()
        )

    total_wu = float(result["total_wu"]) if result and result["total_wu"] else 0
    drink_count = int(drink_result["drink_count"]) if drink_result else 0

    if drink_count < 100:
        logger.warning("Insufficient data for WU/drink ratio, using default 3.5")
        return 3.5

    ratio = total_wu / drink_count
    logger.debug("WU per drink ratio: %.2f (from %d drinks, %.0f WU)", ratio, drink_count, total_wu)
    return ratio


# ============================================================
# Deputy Rosters
# ============================================================


def store_deputy_rosters(site_id: str, rosters: list[dict]) -> int:
    """
    Store Deputy roster records with upsert on deputy_id.
    Same idempotent pattern as store_orders_raw().
    """
    if not rosters:
        return 0

    stored = 0
    with engine.connect() as conn:
        for r in rosters:
            conn.execute(
                _text(
                    "INSERT INTO deputy_rosters "
                    "(site_id, shift_date, start_time, end_time, employee_id, "
                    "employee_name, total_hours, cost_dollars, is_published, "
                    "is_open, deputy_id) "
                    "VALUES (:sid, :sd, :st, :et, :eid, :ename, :hours, "
                    ":cost, :pub, :open, :did) "
                    "ON CONFLICT (deputy_id) DO UPDATE SET "
                    "shift_date = EXCLUDED.shift_date, "
                    "start_time = EXCLUDED.start_time, "
                    "end_time = EXCLUDED.end_time, "
                    "employee_id = EXCLUDED.employee_id, "
                    "employee_name = EXCLUDED.employee_name, "
                    "total_hours = EXCLUDED.total_hours, "
                    "cost_dollars = EXCLUDED.cost_dollars, "
                    "is_published = EXCLUDED.is_published, "
                    "is_open = EXCLUDED.is_open"
                ),
                {
                    "sid": site_id,
                    "sd": r["shift_date"],
                    "st": r["start_time"],
                    "et": r["end_time"],
                    "eid": r.get("employee_id"),
                    "ename": r.get("employee_name"),
                    "hours": r.get("total_hours"),
                    "cost": r.get("cost_dollars"),
                    "pub": r.get("is_published", True),
                    "open": r.get("is_open", False),
                    "did": r["deputy_id"],
                },
            )
            stored += 1

        conn.commit()

    logger.info("Stored %d deputy rosters", stored)
    return stored


def get_rosters_for_date(site_id: str, target_date: date) -> list[dict]:
    """Get all shifts for a specific date, ordered by start time."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "SELECT shift_date, start_time, end_time, employee_name, "
                "total_hours, cost_dollars, is_published, is_open "
                "FROM deputy_rosters "
                "WHERE site_id = :sid AND shift_date = :d "
                "ORDER BY start_time"
            ),
            {"sid": site_id, "d": target_date},
        )
        return [dict(row) for row in result.mappings()]


def get_roster_summary(site_id: str, start_date: date, end_date: date) -> list[dict]:
    """
    Daily roster aggregation: staff count, total hours, total cost, open shifts.
    Used by chat for multi-day staffing overview.
    """
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "SELECT shift_date, "
                "COUNT(*) AS total_shifts, "
                "COUNT(DISTINCT employee_name) FILTER (WHERE employee_name IS NOT NULL) AS staff_count, "
                "COALESCE(SUM(total_hours), 0) AS total_hours, "
                "COALESCE(SUM(cost_dollars), 0) AS total_cost, "
                "COUNT(*) FILTER (WHERE is_open = TRUE) AS open_shifts "
                "FROM deputy_rosters "
                "WHERE site_id = :sid AND shift_date BETWEEN :s AND :e "
                "GROUP BY shift_date "
                "ORDER BY shift_date"
            ),
            {"sid": site_id, "s": start_date, "e": end_date},
        )
        return [
            {
                "date": str(row["shift_date"]),
                "staff_count": int(row["staff_count"]),
                "total_shifts": int(row["total_shifts"]),
                "total_hours": float(row["total_hours"]),
                "total_cost": float(row["total_cost"]),
                "open_shifts": int(row["open_shifts"]),
            }
            for row in result.mappings()
        ]


def get_staffing_vs_workload(site_id: str, start_date: date, end_date: date) -> list[dict]:
    """
    Join deputy_rosters with workload_timeline to correlate staffing vs demand.
    Returns daily: date, staff_on, total_drinks, drinks_per_staff, total_workload.
    This is the key correlation query for chat insights.
    """
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                """
                SELECT
                    dr.shift_date AS the_date,
                    COUNT(DISTINCT dr.employee_name) FILTER (WHERE dr.employee_name IS NOT NULL) AS staff_on,
                    COALESCE(SUM(dr.total_hours), 0) AS staff_hours,
                    COALESCE(SUM(dr.cost_dollars), 0) AS labour_cost,
                    w.total_items,
                    w.total_workload,
                    CASE WHEN COUNT(DISTINCT dr.employee_name) > 0
                         THEN ROUND(w.total_items::numeric / COUNT(DISTINCT dr.employee_name), 1)
                         ELSE NULL END AS drinks_per_staff
                FROM deputy_rosters dr
                LEFT JOIN (
                    SELECT DATE(interval_start) AS work_date,
                           SUM(items_count) AS total_items,
                           SUM(workload_units) AS total_workload
                    FROM workload_timeline
                    WHERE site_id = :sid
                    GROUP BY DATE(interval_start)
                ) w ON dr.shift_date = w.work_date
                WHERE dr.site_id = :sid
                AND dr.shift_date BETWEEN :s AND :e
                GROUP BY dr.shift_date, w.total_items, w.total_workload
                ORDER BY dr.shift_date
            """
            ),
            {"sid": site_id, "s": start_date, "e": end_date},
        )
        return [
            {
                "date": str(row["the_date"]),
                "staff_on": int(row["staff_on"]),
                "staff_hours": float(row["staff_hours"]),
                "labour_cost": float(row["labour_cost"]),
                "total_drinks": int(row["total_items"]) if row["total_items"] else None,
                "total_workload": float(row["total_workload"]) if row["total_workload"] else None,
                "drinks_per_staff": (
                    float(row["drinks_per_staff"]) if row["drinks_per_staff"] else None
                ),
            }
            for row in result.mappings()
        ]


def get_staffing_variance_intervals(site_id: str, target_date: date) -> dict:
    """
    Compare active staff vs workload per 15-minute interval for a given day.

    Returns intervals with staffing status and a day summary:
      - understaffed windows
      - overstaffed windows
      - balanced windows
      - no-staff windows while work exists
    """
    from config.constants import (
        STAFFING_WU_PER_PERSON_HIGH,
        STAFFING_WU_PER_PERSON_LOW,
        STAFFING_WU_PER_PERSON_TARGET,
    )

    with engine.connect() as conn:
        result = conn.execute(
            _text(
                """
                WITH intervals AS (
                    SELECT interval_start, workload_units, items_count
                    FROM workload_timeline
                    WHERE site_id = :sid
                      AND DATE(interval_start) = :d
                )
                SELECT
                    i.interval_start,
                    i.workload_units,
                    i.items_count,
                    (
                        SELECT COUNT(DISTINCT COALESCE(dr.employee_id::text, dr.employee_name, dr.deputy_id::text))
                        FROM deputy_rosters dr
                        WHERE dr.site_id = :sid
                          AND dr.shift_date = :d
                          AND dr.start_time <= i.interval_start
                          AND dr.end_time > i.interval_start
                    ) AS staff_on
                FROM intervals i
                ORDER BY i.interval_start
                """
            ),
            {"sid": site_id, "d": target_date},
        )
        rows = list(result.mappings())

    intervals = []
    summary = {
        "date": target_date.isoformat(),
        "interval_count": 0,
        "understaffed_intervals": 0,
        "overstaffed_intervals": 0,
        "balanced_intervals": 0,
        "no_staff_intervals": 0,
        "peak_workload_units": 0.0,
    }

    for row in rows:
        workload_units = float(row["workload_units"] or 0.0)
        items_count = int(row["items_count"] or 0)
        staff_on = int(row["staff_on"] or 0)
        expected_staff = (
            int(math.ceil(workload_units / STAFFING_WU_PER_PERSON_TARGET))
            if workload_units > 0
            else 0
        )
        workload_per_staff = round(workload_units / staff_on, 2) if staff_on > 0 else None

        if staff_on == 0 and workload_units > 0:
            status = "no_staff"
            severity = "high"
        elif workload_per_staff is None:
            status = "no_workload"
            severity = "low"
        elif workload_per_staff > STAFFING_WU_PER_PERSON_HIGH:
            status = "understaffed"
            severity = "high"
        elif workload_per_staff < STAFFING_WU_PER_PERSON_LOW:
            status = "overstaffed"
            severity = "medium"
        else:
            status = "balanced"
            severity = "low"

        intervals.append(
            {
                "interval_start": str(row["interval_start"]),
                "workload_units": round(workload_units, 2),
                "items_count": items_count,
                "staff_on": staff_on,
                "expected_staff": expected_staff,
                "staff_delta": staff_on - expected_staff,
                "workload_per_staff": workload_per_staff,
                "status": status,
                "severity": severity,
            }
        )

        summary["interval_count"] += 1
        summary["peak_workload_units"] = max(summary["peak_workload_units"], workload_units)
        if status == "understaffed":
            summary["understaffed_intervals"] += 1
        elif status == "overstaffed":
            summary["overstaffed_intervals"] += 1
        elif status == "balanced":
            summary["balanced_intervals"] += 1
        elif status == "no_staff":
            summary["no_staff_intervals"] += 1

    return {
        "date": target_date.isoformat(),
        "thresholds": {
            "target_wu_per_staff": STAFFING_WU_PER_PERSON_TARGET,
            "high_wu_per_staff": STAFFING_WU_PER_PERSON_HIGH,
            "low_wu_per_staff": STAFFING_WU_PER_PERSON_LOW,
        },
        "summary": summary,
        "intervals": intervals,
    }


def get_daily_efficiency_snapshot(site_id: str, target_date: date) -> dict:
    """
    Daily efficiency view aligned to 15-minute intervals.

    Combines:
      - staffing/workload status from Deputy + workload_timeline
      - trade intensity from orders_raw (orders + revenue per 15-min bucket)
      - daily staffing totals from Deputy
    """
    variance = get_staffing_variance_intervals(site_id, target_date)
    base_intervals = variance.get("intervals", [])

    def _bucket_iso(ts) -> str:
        if isinstance(ts, datetime):
            dt = ts
        else:
            dt = datetime.fromisoformat(str(ts))
        minute = (dt.minute // 15) * 15
        dt = dt.replace(minute=minute, second=0, microsecond=0)
        return dt.isoformat()

    with engine.connect() as conn:
        trade_rows = conn.execute(
            _text(
                """
                SELECT
                    date_trunc('hour', closed_at)
                    + (floor(extract(minute from closed_at) / 15) * interval '15 minutes')
                    AS interval_start,
                    COUNT(*) AS orders_count,
                    COALESCE(SUM(total_money_cents), 0) AS revenue_cents
                FROM orders_raw
                WHERE site_id = :sid
                  AND closed_at IS NOT NULL
                  AND DATE(closed_at) = :d
                GROUP BY 1
                """
            ),
            {"sid": site_id, "d": target_date},
        ).mappings()

        deputy_totals = (
            conn.execute(
                _text(
                    """
                SELECT
                    COUNT(*) AS shifts_count,
                    COALESCE(SUM(total_hours), 0) AS total_hours,
                    COALESCE(SUM(cost_dollars), 0) AS total_cost_dollars
                FROM deputy_rosters
                WHERE site_id = :sid
                  AND shift_date = :d
                """
                ),
                {"sid": site_id, "d": target_date},
            )
            .mappings()
            .first()
        )

    trade_by_bucket = {}
    for row in trade_rows:
        key = _bucket_iso(row["interval_start"])
        trade_by_bucket[key] = {
            "orders_count": int(row["orders_count"] or 0),
            "revenue_cents": int(row["revenue_cents"] or 0),
        }

    enriched = []
    for row in base_intervals:
        bucket_key = _bucket_iso(row["interval_start"])
        trade = trade_by_bucket.get(bucket_key, {})
        orders_count = int(trade.get("orders_count", 0))
        revenue_cents = int(trade.get("revenue_cents", 0))
        staff_on = int(row.get("staff_on") or 0)
        rev_per_staff_hour = None
        if staff_on > 0:
            rev_per_staff_hour = round(revenue_cents / (staff_on * 0.25))

        enriched.append(
            {
                **row,
                "orders_count": orders_count,
                "revenue_cents": revenue_cents,
                "revenue_per_staff_hour_cents": rev_per_staff_hour,
            }
        )

    trade_intervals = sorted(
        [r for r in enriched if (r.get("orders_count", 0) > 0 or r.get("revenue_cents", 0) > 0)],
        key=lambda x: (x.get("revenue_cents", 0), x.get("orders_count", 0)),
        reverse=True,
    )
    staffing_intervals = sorted(
        enriched,
        key=lambda x: (x.get("staff_on", 0), x.get("workload_units", 0)),
        reverse=True,
    )
    mismatch_intervals = sorted(
        [r for r in enriched if r.get("status") in ("understaffed", "overstaffed", "no_staff")],
        key=lambda x: (x.get("revenue_cents", 0), x.get("workload_units", 0)),
        reverse=True,
    )

    total_revenue = sum(r.get("revenue_cents", 0) or 0 for r in enriched)
    total_orders = sum(r.get("orders_count", 0) or 0 for r in enriched)
    total_items = sum(r.get("items_count", 0) or 0 for r in enriched)
    total_workload = round(sum(r.get("workload_units", 0.0) or 0.0 for r in enriched), 2)
    total_staff_hours = float((deputy_totals or {}).get("total_hours") or 0.0)
    total_labor_cost_cents = round(
        float((deputy_totals or {}).get("total_cost_dollars") or 0.0) * 100
    )
    labor_pct = (
        round((total_labor_cost_cents / total_revenue) * 100, 2) if total_revenue > 0 else None
    )
    rev_per_labor_hour = round(total_revenue / total_staff_hours) if total_staff_hours > 0 else None

    return {
        "date": target_date.isoformat(),
        "summary": {
            "intervals_analyzed": len(enriched),
            "total_revenue_cents": total_revenue,
            "total_orders": total_orders,
            "total_items": total_items,
            "total_workload_units": total_workload,
            "deputy_shift_count": int((deputy_totals or {}).get("shifts_count") or 0),
            "deputy_staff_hours": round(total_staff_hours, 2),
            "deputy_labor_cost_cents": total_labor_cost_cents,
            "labor_pct": labor_pct,
            "revenue_per_labor_hour_cents": rev_per_labor_hour,
        },
        "peaks": {
            "trade": trade_intervals[:5],
            "staffing": staffing_intervals[:5],
            "mismatch": mismatch_intervals[:8],
        },
        "variance_summary": variance.get("summary", {}),
        "intervals": enriched,
    }


# ============================================================
# Staffing Efficiency Gap
# ============================================================


def get_efficiency_gap_range(site_id: str, start_date: date, end_date: date) -> dict:
    """
    Compute staffing efficiency gap across a date range using real Deputy costs.

    All revenue figures are **ex-GST** (true cash position). GST is a
    pass-through to the ATO and excluded from business decisions.
    Square figures are divided by (1 + GST_RATE); Xero P&L is already ex-GST.

    Uses actual roster cost_dollars (incl. casual loading, weekend rates, age-based
    award rates) for the "actual" side. Computes minimum viable cost using real
    per-day rates with the constraint that at least 1 senior (>=18yo, hourly rate
    >= JUNIOR_HOURLY_RATE_THRESHOLD) must always be on shift.

    Cheapest valid team for N staff = 1 × cheapest_senior + (N-1) × cheapest_available.
    All staff are casual — Deputy cost_dollars already includes casual loading but
    NOT super. We add SUPERANNUATION_RATE on top for true cost.
    Owner shifts use OWNER_HOURLY_RATE_CENTS instead of Deputy's understated rate.
    """
    from config.constants import (
        GST_RATE,
        JUNIOR_HOURLY_RATE_THRESHOLD,
        OWNER_DEPUTY_NAME,
        OWNER_HOURLY_RATE_CENTS,
        SUPERANNUATION_RATE,
    )
    from config.workflow_profiles import minimum_viable_staff

    junior_threshold_dollars = JUNIOR_HOURLY_RATE_THRESHOLD / 100
    super_mult = 1 + SUPERANNUATION_RATE  # 1.115
    gst_divisor = 1 + GST_RATE  # 1.10 — Square is inc-GST, we report ex-GST
    owner_interval_cost_cents = round(OWNER_HOURLY_RATE_CENTS * 0.25)  # per 15-min

    with engine.connect() as conn:
        # 1. Per-interval: workload + per-staff details (name, rate) for true cost calc
        interval_rows = (
            conn.execute(
                _text(
                    """
                SELECT
                    wt.interval_start,
                    DATE(wt.interval_start) AS day,
                    wt.workload_units,
                    (
                        SELECT COUNT(DISTINCT COALESCE(dr.employee_id::text, dr.employee_name, dr.deputy_id::text))
                        FROM deputy_rosters dr
                        WHERE dr.site_id = :sid
                          AND dr.shift_date = DATE(wt.interval_start)
                          AND dr.start_time <= wt.interval_start
                          AND dr.end_time > wt.interval_start
                    ) AS staff_on,
                    (
                        SELECT COALESCE(SUM(
                            CASE WHEN dr.employee_name = :owner_name
                                 THEN 0
                                 ELSE dr.cost_dollars / NULLIF(dr.total_hours, 0) * 0.25
                            END
                        ), 0)
                        FROM deputy_rosters dr
                        WHERE dr.site_id = :sid
                          AND dr.shift_date = DATE(wt.interval_start)
                          AND dr.start_time <= wt.interval_start
                          AND dr.end_time > wt.interval_start
                          AND dr.total_hours > 0
                    ) AS staff_cost_dollars,
                    (
                        SELECT COUNT(*)
                        FROM deputy_rosters dr
                        WHERE dr.site_id = :sid
                          AND dr.shift_date = DATE(wt.interval_start)
                          AND dr.start_time <= wt.interval_start
                          AND dr.end_time > wt.interval_start
                          AND dr.employee_name = :owner_name
                    ) AS owner_on
                FROM workload_timeline wt
                WHERE wt.site_id = :sid
                  AND wt.interval_start >= :s
                  AND wt.interval_start < :e ::date + interval '1 day'
                ORDER BY wt.interval_start
                """
                ),
                {"sid": site_id, "s": start_date, "e": end_date, "owner_name": OWNER_DEPUTY_NAME},
            )
            .mappings()
            .all()
        )

        # 2a. Revenue from daily_sales_history (may include Xero cross-check columns).
        try:
            _ensure_daily_sales_xero_columns(conn)
            history_rows = (
                conn.execute(
                    _text(
                        """
                    SELECT
                        sale_date AS day,
                        gross_sales_cents,
                        xero_revenue_cents,
                        source
                    FROM daily_sales_history
                    WHERE site_id = :sid
                      AND sale_date >= :s
                      AND sale_date <= :e
                    """
                    ),
                    {"sid": site_id, "s": start_date, "e": end_date},
                )
                .mappings()
                .all()
            )
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.warning("daily_sales_history xero columns unavailable (non-fatal): %s", e)
            history_rows = (
                conn.execute(
                    _text(
                        """
                    SELECT
                        sale_date AS day,
                        gross_sales_cents,
                        NULL::INT AS xero_revenue_cents,
                        source
                    FROM daily_sales_history
                    WHERE site_id = :sid
                      AND sale_date >= :s
                      AND sale_date <= :e
                    """
                    ),
                    {"sid": site_id, "s": start_date, "e": end_date},
                )
                .mappings()
                .all()
            )

        # 2b. Revenue from orders_raw as fallback
        orders_raw_rows = (
            conn.execute(
                _text(
                    """
                SELECT
                    DATE(closed_at) AS day,
                    COUNT(*) AS order_count,
                    COALESCE(SUM(total_money_cents), 0) AS revenue_cents
                FROM orders_raw
                WHERE site_id = :sid
                  AND closed_at IS NOT NULL
                  AND DATE(closed_at) >= :s
                  AND DATE(closed_at) <= :e
                GROUP BY DATE(closed_at)
                """
                ),
                {"sid": site_id, "s": start_date, "e": end_date},
            )
            .mappings()
            .all()
        )

        # 3. Per-day cheapest senior & junior rates (for min viable cost)
        rate_rows = (
            conn.execute(
                _text(
                    """
                SELECT
                    shift_date,
                    cost_dollars / NULLIF(total_hours, 0) AS hourly_rate
                FROM deputy_rosters
                WHERE site_id = :sid
                  AND shift_date >= :s AND shift_date <= :e
                  AND total_hours > 0 AND cost_dollars > 0
                ORDER BY shift_date
                """
                ),
                {"sid": site_id, "s": start_date, "e": end_date},
            )
            .mappings()
            .all()
        )

    # Build daily revenue lookup — ALL figures ex-GST (true cash position).
    # GST is a pass-through to the ATO, not real revenue.
    # Priority: xero (already ex-GST) > daily_sales_history > orders_raw
    # Square sources are inc-GST and must be divided by gst_divisor.
    revenue_by_day: dict[str, int] = {}
    revenue_source_by_day: dict[str, str] = {}
    for row in orders_raw_rows:
        day_str = str(row["day"])
        inc_gst = int(row["revenue_cents"] or 0)
        revenue_by_day[day_str] = round(inc_gst / gst_divisor)
        revenue_source_by_day[day_str] = "square_api"

    # Override with daily_sales_history (CSV imports may be more complete)
    for row in history_rows:
        day_str = str(row["day"])
        gross = int(row["gross_sales_cents"] or 0)
        xero_rev = row["xero_revenue_cents"]
        source = row["source"] or "unknown"

        if xero_rev is not None and int(xero_rev) > 0:
            # Xero P&L income is already ex-GST — use directly
            revenue_by_day[day_str] = int(xero_rev)
            revenue_source_by_day[day_str] = "xero"
        elif gross > 0 and source == "csv":
            # CSV-imported Square Dashboard data is inc-GST — strip GST
            revenue_by_day[day_str] = round(gross / gst_divisor)
            revenue_source_by_day[day_str] = "square_csv"

    # Build per-day cheapest senior and junior rates (excl. owner, incl. super)
    day_rates: dict[str, dict] = {}
    for row in rate_rows:
        day_str = str(row["shift_date"])
        rate = float(row["hourly_rate"] or 0)
        if rate <= 0:
            continue
        if day_str not in day_rates:
            day_rates[day_str] = {"cheapest_senior": None, "cheapest_junior": None}
        dr = day_rates[day_str]
        # Apply super to get true cost rate
        true_rate = rate * super_mult
        if rate >= junior_threshold_dollars:
            if dr["cheapest_senior"] is None or true_rate < dr["cheapest_senior"]:
                dr["cheapest_senior"] = true_rate
        else:
            if dr["cheapest_junior"] is None or true_rate < dr["cheapest_junior"]:
                dr["cheapest_junior"] = true_rate

    # Aggregate per-day
    day_data: dict[str, dict] = {}
    for row in interval_rows:
        staff_on = int(row["staff_on"] or 0)
        if staff_on <= 0:
            continue

        day_str = str(row["day"])
        wu = float(row["workload_units"] or 0)
        owner_on = int(row["owner_on"] or 0)
        min_staff = minimum_viable_staff(wu)

        # Actual cost: Deputy staff cost (+ super) + owner cost (salary-based)
        staff_cost_cents = round(float(row["staff_cost_dollars"] or 0) * 100 * super_mult)
        owner_cost_cents = owner_interval_cost_cents * owner_on
        actual_cost_cents = staff_cost_cents + owner_cost_cents

        # Minimum viable cost using real rates + senior constraint
        rates = day_rates.get(day_str, {})
        cheapest_senior = rates.get("cheapest_senior")
        cheapest_junior = rates.get("cheapest_junior")

        if cheapest_senior is not None and min_staff > 0:
            fill_rate = cheapest_junior if cheapest_junior is not None else cheapest_senior
            min_cost_cents = round(
                (cheapest_senior * 0.25 + max(0, min_staff - 1) * fill_rate * 0.25) * 100
            )
        elif min_staff > 0:
            from config.constants import LABOR_COST_PER_PERSON_PER_INTERVAL_CENTS

            min_cost_cents = min_staff * LABOR_COST_PER_PERSON_PER_INTERVAL_CENTS
        else:
            min_cost_cents = 0

        excess = max(0, actual_cost_cents - min_cost_cents)
        deficit = max(0, min_cost_cents - actual_cost_cents)

        if day_str not in day_data:
            day_data[day_str] = {
                "actual_labor_cents": 0,
                "min_labor_cents": 0,
                "excess_labor_cents": 0,
                "deficit_labor_cents": 0,
                "intervals": 0,
                "overstaffed_intervals": 0,
                "understaffed_intervals": 0,
            }
        d = day_data[day_str]
        d["actual_labor_cents"] += actual_cost_cents
        d["min_labor_cents"] += min_cost_cents
        d["excess_labor_cents"] += excess
        d["deficit_labor_cents"] += deficit
        d["intervals"] += 1
        if staff_on > min_staff:
            d["overstaffed_intervals"] += 1
        elif staff_on < min_staff:
            d["understaffed_intervals"] += 1

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    by_day = []
    for day_str, d in sorted(day_data.items()):
        try:
            dt = date.fromisoformat(day_str)
            dow = dt.weekday()
            day_name = day_names[dow]
        except Exception:
            dow = 0
            day_name = "Unknown"
        # efficiency_score: capped at 1.0 (1.0 = no overstaffing waste)
        # raw_ratio > 1.0 means understaffed (min > actual)
        raw_ratio = (
            d["min_labor_cents"] / d["actual_labor_cents"] if d["actual_labor_cents"] > 0 else 1.0
        )
        eff = min(1.0, round(raw_ratio, 4))
        by_day.append(
            {
                "date": day_str,
                "day_name": day_name,
                "dow": dow,
                "actual_labor_cents": d["actual_labor_cents"],
                "min_labor_cents": d["min_labor_cents"],
                "excess_labor_cents": d["excess_labor_cents"],
                "deficit_labor_cents": d["deficit_labor_cents"],
                "efficiency_score": round(eff, 4),
                "total_revenue_cents": revenue_by_day.get(day_str, 0),
                "revenue_source": revenue_source_by_day.get(day_str, "none"),
                "intervals": d["intervals"],
                "overstaffed_intervals": d["overstaffed_intervals"],
                "understaffed_intervals": d["understaffed_intervals"],
            }
        )

    # Aggregate by day-of-week — dollar-weighted efficiency
    dow_agg: dict[int, dict] = {}
    for entry in by_day:
        dow = entry["dow"]
        if dow not in dow_agg:
            dow_agg[dow] = {
                "total_actual": 0,
                "total_min": 0,
                "total_excess": 0,
                "total_deficit": 0,
                "days": 0,
                "day_name": entry["day_name"],
            }
        a = dow_agg[dow]
        a["total_actual"] += entry["actual_labor_cents"]
        a["total_min"] += entry["min_labor_cents"]
        a["total_excess"] += entry["excess_labor_cents"]
        a["total_deficit"] += entry["deficit_labor_cents"]
        a["days"] += 1

    by_dow = []
    for dow in sorted(dow_agg):
        a = dow_agg[dow]
        n = a["days"]
        # Dollar-weighted efficiency: total_min / total_actual across all days for this DOW
        weighted_eff = (
            min(1.0, round(a["total_min"] / a["total_actual"], 4)) if a["total_actual"] > 0 else 1.0
        )
        by_dow.append(
            {
                "dow": dow,
                "day_name": a["day_name"],
                "sample_days": n,
                "avg_excess_labor_cents": round(a["total_excess"] / n) if n else 0,
                "avg_deficit_labor_cents": round(a["total_deficit"] / n) if n else 0,
                "avg_efficiency_score": weighted_eff,
            }
        )

    # Totals
    total_actual = sum(d["actual_labor_cents"] for d in by_day)
    total_min = sum(d["min_labor_cents"] for d in by_day)
    total_excess = sum(d["excess_labor_cents"] for d in by_day)
    total_deficit = sum(d["deficit_labor_cents"] for d in by_day)
    total_rev = sum(d["total_revenue_cents"] for d in by_day)
    # Cap at 1.0: above 1.0 means understaffed, not overstaffing waste
    eff_score = min(1.0, round(total_min / total_actual, 4)) if total_actual > 0 else 1.0

    return {
        "totals": {
            "days_analyzed": len(by_day),
            "actual_labor_cents": total_actual,
            "minimum_labor_cents": total_min,
            "excess_labor_cents": total_excess,
            "deficit_labor_cents": total_deficit,
            "total_revenue_cents": total_rev,
            "efficiency_score": eff_score,
        },
        "by_day": by_day,
        "by_dow": by_dow,
    }


# ============================================================
# Item Costs (COGS)
# ============================================================


def seed_item_costs(site_id: str) -> int:
    """
    Seed default COGS into item_costs table.
    Uses ON CONFLICT DO NOTHING so existing values are preserved.
    Returns count of newly inserted rows.
    """
    from config.constants import DEFAULT_ITEM_COSTS

    inserted = 0
    with engine.connect() as conn:
        for score_key, info in DEFAULT_ITEM_COSTS.items():
            result = conn.execute(
                _text(
                    "INSERT INTO item_costs "
                    "(site_id, score_key, category, cost_cents, description) "
                    "VALUES (:sid, :sk, :cat, :cost, :desc) "
                    "ON CONFLICT (site_id, score_key) DO NOTHING"
                ),
                {
                    "sid": site_id,
                    "sk": score_key,
                    "cat": info["category"],
                    "cost": info["cost_cents"],
                    "desc": info.get("description"),
                },
            )
            inserted += result.rowcount
        conn.commit()

    logger.info("Seeded %d item costs for site %s", inserted, site_id)
    return inserted


def get_item_costs(site_id: str) -> dict[str, int]:
    """Return {score_key: cost_cents} for a site."""
    with engine.connect() as conn:
        result = conn.execute(
            _text("SELECT score_key, cost_cents FROM item_costs " "WHERE site_id = :sid"),
            {"sid": site_id},
        )
        return {row[0]: int(row[1]) for row in result}


# ============================================================
# Inventory (stock items, usage rules, counts, receipts)
# ============================================================


def upsert_inventory_item(
    site_id: str,
    item_name: str,
    unit: str,
    reorder_point: float,
    par_level: float = None,
    lead_time_days: int = 2,
    active: bool = True,
    score_key: str = None,
    metadata: dict = None,
) -> str:
    """
    Upsert an inventory item by (site_id, item_name).
    Returns inventory_item_id.
    """
    normalized_name = (item_name or "").strip()
    if not normalized_name:
        raise ValueError("item_name is required")

    with engine.connect() as conn:
        result = conn.execute(
            _text(
                """
                INSERT INTO inventory_items
                    (site_id, item_name, score_key, unit, reorder_point, par_level,
                     lead_time_days, active, metadata, updated_at)
                VALUES
                    (:sid, :name, :sk, :unit, :rp, :pl, :ltd, :active, :meta, NOW())
                ON CONFLICT (site_id, item_name) DO UPDATE SET
                    score_key = EXCLUDED.score_key,
                    unit = EXCLUDED.unit,
                    reorder_point = EXCLUDED.reorder_point,
                    par_level = EXCLUDED.par_level,
                    lead_time_days = EXCLUDED.lead_time_days,
                    active = EXCLUDED.active,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                RETURNING inventory_item_id
                """
            ),
            {
                "sid": site_id,
                "name": normalized_name,
                "sk": (score_key or "").strip() or None,
                "unit": (unit or "units").strip() or "units",
                "rp": max(0.0, float(reorder_point or 0)),
                "pl": float(par_level) if par_level is not None else None,
                "ltd": max(0, int(lead_time_days or 0)),
                "active": bool(active),
                "meta": _json_dumps(metadata) if metadata is not None else None,
            },
        )
        item_id = str(result.scalar())
        conn.commit()
    return item_id


def get_inventory_item_by_score_key(site_id: str, score_key: str) -> Optional[dict]:
    """
    Resolve inventory item by score_key.
    Returns None when inventory tables are unavailable or no match exists.
    """
    if not score_key:
        return None
    try:
        with engine.connect() as conn:
            row = (
                conn.execute(
                    _text(
                        """
                    SELECT inventory_item_id, site_id, item_name, score_key, unit,
                           reorder_point, par_level, lead_time_days, active
                    FROM inventory_items
                    WHERE site_id = :sid AND score_key = :sk
                    LIMIT 1
                    """
                    ),
                    {"sid": site_id, "sk": score_key},
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None
    except Exception as e:
        logger.warning("get_inventory_item_by_score_key unavailable (non-fatal): %s", e)
        return None


def list_inventory_items(site_id: str, active_only: bool = True) -> list[dict]:
    """List inventory items with latest physical count snapshot."""
    try:
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    _text(
                        """
                    SELECT
                        ii.inventory_item_id,
                        ii.item_name,
                        ii.score_key,
                        ii.unit,
                        ii.reorder_point,
                        ii.par_level,
                        ii.lead_time_days,
                        ii.active,
                        ii.metadata,
                        ii.updated_at,
                        lc.quantity_on_hand,
                        lc.counted_at
                    FROM inventory_items ii
                    LEFT JOIN LATERAL (
                        SELECT quantity_on_hand, counted_at
                        FROM inventory_counts ic
                        WHERE ic.site_id = ii.site_id
                          AND ic.inventory_item_id = ii.inventory_item_id
                        ORDER BY counted_at DESC
                        LIMIT 1
                    ) lc ON TRUE
                    WHERE ii.site_id = :sid
                      AND (:active_only = FALSE OR ii.active = TRUE)
                    ORDER BY ii.item_name
                    """
                    ),
                    {"sid": site_id, "active_only": active_only},
                )
                .mappings()
                .all()
            )

        out = []
        for r in rows:
            metadata = r.get("metadata")
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (ValueError, TypeError):
                    metadata = {}
            out.append(
                {
                    "inventory_item_id": str(r["inventory_item_id"]),
                    "item_name": r["item_name"],
                    "score_key": r.get("score_key"),
                    "unit": r.get("unit") or "units",
                    "reorder_point": float(r.get("reorder_point") or 0),
                    "par_level": (
                        float(r.get("par_level")) if r.get("par_level") is not None else None
                    ),
                    "lead_time_days": int(r.get("lead_time_days") or 0),
                    "active": bool(r.get("active")),
                    "metadata": metadata or {},
                    "updated_at": str(r.get("updated_at")) if r.get("updated_at") else None,
                    "last_count_on_hand": (
                        float(r.get("quantity_on_hand"))
                        if r.get("quantity_on_hand") is not None
                        else None
                    ),
                    "last_counted_at": (str(r.get("counted_at")) if r.get("counted_at") else None),
                }
            )
        return out
    except Exception as e:
        logger.warning("list_inventory_items unavailable (non-fatal): %s", e)
        return []


def store_inventory_count(
    site_id: str,
    inventory_item_id: str,
    quantity_on_hand: float,
    counted_at: datetime = None,
    source: str = "manual",
    notes: str = None,
) -> Optional[str]:
    """
    Store a physical count snapshot for one inventory item.
    Returns count_id, or None when inventory tables are unavailable.
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(
                _text(
                    """
                    INSERT INTO inventory_counts
                        (site_id, inventory_item_id, counted_at, quantity_on_hand, source, notes)
                    VALUES
                        (:sid, :iid, :counted_at, :qty, :source, :notes)
                    RETURNING count_id
                    """
                ),
                {
                    "sid": site_id,
                    "iid": inventory_item_id,
                    "counted_at": counted_at or datetime.utcnow(),
                    "qty": max(0.0, float(quantity_on_hand or 0)),
                    "source": (source or "manual").strip() or "manual",
                    "notes": notes,
                },
            )
            count_id = str(result.scalar())
            conn.commit()
        return count_id
    except Exception as e:
        logger.warning("store_inventory_count unavailable (non-fatal): %s", e)
        return None


def _normalize_terms(raw_terms: str) -> str:
    if not raw_terms:
        return ""
    terms = [t.strip().lower() for t in str(raw_terms).split(",") if t and t.strip()]
    return ",".join(terms)


def upsert_inventory_usage_rule(
    site_id: str,
    inventory_item_id: str,
    trigger_item_name: str,
    units_per_sale: float,
    required_modifier_terms: str = None,
    excluded_modifier_terms: str = None,
    priority: int = 100,
    active: bool = True,
) -> str:
    """
    Upsert a consumption rule for an inventory item.
    Returns rule_id.
    """
    trigger_norm = (trigger_item_name or "").strip().lower()
    if not trigger_norm:
        raise ValueError("trigger_item_name is required")

    req_norm = _normalize_terms(required_modifier_terms)
    exc_norm = _normalize_terms(excluded_modifier_terms)

    with engine.connect() as conn:
        existing = (
            conn.execute(
                _text(
                    """
                SELECT rule_id
                FROM inventory_usage_rules
                WHERE site_id = :sid
                  AND inventory_item_id = :iid
                  AND LOWER(trigger_item_name) = :trigger
                  AND COALESCE(LOWER(required_modifier_terms), '') = :req
                  AND COALESCE(LOWER(excluded_modifier_terms), '') = :exc
                LIMIT 1
                """
                ),
                {
                    "sid": site_id,
                    "iid": inventory_item_id,
                    "trigger": trigger_norm,
                    "req": req_norm,
                    "exc": exc_norm,
                },
            )
            .mappings()
            .first()
        )

        if existing:
            rule_id = str(existing["rule_id"])
            conn.execute(
                _text(
                    """
                    UPDATE inventory_usage_rules
                    SET units_per_sale = :ups,
                        priority = :priority,
                        active = :active,
                        updated_at = NOW()
                    WHERE rule_id = :rid
                    """
                ),
                {
                    "rid": rule_id,
                    "ups": max(0.0, float(units_per_sale or 0)),
                    "priority": int(priority or 100),
                    "active": bool(active),
                },
            )
        else:
            result = conn.execute(
                _text(
                    """
                    INSERT INTO inventory_usage_rules
                        (site_id, inventory_item_id, trigger_item_name,
                         required_modifier_terms, excluded_modifier_terms,
                         units_per_sale, priority, active)
                    VALUES
                        (:sid, :iid, :trigger, :req, :exc, :ups, :priority, :active)
                    RETURNING rule_id
                    """
                ),
                {
                    "sid": site_id,
                    "iid": inventory_item_id,
                    "trigger": trigger_norm,
                    "req": req_norm or None,
                    "exc": exc_norm or None,
                    "ups": max(0.0, float(units_per_sale or 0)),
                    "priority": int(priority or 100),
                    "active": bool(active),
                },
            )
            rule_id = str(result.scalar())

        conn.commit()
    return rule_id


def list_inventory_usage_rules(site_id: str, active_only: bool = True) -> list[dict]:
    """List inventory consumption rules with target item names."""
    try:
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    _text(
                        """
                    SELECT
                        r.rule_id,
                        r.inventory_item_id,
                        i.item_name AS inventory_item_name,
                        i.unit AS inventory_unit,
                        r.trigger_item_name,
                        r.required_modifier_terms,
                        r.excluded_modifier_terms,
                        r.units_per_sale,
                        r.priority,
                        r.active,
                        r.updated_at
                    FROM inventory_usage_rules r
                    JOIN inventory_items i
                      ON i.inventory_item_id = r.inventory_item_id
                     AND i.site_id = r.site_id
                    WHERE r.site_id = :sid
                      AND (:active_only = FALSE OR r.active = TRUE)
                    ORDER BY r.priority ASC, r.trigger_item_name ASC
                    """
                    ),
                    {"sid": site_id, "active_only": active_only},
                )
                .mappings()
                .all()
            )

        return [
            {
                "rule_id": str(r["rule_id"]),
                "inventory_item_id": str(r["inventory_item_id"]),
                "inventory_item_name": r["inventory_item_name"],
                "inventory_unit": r.get("inventory_unit") or "units",
                "trigger_item_name": r["trigger_item_name"],
                "required_modifier_terms": r.get("required_modifier_terms"),
                "excluded_modifier_terms": r.get("excluded_modifier_terms"),
                "units_per_sale": float(r.get("units_per_sale") or 0),
                "priority": int(r.get("priority") or 100),
                "active": bool(r.get("active")),
                "updated_at": str(r.get("updated_at")) if r.get("updated_at") else None,
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning("list_inventory_usage_rules unavailable (non-fatal): %s", e)
        return []


# ============================================================
# Operator Rules (chat-confirmed operating knowledge)
# ============================================================


def _ensure_operator_rules_table(conn) -> bool:
    try:
        conn.execute(
            _text(
                """
                CREATE TABLE IF NOT EXISTS operator_rules (
                    rule_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                    site_id UUID NOT NULL REFERENCES sites(site_id),
                    rule_type TEXT NOT NULL,
                    rule_name TEXT NOT NULL,
                    payload JSONB NOT NULL,
                    source TEXT NOT NULL DEFAULT 'chat',
                    status TEXT NOT NULL DEFAULT 'proposed',
                    confidence REAL,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_by TEXT,
                    confirmed_by TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    confirmed_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
        )
        conn.execute(
            _text(
                """
                CREATE INDEX IF NOT EXISTS idx_operator_rules_site_status
                ON operator_rules(site_id, status, active, updated_at DESC)
                """
            )
        )
        conn.execute(
            _text(
                """
                CREATE INDEX IF NOT EXISTS idx_operator_rules_site_type
                ON operator_rules(site_id, rule_type, updated_at DESC)
                """
            )
        )
        return True
    except Exception as exc:  # pragma: no cover - depends on DB privileges
        try:
            conn.rollback()
        except Exception:
            pass
        logger.info("operator_rules table unavailable (non-fatal): %s", exc)
        return False


def _row_to_operator_rule(row) -> dict:
    payload = row.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            payload = {}

    return {
        "rule_id": str(row["rule_id"]),
        "site_id": str(row["site_id"]),
        "rule_type": row["rule_type"],
        "rule_name": row.get("rule_name") or row["rule_type"],
        "payload": payload or {},
        "source": row.get("source") or "chat",
        "status": row.get("status") or "proposed",
        "confidence": float(row["confidence"]) if row.get("confidence") is not None else None,
        "active": bool(row.get("active", True)),
        "created_by": row.get("created_by"),
        "confirmed_by": row.get("confirmed_by"),
        "created_at": str(row.get("created_at")) if row.get("created_at") else None,
        "confirmed_at": str(row.get("confirmed_at")) if row.get("confirmed_at") else None,
        "updated_at": str(row.get("updated_at")) if row.get("updated_at") else None,
    }


def create_operator_rule(
    site_id: str,
    rule_type: str,
    rule_name: str,
    payload: dict,
    source: str = "chat",
    status: str = "proposed",
    confidence: float = None,
    created_by: str = "chat",
) -> Optional[dict]:
    try:
        with engine.connect() as conn:
            if not _ensure_operator_rules_table(conn):
                return None

            row = (
                conn.execute(
                    _text(
                        """
                        INSERT INTO operator_rules
                            (site_id, rule_type, rule_name, payload, source, status,
                             confidence, active, created_by, updated_at)
                        VALUES
                            (:sid, :rtype, :rname, :payload, :source, :status,
                             :confidence, TRUE, :created_by, NOW())
                        RETURNING rule_id, site_id, rule_type, rule_name, payload, source, status,
                                  confidence, active, created_by, confirmed_by,
                                  created_at, confirmed_at, updated_at
                        """
                    ),
                    {
                        "sid": site_id,
                        "rtype": (rule_type or "").strip(),
                        "rname": (rule_name or rule_type or "").strip(),
                        "payload": _json_dumps(payload or {}),
                        "source": (source or "chat").strip() or "chat",
                        "status": (status or "proposed").strip() or "proposed",
                        "confidence": confidence,
                        "created_by": created_by,
                    },
                )
                .mappings()
                .first()
            )
            conn.commit()
        return _row_to_operator_rule(row) if row else None
    except Exception as exc:
        logger.warning("create_operator_rule unavailable (non-fatal): %s", exc)
        return None


def get_pending_operator_rule(site_id: str) -> Optional[dict]:
    try:
        with engine.connect() as conn:
            if not _ensure_operator_rules_table(conn):
                return None

            row = (
                conn.execute(
                    _text(
                        """
                        SELECT rule_id, site_id, rule_type, rule_name, payload, source, status,
                               confidence, active, created_by, confirmed_by,
                               created_at, confirmed_at, updated_at
                        FROM operator_rules
                        WHERE site_id = :sid
                          AND status = 'proposed'
                          AND active = TRUE
                        ORDER BY updated_at DESC, created_at DESC
                        LIMIT 1
                        """
                    ),
                    {"sid": site_id},
                )
                .mappings()
                .first()
            )
        return _row_to_operator_rule(row) if row else None
    except Exception as exc:
        logger.warning("get_pending_operator_rule unavailable (non-fatal): %s", exc)
        return None


def list_operator_rules(
    site_id: str,
    statuses: list[str] = None,
    active_only: bool = True,
    limit: int = 25,
) -> list[dict]:
    try:
        with engine.connect() as conn:
            if not _ensure_operator_rules_table(conn):
                return []

            rows = (
                conn.execute(
                    _text(
                        """
                        SELECT rule_id, site_id, rule_type, rule_name, payload, source, status,
                               confidence, active, created_by, confirmed_by,
                               created_at, confirmed_at, updated_at
                        FROM operator_rules
                        WHERE site_id = :sid
                          AND (:active_only = FALSE OR active = TRUE)
                        ORDER BY
                            CASE status
                                WHEN 'confirmed' THEN 0
                                WHEN 'proposed' THEN 1
                                ELSE 2
                            END,
                            updated_at DESC,
                            created_at DESC
                        LIMIT :lim
                        """
                    ),
                    {
                        "sid": site_id,
                        "active_only": bool(active_only),
                        "lim": max(1, int(limit or 25)),
                    },
                )
                .mappings()
                .all()
            )
        results = [_row_to_operator_rule(row) for row in rows]
        if statuses:
            allowed = {str(status).strip().lower() for status in statuses if str(status).strip()}
            results = [rule for rule in results if rule["status"].lower() in allowed]
        return results
    except Exception as exc:
        logger.warning("list_operator_rules unavailable (non-fatal): %s", exc)
        return []


def confirm_operator_rule(
    site_id: str,
    rule_id: str = None,
    confirmed_by: str = "chat",
) -> Optional[dict]:
    try:
        with engine.connect() as conn:
            if not _ensure_operator_rules_table(conn):
                return None

            if rule_id:
                target = {"sid": site_id, "rid": rule_id}
                where_sql = "site_id = :sid AND rule_id = :rid"
            else:
                target_row = (
                    conn.execute(
                        _text(
                            """
                            SELECT rule_id
                            FROM operator_rules
                            WHERE site_id = :sid
                              AND status = 'proposed'
                              AND active = TRUE
                            ORDER BY updated_at DESC, created_at DESC
                            LIMIT 1
                            """
                        ),
                        {"sid": site_id},
                    )
                    .mappings()
                    .first()
                )
                if not target_row:
                    return None
                target = {"sid": site_id, "rid": str(target_row["rule_id"])}
                where_sql = "site_id = :sid AND rule_id = :rid"

            row = (
                conn.execute(
                    _text(
                        f"""
                        UPDATE operator_rules
                        SET status = 'confirmed',
                            confirmed_by = :confirmed_by,
                            confirmed_at = NOW(),
                            updated_at = NOW()
                        WHERE {where_sql}
                        RETURNING rule_id, site_id, rule_type, rule_name, payload, source, status,
                                  confidence, active, created_by, confirmed_by,
                                  created_at, confirmed_at, updated_at
                        """
                    ),
                    {
                        **target,
                        "confirmed_by": confirmed_by,
                    },
                )
                .mappings()
                .first()
            )
            conn.commit()
        return _row_to_operator_rule(row) if row else None
    except Exception as exc:
        logger.warning("confirm_operator_rule unavailable (non-fatal): %s", exc)
        return None


def reject_operator_rule(
    site_id: str,
    rule_id: str = None,
    rejected_by: str = "chat",
) -> Optional[dict]:
    try:
        with engine.connect() as conn:
            if not _ensure_operator_rules_table(conn):
                return None

            if rule_id:
                target = {"sid": site_id, "rid": rule_id}
                where_sql = "site_id = :sid AND rule_id = :rid"
            else:
                target_row = (
                    conn.execute(
                        _text(
                            """
                            SELECT rule_id
                            FROM operator_rules
                            WHERE site_id = :sid
                              AND status = 'proposed'
                              AND active = TRUE
                            ORDER BY updated_at DESC, created_at DESC
                            LIMIT 1
                            """
                        ),
                        {"sid": site_id},
                    )
                    .mappings()
                    .first()
                )
                if not target_row:
                    return None
                target = {"sid": site_id, "rid": str(target_row["rule_id"])}
                where_sql = "site_id = :sid AND rule_id = :rid"

            row = (
                conn.execute(
                    _text(
                        f"""
                        UPDATE operator_rules
                        SET status = 'rejected',
                            active = FALSE,
                            confirmed_by = :rejected_by,
                            updated_at = NOW()
                        WHERE {where_sql}
                        RETURNING rule_id, site_id, rule_type, rule_name, payload, source, status,
                                  confidence, active, created_by, confirmed_by,
                                  created_at, confirmed_at, updated_at
                        """
                    ),
                    {
                        **target,
                        "rejected_by": rejected_by,
                    },
                )
                .mappings()
                .first()
            )
            conn.commit()
        return _row_to_operator_rule(row) if row else None
    except Exception as exc:
        logger.warning("reject_operator_rule unavailable (non-fatal): %s", exc)
        return None


def store_inventory_receipt(
    site_id: str,
    inventory_item_id: str,
    quantity_units: float,
    received_at: datetime = None,
    unit_cost_cents: int = None,
    supplier_name: str = None,
    source: str = "xero",
    external_ref: str = None,
    raw_line_description: str = None,
) -> Optional[str]:
    """
    Store stock receipt movement (idempotent by external_ref).
    Returns receipt_id (or existing row id) when possible.
    """
    if not external_ref:
        raise ValueError("external_ref is required for idempotent receipts")

    try:
        with engine.connect() as conn:
            # Try insert; if already present, return existing id.
            inserted = (
                conn.execute(
                    _text(
                        """
                    INSERT INTO inventory_receipts
                        (site_id, inventory_item_id, received_at, quantity_units,
                         unit_cost_cents, supplier_name, source, external_ref, raw_line_description)
                    VALUES
                        (:sid, :iid, :ra, :qty, :cost, :supplier, :source, :eref, :raw)
                    ON CONFLICT (site_id, external_ref) DO NOTHING
                    RETURNING receipt_id
                    """
                    ),
                    {
                        "sid": site_id,
                        "iid": inventory_item_id,
                        "ra": received_at or datetime.utcnow(),
                        "qty": max(0.0, float(quantity_units or 0)),
                        "cost": int(unit_cost_cents) if unit_cost_cents is not None else None,
                        "supplier": supplier_name,
                        "source": (source or "xero").strip() or "xero",
                        "eref": external_ref,
                        "raw": raw_line_description,
                    },
                )
                .mappings()
                .first()
            )

            if inserted:
                conn.commit()
                return str(inserted["receipt_id"])

            existing = (
                conn.execute(
                    _text(
                        """
                    SELECT receipt_id
                    FROM inventory_receipts
                    WHERE site_id = :sid AND external_ref = :eref
                    LIMIT 1
                    """
                    ),
                    {"sid": site_id, "eref": external_ref},
                )
                .mappings()
                .first()
            )
            conn.commit()
        return str(existing["receipt_id"]) if existing else None
    except Exception as e:
        logger.warning("store_inventory_receipt unavailable (non-fatal): %s", e)
        return None


def _parse_modifier_tokens(raw_modifiers) -> list[str]:
    """
    Normalize order_item.modifiers payload into lowercase tokens.
    """
    data = raw_modifiers
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (ValueError, TypeError):
            data = [data]

    tokens = []
    if isinstance(data, list):
        for m in data:
            if isinstance(m, dict):
                name = m.get("name") or m.get("modifier_name") or m.get("label")
                if name:
                    tokens.append(str(name).lower())
            elif m is not None:
                tokens.append(str(m).lower())
    elif isinstance(data, dict):
        for value in data.values():
            if value is not None:
                tokens.append(str(value).lower())
    elif data is not None:
        tokens.append(str(data).lower())
    return tokens


def _to_naive_utc(dt_val: datetime) -> Optional[datetime]:
    if not isinstance(dt_val, datetime):
        return None
    if dt_val.tzinfo is not None:
        return dt_val.astimezone(timezone.utc).replace(tzinfo=None)
    return dt_val


def _terms_match(tokens: list[str], terms_csv: str, require: bool) -> bool:
    """
    CSV term matching helper used by inventory usage rules.
    require=True  => at least one term must match token text.
    require=False => no term may match token text.
    """
    terms = [t.strip().lower() for t in str(terms_csv or "").split(",") if t.strip()]
    if not terms:
        return True
    token_text = " ".join(tokens)
    matched = any(term in token_text for term in terms)
    return matched if require else (not matched)


_WEEKDAY_TO_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _normalize_inventory_text(raw: str | None) -> str:
    text = str(raw or "").strip().lower().replace("_", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _inventory_match_score(subject: str | None, item: dict) -> int:
    needle = _normalize_inventory_text(subject)
    if not needle:
        return 0

    candidates = [
        _normalize_inventory_text(item.get("item_name")),
        _normalize_inventory_text(item.get("score_key")),
    ]
    needle_tokens = set(needle.split())
    best = 0

    for candidate in candidates:
        if not candidate:
            continue
        candidate_tokens = set(candidate.split())
        if needle == candidate:
            best = max(best, 100)
            continue
        if needle in candidate or candidate in needle:
            best = max(best, 70)
            continue
        overlap = needle_tokens & candidate_tokens
        if not overlap:
            continue
        score = len(overlap) * 15
        if overlap == needle_tokens:
            score += 20
        best = max(best, score)

    return best


def _best_inventory_item_match(items: list[dict], subject: str | None) -> Optional[dict]:
    ranked = sorted(
        ((item, _inventory_match_score(subject, item)) for item in items),
        key=lambda pair: pair[1],
        reverse=True,
    )
    if not ranked or ranked[0][1] <= 0:
        return None
    return ranked[0][0]


def _build_virtual_inventory_usage_rules(
    items: list[dict],
    usage_rules: list[dict],
    operator_rules: list[dict],
) -> list[dict]:
    explicit_pairs = {
        (
            str(rule.get("inventory_item_id") or ""),
            _normalize_inventory_text(rule.get("trigger_item_name")),
        )
        for rule in usage_rules
    }
    virtual_rules: list[dict] = []
    seen_pairs = set(explicit_pairs)

    for rule in operator_rules:
        if rule.get("rule_type") != "recipe_definition":
            continue
        payload = rule.get("payload") or {}
        trigger = _normalize_inventory_text(payload.get("trigger_item_name"))
        if not trigger:
            continue
        for component in payload.get("components") or []:
            quantity = float(component.get("quantity") or 0)
            if quantity <= 0:
                continue
            matched_item = _best_inventory_item_match(items, component.get("item_name"))
            if not matched_item:
                continue
            pair = (str(matched_item.get("inventory_item_id") or ""), trigger)
            if pair in seen_pairs:
                continue
            virtual_rules.append(
                {
                    "rule_id": f"recipe:{rule.get('rule_id')}:{matched_item.get('inventory_item_id')}",
                    "inventory_item_id": str(matched_item.get("inventory_item_id") or ""),
                    "inventory_item_name": matched_item.get("item_name"),
                    "inventory_unit": matched_item.get("unit") or "units",
                    "trigger_item_name": trigger,
                    "required_modifier_terms": None,
                    "excluded_modifier_terms": None,
                    "units_per_sale": quantity,
                    "priority": 500,
                    "active": True,
                    "updated_at": rule.get("updated_at"),
                    "source": "recipe_definition",
                }
            )
            seen_pairs.add(pair)

    return virtual_rules


def _next_weekday_on_or_after(start_day: date, weekday_idx: int) -> date:
    delta = (weekday_idx - start_day.weekday()) % 7
    return start_day + timedelta(days=delta)


def _parse_schedule_time(raw_time: str | None):
    token = str(raw_time or "").strip()
    if not token:
        return None
    try:
        return datetime.strptime(token, "%H:%M").time()
    except ValueError:
        return None


def _matched_operator_rules_for_item(
    item: dict, operator_rules: list[dict], rule_type: str
) -> list[dict]:
    matched: list[tuple[int, dict]] = []
    for rule in operator_rules:
        if rule.get("rule_type") != rule_type:
            continue
        payload = rule.get("payload") or {}
        score = _inventory_match_score(payload.get("subject"), item)
        if score <= 0:
            continue
        matched.append((score, rule))
    return [rule for _score, rule in sorted(matched, key=lambda pair: pair[0], reverse=True)]


def _resolve_inventory_schedule_context(
    item: dict,
    operator_rules: list[dict],
    as_of: datetime,
    effective_on_hand: float | None,
    daily_usage_units: float,
) -> dict:
    order_rules = _matched_operator_rules_for_item(item, operator_rules, "ordering_schedule")
    delivery_rules = _matched_operator_rules_for_item(item, operator_rules, "delivery_schedule")

    candidate = None
    today = as_of.date()

    for rule in order_rules:
        payload = rule.get("payload") or {}
        delivery_idx = _WEEKDAY_TO_INDEX.get(str(payload.get("delivery_day") or "").lower())
        cutoff_idx = _WEEKDAY_TO_INDEX.get(str(payload.get("cutoff_day") or "").lower())
        cutoff_time = _parse_schedule_time(payload.get("cutoff_time"))
        if delivery_idx is None or cutoff_idx is None or cutoff_time is None:
            continue

        for days_ahead in range(0, 15):
            delivery_date = today + timedelta(days=days_ahead)
            if delivery_date.weekday() != delivery_idx:
                continue

            cutoff_date = delivery_date
            for days_back in range(0, 7):
                probe = delivery_date - timedelta(days=days_back)
                if probe.weekday() == cutoff_idx:
                    cutoff_date = probe
                    break

            cutoff_dt = datetime.combine(cutoff_date, cutoff_time)
            if as_of.date() > delivery_date:
                continue

            candidate = {
                "schedule_source": "ordering_schedule",
                "schedule_subject": payload.get("subject"),
                "next_delivery_date": delivery_date.isoformat(),
                "next_order_cutoff_at": cutoff_dt.isoformat(),
                "cutoff_passed": as_of > cutoff_dt,
            }
            break
        if candidate:
            break

    if candidate is None:
        delivery_candidates = []
        for rule in delivery_rules:
            payload = rule.get("payload") or {}
            for raw_day in payload.get("days") or []:
                weekday_idx = _WEEKDAY_TO_INDEX.get(str(raw_day or "").lower())
                if weekday_idx is None:
                    continue
                delivery_candidates.append(
                    (
                        _next_weekday_on_or_after(today, weekday_idx),
                        payload.get("subject"),
                    )
                )
        if delivery_candidates:
            delivery_date, subject = sorted(delivery_candidates, key=lambda pair: pair[0])[0]
            candidate = {
                "schedule_source": "delivery_schedule",
                "schedule_subject": subject,
                "next_delivery_date": delivery_date.isoformat(),
                "next_order_cutoff_at": None,
                "cutoff_passed": None,
            }

    if candidate is None:
        lead_time_days = max(0, int(item.get("lead_time_days") or 0))
        if lead_time_days > 0:
            next_delivery = today + timedelta(days=lead_time_days)
            candidate = {
                "schedule_source": "lead_time_proxy",
                "schedule_subject": item.get("item_name"),
                "next_delivery_date": next_delivery.isoformat(),
                "next_order_cutoff_at": None,
                "cutoff_passed": None,
            }

    if candidate is None:
        return {
            "schedule_source": None,
            "schedule_subject": None,
            "next_delivery_date": None,
            "next_order_cutoff_at": None,
            "days_until_next_delivery": None,
            "projected_on_hand_at_next_delivery": None,
            "stockout_before_next_delivery": False,
            "order_timing_status": "schedule_missing",
            "order_timing_note": "No confirmed delivery or ordering schedule for this item.",
        }

    next_delivery = date.fromisoformat(candidate["next_delivery_date"])
    days_until_next_delivery = max(0, (next_delivery - today).days)
    projected_on_hand = None
    stockout_before_next_delivery = False
    if effective_on_hand is not None:
        projected_on_hand = float(effective_on_hand) - (
            daily_usage_units * days_until_next_delivery
        )
        stockout_before_next_delivery = projected_on_hand <= 0

    order_timing_status = "monitor"
    order_timing_note = f"Next scheduled delivery is {candidate['next_delivery_date']}."

    if candidate["schedule_source"] == "ordering_schedule":
        cutoff_at = candidate["next_order_cutoff_at"]
        if stockout_before_next_delivery and candidate.get("cutoff_passed"):
            order_timing_status = "expedite"
            order_timing_note = (
                f"Projected to stock out before {candidate['next_delivery_date']}; "
                "the cutoff for that delivery has already passed."
            )
        elif stockout_before_next_delivery:
            order_timing_status = "order_now"
            order_timing_note = (
                f"Projected to stock out before {candidate['next_delivery_date']}; "
                f"order before {cutoff_at}."
            )
        elif not candidate.get("cutoff_passed"):
            order_timing_status = "before_cutoff"
            order_timing_note = (
                f"Order before {cutoff_at} for {candidate['next_delivery_date']} delivery."
            )
        else:
            order_timing_status = "delivery_pending"
            order_timing_note = f"Current cycle cutoff has passed; next scheduled delivery is {candidate['next_delivery_date']}."
    elif candidate["schedule_source"] == "delivery_schedule":
        if stockout_before_next_delivery:
            order_timing_status = "order_now"
            order_timing_note = f"Projected to stock out before the next delivery on {candidate['next_delivery_date']}."
    elif candidate["schedule_source"] == "lead_time_proxy":
        if stockout_before_next_delivery:
            order_timing_status = "order_now"
        else:
            order_timing_status = "lead_time_proxy"
        order_timing_note = (
            f"No confirmed schedule; using lead-time proxy to {candidate['next_delivery_date']}."
        )

    return {
        **candidate,
        "days_until_next_delivery": days_until_next_delivery,
        "projected_on_hand_at_next_delivery": (
            round(float(projected_on_hand), 3) if projected_on_hand is not None else None
        ),
        "stockout_before_next_delivery": stockout_before_next_delivery,
        "order_timing_status": order_timing_status,
        "order_timing_note": order_timing_note,
    }


def _coerce_positive_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _coerce_positive_int(value, default: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(default, number)


def _pluralize_order_unit(order_unit_name: str | None, count: int | None) -> str:
    base = str(order_unit_name or "pack").strip() or "pack"
    if count == 1 or base.endswith("s"):
        return base
    return f"{base}s"


def _get_xero_pack_profiles(site_id: str, items: list[dict]) -> dict[str, dict]:
    score_keys = {
        str(item.get("score_key") or "").strip()
        for item in items
        if str(item.get("score_key") or "").strip()
    }
    if not score_keys:
        return {}

    try:
        with engine.connect() as conn:
            if not _xero_table_exists(conn, "xero_line_mappings"):
                return {}

            rows = (
                conn.execute(
                    _text(
                        """
                        SELECT score_key, units_per_pack, source, status, confidence,
                               approved_at, updated_at, created_at
                        FROM xero_line_mappings
                        WHERE site_id = :sid
                          AND status = 'approved'
                          AND score_key IS NOT NULL
                          AND COALESCE(units_per_pack, 1) > 1
                        ORDER BY score_key ASC,
                                 approved_at DESC NULLS LAST,
                                 updated_at DESC NULLS LAST,
                                 created_at DESC
                        """
                    ),
                    {"sid": site_id},
                )
                .mappings()
                .all()
            )
    except Exception as exc:
        logger.info("Xero pack profile lookup unavailable (non-fatal): %s", exc)
        return {}

    profiles: dict[str, dict] = {}
    for row in rows:
        score_key = str(row.get("score_key") or "").strip()
        if score_key not in score_keys or score_key in profiles:
            continue
        profiles[score_key] = {
            "units_per_order": max(1.0, float(row.get("units_per_pack") or 1)),
            "source": "xero_mapping",
            "order_unit_name": "pack",
        }
    return profiles


def _resolve_inventory_order_profile(item: dict, xero_pack_profiles: dict[str, dict]) -> dict:
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    units_per_order = _coerce_positive_float(
        metadata.get("units_per_order")
        or metadata.get("order_pack_units")
        or metadata.get("pack_size_units")
        or metadata.get("units_per_pack")
    )
    order_unit_name = (
        str(
            metadata.get("order_unit_name")
            or metadata.get("pack_label")
            or metadata.get("purchase_unit")
            or ""
        ).strip()
        or None
    )
    supplier_name = (
        str(metadata.get("supplier_name") or metadata.get("preferred_supplier") or "").strip()
        or None
    )
    minimum_order_units = _coerce_positive_int(
        metadata.get("minimum_order_units")
        or metadata.get("minimum_order_quantity")
        or metadata.get("minimum_order_packs")
        or 1,
        default=1,
    )
    order_multiple_units = _coerce_positive_int(
        metadata.get("order_multiple_units")
        or metadata.get("order_multiple")
        or metadata.get("order_multiple_packs")
        or 1,
        default=1,
    )
    profile_source = "metadata" if units_per_order else None

    if units_per_order is None:
        score_key = str(item.get("score_key") or "").strip()
        xero_profile = xero_pack_profiles.get(score_key)
        if xero_profile:
            units_per_order = _coerce_positive_float(xero_profile.get("units_per_order"))
            order_unit_name = order_unit_name or xero_profile.get("order_unit_name") or "pack"
            profile_source = xero_profile.get("source") or "xero_mapping"

    return {
        "units_per_order": units_per_order,
        "order_unit_name": order_unit_name or "pack",
        "minimum_order_units": minimum_order_units,
        "order_multiple_units": order_multiple_units,
        "supplier_name": supplier_name,
        "order_profile_source": profile_source,
    }


def _build_inventory_purchase_recommendation(
    item: dict,
    recommended_reorder_units: float | None,
    order_profile: dict,
) -> dict:
    units_per_order = _coerce_positive_float(order_profile.get("units_per_order"))
    order_unit_name = order_profile.get("order_unit_name") or "pack"
    supplier_name = order_profile.get("supplier_name")

    if recommended_reorder_units is None or recommended_reorder_units <= 0:
        return {
            "recommended_order_count": None,
            "recommended_order_quantity_units": None,
            "recommended_order_note": None,
            "order_unit_name": order_unit_name,
            "units_per_order": units_per_order,
            "minimum_order_units": order_profile.get("minimum_order_units"),
            "order_multiple_units": order_profile.get("order_multiple_units"),
            "supplier_name": supplier_name,
            "order_profile_source": order_profile.get("order_profile_source"),
        }

    if not units_per_order:
        unit = item.get("unit") or "units"
        note = f"Order {float(recommended_reorder_units):.1f} {unit}."
        if supplier_name:
            note = f"{note[:-1]} from {supplier_name}."
        return {
            "recommended_order_count": None,
            "recommended_order_quantity_units": round(float(recommended_reorder_units), 3),
            "recommended_order_note": note,
            "order_unit_name": None,
            "units_per_order": None,
            "minimum_order_units": order_profile.get("minimum_order_units"),
            "order_multiple_units": order_profile.get("order_multiple_units"),
            "supplier_name": supplier_name,
            "order_profile_source": order_profile.get("order_profile_source"),
        }

    order_count = math.ceil(float(recommended_reorder_units) / units_per_order)
    order_count = max(
        order_count, _coerce_positive_int(order_profile.get("minimum_order_units"), 1)
    )
    multiple = _coerce_positive_int(order_profile.get("order_multiple_units"), 1)
    if order_count % multiple:
        order_count = int(math.ceil(order_count / multiple) * multiple)

    recommended_order_quantity_units = order_count * units_per_order
    order_label = _pluralize_order_unit(order_unit_name, order_count)
    unit = item.get("unit") or "units"
    note = f"Order {order_count} {order_label} " f"({recommended_order_quantity_units:.1f} {unit})."
    if supplier_name:
        note = f"{note[:-1]} from {supplier_name}."

    return {
        "recommended_order_count": int(order_count),
        "recommended_order_quantity_units": round(float(recommended_order_quantity_units), 3),
        "recommended_order_note": note,
        "order_unit_name": order_unit_name,
        "units_per_order": round(float(units_per_order), 3),
        "minimum_order_units": _coerce_positive_int(order_profile.get("minimum_order_units"), 1),
        "order_multiple_units": multiple,
        "supplier_name": supplier_name,
        "order_profile_source": order_profile.get("order_profile_source"),
    }


def get_inventory_alerts(
    site_id: str,
    lookback_days: int = 21,
    include_ok: bool = False,
) -> list[dict]:
    """
    Compute live inventory position and low-stock alerts.

    Formula per item:
      effective_on_hand =
        latest_physical_count
        + receipts_since_count
        - consumed_since_count (derived from order_items + usage rules)
    """
    items = list_inventory_items(site_id, active_only=True)
    if not items:
        return []

    now = datetime.utcnow()
    default_start = now - timedelta(days=max(1, int(lookback_days or 21)))
    operator_rules = list_operator_rules(
        site_id,
        statuses=["confirmed"],
        active_only=True,
        limit=200,
    )
    xero_pack_profiles = _get_xero_pack_profiles(site_id, items)

    item_by_id = {it["inventory_item_id"]: it for it in items}
    item_start: dict[str, datetime] = {}
    for it in items:
        counted_at = it.get("last_counted_at")
        if counted_at:
            try:
                parsed = datetime.fromisoformat(str(counted_at).replace("Z", "+00:00"))
                start_dt = _to_naive_utc(parsed) or default_start
            except ValueError:
                start_dt = default_start
        else:
            start_dt = default_start
        item_start[it["inventory_item_id"]] = start_dt

    global_start = min(item_start.values()) if item_start else default_start

    # Receipts since earliest relevant start.
    receipts_by_item: dict[str, float] = {item_id: 0.0 for item_id in item_by_id}
    try:
        with engine.connect() as conn:
            receipt_rows = (
                conn.execute(
                    _text(
                        """
                    SELECT inventory_item_id, received_at, quantity_units
                    FROM inventory_receipts
                    WHERE site_id = :sid
                      AND received_at >= :start_at
                      AND received_at <= :end_at
                    """
                    ),
                    {"sid": site_id, "start_at": global_start, "end_at": now},
                )
                .mappings()
                .all()
            )

        for r in receipt_rows:
            item_id = str(r["inventory_item_id"])
            if item_id not in item_by_id:
                continue
            received_at = _to_naive_utc(r.get("received_at"))
            if received_at and received_at < item_start[item_id]:
                continue
            receipts_by_item[item_id] += float(r.get("quantity_units") or 0)
    except Exception as e:
        logger.warning("Inventory receipts query unavailable (non-fatal): %s", e)

    # Usage since earliest relevant start (computed from rules + orders).
    usage_by_item: dict[str, float] = {item_id: 0.0 for item_id in item_by_id}
    usage_rule_sources: dict[str, set[str]] = {item_id: set() for item_id in item_by_id}
    rules = list_inventory_usage_rules(site_id, active_only=True)
    if operator_rules:
        rules = rules + _build_virtual_inventory_usage_rules(items, rules, operator_rules)
    if rules:
        rules_by_trigger: dict[str, list[dict]] = {}
        for rule in rules:
            trigger = (rule.get("trigger_item_name") or "").strip().lower()
            if not trigger:
                continue
            rules_by_trigger.setdefault(trigger, []).append(rule)

        if rules_by_trigger:
            try:
                with engine.connect() as conn:
                    order_rows = (
                        conn.execute(
                            _text(
                                """
                            SELECT item_name, quantity, modifiers, created_at
                            FROM order_items
                            WHERE site_id = :sid
                              AND created_at >= :start_at
                              AND created_at <= :end_at
                            """
                            ),
                            {"sid": site_id, "start_at": global_start, "end_at": now},
                        )
                        .mappings()
                        .all()
                    )
            except Exception as e:
                logger.warning("Inventory usage order query unavailable (non-fatal): %s", e)
                order_rows = []

            for row in order_rows:
                trigger_key = (row.get("item_name") or "").strip().lower()
                if not trigger_key:
                    continue
                candidates = rules_by_trigger.get(trigger_key) or []
                if not candidates:
                    continue

                created_at = _to_naive_utc(row.get("created_at")) or now
                qty = max(0, int(row.get("quantity") or 0))
                if qty <= 0:
                    continue
                tokens = _parse_modifier_tokens(row.get("modifiers"))

                for rule in candidates:
                    item_id = rule.get("inventory_item_id")
                    if item_id not in item_by_id:
                        continue
                    if created_at < item_start[item_id]:
                        continue
                    if not _terms_match(tokens, rule.get("required_modifier_terms"), require=True):
                        continue
                    if not _terms_match(tokens, rule.get("excluded_modifier_terms"), require=False):
                        continue

                    usage_by_item[item_id] += qty * float(rule.get("units_per_sale") or 0)
                    usage_rule_sources[item_id].add(rule.get("source") or "inventory_usage_rule")

    # Build positions and alert payloads.
    alerts = []
    for item_id, item in item_by_id.items():
        base_count = item.get("last_count_on_hand")
        reorder_point = float(item.get("reorder_point") or 0)
        par_level = item.get("par_level")
        lead_time_days = int(item.get("lead_time_days") or 0)
        start_dt = item_start[item_id]
        days_observed = max(1, (now.date() - start_dt.date()).days + 1)

        received_units = float(receipts_by_item.get(item_id) or 0)
        consumed_units = float(usage_by_item.get(item_id) or 0)

        effective_on_hand = None
        if base_count is not None:
            effective_on_hand = float(base_count) + received_units - consumed_units

        daily_usage_units = consumed_units / days_observed if consumed_units > 0 else 0.0
        days_remaining = (
            (effective_on_hand / daily_usage_units)
            if effective_on_hand is not None and daily_usage_units > 0
            else None
        )
        schedule_context = _resolve_inventory_schedule_context(
            item=item,
            operator_rules=operator_rules,
            as_of=now,
            effective_on_hand=effective_on_hand,
            daily_usage_units=daily_usage_units,
        )

        if base_count is None:
            status = "needs_count"
            severity = "warning"
        elif effective_on_hand <= 0:
            status = "out_of_stock"
            severity = "warning"
        elif effective_on_hand <= reorder_point:
            status = "low_stock"
            severity = "warning"
        elif days_remaining is not None and days_remaining <= max(1, lead_time_days):
            status = "reorder_soon"
            severity = "opportunity"
        else:
            status = "ok"
            severity = "info"

        if status not in {"needs_count", "out_of_stock"} and schedule_context.get(
            "stockout_before_next_delivery"
        ):
            status = "stockout_before_delivery"
            severity = "warning"

        target_level = float(par_level) if par_level is not None else max(reorder_point, 0.0)
        reorder_basis = effective_on_hand
        if schedule_context.get("projected_on_hand_at_next_delivery") is not None:
            reorder_basis = float(schedule_context["projected_on_hand_at_next_delivery"])
        recommended_reorder_units = (
            max(0.0, target_level - float(reorder_basis))
            if reorder_basis is not None and target_level > 0
            else None
        )
        order_profile = _resolve_inventory_order_profile(item, xero_pack_profiles)
        purchase_recommendation = _build_inventory_purchase_recommendation(
            item=item,
            recommended_reorder_units=recommended_reorder_units,
            order_profile=order_profile,
        )

        payload = {
            "inventory_item_id": item_id,
            "item_name": item.get("item_name"),
            "score_key": item.get("score_key"),
            "unit": item.get("unit") or "units",
            "status": status,
            "severity": severity,
            "effective_on_hand": (
                round(float(effective_on_hand), 3) if effective_on_hand is not None else None
            ),
            "last_count_on_hand": (round(float(base_count), 3) if base_count is not None else None),
            "receipts_since_count": round(received_units, 3),
            "consumed_since_count": round(consumed_units, 3),
            "daily_usage_units": round(daily_usage_units, 3),
            "days_remaining": (
                round(float(days_remaining), 2) if days_remaining is not None else None
            ),
            "reorder_point": reorder_point,
            "par_level": float(par_level) if par_level is not None else None,
            "lead_time_days": lead_time_days,
            "recommended_reorder_units": (
                round(float(recommended_reorder_units), 3)
                if recommended_reorder_units is not None
                else None
            ),
            "last_counted_at": item.get("last_counted_at"),
            "window_start": start_dt.isoformat(),
            "window_days": days_observed,
            "usage_rule_sources": sorted(usage_rule_sources.get(item_id) or []),
            "schedule_source": schedule_context.get("schedule_source"),
            "schedule_subject": schedule_context.get("schedule_subject"),
            "matched_schedule_subject": schedule_context.get("schedule_subject"),
            "next_delivery_date": schedule_context.get("next_delivery_date"),
            "next_order_cutoff_at": schedule_context.get("next_order_cutoff_at"),
            "days_until_next_delivery": schedule_context.get("days_until_next_delivery"),
            "projected_on_hand_at_next_delivery": schedule_context.get(
                "projected_on_hand_at_next_delivery"
            ),
            "stockout_before_next_delivery": schedule_context.get("stockout_before_next_delivery"),
            "order_timing_status": schedule_context.get("order_timing_status"),
            "order_timing_note": schedule_context.get("order_timing_note"),
            "units_per_order": purchase_recommendation.get("units_per_order"),
            "order_unit_name": purchase_recommendation.get("order_unit_name"),
            "minimum_order_units": purchase_recommendation.get("minimum_order_units"),
            "order_multiple_units": purchase_recommendation.get("order_multiple_units"),
            "supplier_name": purchase_recommendation.get("supplier_name"),
            "order_profile_source": purchase_recommendation.get("order_profile_source"),
            "recommended_order_count": purchase_recommendation.get("recommended_order_count"),
            "recommended_order_quantity_units": purchase_recommendation.get(
                "recommended_order_quantity_units"
            ),
            "recommended_order_note": purchase_recommendation.get("recommended_order_note"),
        }
        if include_ok or status != "ok":
            alerts.append(payload)

    severity_rank = {"warning": 0, "opportunity": 1, "info": 2}
    alerts.sort(
        key=lambda a: (
            severity_rank.get(a.get("severity"), 9),
            (
                a.get("days_remaining")
                if a.get("days_remaining") is not None
                else (
                    a.get("days_until_next_delivery")
                    if a.get("days_until_next_delivery") is not None
                    else 9999
                )
            ),
            a.get("item_name") or "",
        )
    )
    return alerts


def bootstrap_default_inventory_rules(site_id: str) -> dict:
    """
    Bootstrap a practical default consumables model for cafe beverages:
      - 12oz cup + 90mm lid per hot coffee sold
      - 20g coffee beans per espresso-based coffee
      - 355ml milk per milk-based drink with modifier-based milk variants
    """
    items = [
        {
            "item_name": "12oz cups",
            "score_key": "cup_12oz",
            "unit": "each",
            "reorder_point": 300,
            "par_level": 1200,
            "lead_time_days": 2,
        },
        {
            "item_name": "90mm lids",
            "score_key": "lid_90mm",
            "unit": "each",
            "reorder_point": 300,
            "par_level": 1200,
            "lead_time_days": 2,
        },
        {
            "item_name": "coffee beans",
            "score_key": "coffee_beans_g",
            "unit": "g",
            "reorder_point": 2500,
            "par_level": 12000,
            "lead_time_days": 3,
        },
        {
            "item_name": "full cream milk",
            "score_key": "full_cream_milk_ml",
            "unit": "ml",
            "reorder_point": 5000,
            "par_level": 30000,
            "lead_time_days": 2,
        },
        {
            "item_name": "skim milk",
            "score_key": "skim_milk_ml",
            "unit": "ml",
            "reorder_point": 2000,
            "par_level": 12000,
            "lead_time_days": 2,
        },
        {
            "item_name": "almond milk",
            "score_key": "almond_milk_ml",
            "unit": "ml",
            "reorder_point": 2000,
            "par_level": 12000,
            "lead_time_days": 2,
        },
        {
            "item_name": "soy milk",
            "score_key": "soy_milk_ml",
            "unit": "ml",
            "reorder_point": 2000,
            "par_level": 12000,
            "lead_time_days": 2,
        },
        {
            "item_name": "oat milk",
            "score_key": "oat_milk_ml",
            "unit": "ml",
            "reorder_point": 4000,
            "par_level": 20000,
            "lead_time_days": 2,
        },
    ]

    item_ids: dict[str, str] = {}
    for spec in items:
        item_id = upsert_inventory_item(
            site_id=site_id,
            item_name=spec["item_name"],
            score_key=spec["score_key"],
            unit=spec["unit"],
            reorder_point=spec["reorder_point"],
            par_level=spec["par_level"],
            lead_time_days=spec["lead_time_days"],
            active=True,
            metadata={"bootstrap": "coffee_defaults_v1"},
        )
        item_ids[spec["score_key"]] = item_id

    # Trigger names should match order_items.item_name (case-insensitive).
    hot_12oz = ["latte", "cappuccino", "flat white", "mocha"]
    espresso_based = ["espresso", "long black", "latte", "cappuccino", "flat white", "mocha"]
    milk_based = ["latte", "cappuccino", "flat white", "mocha"]

    rules_count = 0

    for trigger in hot_12oz:
        upsert_inventory_usage_rule(
            site_id=site_id,
            inventory_item_id=item_ids["cup_12oz"],
            trigger_item_name=trigger,
            units_per_sale=1,
            priority=10,
            active=True,
        )
        rules_count += 1
        upsert_inventory_usage_rule(
            site_id=site_id,
            inventory_item_id=item_ids["lid_90mm"],
            trigger_item_name=trigger,
            units_per_sale=1,
            priority=10,
            active=True,
        )
        rules_count += 1

    for trigger in espresso_based:
        upsert_inventory_usage_rule(
            site_id=site_id,
            inventory_item_id=item_ids["coffee_beans_g"],
            trigger_item_name=trigger,
            units_per_sale=20,
            priority=20,
            active=True,
        )
        rules_count += 1

    for trigger in milk_based:
        # Default milk allocation: full cream unless alt modifier present.
        upsert_inventory_usage_rule(
            site_id=site_id,
            inventory_item_id=item_ids["full_cream_milk_ml"],
            trigger_item_name=trigger,
            units_per_sale=355,
            excluded_modifier_terms="oat,soy,almond,skim",
            priority=30,
            active=True,
        )
        rules_count += 1

        upsert_inventory_usage_rule(
            site_id=site_id,
            inventory_item_id=item_ids["oat_milk_ml"],
            trigger_item_name=trigger,
            units_per_sale=355,
            required_modifier_terms="oat",
            priority=31,
            active=True,
        )
        rules_count += 1
        upsert_inventory_usage_rule(
            site_id=site_id,
            inventory_item_id=item_ids["soy_milk_ml"],
            trigger_item_name=trigger,
            units_per_sale=355,
            required_modifier_terms="soy",
            priority=31,
            active=True,
        )
        rules_count += 1
        upsert_inventory_usage_rule(
            site_id=site_id,
            inventory_item_id=item_ids["almond_milk_ml"],
            trigger_item_name=trigger,
            units_per_sale=355,
            required_modifier_terms="almond",
            priority=31,
            active=True,
        )
        rules_count += 1
        upsert_inventory_usage_rule(
            site_id=site_id,
            inventory_item_id=item_ids["skim_milk_ml"],
            trigger_item_name=trigger,
            units_per_sale=355,
            required_modifier_terms="skim",
            priority=31,
            active=True,
        )
        rules_count += 1

    return {
        "items_upserted": len(item_ids),
        "rules_upserted": rules_count,
        "inventory_item_ids_by_score_key": item_ids,
    }


# ============================================================
# Daily Profitability
# ============================================================


def _ensure_daily_profitability_quality_columns(conn) -> None:
    """Backwards-safe migration for labor quality metadata columns."""
    try:
        conn.execute(
            _text(
                "ALTER TABLE daily_profitability "
                "ADD COLUMN IF NOT EXISTS labor_data_quality TEXT"
            )
        )
        conn.execute(
            _text(
                "ALTER TABLE daily_profitability "
                "ADD COLUMN IF NOT EXISTS labor_data_issues JSONB"
            )
        )
    except Exception as exc:  # pragma: no cover - depends on DB privileges
        # Runtime should remain read-capable even when app role cannot run DDL.
        try:
            conn.rollback()
        except Exception:
            pass
        logger.info(
            "Skipping daily_profitability quality-column ensure (non-fatal): %s",
            exc,
        )


def store_daily_profitability(site_id: str, profit_date: date, metrics: dict) -> None:
    """Upsert a daily profitability record."""
    with engine.connect() as conn:
        _ensure_daily_profitability_quality_columns(conn)
        params = {
            "sid": site_id,
            "pd": profit_date,
            "rev": metrics["revenue_cents"],
            "labor": metrics["labor_cost_cents"],
            "cogs": metrics.get("cogs_cents"),
            "gross": metrics.get("gross_profit_cents"),
            "net": metrics.get("net_profit_cents"),
            "orders": metrics.get("order_count"),
            "items": metrics.get("item_count"),
            "drinks": metrics.get("drink_count"),
            "hours": metrics.get("labor_hours"),
            "rev_hr": metrics.get("revenue_per_labor_hour"),
            "cpd": metrics.get("cost_per_drink"),
            "labor_pct": metrics.get("labor_pct"),
            "ldq": metrics.get("labor_data_quality"),
            "ldi": _json_dumps(metrics.get("labor_data_issues", [])),
        }

        try:
            conn.execute(
                _text(
                    """
                    INSERT INTO daily_profitability
                        (site_id, profit_date, revenue_cents, labor_cost_cents,
                         cogs_cents, gross_profit_cents, net_profit_cents,
                         order_count, item_count, drink_count, labor_hours,
                         revenue_per_labor_hour, cost_per_drink, labor_pct,
                         labor_data_quality, labor_data_issues)
                    VALUES
                        (:sid, :pd, :rev, :labor, :cogs, :gross, :net,
                         :orders, :items, :drinks, :hours,
                         :rev_hr, :cpd, :labor_pct, :ldq, :ldi)
                    ON CONFLICT (site_id, profit_date) DO UPDATE SET
                        revenue_cents = EXCLUDED.revenue_cents,
                        labor_cost_cents = EXCLUDED.labor_cost_cents,
                        cogs_cents = EXCLUDED.cogs_cents,
                        gross_profit_cents = EXCLUDED.gross_profit_cents,
                        net_profit_cents = EXCLUDED.net_profit_cents,
                        order_count = EXCLUDED.order_count,
                        item_count = EXCLUDED.item_count,
                        drink_count = EXCLUDED.drink_count,
                        labor_hours = EXCLUDED.labor_hours,
                        revenue_per_labor_hour = EXCLUDED.revenue_per_labor_hour,
                        cost_per_drink = EXCLUDED.cost_per_drink,
                        labor_pct = EXCLUDED.labor_pct,
                        labor_data_quality = EXCLUDED.labor_data_quality,
                        labor_data_issues = EXCLUDED.labor_data_issues,
                        computed_at = NOW()
                """
                ),
                params,
            )
            conn.commit()
        except Exception as exc:  # pragma: no cover - depends on DB schema state
            try:
                conn.rollback()
            except Exception:
                pass
            logger.info(
                "daily_profitability quality columns unavailable on write (non-fatal): %s",
                exc,
            )
            conn.execute(
                _text(
                    """
                    INSERT INTO daily_profitability
                        (site_id, profit_date, revenue_cents, labor_cost_cents,
                         cogs_cents, gross_profit_cents, net_profit_cents,
                         order_count, item_count, drink_count, labor_hours,
                         revenue_per_labor_hour, cost_per_drink, labor_pct)
                    VALUES
                        (:sid, :pd, :rev, :labor, :cogs, :gross, :net,
                         :orders, :items, :drinks, :hours,
                         :rev_hr, :cpd, :labor_pct)
                    ON CONFLICT (site_id, profit_date) DO UPDATE SET
                        revenue_cents = EXCLUDED.revenue_cents,
                        labor_cost_cents = EXCLUDED.labor_cost_cents,
                        cogs_cents = EXCLUDED.cogs_cents,
                        gross_profit_cents = EXCLUDED.gross_profit_cents,
                        net_profit_cents = EXCLUDED.net_profit_cents,
                        order_count = EXCLUDED.order_count,
                        item_count = EXCLUDED.item_count,
                        drink_count = EXCLUDED.drink_count,
                        labor_hours = EXCLUDED.labor_hours,
                        revenue_per_labor_hour = EXCLUDED.revenue_per_labor_hour,
                        cost_per_drink = EXCLUDED.cost_per_drink,
                        labor_pct = EXCLUDED.labor_pct,
                        computed_at = NOW()
                """
                ),
                params,
            )
            conn.commit()

    logger.info(
        "Stored daily profitability for %s: rev=$%.2f, net=$%.2f",
        profit_date,
        metrics["revenue_cents"] / 100,
        (metrics.get("net_profit_cents") or 0) / 100,
    )


def get_daily_profitability(site_id: str, start_date: date, end_date: date) -> list[dict]:
    """Retrieve daily P&L records for a date range."""
    with engine.connect() as conn:
        _ensure_daily_profitability_quality_columns(conn)
        try:
            result = conn.execute(
                _text(
                    """
                    SELECT profit_date, revenue_cents, labor_cost_cents,
                           cogs_cents, gross_profit_cents, net_profit_cents,
                           order_count, item_count, drink_count, labor_hours,
                           revenue_per_labor_hour, cost_per_drink, labor_pct,
                           labor_data_quality, labor_data_issues
                    FROM daily_profitability
                    WHERE site_id = :sid AND profit_date BETWEEN :s AND :e
                    ORDER BY profit_date
                """
                ),
                {"sid": site_id, "s": start_date, "e": end_date},
            )
            include_quality = True
        except Exception as exc:  # pragma: no cover - depends on DB schema state
            try:
                conn.rollback()
            except Exception:
                pass
            logger.info(
                "daily_profitability quality columns unavailable (non-fatal): %s",
                exc,
            )
            result = conn.execute(
                _text(
                    """
                    SELECT profit_date, revenue_cents, labor_cost_cents,
                           cogs_cents, gross_profit_cents, net_profit_cents,
                           order_count, item_count, drink_count, labor_hours,
                           revenue_per_labor_hour, cost_per_drink, labor_pct
                    FROM daily_profitability
                    WHERE site_id = :sid AND profit_date BETWEEN :s AND :e
                    ORDER BY profit_date
                """
                ),
                {"sid": site_id, "s": start_date, "e": end_date},
            )
            include_quality = False
        return [
            {
                "date": str(row[0]),
                "revenue_cents": int(row[1]),
                "labor_cost_cents": int(row[2]),
                "cogs_cents": int(row[3]) if row[3] is not None else None,
                "gross_profit_cents": int(row[4]) if row[4] is not None else None,
                "net_profit_cents": int(row[5]) if row[5] is not None else None,
                "order_count": int(row[6]) if row[6] is not None else None,
                "item_count": int(row[7]) if row[7] is not None else None,
                "drink_count": int(row[8]) if row[8] is not None else None,
                "labor_hours": float(row[9]) if row[9] is not None else None,
                "revenue_per_labor_hour": int(row[10]) if row[10] is not None else None,
                "cost_per_drink": int(row[11]) if row[11] is not None else None,
                "labor_pct": float(row[12]) if row[12] is not None else None,
                "labor_data_quality": row[13] if include_quality and len(row) > 13 else None,
                "labor_data_issues": row[14] if include_quality and len(row) > 14 else None,
            }
            for row in result
        ]


def _json_dumps(obj):
    return json.dumps(obj, cls=_JSONEncoder)


def _text(sql: str):
    """Create a SQLAlchemy text object for raw SQL execution."""
    from sqlalchemy import text

    return text(sql)


# ============================================================
# Documents
# ============================================================


def store_document(
    site_id: str,
    filename: str,
    mime_type: str,
    file_size_bytes: int,
    storage_path: str,
) -> str:
    """Store document metadata. Returns the document_id."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "INSERT INTO documents "
                "(site_id, filename, mime_type, file_size_bytes, storage_path) "
                "VALUES (:sid, :fn, :mt, :fs, :sp) "
                "RETURNING document_id"
            ),
            {
                "sid": site_id,
                "fn": filename,
                "mt": mime_type,
                "fs": file_size_bytes,
                "sp": storage_path,
            },
        )
        doc_id = str(result.scalar())
        conn.commit()

    logger.info("Stored document %s: %s (%s)", doc_id, filename, mime_type)
    return doc_id


def get_document(document_id: str) -> Optional[dict]:
    """Retrieve a document by its UUID."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "SELECT document_id, site_id, filename, mime_type, "
                "file_size_bytes, storage_path, document_type, "
                "extracted_data, extraction_summary, items_updated, "
                "uploaded_at, processed_at "
                "FROM documents WHERE document_id = :did"
            ),
            {"did": document_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None


def update_document_extraction(
    document_id: str,
    document_type: str,
    extracted_data: dict,
    extraction_summary: str,
    items_updated: list,
) -> None:
    """Update a document with extraction results."""
    with engine.connect() as conn:
        conn.execute(
            _text(
                "UPDATE documents SET "
                "document_type = :dt, extracted_data = :ed, "
                "extraction_summary = :es, items_updated = :iu, "
                "processed_at = NOW() "
                "WHERE document_id = :did"
            ),
            {
                "did": document_id,
                "dt": document_type,
                "ed": _json_dumps(extracted_data),
                "es": extraction_summary,
                "iu": _json_dumps(items_updated),
            },
        )
        conn.commit()


def get_recent_documents(site_id: str, limit: int = 5) -> list[dict]:
    """Get recent documents for a site."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "SELECT document_id, filename, mime_type, document_type, "
                "extraction_summary, uploaded_at, processed_at "
                "FROM documents "
                "WHERE site_id = :sid "
                "ORDER BY uploaded_at DESC LIMIT :lim"
            ),
            {"sid": site_id, "lim": limit},
        )
        return [dict(row) for row in result.mappings()]


# ============================================================
# Special Events (store + query)
# ============================================================


def store_special_event(
    site_id: str,
    name: str,
    event_date: date,
    event_type: str = "one_time",
    impact: float = None,
) -> str:
    """Store a special event / closure. Returns event_id."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "INSERT INTO special_events "
                "(site_id, event_name, event_date, event_type, recurrence, historical_impact) "
                "VALUES (:sid, :name, :ed, :et, 'one_time', :impact) "
                "RETURNING event_id"
            ),
            {
                "sid": site_id,
                "name": name,
                "ed": event_date,
                "et": event_type,
                "impact": impact,
            },
        )
        event_id = str(result.scalar())
        conn.commit()

    logger.info("Stored special event %s: %s on %s", event_id, name, event_date)
    return event_id


def get_events_range(site_id: str, start: date, end: date) -> list[dict]:
    """Get all events in a date range."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "SELECT event_id, event_name, event_date, event_type, "
                "recurrence, historical_impact "
                "FROM special_events "
                "WHERE site_id = :sid AND event_date BETWEEN :s AND :e "
                "ORDER BY event_date"
            ),
            {"sid": site_id, "s": start, "e": end},
        )
        return [dict(row) for row in result.mappings()]


# ============================================================
# Item Costs (COGS) — with source tracking
# ============================================================


def upsert_item_cost(
    site_id: str,
    score_key: str,
    category: str,
    cost_cents: int,
    description: str = None,
    source: str = "document",
) -> None:
    """Insert or update an item cost, tracking source."""
    with engine.connect() as conn:
        conn.execute(
            _text(
                "INSERT INTO item_costs "
                "(site_id, score_key, category, cost_cents, description, source, updated_at) "
                "VALUES (:sid, :sk, :cat, :cost, :desc, :src, NOW()) "
                "ON CONFLICT (site_id, score_key) DO UPDATE SET "
                "cost_cents = EXCLUDED.cost_cents, "
                "description = EXCLUDED.description, "
                "source = EXCLUDED.source, "
                "updated_at = NOW()"
            ),
            {
                "sid": site_id,
                "sk": score_key,
                "cat": category,
                "cost": cost_cents,
                "desc": description,
                "src": source,
            },
        )
        conn.commit()

    logger.info("Upserted item cost %s: %d cents (source=%s)", score_key, cost_cents, source)


def has_real_cogs(site_id: str) -> bool:
    """Check if any item costs have been updated from real sources (not defaults)."""
    with engine.connect() as conn:
        count = conn.execute(
            _text(
                "SELECT COUNT(*) FROM item_costs "
                "WHERE site_id = :sid AND source IN ('document', 'xero')"
            ),
            {"sid": site_id},
        ).scalar()
        return (count or 0) > 0


def get_item_costs_detailed(site_id: str) -> list[dict]:
    """Return full item cost records including source and timestamps."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "SELECT score_key, category, cost_cents, description, source, updated_at "
                "FROM item_costs WHERE site_id = :sid "
                "ORDER BY source, score_key"
            ),
            {"sid": site_id},
        )
        return [
            {
                "score_key": row[0],
                "category": row[1],
                "cost_cents": int(row[2]),
                "description": row[3],
                "source": row[4] or "default",
                "updated_at": str(row[5]) if row[5] else None,
            }
            for row in result
        ]


def get_cogs_source_summary(site_id: str) -> dict:
    """Lightweight COGS source aggregation: count and last update per source."""
    with engine.connect() as conn:
        rows = (
            conn.execute(
                _text(
                    "SELECT COALESCE(source, 'default') AS src, COUNT(*) AS cnt, "
                    "MAX(updated_at) AS last_updated "
                    "FROM item_costs WHERE site_id = :sid "
                    "GROUP BY COALESCE(source, 'default')"
                ),
                {"sid": site_id},
            )
            .mappings()
            .all()
        )

    return {
        row["src"]: {
            "count": int(row["cnt"]),
            "last_updated": str(row["last_updated"])[:10] if row["last_updated"] else None,
        }
        for row in rows
    }


def get_profitability_correlations(site_id: str, days: int = 28) -> dict:
    """
    Three-way profitability correlations by day-of-week.

    Joins daily_profitability + deputy_rosters to compute per-DOW:
    avg revenue, labor, COGS, net profit, staff count, profit/staff, rev/$labor.
    """
    with engine.connect() as conn:
        rows = (
            conn.execute(
                _text(
                    """
                SELECT
                    EXTRACT(DOW FROM dp.profit_date)::int AS dow,
                    TRIM(TO_CHAR(dp.profit_date, 'Day')) AS day_name,
                    AVG(dp.revenue_cents) AS avg_revenue_cents,
                    AVG(dp.labor_cost_cents) AS avg_labor_cents,
                    AVG(COALESCE(dp.cogs_cents, 0)) AS avg_cogs_cents,
                    AVG(COALESCE(dp.net_profit_cents, 0)) AS avg_net_profit_cents,
                    AVG(dr_staff.staff_count) AS avg_staff_count
                FROM daily_profitability dp
                LEFT JOIN LATERAL (
                    SELECT COUNT(DISTINCT COALESCE(dr.employee_id::text, dr.employee_name, dr.deputy_id::text)) AS staff_count
                    FROM deputy_rosters dr
                    WHERE dr.site_id = dp.site_id AND dr.shift_date = dp.profit_date
                ) dr_staff ON TRUE
                WHERE dp.site_id = :sid
                  AND dp.profit_date >= CURRENT_DATE - :days
                GROUP BY EXTRACT(DOW FROM dp.profit_date), TRIM(TO_CHAR(dp.profit_date, 'Day'))
                ORDER BY EXTRACT(DOW FROM dp.profit_date)
                """
                ),
                {"sid": site_id, "days": days},
            )
            .mappings()
            .all()
        )

    by_dow = []
    for r in rows:
        avg_rev = int(float(r["avg_revenue_cents"] or 0))
        avg_labor = int(float(r["avg_labor_cents"] or 0))
        avg_cogs = int(float(r["avg_cogs_cents"] or 0))
        avg_net = int(float(r["avg_net_profit_cents"] or 0))
        avg_staff = round(float(r["avg_staff_count"] or 0), 1)

        profit_per_staff = round(avg_net / avg_staff) if avg_staff > 0 else None
        rev_per_labor_dollar = round(avg_rev / avg_labor, 2) if avg_labor > 0 else None

        by_dow.append(
            {
                "dow": int(r["dow"]),
                "day_name": r["day_name"],
                "avg_revenue_cents": avg_rev,
                "avg_labor_cents": avg_labor,
                "avg_cogs_cents": avg_cogs,
                "avg_net_profit_cents": avg_net,
                "avg_staff_count": avg_staff,
                "profit_per_staff_cents": profit_per_staff,
                "rev_per_labor_dollar": rev_per_labor_dollar,
            }
        )

    # Optimal staffing: for each DOW, find staff count that maximizes profit/staff
    optimal_staffing = []
    try:
        staff_rows = (
            conn.execute(
                _text(
                    """
                SELECT
                    EXTRACT(DOW FROM dp.profit_date)::int AS dow,
                    TRIM(TO_CHAR(dp.profit_date, 'Day')) AS day_name,
                    dr_staff.staff_count,
                    AVG(COALESCE(dp.net_profit_cents, 0)) AS avg_profit,
                    COUNT(*) AS sample_size
                FROM daily_profitability dp
                LEFT JOIN LATERAL (
                    SELECT COUNT(DISTINCT COALESCE(dr.employee_id::text, dr.employee_name, dr.deputy_id::text)) AS staff_count
                    FROM deputy_rosters dr
                    WHERE dr.site_id = dp.site_id AND dr.shift_date = dp.profit_date
                ) dr_staff ON TRUE
                WHERE dp.site_id = :sid
                  AND dp.profit_date >= CURRENT_DATE - :days
                  AND dr_staff.staff_count > 0
                GROUP BY EXTRACT(DOW FROM dp.profit_date), TRIM(TO_CHAR(dp.profit_date, 'Day')), dr_staff.staff_count
                HAVING COUNT(*) >= 2
                ORDER BY EXTRACT(DOW FROM dp.profit_date), avg_profit DESC
                """
                ),
                {"sid": site_id, "days": days},
            )
            .mappings()
            .all()
        )

        # For each DOW, pick the staff count with best profit/staff
        seen_dow = set()
        for sr in staff_rows:
            dow_val = int(sr["dow"])
            if dow_val in seen_dow:
                continue
            staff_count = int(sr["staff_count"])
            avg_profit = int(float(sr["avg_profit"] or 0))
            if staff_count > 0:
                optimal_staffing.append(
                    {
                        "dow": dow_val,
                        "day_name": sr["day_name"],
                        "optimal_staff": staff_count,
                        "profit_at_optimal": avg_profit,
                        "profit_per_staff": round(avg_profit / staff_count),
                    }
                )
                seen_dow.add(dow_val)
    except Exception:
        logger.warning("Optimal staffing query failed (non-fatal)")

    return {
        "by_dow": by_dow,
        "optimal_staffing": optimal_staffing,
    }


# ============================================================
# Xero Tokens & Line Mappings
# ============================================================


def store_xero_tokens(
    site_id: str,
    tenant_id: str,
    access_token: str,
    refresh_token: str,
    expires_at: datetime,
    scope: str = None,
) -> None:
    """Upsert OAuth2 tokens for a Xero connection (encrypted at rest)."""
    if not token_encryption_ready():
        raise TokenEncryptionError(
            "AUTOPILOT_TOKEN_ENC_KEY missing/invalid or cryptography unavailable."
        )

    encrypted_access = encrypt_secret(access_token)
    encrypted_refresh = encrypt_secret(refresh_token)

    with engine.connect() as conn:
        conn.execute(
            _text(
                "INSERT INTO xero_tokens "
                "(site_id, tenant_id, access_token, refresh_token, expires_at, scope) "
                "VALUES (:sid, :tid, :at, :rt, :ea, :sc) "
                "ON CONFLICT (site_id) DO UPDATE SET "
                "tenant_id = EXCLUDED.tenant_id, "
                "access_token = EXCLUDED.access_token, "
                "refresh_token = EXCLUDED.refresh_token, "
                "expires_at = EXCLUDED.expires_at, "
                "scope = EXCLUDED.scope, "
                "updated_at = NOW()"
            ),
            {
                "sid": site_id,
                "tid": tenant_id,
                "at": encrypted_access,
                "rt": encrypted_refresh,
                "ea": expires_at,
                "sc": scope,
            },
        )
        conn.commit()

    logger.info("Stored Xero tokens for site %s (tenant %s)", site_id, tenant_id)


def _ensure_xero_oauth_states_table(conn) -> None:
    """Create OAuth state table lazily for backward compatibility on existing DBs."""
    conn.execute(
        _text(
            """
            CREATE TABLE IF NOT EXISTS xero_oauth_states (
                state TEXT PRIMARY KEY,
                site_id UUID NOT NULL REFERENCES sites(site_id),
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
    )
    conn.execute(
        _text(
            """
            CREATE INDEX IF NOT EXISTS idx_xero_oauth_states_expires
            ON xero_oauth_states(expires_at)
            """
        )
    )


def store_xero_oauth_state(site_id: str, state: str, expires_at: datetime) -> None:
    """Persist OAuth state so callback validation survives app restarts."""
    with engine.connect() as conn:
        _ensure_xero_oauth_states_table(conn)
        conn.execute(
            _text(
                """
                INSERT INTO xero_oauth_states (state, site_id, expires_at)
                VALUES (:st, :sid, :ea)
                ON CONFLICT (state) DO UPDATE SET
                    site_id = EXCLUDED.site_id,
                    expires_at = EXCLUDED.expires_at,
                    created_at = NOW()
                """
            ),
            {"st": state, "sid": site_id, "ea": expires_at},
        )
        # Opportunistic cleanup of expired states.
        conn.execute(
            _text("DELETE FROM xero_oauth_states WHERE expires_at < NOW()"),
        )
        conn.commit()


def consume_xero_oauth_state(state: str) -> Optional[str]:
    """
    Validate and consume OAuth state token.
    Returns site_id when valid; otherwise None.
    """
    with engine.connect() as conn:
        _ensure_xero_oauth_states_table(conn)
        row = (
            conn.execute(
                _text(
                    """
                DELETE FROM xero_oauth_states
                WHERE state = :st
                  AND expires_at >= NOW()
                RETURNING site_id
                """
                ),
                {"st": state},
            )
            .mappings()
            .first()
        )
        # Also clear expired states to keep table small.
        conn.execute(
            _text("DELETE FROM xero_oauth_states WHERE expires_at < NOW()"),
        )
        conn.commit()
        return str(row["site_id"]) if row else None


def get_xero_tokens(site_id: str) -> Optional[dict]:
    """Fetch current Xero OAuth2 tokens for a site."""
    if not token_encryption_ready():
        logger.warning(
            "Xero token encryption not ready; set AUTOPILOT_TOKEN_ENC_KEY and install cryptography "
            "to enable Xero sync."
        )
        return None

    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "SELECT tenant_id, access_token, refresh_token, "
                "expires_at, scope, connected_at, updated_at "
                "FROM xero_tokens WHERE site_id = :sid"
            ),
            {"sid": site_id},
        )
        row = result.mappings().first()
        if not row:
            return None

        access_raw = row["access_token"]
        refresh_raw = row["refresh_token"]
        needs_migration = not is_encrypted_secret(access_raw) or not is_encrypted_secret(
            refresh_raw
        )

        tokens = dict(row)
        tokens["access_token"] = decrypt_secret(access_raw)
        tokens["refresh_token"] = decrypt_secret(refresh_raw)

        if needs_migration:
            conn.execute(
                _text(
                    "UPDATE xero_tokens SET access_token = :at, refresh_token = :rt, updated_at = NOW() "
                    "WHERE site_id = :sid"
                ),
                {
                    "sid": site_id,
                    "at": encrypt_secret(tokens["access_token"]),
                    "rt": encrypt_secret(tokens["refresh_token"]),
                },
            )
            conn.commit()
            logger.info("Migrated plaintext Xero tokens to encrypted-at-rest for site %s", site_id)

        return tokens


def update_xero_tokens(
    site_id: str,
    access_token: str,
    refresh_token: str,
    expires_at: datetime,
) -> None:
    """
    Update tokens after a refresh (keeps tenant_id unchanged).
    Xero rotates refresh tokens, so this always overwrites the stored refresh token.
    """
    if not token_encryption_ready():
        raise TokenEncryptionError(
            "AUTOPILOT_TOKEN_ENC_KEY missing/invalid or cryptography unavailable."
        )

    encrypted_access = encrypt_secret(access_token)
    encrypted_refresh = encrypt_secret(refresh_token)

    with engine.connect() as conn:
        conn.execute(
            _text(
                "UPDATE xero_tokens SET "
                "access_token = :at, refresh_token = :rt, "
                "expires_at = :ea, updated_at = NOW() "
                "WHERE site_id = :sid"
            ),
            {
                "sid": site_id,
                "at": encrypted_access,
                "rt": encrypted_refresh,
                "ea": expires_at,
            },
        )
        conn.commit()

    logger.info("Refreshed Xero tokens for site %s", site_id)


def _mapping_confidence_value(confidence) -> Optional[float]:
    if confidence is None:
        return None
    if isinstance(confidence, (int, float)):
        value = float(confidence)
        return max(0.0, min(1.0, value))

    raw = str(confidence).strip().lower()
    if not raw:
        return None
    if raw in ("confirmed", "high"):
        return 0.95
    if raw == "medium":
        return 0.70
    if raw == "low":
        return 0.30
    if raw in ("unconfirmed", "unknown"):
        return 0.0

    try:
        value = float(raw)
    except ValueError:
        return None
    return max(0.0, min(1.0, value))


def _ensure_xero_review_queue_table(conn) -> None:
    try:
        conn.execute(
            _text(
                """
                CREATE TABLE IF NOT EXISTS xero_review_queue (
                    review_id            SERIAL PRIMARY KEY,
                    site_id              UUID NOT NULL REFERENCES sites(site_id),
                    queue_status         TEXT NOT NULL DEFAULT 'open',
                    reason_code          TEXT NOT NULL,
                    invoice_id           TEXT,
                    invoice_number       TEXT,
                    supplier             TEXT,
                    line_description     TEXT NOT NULL,
                    line_quantity        NUMERIC,
                    unit_amount          NUMERIC,
                    line_total           NUMERIC,
                    bill_date            DATE,
                    suggested_score_key  TEXT,
                    suggested_confidence REAL,
                    mapping_id           INTEGER REFERENCES xero_line_mappings(id),
                    payload              JSONB,
                    resolution_note      TEXT,
                    resolved_by          TEXT,
                    created_at           TIMESTAMPTZ DEFAULT NOW(),
                    resolved_at          TIMESTAMPTZ
                )
                """
            )
        )
        conn.execute(
            _text(
                """
                CREATE INDEX IF NOT EXISTS idx_xero_review_queue_site_status
                ON xero_review_queue(site_id, queue_status, created_at DESC)
                """
            )
        )
        conn.execute(
            _text(
                """
                CREATE INDEX IF NOT EXISTS idx_xero_review_queue_reason
                ON xero_review_queue(site_id, reason_code, created_at DESC)
                """
            )
        )
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return


def _ensure_xero_cost_history_table(conn) -> None:
    try:
        conn.execute(
            _text(
                """
                CREATE TABLE IF NOT EXISTS xero_cost_history (
                    history_id  SERIAL PRIMARY KEY,
                    site_id     UUID NOT NULL REFERENCES sites(site_id),
                    score_key   TEXT NOT NULL,
                    cost_cents  INT NOT NULL,
                    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    source      TEXT NOT NULL DEFAULT 'xero',
                    reference   TEXT
                )
                """
            )
        )
        conn.execute(
            _text(
                """
                CREATE INDEX IF NOT EXISTS idx_xero_cost_history_site_key_time
                ON xero_cost_history(site_id, score_key, observed_at DESC)
                """
            )
        )
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return


def _xero_table_exists(conn, table_name: str) -> bool:
    try:
        value = conn.execute(
            _text("SELECT to_regclass(:name)"),
            {"name": f"public.{table_name}"},
        ).scalar()
        return value is not None
    except Exception:
        return False


def get_xero_line_mapping(site_id: str, description: str, status: str = None) -> Optional[dict]:
    """Read one Xero line mapping, optionally scoped by workflow status."""
    with engine.connect() as conn:
        try:
            row = (
                conn.execute(
                    _text(
                        """
                        SELECT id, score_key, confidence, units_per_pack, source, status,
                               proposed_at, approved_at, approved_by, model, prompt_version,
                               created_at, updated_at
                        FROM xero_line_mappings
                        WHERE site_id = :sid
                          AND xero_description = :desc
                          AND (:status IS NULL OR status = :status)
                        ORDER BY
                            CASE status
                                WHEN 'approved' THEN 0
                                WHEN 'proposed' THEN 1
                                ELSE 2
                            END,
                            updated_at DESC NULLS LAST,
                            created_at DESC
                        LIMIT 1
                        """
                    ),
                    {"sid": site_id, "desc": description, "status": status},
                )
                .mappings()
                .first()
            )
        except Exception:
            row = (
                conn.execute(
                    _text(
                        "SELECT id, score_key, confidence, units_per_pack, created_at "
                        "FROM xero_line_mappings "
                        "WHERE site_id = :sid AND xero_description = :desc "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"sid": site_id, "desc": description},
                )
                .mappings()
                .first()
            )
            if row:
                legacy_conf = str(row.get("confidence") or "").strip().lower()
                legacy_status = "approved" if legacy_conf in ("confirmed", "high") else "proposed"
                if status and status != legacy_status:
                    return None
                return {
                    "id": int(row["id"]) if row.get("id") is not None else None,
                    "score_key": row["score_key"],
                    "confidence": _mapping_confidence_value(row.get("confidence")) or 0.0,
                    "units_per_pack": int(row.get("units_per_pack") or 1),
                    "source": "llm",
                    "status": legacy_status,
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("created_at"),
                }
            return None

        if not row:
            return None

        return {
            "id": int(row["id"]),
            "score_key": row["score_key"],
            "confidence": _mapping_confidence_value(row.get("confidence")) or 0.0,
            "units_per_pack": int(row.get("units_per_pack") or 1),
            "source": row.get("source") or "llm",
            "status": row.get("status") or "proposed",
            "proposed_at": row.get("proposed_at"),
            "approved_at": row.get("approved_at"),
            "approved_by": row.get("approved_by"),
            "model": row.get("model"),
            "prompt_version": row.get("prompt_version"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }


def upsert_xero_line_mapping(
    site_id: str,
    description: str,
    score_key: str,
    confidence: float = None,
    units_per_pack: int = 1,
    source: str = "llm",
    status: str = "proposed",
    model: str = None,
    prompt_version: str = None,
    approved_by: str = None,
) -> Optional[dict]:
    """
    Upsert a mapping with audit metadata.
    Returns the current mapping row when available.
    """
    confidence_value = _mapping_confidence_value(confidence)
    source_value = (source or "llm").strip().lower()
    if source_value not in ("human", "llm", "rule"):
        source_value = "llm"
    status_value = (status or "proposed").strip().lower()
    if status_value not in ("proposed", "approved", "rejected"):
        status_value = "proposed"

    with engine.connect() as conn:
        try:
            conn.execute(
                _text(
                    """
                    INSERT INTO xero_line_mappings
                        (site_id, xero_description, score_key, confidence, source, status,
                         proposed_at, approved_at, approved_by, model, prompt_version,
                         units_per_pack, created_at, updated_at)
                    VALUES
                        (:sid, :desc, :sk, :conf, :src, :status, NOW(),
                         CASE WHEN :status = 'approved' THEN NOW() ELSE NULL END,
                         CASE WHEN :status = 'approved' THEN :approved_by ELSE NULL END,
                         :model, :prompt_version, :upp, NOW(), NOW())
                    ON CONFLICT (site_id, xero_description) DO UPDATE SET
                        score_key = EXCLUDED.score_key,
                        confidence = EXCLUDED.confidence,
                        source = EXCLUDED.source,
                        model = EXCLUDED.model,
                        prompt_version = EXCLUDED.prompt_version,
                        units_per_pack = EXCLUDED.units_per_pack,
                        status = CASE
                            WHEN xero_line_mappings.status = 'approved'
                                 AND EXCLUDED.status = 'proposed'
                            THEN xero_line_mappings.status
                            ELSE EXCLUDED.status
                        END,
                        proposed_at = CASE
                            WHEN EXCLUDED.status = 'proposed' THEN NOW()
                            ELSE xero_line_mappings.proposed_at
                        END,
                        approved_at = CASE
                            WHEN EXCLUDED.status = 'approved' THEN NOW()
                            ELSE xero_line_mappings.approved_at
                        END,
                        approved_by = CASE
                            WHEN EXCLUDED.status = 'approved' THEN COALESCE(:approved_by, xero_line_mappings.approved_by)
                            ELSE xero_line_mappings.approved_by
                        END,
                        updated_at = NOW()
                    """
                ),
                {
                    "sid": site_id,
                    "desc": description,
                    "sk": score_key,
                    "conf": confidence_value,
                    "src": source_value,
                    "status": status_value,
                    "approved_by": approved_by,
                    "model": model,
                    "prompt_version": prompt_version,
                    "upp": max(1, int(units_per_pack or 1)),
                },
            )
        except Exception:
            # Legacy fallback for older table structure.
            conn.execute(
                _text(
                    "INSERT INTO xero_line_mappings "
                    "(site_id, xero_description, score_key, confidence, units_per_pack) "
                    "VALUES (:sid, :desc, :sk, :conf, :upp) "
                    "ON CONFLICT (site_id, xero_description) DO UPDATE SET "
                    "score_key = EXCLUDED.score_key, "
                    "confidence = EXCLUDED.confidence, "
                    "units_per_pack = EXCLUDED.units_per_pack"
                ),
                {
                    "sid": site_id,
                    "desc": description,
                    "sk": score_key,
                    "conf": str(
                        confidence_value if confidence_value is not None else "unconfirmed"
                    ),
                    "upp": max(1, int(units_per_pack or 1)),
                },
            )
        conn.commit()

    logger.debug(
        "Upserted Xero mapping: '%s' → %s (status=%s, source=%s)",
        description,
        score_key,
        status_value,
        source_value,
    )
    return get_xero_line_mapping(site_id, description)


def store_xero_line_mapping(
    site_id: str,
    description: str,
    score_key: str,
    confidence: float = None,
    units_per_pack: int = 1,
) -> None:
    """
    Backward-compatible wrapper for historical call sites.
    New mappings are stored as proposed LLM suggestions.
    """
    upsert_xero_line_mapping(
        site_id=site_id,
        description=description,
        score_key=score_key,
        confidence=confidence,
        units_per_pack=units_per_pack,
        source="llm",
        status="proposed",
    )


def update_xero_line_mapping_status(
    site_id: str,
    mapping_id: int,
    status: str,
    score_key: str = None,
    approved_by: str = None,
) -> bool:
    """Update mapping status for operator review actions."""
    status_value = (status or "").strip().lower()
    if status_value not in ("proposed", "approved", "rejected"):
        raise ValueError("status must be one of: proposed, approved, rejected")

    with engine.connect() as conn:
        try:
            result = conn.execute(
                _text(
                    """
                    UPDATE xero_line_mappings
                    SET status = :status,
                        score_key = COALESCE(:sk, score_key),
                        approved_at = CASE WHEN :status = 'approved' THEN NOW() ELSE approved_at END,
                        approved_by = CASE WHEN :status = 'approved' THEN COALESCE(:approved_by, approved_by) ELSE approved_by END,
                        updated_at = NOW()
                    WHERE site_id = :sid AND id = :mid
                    """
                ),
                {
                    "sid": site_id,
                    "mid": mapping_id,
                    "status": status_value,
                    "sk": score_key,
                    "approved_by": approved_by,
                },
            )
        except Exception:
            legacy_conf = "confirmed" if status_value == "approved" else "unconfirmed"
            result = conn.execute(
                _text(
                    """
                    UPDATE xero_line_mappings
                    SET score_key = COALESCE(:sk, score_key),
                        confidence = :conf
                    WHERE site_id = :sid AND id = :mid
                    """
                ),
                {"sid": site_id, "mid": mapping_id, "sk": score_key, "conf": legacy_conf},
            )
        conn.commit()
        return result.rowcount > 0


def get_xero_line_mapping_by_id(site_id: str, mapping_id: int) -> Optional[dict]:
    """Fetch mapping row by integer id."""
    with engine.connect() as conn:
        try:
            row = (
                conn.execute(
                    _text(
                        """
                        SELECT id, site_id, xero_description, score_key, confidence, source, status,
                               proposed_at, approved_at, approved_by, model, prompt_version,
                               units_per_pack, created_at, updated_at
                        FROM xero_line_mappings
                        WHERE site_id = :sid AND id = :mid
                        LIMIT 1
                        """
                    ),
                    {"sid": site_id, "mid": mapping_id},
                )
                .mappings()
                .first()
            )
        except Exception:
            row = (
                conn.execute(
                    _text(
                        """
                        SELECT id, site_id, xero_description, score_key, confidence,
                               units_per_pack, created_at
                        FROM xero_line_mappings
                        WHERE site_id = :sid AND id = :mid
                        LIMIT 1
                        """
                    ),
                    {"sid": site_id, "mid": mapping_id},
                )
                .mappings()
                .first()
            )
            if row:
                return {
                    "id": int(row["id"]),
                    "site_id": str(row["site_id"]),
                    "xero_description": row["xero_description"],
                    "score_key": row["score_key"],
                    "confidence": _mapping_confidence_value(row.get("confidence")) or 0.0,
                    "source": "llm",
                    "status": (
                        "approved"
                        if str(row.get("confidence") or "").strip().lower() in ("confirmed", "high")
                        else "proposed"
                    ),
                    "units_per_pack": int(row.get("units_per_pack") or 1),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("created_at"),
                }
            return None

        if not row:
            return None
        payload = dict(row)
        payload["site_id"] = str(payload["site_id"])
        payload["confidence"] = _mapping_confidence_value(payload.get("confidence")) or 0.0
        payload["units_per_pack"] = int(payload.get("units_per_pack") or 1)
        return payload


def get_all_xero_mappings(site_id: str) -> list[dict]:
    """Get all cached Xero line-item mappings for review/display."""
    with engine.connect() as conn:
        try:
            result = conn.execute(
                _text(
                    """
                    SELECT id, xero_description, score_key, confidence, source, status,
                           proposed_at, approved_at, approved_by, model, prompt_version,
                           units_per_pack, created_at, updated_at
                    FROM xero_line_mappings
                    WHERE site_id = :sid
                    ORDER BY created_at DESC
                    """
                ),
                {"sid": site_id},
            )
            rows = []
            for row in result.mappings():
                payload = dict(row)
                payload["confidence"] = _mapping_confidence_value(payload.get("confidence")) or 0.0
                payload["units_per_pack"] = int(payload.get("units_per_pack") or 1)
                rows.append(payload)
            return rows
        except Exception:
            result = conn.execute(
                _text(
                    "SELECT id, xero_description, score_key, confidence, units_per_pack, created_at "
                    "FROM xero_line_mappings "
                    "WHERE site_id = :sid "
                    "ORDER BY created_at DESC"
                ),
                {"sid": site_id},
            )
            rows = []
            for row in result.mappings():
                payload = dict(row)
                payload["confidence"] = _mapping_confidence_value(payload.get("confidence")) or 0.0
                payload["source"] = "llm"
                payload["status"] = (
                    "approved"
                    if str(row.get("confidence") or "").strip().lower() in ("confirmed", "high")
                    else "proposed"
                )
                payload["units_per_pack"] = int(payload.get("units_per_pack") or 1)
                rows.append(payload)
            return rows


def enqueue_xero_review_item(
    site_id: str,
    reason_code: str,
    line_description: str,
    invoice_id: str = None,
    invoice_number: str = None,
    supplier: str = None,
    line_quantity: float = None,
    unit_amount: float = None,
    line_total: float = None,
    bill_date: date = None,
    suggested_score_key: str = None,
    suggested_confidence: float = None,
    mapping_id: int = None,
    payload: dict = None,
) -> Optional[int]:
    """Insert an open review queue item (deduped on core identity fields)."""
    try:
        with engine.connect() as conn:
            _ensure_xero_review_queue_table(conn)
            if not _xero_table_exists(conn, "xero_review_queue"):
                return None
            existing = (
                conn.execute(
                    _text(
                        """
                        SELECT review_id
                        FROM xero_review_queue
                        WHERE site_id = :sid
                          AND queue_status = 'open'
                          AND reason_code = :reason
                          AND COALESCE(invoice_id, '') = COALESCE(:invoice_id, '')
                          AND line_description = :line_description
                          AND COALESCE(suggested_score_key, '') = COALESCE(:suggested_score_key, '')
                        ORDER BY created_at DESC
                        LIMIT 1
                        """
                    ),
                    {
                        "sid": site_id,
                        "reason": reason_code,
                        "invoice_id": invoice_id,
                        "line_description": line_description,
                        "suggested_score_key": suggested_score_key,
                    },
                )
                .mappings()
                .first()
            )
            if existing:
                return int(existing["review_id"])

            row = (
                conn.execute(
                    _text(
                        """
                        INSERT INTO xero_review_queue
                            (site_id, reason_code, invoice_id, invoice_number, supplier,
                             line_description, line_quantity, unit_amount, line_total, bill_date,
                             suggested_score_key, suggested_confidence, mapping_id, payload)
                        VALUES
                            (:sid, :reason, :invoice_id, :invoice_number, :supplier,
                             :line_description, :line_quantity, :unit_amount, :line_total, :bill_date,
                             :suggested_score_key, :suggested_confidence, :mapping_id, :payload)
                        RETURNING review_id
                        """
                    ),
                    {
                        "sid": site_id,
                        "reason": reason_code,
                        "invoice_id": invoice_id,
                        "invoice_number": invoice_number,
                        "supplier": supplier,
                        "line_description": line_description or "",
                        "line_quantity": line_quantity,
                        "unit_amount": unit_amount,
                        "line_total": line_total,
                        "bill_date": bill_date,
                        "suggested_score_key": suggested_score_key,
                        "suggested_confidence": _mapping_confidence_value(suggested_confidence),
                        "mapping_id": mapping_id,
                        "payload": _json_dumps(payload) if payload is not None else None,
                    },
                )
                .mappings()
                .first()
            )
            conn.commit()
            return int(row["review_id"]) if row else None
    except Exception as e:
        logger.warning("enqueue_xero_review_item unavailable (non-fatal): %s", e)
        return None


def list_xero_review_queue(
    site_id: str,
    since: datetime = None,
    queue_status: str = "open",
    limit: int = 500,
) -> list[dict]:
    """List Xero review queue rows with optional time/status filtering."""
    try:
        with engine.connect() as conn:
            _ensure_xero_review_queue_table(conn)
            if not _xero_table_exists(conn, "xero_review_queue"):
                return []
            rows = (
                conn.execute(
                    _text(
                        """
                        SELECT review_id, queue_status, reason_code, invoice_id, invoice_number, supplier,
                               line_description, line_quantity, unit_amount, line_total, bill_date,
                               suggested_score_key, suggested_confidence, mapping_id, payload,
                               resolution_note, resolved_by, created_at, resolved_at
                        FROM xero_review_queue
                        WHERE site_id = :sid
                          AND (:status IS NULL OR queue_status = :status)
                          AND (:since IS NULL OR created_at >= :since)
                        ORDER BY created_at DESC
                        LIMIT :lim
                        """
                    ),
                    {
                        "sid": site_id,
                        "status": queue_status,
                        "since": since,
                        "lim": max(1, int(limit or 500)),
                    },
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]
    except Exception as e:
        logger.warning("list_xero_review_queue unavailable (non-fatal): %s", e)
        return []


def resolve_xero_review_item(
    site_id: str,
    review_id: int,
    resolved_by: str = "operator",
    resolution_note: str = None,
) -> bool:
    """Mark one review item as resolved."""
    try:
        with engine.connect() as conn:
            _ensure_xero_review_queue_table(conn)
            if not _xero_table_exists(conn, "xero_review_queue"):
                return False
            result = conn.execute(
                _text(
                    """
                    UPDATE xero_review_queue
                    SET queue_status = 'resolved',
                        resolved_by = :resolved_by,
                        resolution_note = :resolution_note,
                        resolved_at = NOW()
                    WHERE site_id = :sid
                      AND review_id = :rid
                      AND queue_status = 'open'
                    """
                ),
                {
                    "sid": site_id,
                    "rid": review_id,
                    "resolved_by": resolved_by,
                    "resolution_note": resolution_note,
                },
            )
            conn.commit()
            return result.rowcount > 0
    except Exception as e:
        logger.warning("resolve_xero_review_item unavailable (non-fatal): %s", e)
        return False


def resolve_xero_review_items_for_mapping(
    site_id: str,
    mapping_id: int,
    resolved_by: str = "operator",
    resolution_note: str = None,
) -> int:
    """Resolve all open review items linked to a mapping id."""
    try:
        with engine.connect() as conn:
            _ensure_xero_review_queue_table(conn)
            if not _xero_table_exists(conn, "xero_review_queue"):
                return 0
            result = conn.execute(
                _text(
                    """
                    UPDATE xero_review_queue
                    SET queue_status = 'resolved',
                        resolved_by = :resolved_by,
                        resolution_note = :resolution_note,
                        resolved_at = NOW()
                    WHERE site_id = :sid
                      AND mapping_id = :mid
                      AND queue_status = 'open'
                    """
                ),
                {
                    "sid": site_id,
                    "mid": mapping_id,
                    "resolved_by": resolved_by,
                    "resolution_note": resolution_note,
                },
            )
            conn.commit()
            return int(result.rowcount or 0)
    except Exception as e:
        logger.warning("resolve_xero_review_items_for_mapping unavailable (non-fatal): %s", e)
        return 0


def get_xero_review_counts(site_id: str, queue_status: str = "open") -> dict:
    """Return {reason_code: count} summary for review queue items."""
    try:
        with engine.connect() as conn:
            _ensure_xero_review_queue_table(conn)
            if not _xero_table_exists(conn, "xero_review_queue"):
                return {}
            rows = (
                conn.execute(
                    _text(
                        """
                        SELECT reason_code, COUNT(*) AS count
                        FROM xero_review_queue
                        WHERE site_id = :sid
                          AND (:status IS NULL OR queue_status = :status)
                        GROUP BY reason_code
                        """
                    ),
                    {"sid": site_id, "status": queue_status},
                )
                .mappings()
                .all()
            )
        return {str(row["reason_code"]): int(row["count"] or 0) for row in rows}
    except Exception as e:
        logger.warning("get_xero_review_counts unavailable (non-fatal): %s", e)
        return {}


def store_xero_cost_history(
    site_id: str,
    score_key: str,
    cost_cents: int,
    observed_at: datetime = None,
    source: str = "xero",
    reference: str = None,
) -> None:
    """Append observed Xero unit cost history for guardrail bounds."""
    try:
        with engine.connect() as conn:
            _ensure_xero_cost_history_table(conn)
            if not _xero_table_exists(conn, "xero_cost_history"):
                return
            conn.execute(
                _text(
                    """
                    INSERT INTO xero_cost_history
                        (site_id, score_key, cost_cents, observed_at, source, reference)
                    VALUES
                        (:sid, :score_key, :cost_cents, :observed_at, :source, :reference)
                    """
                ),
                {
                    "sid": site_id,
                    "score_key": score_key,
                    "cost_cents": int(cost_cents),
                    "observed_at": observed_at or datetime.now(timezone.utc),
                    "source": source or "xero",
                    "reference": reference,
                },
            )
            conn.commit()
    except Exception as e:
        logger.warning("store_xero_cost_history unavailable (non-fatal): %s", e)


def get_recent_xero_cost_history(site_id: str, score_key: str, limit: int = 20) -> list[int]:
    """Return recent cost_cents history ordered oldest→newest."""
    try:
        with engine.connect() as conn:
            _ensure_xero_cost_history_table(conn)
            if not _xero_table_exists(conn, "xero_cost_history"):
                return []
            rows = conn.execute(
                _text(
                    """
                        SELECT cost_cents
                        FROM xero_cost_history
                        WHERE site_id = :sid AND score_key = :score_key
                        ORDER BY observed_at DESC
                        LIMIT :lim
                        """
                ),
                {
                    "sid": site_id,
                    "score_key": score_key,
                    "lim": max(1, int(limit or 20)),
                },
            ).all()
        values = [int(row[0]) for row in rows if row and row[0] is not None]
        values.reverse()
        return values
    except Exception as e:
        logger.warning("get_recent_xero_cost_history unavailable (non-fatal): %s", e)
        return []


def _ensure_xero_financial_facts_table(conn) -> None:
    """Create daily Xero financial facts table lazily for backward compatibility."""
    conn.execute(
        _text(
            """
            CREATE TABLE IF NOT EXISTS xero_financial_facts (
                fact_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                site_id UUID NOT NULL REFERENCES sites(site_id),
                fact_date DATE NOT NULL,
                income_cents INT NOT NULL DEFAULT 0,
                expense_cents INT NOT NULL DEFAULT 0,
                payroll_cents INT,
                net_cash_cents INT NOT NULL DEFAULT 0,
                txn_count INT NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'xero_bank_transactions',
                completeness TEXT NOT NULL DEFAULT 'partial',
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(site_id, fact_date)
            )
            """
        )
    )
    conn.execute(
        _text(
            """
            CREATE INDEX IF NOT EXISTS idx_xero_financial_facts_site_date
            ON xero_financial_facts(site_id, fact_date DESC)
            """
        )
    )


def upsert_xero_financial_fact(
    site_id: str,
    fact_date: date,
    income_cents: int,
    expense_cents: int,
    payroll_cents: Optional[int] = None,
    txn_count: int = 0,
    source: str = "xero_bank_transactions",
    completeness: str = "partial",
) -> None:
    """
    Upsert one day of factual Xero cashflow.
    """
    income = int(income_cents or 0)
    expense = int(expense_cents or 0)
    payroll = int(payroll_cents) if payroll_cents is not None else None
    net_cash = income - expense
    txns = int(txn_count or 0)

    params = {
        "sid": site_id,
        "d": fact_date,
        "income": income,
        "expense": expense,
        "payroll": payroll,
        "net_cash": net_cash,
        "txns": txns,
        "src": source,
        "comp": completeness,
    }
    statement = _text(
        """
        INSERT INTO xero_financial_facts
            (site_id, fact_date, income_cents, expense_cents, payroll_cents,
             net_cash_cents, txn_count, source, completeness, updated_at)
        VALUES
            (:sid, :d, :income, :expense, :payroll,
             :net_cash, :txns, :src, :comp, NOW())
        ON CONFLICT (site_id, fact_date) DO UPDATE SET
            income_cents = EXCLUDED.income_cents,
            expense_cents = EXCLUDED.expense_cents,
            payroll_cents = EXCLUDED.payroll_cents,
            net_cash_cents = EXCLUDED.net_cash_cents,
            txn_count = EXCLUDED.txn_count,
            source = EXCLUDED.source,
            completeness = EXCLUDED.completeness,
            updated_at = NOW()
        """
    )

    with engine.connect() as conn:
        try:
            conn.execute(statement, params)
            conn.commit()
            return
        except Exception as e:
            if "xero_financial_facts" not in str(e) or "does not exist" not in str(e):
                logger.warning("Skipping xero_financial_facts upsert (non-fatal): %s", e)
                return

        try:
            _ensure_xero_financial_facts_table(conn)
            conn.execute(statement, params)
            conn.commit()
        except Exception as e:
            logger.warning("Skipping xero_financial_facts upsert (non-fatal): %s", e)


def get_xero_financial_facts_summary(site_id: str, start_date: date, end_date: date) -> dict:
    """
    Aggregate factual Xero cashflow for a date window.
    """
    row = None
    query = _text(
        """
        SELECT
            COUNT(*) AS days_covered,
            COALESCE(SUM(income_cents), 0) AS income_cents,
            COALESCE(SUM(expense_cents), 0) AS expense_cents,
            COALESCE(SUM(payroll_cents), 0) AS payroll_cents,
            COALESCE(SUM(net_cash_cents), 0) AS net_cash_cents,
            COALESCE(SUM(txn_count), 0) AS txn_count,
            MAX(fact_date) AS latest_fact_date,
            MAX(updated_at)::date AS latest_update_date,
            COALESCE(SUM(CASE WHEN completeness = 'full' THEN 1 ELSE 0 END), 0) AS full_days
        FROM xero_financial_facts
        WHERE site_id = :sid
          AND fact_date BETWEEN :s AND :e
        """
    )
    try:
        with engine.connect() as conn:
            row = (
                conn.execute(
                    query,
                    {"sid": site_id, "s": start_date, "e": end_date},
                )
                .mappings()
                .first()
            )
    except Exception as e:
        if "xero_financial_facts" in str(e) and "does not exist" in str(e):
            try:
                with engine.connect() as conn:
                    _ensure_xero_financial_facts_table(conn)
                    row = (
                        conn.execute(
                            query,
                            {"sid": site_id, "s": start_date, "e": end_date},
                        )
                        .mappings()
                        .first()
                    )
            except Exception as inner_e:
                logger.warning("xero_financial_facts summary unavailable (non-fatal): %s", inner_e)
        else:
            logger.warning("xero_financial_facts summary unavailable (non-fatal): %s", e)

    return {
        "days_covered": int((row or {}).get("days_covered") or 0),
        "income_cents": int((row or {}).get("income_cents") or 0),
        "expense_cents": int((row or {}).get("expense_cents") or 0),
        "payroll_cents": int((row or {}).get("payroll_cents") or 0),
        "net_cash_cents": int((row or {}).get("net_cash_cents") or 0),
        "txn_count": int((row or {}).get("txn_count") or 0),
        "latest_fact_date": (row or {}).get("latest_fact_date"),
        "latest_update_date": (row or {}).get("latest_update_date"),
        "full_days": int((row or {}).get("full_days") or 0),
    }


def get_data_freshness(site_id: str) -> Optional[str]:
    """Get the most recent closed_at date from orders_raw."""
    with engine.connect() as conn:
        result = conn.execute(
            _text("SELECT MAX(closed_at)::date AS latest " "FROM orders_raw WHERE site_id = :sid"),
            {"sid": site_id},
        ).scalar()
        return str(result) if result else None


# ============================================================
# Data Quality Flags (fail-closed controls)
# ============================================================


def _ensure_data_quality_flags_table(conn) -> bool:
    try:
        conn.execute(
            _text(
                """
                CREATE TABLE IF NOT EXISTS data_quality_flags (
                    flag_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                    site_id UUID REFERENCES sites(site_id),
                    flag_date DATE NOT NULL,
                    flag_type TEXT NOT NULL,
                    severity TEXT NOT NULL DEFAULT 'medium',
                    source TEXT NOT NULL DEFAULT 'system',
                    reason TEXT,
                    metadata JSONB,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    resolved_at TIMESTAMPTZ
                )
                """
            )
        )
        conn.execute(
            _text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_data_quality_unique_active
                ON data_quality_flags(site_id, flag_date, flag_type, source, active)
                """
            )
        )
        conn.execute(
            _text(
                """
                CREATE INDEX IF NOT EXISTS idx_data_quality_site_date
                ON data_quality_flags(site_id, flag_date DESC)
                """
            )
        )
        return True
    except Exception as exc:  # pragma: no cover - depends on DB privileges
        try:
            conn.rollback()
        except Exception:
            pass
        logger.info("data_quality_flags table unavailable (non-fatal): %s", exc)
        return False


def upsert_data_quality_flag(
    site_id: str,
    flag_date: date,
    flag_type: str,
    severity: str = "medium",
    source: str = "system",
    reason: str | None = None,
    metadata: dict | None = None,
) -> str:
    with engine.connect() as conn:
        if not _ensure_data_quality_flags_table(conn):
            return ""
        existing = conn.execute(
            _text(
                """
                SELECT flag_id
                FROM data_quality_flags
                WHERE site_id = :sid
                  AND flag_date = :d
                  AND flag_type = :t
                  AND source = :src
                  AND active = TRUE
                LIMIT 1
                """
            ),
            {"sid": site_id, "d": flag_date, "t": flag_type, "src": source},
        ).scalar()
        if existing:
            conn.execute(
                _text(
                    """
                    UPDATE data_quality_flags
                    SET severity = :sev,
                        reason = :reason,
                        metadata = :meta
                    WHERE flag_id = :fid
                    """
                ),
                {
                    "sev": severity,
                    "reason": reason,
                    "meta": _json_dumps(metadata) if metadata else None,
                    "fid": existing,
                },
            )
            conn.commit()
            return str(existing)

        flag_id = conn.execute(
            _text(
                """
                INSERT INTO data_quality_flags
                    (site_id, flag_date, flag_type, severity, source, reason, metadata, active)
                VALUES
                    (:sid, :d, :t, :sev, :src, :reason, :meta, TRUE)
                RETURNING flag_id
                """
            ),
            {
                "sid": site_id,
                "d": flag_date,
                "t": flag_type,
                "sev": severity,
                "src": source,
                "reason": reason,
                "meta": _json_dumps(metadata) if metadata else None,
            },
        ).scalar()
        conn.commit()
        return str(flag_id)


def resolve_data_quality_flag(
    site_id: str,
    flag_date: date,
    flag_type: str,
    source: str | None = None,
) -> int:
    with engine.connect() as conn:
        if not _ensure_data_quality_flags_table(conn):
            return 0
        result = conn.execute(
            _text(
                """
                UPDATE data_quality_flags
                SET active = FALSE,
                    resolved_at = NOW()
                WHERE site_id = :sid
                  AND flag_date = :d
                  AND flag_type = :t
                  AND active = TRUE
                  AND (:src IS NULL OR source = :src)
                """
            ),
            {"sid": site_id, "d": flag_date, "t": flag_type, "src": source},
        )
        conn.commit()
        return int(result.rowcount or 0)


def get_data_quality_flags(
    site_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    active_only: bool = True,
    limit: int = 200,
) -> list[dict]:
    lim = max(1, min(limit, 1000))
    with engine.connect() as conn:
        has_flags = bool(
            conn.execute(
                _text("SELECT to_regclass('public.data_quality_flags') IS NOT NULL")
            ).scalar()
        )
        if not has_flags:
            return []

        rows = (
            conn.execute(
                _text(
                    """
                SELECT
                    flag_id, site_id, flag_date, flag_type, severity, source,
                    reason, metadata, active, created_at, resolved_at
                FROM data_quality_flags
                WHERE site_id = :sid
                  AND (:active_only = FALSE OR active = TRUE)
                  AND (:s IS NULL OR flag_date >= :s)
                  AND (:e IS NULL OR flag_date <= :e)
                ORDER BY flag_date DESC, created_at DESC
                LIMIT :lim
                """
                ),
                {
                    "sid": site_id,
                    "active_only": active_only,
                    "s": start_date,
                    "e": end_date,
                    "lim": lim,
                },
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def get_day_ingest_diagnostics(site_id: str, target_date: date) -> dict:
    """
    Diagnose whether a day looks like partial ingestion by comparing
    completed orders/revenue/active hours to recent same-weekday history.
    """
    with engine.connect() as conn:
        day = (
            conn.execute(
                _text(
                    """
                SELECT
                    COUNT(*) AS total_orders,
                    COUNT(*) FILTER (WHERE state = 'COMPLETED') AS completed_orders,
                    COALESCE(SUM(total_money_cents) FILTER (WHERE state = 'COMPLETED'), 0) AS completed_revenue_cents,
                    COUNT(DISTINCT date_trunc('hour', closed_at)) FILTER (WHERE state = 'COMPLETED' AND closed_at IS NOT NULL) AS active_hours
                FROM orders_raw
                WHERE site_id = :sid
                  AND DATE(closed_at) = :d
                """
                ),
                {"sid": site_id, "d": target_date},
            )
            .mappings()
            .first()
        )

        baseline_rows = (
            conn.execute(
                _text(
                    """
                SELECT
                    DATE(closed_at) AS trade_date,
                    COUNT(*) FILTER (WHERE state = 'COMPLETED') AS completed_orders,
                    COALESCE(SUM(total_money_cents) FILTER (WHERE state = 'COMPLETED'), 0) AS completed_revenue_cents,
                    COUNT(DISTINCT date_trunc('hour', closed_at)) FILTER (WHERE state = 'COMPLETED' AND closed_at IS NOT NULL) AS active_hours
                FROM orders_raw
                WHERE site_id = :sid
                  AND closed_at IS NOT NULL
                  AND DATE(closed_at) < :d
                  AND EXTRACT(DOW FROM closed_at) = EXTRACT(DOW FROM CAST(:d AS date))
                GROUP BY DATE(closed_at)
                ORDER BY trade_date DESC
                LIMIT 8
                """
                ),
                {"sid": site_id, "d": target_date},
            )
            .mappings()
            .all()
        )

    day_completed_orders = int((day or {}).get("completed_orders") or 0)
    day_revenue = int((day or {}).get("completed_revenue_cents") or 0)
    day_active_hours = int((day or {}).get("active_hours") or 0)
    baseline_orders = sorted(int(r.get("completed_orders") or 0) for r in baseline_rows)
    baseline_revenue = sorted(int(r.get("completed_revenue_cents") or 0) for r in baseline_rows)
    baseline_hours = sorted(int(r.get("active_hours") or 0) for r in baseline_rows)

    def _median(vals: list[int]) -> float | None:
        if not vals:
            return None
        n = len(vals)
        mid = n // 2
        if n % 2 == 1:
            return float(vals[mid])
        return (vals[mid - 1] + vals[mid]) / 2.0

    med_orders = _median(baseline_orders)
    med_revenue = _median(baseline_revenue)
    med_hours = _median(baseline_hours)

    partial_signals = []
    if med_orders and day_completed_orders < max(10, round(med_orders * 0.35)):
        partial_signals.append("orders_below_35pct_baseline")
    if med_revenue and day_revenue < max(50_00, round(med_revenue * 0.35)):
        partial_signals.append("revenue_below_35pct_baseline")
    if med_hours and day_active_hours < max(3, round(med_hours * 0.45)):
        partial_signals.append("active_hours_below_45pct_baseline")

    suspected_partial = len(partial_signals) >= 2
    return {
        "date": target_date.isoformat(),
        "day": {
            "completed_orders": day_completed_orders,
            "completed_revenue_cents": day_revenue,
            "active_hours": day_active_hours,
            "total_orders": int((day or {}).get("total_orders") or 0),
        },
        "baseline_same_dow": {
            "sample_days": len(baseline_rows),
            "median_completed_orders": med_orders,
            "median_completed_revenue_cents": med_revenue,
            "median_active_hours": med_hours,
        },
        "suspected_partial_ingest": suspected_partial,
        "signals": partial_signals,
    }


def apply_partial_ingest_guard(site_id: str, target_date: date) -> dict:
    """
    Strict guard: if a day looks partially ingested, flag and exclude from forecasts.
    """
    diag = get_day_ingest_diagnostics(site_id, target_date)
    if diag["suspected_partial_ingest"]:
        flag_id = upsert_data_quality_flag(
            site_id=site_id,
            flag_date=target_date,
            flag_type="partial_ingest",
            severity="high",
            source="system",
            reason="Day appears partially ingested vs same-weekday baseline.",
            metadata=diag,
        )
        if not flag_id:
            return {
                "status": "skipped",
                "reason": "data_quality_flags_unavailable",
                "diagnostics": diag,
            }
        return {"status": "flagged", "flag_id": flag_id, "diagnostics": diag}

    resolved = resolve_data_quality_flag(site_id, target_date, "partial_ingest", source="system")
    return {"status": "clear", "resolved": resolved, "diagnostics": diag}


# ============================================================
# Reliability Observability
# ============================================================


def _ensure_pipeline_runs_table(conn) -> bool:
    """Backwards-safe migration for pipeline run observability."""
    try:
        conn.execute(
            _text(
                """
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    run_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                    site_id UUID REFERENCES sites(site_id),
                    job_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    finished_at TIMESTAMPTZ,
                    duration_ms INT,
                    result_json JSONB,
                    error_text TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
        )
        conn.execute(
            _text(
                """
                CREATE INDEX IF NOT EXISTS idx_pipeline_runs_site_started
                ON pipeline_runs(site_id, started_at DESC)
                """
            )
        )
        conn.execute(
            _text(
                """
                CREATE INDEX IF NOT EXISTS idx_pipeline_runs_site_job_started
                ON pipeline_runs(site_id, job_name, started_at DESC)
                """
            )
        )
        return True
    except Exception as exc:  # pragma: no cover - depends on DB privileges
        try:
            conn.rollback()
        except Exception:
            pass
        logger.info("pipeline_runs table unavailable (non-fatal): %s", exc)
        return False


def store_pipeline_run(
    site_id: str,
    job_name: str,
    status: str,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
    result: Optional[dict] = None,
    error_text: Optional[str] = None,
) -> str:
    """Persist a scheduler/manual pipeline run status row."""
    started = started_at or datetime.utcnow()
    finished = finished_at
    duration_ms = None
    if started and finished:
        duration_ms = max(0, round((finished - started).total_seconds() * 1000))

    with engine.connect() as conn:
        if not _ensure_pipeline_runs_table(conn):
            return ""
        run_id = conn.execute(
            _text(
                """
                INSERT INTO pipeline_runs
                    (site_id, job_name, status, started_at, finished_at, duration_ms, result_json, error_text)
                VALUES
                    (:sid, :job, :status, :started, :finished, :dur, :result, :err)
                RETURNING run_id
                """
            ),
            {
                "sid": site_id,
                "job": job_name,
                "status": status,
                "started": started,
                "finished": finished,
                "dur": duration_ms,
                "result": _json_dumps(result) if result is not None else None,
                "err": error_text,
            },
        ).scalar()
        conn.commit()

    return str(run_id)


def get_recent_pipeline_runs(
    site_id: str, limit: int = 30, job_name: Optional[str] = None
) -> list[dict]:
    """Get recent pipeline run rows for operator/debug visibility."""
    lim = max(1, min(limit, 200))
    with engine.connect() as conn:
        if not _ensure_pipeline_runs_table(conn):
            return []
        rows = (
            conn.execute(
                _text(
                    """
                SELECT
                    run_id, site_id, job_name, status, started_at, finished_at,
                    duration_ms, result_json, error_text, created_at
                FROM pipeline_runs
                WHERE site_id = :sid
                  AND (:job_name IS NULL OR job_name = :job_name)
                ORDER BY started_at DESC
                LIMIT :lim
                """
                ),
                {"sid": site_id, "job_name": job_name, "lim": lim},
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def get_pipeline_health(site_id: str, hours: int = 24) -> dict:
    """Summarize run success/failed/skipped rates for recent scheduler reliability."""
    window_hours = max(1, min(hours, 24 * 30))
    with engine.connect() as conn:
        if not _ensure_pipeline_runs_table(conn):
            return {
                "window_hours": window_hours,
                "status": "unknown",
                "overall_success_rate": None,
                "total_runs": 0,
                "ok_runs": 0,
                "error_runs": 0,
                "skipped_runs": 0,
                "components": [],
            }
        rows = (
            conn.execute(
                _text(
                    """
                SELECT
                    job_name,
                    COUNT(*) AS total_runs,
                    COUNT(*) FILTER (WHERE status = 'ok') AS ok_runs,
                    COUNT(*) FILTER (WHERE status = 'error') AS error_runs,
                    COUNT(*) FILTER (WHERE status = 'skipped') AS skipped_runs,
                    MAX(started_at) AS last_started_at
                FROM pipeline_runs
                WHERE site_id = :sid
                  AND started_at >= (NOW() - (:h * INTERVAL '1 hour'))
                GROUP BY job_name
                ORDER BY job_name
                """
                ),
                {"sid": site_id, "h": window_hours},
            )
            .mappings()
            .all()
        )

    components = []
    totals = {"total_runs": 0, "ok_runs": 0, "error_runs": 0, "skipped_runs": 0}
    for r in rows:
        total = int(r.get("total_runs") or 0)
        ok_runs = int(r.get("ok_runs") or 0)
        error_runs = int(r.get("error_runs") or 0)
        skipped_runs = int(r.get("skipped_runs") or 0)
        totals["total_runs"] += total
        totals["ok_runs"] += ok_runs
        totals["error_runs"] += error_runs
        totals["skipped_runs"] += skipped_runs
        success_rate = round(ok_runs / total, 3) if total > 0 else None
        components.append(
            {
                "job_name": r.get("job_name"),
                "total_runs": total,
                "ok_runs": ok_runs,
                "error_runs": error_runs,
                "skipped_runs": skipped_runs,
                "success_rate": success_rate,
                "last_started_at": (
                    str(r.get("last_started_at")) if r.get("last_started_at") else None
                ),
            }
        )

    overall_success = (
        round(totals["ok_runs"] / totals["total_runs"], 3) if totals["total_runs"] > 0 else None
    )
    status = "green"
    if totals["error_runs"] > 0:
        status = "yellow"
    if totals["error_runs"] >= 3:
        status = "red"

    return {
        "window_hours": window_hours,
        "status": status,
        "overall_success_rate": overall_success,
        **totals,
        "components": components,
    }


def _profitability_summary(site_id: str, start_date: date, end_date: date) -> dict:
    """Aggregate daily_profitability KPIs for a date range."""
    with engine.connect() as conn:
        row = (
            conn.execute(
                _text(
                    """
                SELECT
                    COUNT(*) AS days_count,
                    SUM(revenue_cents) AS total_revenue_cents,
                    SUM(labor_cost_cents) AS total_labor_cost_cents,
                    SUM(cogs_cents) AS total_cogs_cents,
                    SUM(net_profit_cents) AS total_net_profit_cents,
                    AVG(labor_pct) AS avg_labor_pct,
                    AVG(revenue_per_labor_hour) AS avg_revenue_per_labor_hour_cents
                FROM daily_profitability
                WHERE site_id = :sid
                  AND profit_date BETWEEN :s AND :e
                """
                ),
                {"sid": site_id, "s": start_date, "e": end_date},
            )
            .mappings()
            .first()
        )

    total_revenue = int((row or {}).get("total_revenue_cents") or 0)
    total_net = int((row or {}).get("total_net_profit_cents") or 0)
    return {
        "days_count": int((row or {}).get("days_count") or 0),
        "total_revenue_cents": total_revenue,
        "total_labor_cost_cents": int((row or {}).get("total_labor_cost_cents") or 0),
        "total_cogs_cents": int((row or {}).get("total_cogs_cents") or 0),
        "total_net_profit_cents": total_net,
        "net_margin_pct": (
            round((total_net / total_revenue) * 100, 2) if total_revenue > 0 else None
        ),
        "avg_labor_pct": (
            round(float(row["avg_labor_pct"]), 2)
            if row and row.get("avg_labor_pct") is not None
            else None
        ),
        "avg_revenue_per_labor_hour_cents": (
            round(float(row["avg_revenue_per_labor_hour_cents"]))
            if row and row.get("avg_revenue_per_labor_hour_cents") is not None
            else None
        ),
    }


def _pct_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous is None or previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 2)


def _trend_direction(delta: Optional[float], inverse: bool = False, epsilon: float = 0.01) -> str:
    if delta is None:
        return "unknown"
    adj = -delta if inverse else delta
    if abs(adj) <= epsilon:
        return "stable"
    return "improving" if adj > 0 else "declining"


def get_bottom_line_scorecard(
    site_id: str,
    days: int = 30,
    compare_days: int = 7,
    top_actions_limit: int = 6,
) -> dict:
    """
    Unified bottom-line view for dashboard/chat grounding.

    Includes:
      - trailing profitability window KPIs
      - current vs previous short-window trend deltas
      - staffing efficiency delta from workload/deputy model
      - recommendation adoption + realized impact attribution
    """
    window_days = max(7, min(days, 120))
    compare_window_days = max(3, min(compare_days, 28))
    top_limit = max(1, min(top_actions_limit, 12))

    with engine.connect() as conn:
        anchor_row = (
            conn.execute(
                _text(
                    """
                SELECT MAX(profit_date) AS latest_date
                FROM daily_profitability
                WHERE site_id = :sid
                """
                ),
                {"sid": site_id},
            )
            .mappings()
            .first()
        )

    anchor_date = (anchor_row or {}).get("latest_date")
    if not anchor_date:
        return {
            "site_id": site_id,
            "window": {
                "days": window_days,
                "compare_days": compare_window_days,
                "start_date": None,
                "end_date": None,
            },
            "headline": "No profitability data available yet.",
            "kpis": {},
            "trend": {"current_window": {}, "previous_window": {}, "deltas": {}, "directions": {}},
            "actions": {
                "recommendations_generated": 0,
                "recommendations_adopted": 0,
                "adoption_rate": None,
                "realized_actions": 0,
                "avg_realized_weekly_profit_delta_cents": None,
                "total_realized_weekly_profit_delta_cents": None,
                "avg_realized_labor_pct_delta_pp": None,
                "avg_realized_rev_per_labor_hour_delta_pct": None,
                "top_proven_action_types": [],
            },
            "financial_truth": {
                "mode": "estimated_fallback",
                "reporting_breakdown_source": "square_orders",
                "factual_source": "unavailable",
                "coverage_days": 0,
                "coverage_ratio": 0.0,
                "window_days": window_days,
                "income_cents": 0,
                "expense_cents": 0,
                "payroll_cents": None,
                "net_cash_cents": 0,
                "txn_count": 0,
                "latest_fact_date": None,
                "latest_update_date": None,
                "full_days": 0,
                "estimated_fallback": True,
            },
        }

    end_date = anchor_date
    start_date = end_date - timedelta(days=window_days - 1)
    current_start = end_date - timedelta(days=compare_window_days - 1)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=compare_window_days - 1)

    overall = _profitability_summary(site_id, start_date, end_date)
    current = _profitability_summary(site_id, current_start, end_date)
    previous = _profitability_summary(site_id, previous_start, previous_end)

    current_efficiency = None
    previous_efficiency = None
    current_excess = None
    previous_excess = None
    try:
        current_gap = get_efficiency_gap_range(site_id, current_start, end_date)
        current_totals = current_gap.get("totals", {})
        current_efficiency = current_totals.get("efficiency_score")
        current_excess = int(current_totals.get("excess_labor_cents") or 0)
    except Exception:
        logger.exception("Unable to compute current efficiency for scorecard")

    try:
        previous_gap = get_efficiency_gap_range(site_id, previous_start, previous_end)
        previous_totals = previous_gap.get("totals", {})
        previous_efficiency = previous_totals.get("efficiency_score")
        previous_excess = int(previous_totals.get("excess_labor_cents") or 0)
    except Exception:
        logger.exception("Unable to compute previous efficiency for scorecard")

    with engine.connect() as conn:
        action_summary = (
            conn.execute(
                _text(
                    """
                WITH recs AS (
                    SELECT rec_id
                    FROM recommendations
                    WHERE site_id = :sid
                      AND DATE(created_at) BETWEEN :s AND :e
                ),
                adopted AS (
                    SELECT DISTINCT al.rec_id
                    FROM adoption_logs al
                    JOIN recs r ON r.rec_id = al.rec_id
                    WHERE al.site_id = :sid
                      AND al.adopted = TRUE
                      AND al.log_date BETWEEN :s AND :e
                ),
                realized AS (
                    SELECT
                        r.rec_id,
                        NULLIF(r.outcome_data->'realized'->>'weekly_net_profit_delta_cents', '')::numeric
                            AS weekly_profit_delta_cents,
                        NULLIF(r.outcome_data->'realized'->>'labor_pct_delta_pp', '')::numeric
                            AS labor_pct_delta_pp,
                        NULLIF(r.outcome_data->'realized'->>'rev_per_labor_hour_delta_pct', '')::numeric
                            AS rev_per_labor_hour_delta_pct
                    FROM recommendations r
                    JOIN adopted a ON a.rec_id = r.rec_id
                    WHERE r.site_id = :sid
                      AND r.outcome_data->'realized' IS NOT NULL
                )
                SELECT
                    (SELECT COUNT(*) FROM recs) AS recommendations_generated,
                    (SELECT COUNT(*) FROM adopted) AS recommendations_adopted,
                    (SELECT COUNT(*) FROM realized) AS realized_actions,
                    (SELECT AVG(weekly_profit_delta_cents) FROM realized)
                        AS avg_realized_weekly_profit_delta_cents,
                    (SELECT SUM(weekly_profit_delta_cents) FROM realized)
                        AS total_realized_weekly_profit_delta_cents,
                    (SELECT AVG(labor_pct_delta_pp) FROM realized)
                        AS avg_realized_labor_pct_delta_pp,
                    (SELECT AVG(rev_per_labor_hour_delta_pct) FROM realized)
                        AS avg_realized_rev_per_labor_hour_delta_pct
                """
                ),
                {"sid": site_id, "s": start_date, "e": end_date},
            )
            .mappings()
            .first()
        )

        top_actions = (
            conn.execute(
                _text(
                    """
                WITH adopted AS (
                    SELECT DISTINCT rec_id
                    FROM adoption_logs
                    WHERE site_id = :sid
                      AND adopted = TRUE
                      AND log_date BETWEEN :s AND :e
                ),
                realized AS (
                    SELECT
                        r.action_type,
                        NULLIF(r.outcome_data->'realized'->>'weekly_net_profit_delta_cents', '')::numeric
                            AS weekly_profit_delta_cents,
                        NULLIF(r.outcome_data->'realized'->>'labor_pct_delta_pp', '')::numeric
                            AS labor_pct_delta_pp,
                        NULLIF(r.outcome_data->'realized'->>'rev_per_labor_hour_delta_pct', '')::numeric
                            AS rev_per_labor_hour_delta_pct
                    FROM recommendations r
                    JOIN adopted a ON a.rec_id = r.rec_id
                    WHERE r.site_id = :sid
                      AND r.outcome_data->'realized' IS NOT NULL
                )
                SELECT
                    action_type,
                    COUNT(*) AS realized_count,
                    AVG(weekly_profit_delta_cents) AS avg_realized_weekly_profit_delta_cents,
                    SUM(weekly_profit_delta_cents) AS total_realized_weekly_profit_delta_cents,
                    AVG(labor_pct_delta_pp) AS avg_realized_labor_pct_delta_pp,
                    AVG(rev_per_labor_hour_delta_pct) AS avg_realized_rev_per_labor_hour_delta_pct
                FROM realized
                GROUP BY action_type
                ORDER BY
                    COALESCE(SUM(weekly_profit_delta_cents), 0) DESC,
                    COUNT(*) DESC
                LIMIT :lim
                """
                ),
                {"sid": site_id, "s": start_date, "e": end_date, "lim": top_limit},
            )
            .mappings()
            .all()
        )

        try:
            insights_summary = (
                conn.execute(
                    _text(
                        """
                    SELECT
                        COUNT(*) AS insights_generated,
                        COUNT(*) FILTER (WHERE severity IN ('warning', 'opportunity')) AS high_priority_insights
                    FROM insights
                    WHERE site_id = :sid
                      AND cycle_date BETWEEN :s AND :e
                    """
                    ),
                    {"sid": site_id, "s": start_date, "e": end_date},
                )
                .mappings()
                .first()
            )
        except Exception as e:
            logger.warning("insights summary unavailable for scorecard (non-fatal): %s", e)
            insights_summary = {"insights_generated": 0, "high_priority_insights": 0}

    profit_delta = (
        current["total_net_profit_cents"] - previous["total_net_profit_cents"]
        if previous["days_count"] > 0
        else None
    )
    labor_pct_delta = (
        round(current["avg_labor_pct"] - previous["avg_labor_pct"], 2)
        if current["avg_labor_pct"] is not None and previous["avg_labor_pct"] is not None
        else None
    )
    rev_per_labor_delta_pct = _pct_change(
        current["avg_revenue_per_labor_hour_cents"],
        previous["avg_revenue_per_labor_hour_cents"],
    )
    efficiency_delta = (
        round((current_efficiency - previous_efficiency) * 100, 2)
        if current_efficiency is not None and previous_efficiency is not None
        else None
    )

    if current["days_count"] == 0:
        headline = "No profitability data available yet."
    elif profit_delta is None:
        headline = "Baseline established; waiting for prior comparison window."
    elif profit_delta >= 0 and (labor_pct_delta is None or labor_pct_delta <= 0):
        headline = "Net profit and labor efficiency are improving versus prior window."
    elif profit_delta >= 0:
        headline = "Net profit is improving, but labor efficiency is mixed."
    else:
        headline = "Net profit declined versus prior window; prioritize proven actions."

    generated = int((action_summary or {}).get("recommendations_generated") or 0)
    adopted = int((action_summary or {}).get("recommendations_adopted") or 0)
    action_payload = {
        "recommendations_generated": generated,
        "recommendations_adopted": adopted,
        "adoption_rate": round(adopted / generated, 3) if generated > 0 else None,
        "realized_actions": int((action_summary or {}).get("realized_actions") or 0),
        "avg_realized_weekly_profit_delta_cents": (
            round(float(action_summary["avg_realized_weekly_profit_delta_cents"]))
            if action_summary
            and action_summary.get("avg_realized_weekly_profit_delta_cents") is not None
            else None
        ),
        "total_realized_weekly_profit_delta_cents": (
            round(float(action_summary["total_realized_weekly_profit_delta_cents"]))
            if action_summary
            and action_summary.get("total_realized_weekly_profit_delta_cents") is not None
            else None
        ),
        "avg_realized_labor_pct_delta_pp": (
            round(float(action_summary["avg_realized_labor_pct_delta_pp"]), 2)
            if action_summary and action_summary.get("avg_realized_labor_pct_delta_pp") is not None
            else None
        ),
        "avg_realized_rev_per_labor_hour_delta_pct": (
            round(float(action_summary["avg_realized_rev_per_labor_hour_delta_pct"]), 2)
            if action_summary
            and action_summary.get("avg_realized_rev_per_labor_hour_delta_pct") is not None
            else None
        ),
        "top_proven_action_types": [
            {
                "action_type": r.get("action_type"),
                "realized_count": int(r.get("realized_count") or 0),
                "avg_realized_weekly_profit_delta_cents": (
                    round(float(r["avg_realized_weekly_profit_delta_cents"]))
                    if r.get("avg_realized_weekly_profit_delta_cents") is not None
                    else None
                ),
                "total_realized_weekly_profit_delta_cents": (
                    round(float(r["total_realized_weekly_profit_delta_cents"]))
                    if r.get("total_realized_weekly_profit_delta_cents") is not None
                    else None
                ),
                "avg_realized_labor_pct_delta_pp": (
                    round(float(r["avg_realized_labor_pct_delta_pp"]), 2)
                    if r.get("avg_realized_labor_pct_delta_pp") is not None
                    else None
                ),
                "avg_realized_rev_per_labor_hour_delta_pct": (
                    round(float(r["avg_realized_rev_per_labor_hour_delta_pct"]), 2)
                    if r.get("avg_realized_rev_per_labor_hour_delta_pct") is not None
                    else None
                ),
            }
            for r in top_actions
        ],
    }

    xero_financial = None
    current_xero_financial = None
    previous_xero_financial = None
    try:
        xero_financial = get_xero_financial_facts_summary(site_id, start_date, end_date)
    except Exception:
        logger.exception("Unable to compute Xero financial truth summary for scorecard")
    try:
        current_xero_financial = get_xero_financial_facts_summary(site_id, current_start, end_date)
    except Exception:
        logger.exception("Unable to compute current-window Xero financial truth for scorecard")
    try:
        previous_xero_financial = get_xero_financial_facts_summary(
            site_id, previous_start, previous_end
        )
    except Exception:
        logger.exception("Unable to compute previous-window Xero financial truth for scorecard")

    coverage_days = int((xero_financial or {}).get("days_covered") or 0)
    coverage_ratio = round(coverage_days / window_days, 3) if window_days > 0 else 0.0
    fallback_expense = int(overall["total_labor_cost_cents"] or 0) + int(
        overall["total_cogs_cents"] or 0
    )
    fallback_income = int(overall["total_revenue_cents"] or 0)
    payroll_cents = (
        int((xero_financial or {}).get("payroll_cents") or 0)
        if coverage_days > 0 and (xero_financial or {}).get("payroll_cents") is not None
        else None
    )
    labor_truth_cents = (
        payroll_cents
        if payroll_cents is not None and payroll_cents > 0
        else int(overall["total_labor_cost_cents"] or 0)
    )
    overhead_proxy_cents = max(
        0,
        (
            int((xero_financial or {}).get("expense_cents") or 0)
            if coverage_days > 0
            else fallback_expense
        )
        - labor_truth_cents
        - int(overall["total_cogs_cents"] or 0),
    )
    financial_truth = {
        "mode": "xero_factual" if coverage_days > 0 else "estimated_fallback",
        "reporting_breakdown_source": "square_orders",
        "factual_source": "xero_bank_transactions" if coverage_days > 0 else "unavailable",
        "coverage_days": coverage_days,
        "coverage_ratio": coverage_ratio,
        "window_days": window_days,
        "income_cents": (
            int((xero_financial or {}).get("income_cents") or 0)
            if coverage_days > 0
            else fallback_income
        ),
        "expense_cents": (
            int((xero_financial or {}).get("expense_cents") or 0)
            if coverage_days > 0
            else fallback_expense
        ),
        "payroll_cents": payroll_cents,
        "labor_truth_cents": labor_truth_cents,
        "labor_truth_source": (
            "xero_payroll"
            if payroll_cents is not None and payroll_cents > 0
            else "operational_labor_proxy"
        ),
        "overhead_proxy_cents": overhead_proxy_cents,
        "overhead_proxy_source": (
            "xero_expense_minus_labor_cogs" if coverage_days > 0 else "operational_proxy"
        ),
        "overhead_proxy_pct": (
            round(
                (overhead_proxy_cents / int((xero_financial or {}).get("income_cents") or 0)) * 100,
                2,
            )
            if coverage_days > 0 and int((xero_financial or {}).get("income_cents") or 0) > 0
            else None
        ),
        "net_cash_cents": (
            int((xero_financial or {}).get("net_cash_cents") or 0)
            if coverage_days > 0
            else fallback_income - fallback_expense
        ),
        "txn_count": int((xero_financial or {}).get("txn_count") or 0),
        "latest_fact_date": (
            str((xero_financial or {}).get("latest_fact_date"))
            if (xero_financial or {}).get("latest_fact_date")
            else None
        ),
        "latest_update_date": (
            str((xero_financial or {}).get("latest_update_date"))
            if (xero_financial or {}).get("latest_update_date")
            else None
        ),
        "full_days": int((xero_financial or {}).get("full_days") or 0),
        "estimated_fallback": coverage_days <= 0,
    }

    try:
        from analysis.financial_targets import build_financial_target_gap

        target_gap = build_financial_target_gap(
            current_window=current,
            previous_window=previous,
            current_financial_truth=current_xero_financial,
            previous_financial_truth=previous_xero_financial,
        )
    except Exception:
        logger.exception("Unable to compute target-gap summary for scorecard")
        target_gap = {}

    return {
        "site_id": site_id,
        "window": {
            "days": window_days,
            "compare_days": compare_window_days,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "anchor_date": end_date.isoformat(),
        },
        "headline": headline,
        "kpis": overall,
        "trend": {
            "current_window": {
                "start_date": current_start.isoformat(),
                "end_date": end_date.isoformat(),
                **current,
                "efficiency_score": current_efficiency,
                "excess_labor_cents": current_excess,
            },
            "previous_window": {
                "start_date": previous_start.isoformat(),
                "end_date": previous_end.isoformat(),
                **previous,
                "efficiency_score": previous_efficiency,
                "excess_labor_cents": previous_excess,
            },
            "deltas": {
                "net_profit_cents": profit_delta,
                "labor_pct_delta_pp": labor_pct_delta,
                "revenue_per_labor_hour_delta_pct": rev_per_labor_delta_pct,
                "efficiency_score_delta_pp": efficiency_delta,
                "excess_labor_cents_delta": (
                    current_excess - previous_excess
                    if current_excess is not None and previous_excess is not None
                    else None
                ),
            },
            "directions": {
                "net_profit": _trend_direction(profit_delta),
                "labor_pct": _trend_direction(labor_pct_delta, inverse=True),
                "revenue_per_labor_hour": _trend_direction(rev_per_labor_delta_pct),
                "efficiency_score": _trend_direction(efficiency_delta),
                "excess_labor": _trend_direction(
                    (
                        current_excess - previous_excess
                        if current_excess is not None and previous_excess is not None
                        else None
                    ),
                    inverse=True,
                ),
            },
        },
        "actions": action_payload,
        "targets": target_gap,
        "financial_truth": financial_truth,
        "intelligence": {
            "insights_generated": int((insights_summary or {}).get("insights_generated") or 0),
            "high_priority_insights": int(
                (insights_summary or {}).get("high_priority_insights") or 0
            ),
        },
    }


def _freshness_component(
    latest: Optional[date], max_green_days: int, max_yellow_days: int
) -> tuple[str, Optional[int]]:
    if latest is None:
        return "red", None
    age_days = max(0, (date.today() - latest).days)
    if age_days <= max_green_days:
        return "green", age_days
    if age_days <= max_yellow_days:
        return "yellow", age_days
    return "red", age_days


def get_data_health(site_id: str) -> dict:
    """
    Compute per-source data trust status and overall health score.
    Used as a gate for recommendation confidence.
    """
    with engine.connect() as conn:
        # Square/orders freshness + today volume.
        orders_row = (
            conn.execute(
                _text(
                    """
                SELECT
                    MAX(closed_at)::date AS latest_date,
                    COUNT(*) FILTER (WHERE DATE(closed_at) = CURRENT_DATE) AS today_orders
                FROM orders_raw
                WHERE site_id = :sid
                """
                ),
                {"sid": site_id},
            )
            .mappings()
            .first()
        )

        # Deputy roster freshness + next 14-day coverage.
        deputy_row = (
            conn.execute(
                _text(
                    """
                SELECT
                    MAX(shift_date) AS latest_date,
                    COUNT(*) FILTER (WHERE shift_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '14 days') AS next_14d_shifts
                FROM deputy_rosters
                WHERE site_id = :sid
                """
                ),
                {"sid": site_id},
            )
            .mappings()
            .first()
        )

        # Profitability freshness.
        pnl_row = (
            conn.execute(
                _text(
                    """
                SELECT MAX(profit_date) AS latest_date
                FROM daily_profitability
                WHERE site_id = :sid
                """
                ),
                {"sid": site_id},
            )
            .mappings()
            .first()
        )

        # Prediction freshness.
        pred_row = (
            conn.execute(
                _text(
                    """
                SELECT MAX(forecast_date) AS latest_date
                FROM predictions
                WHERE site_id = :sid
                """
                ),
                {"sid": site_id},
            )
            .mappings()
            .first()
        )

        # Xero connection + cost freshness.
        xero_row = (
            conn.execute(
                _text(
                    """
                SELECT
                    EXISTS(SELECT 1 FROM xero_tokens xt WHERE xt.site_id = :sid) AS connected,
                    COUNT(*) FILTER (WHERE source = 'xero') AS xero_cost_items,
                    MAX(updated_at) FILTER (WHERE source = 'xero')::date AS xero_latest_date
                FROM item_costs
                WHERE site_id = :sid
                """
                ),
                {"sid": site_id},
            )
            .mappings()
            .first()
        )

        try:
            xero_financial_row = (
                conn.execute(
                    _text(
                        """
                    SELECT
                        MAX(fact_date) AS latest_date,
                        COUNT(*) FILTER (
                            WHERE fact_date BETWEEN CURRENT_DATE - INTERVAL '14 days' AND CURRENT_DATE
                        ) AS days_14d
                    FROM xero_financial_facts
                    WHERE site_id = :sid
                    """
                    ),
                    {"sid": site_id},
                )
                .mappings()
                .first()
            )
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.warning(
                "xero_financial_facts data-health component unavailable (non-fatal): %s", e
            )
            xero_financial_row = {"latest_date": None, "days_14d": 0}

        try:
            quality_flags = (
                conn.execute(
                    _text(
                        """
                    SELECT flag_date, flag_type, severity, source, reason
                    FROM data_quality_flags
                    WHERE site_id = :sid
                      AND active = TRUE
                      AND flag_type IN ('partial_ingest', 'manual_exclude_forecast')
                      AND flag_date >= (CURRENT_DATE - INTERVAL '14 days')
                    ORDER BY flag_date DESC
                    """
                    ),
                    {"sid": site_id},
                )
                .mappings()
                .all()
            )
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.warning("data_quality_flags unavailable for data health (non-fatal): %s", e)
            quality_flags = []

    components = []

    square_latest = orders_row.get("latest_date") if orders_row else None
    square_status, square_age = _freshness_component(
        square_latest, max_green_days=1, max_yellow_days=2
    )
    components.append(
        {
            "source": "square_orders",
            "status": square_status,
            "latest_date": str(square_latest) if square_latest else None,
            "age_days": square_age,
            "today_orders": int((orders_row or {}).get("today_orders") or 0),
        }
    )

    deputy_latest = deputy_row.get("latest_date") if deputy_row else None
    deputy_status = "green" if deputy_latest and deputy_latest >= date.today() else "yellow"
    if deputy_latest is None:
        deputy_status = "red"
    components.append(
        {
            "source": "deputy_rosters",
            "status": deputy_status,
            "latest_date": str(deputy_latest) if deputy_latest else None,
            "next_14d_shifts": int((deputy_row or {}).get("next_14d_shifts") or 0),
        }
    )

    pnl_latest = pnl_row.get("latest_date") if pnl_row else None
    pnl_status, pnl_age = _freshness_component(pnl_latest, max_green_days=1, max_yellow_days=2)
    components.append(
        {
            "source": "daily_profitability",
            "status": pnl_status,
            "latest_date": str(pnl_latest) if pnl_latest else None,
            "age_days": pnl_age,
        }
    )

    pred_latest = pred_row.get("latest_date") if pred_row else None
    pred_status, pred_age = _freshness_component(pred_latest, max_green_days=1, max_yellow_days=2)
    components.append(
        {
            "source": "predictions",
            "status": pred_status,
            "latest_date": str(pred_latest) if pred_latest else None,
            "age_days": pred_age,
        }
    )

    xero_connected = bool((xero_row or {}).get("connected"))
    xero_latest = (xero_row or {}).get("xero_latest_date")
    xero_items = int((xero_row or {}).get("xero_cost_items") or 0)
    if not xero_connected:
        xero_status = "yellow"
        xero_age = None
    else:
        xero_status, xero_age = _freshness_component(
            xero_latest, max_green_days=7, max_yellow_days=14
        )
        if xero_items <= 0:
            xero_status = "yellow"
    components.append(
        {
            "source": "xero_cogs",
            "status": xero_status,
            "connected": xero_connected,
            "latest_date": str(xero_latest) if xero_latest else None,
            "age_days": xero_age,
            "xero_cost_items": xero_items,
        }
    )

    xero_fin_latest = (xero_financial_row or {}).get("latest_date")
    xero_fin_days_14d = int((xero_financial_row or {}).get("days_14d") or 0)
    if not xero_connected:
        xero_fin_status = "yellow"
        xero_fin_age = None
    else:
        xero_fin_status, xero_fin_age = _freshness_component(
            xero_fin_latest, max_green_days=2, max_yellow_days=7
        )
        if xero_fin_days_14d <= 0:
            xero_fin_status = "yellow"
    components.append(
        {
            "source": "xero_financial_facts",
            "status": xero_fin_status,
            "connected": xero_connected,
            "latest_date": str(xero_fin_latest) if xero_fin_latest else None,
            "age_days": xero_fin_age,
            "days_14d": xero_fin_days_14d,
        }
    )

    if quality_flags:
        components.append(
            {
                "source": "data_quality_flags",
                "status": "red",
                "active_flags": [dict(r) for r in quality_flags],
            }
        )
    else:
        components.append(
            {
                "source": "data_quality_flags",
                "status": "green",
                "active_flags": [],
            }
        )

    score_map = {"green": 1.0, "yellow": 0.5, "red": 0.0}
    component_score = (
        round(sum(score_map[c["status"]] for c in components) / len(components), 3)
        if components
        else 0.0
    )
    overall_status = "green"
    if any(c["status"] == "red" for c in components):
        overall_status = "red"
    elif any(c["status"] == "yellow" for c in components):
        overall_status = "yellow"

    return {
        "site_id": site_id,
        "as_of": datetime.utcnow().isoformat(),
        "status": overall_status,
        "score": component_score,
        "components": components,
    }


# ============================================================
# Intelligence Engine — Insights
# ============================================================


def store_insight(
    site_id: str,
    cycle_date: date,
    insight_type: str,
    severity: str,
    title: str,
    body: str,
    data: dict = None,
    action_type: str = None,
    confidence: float = 0.5,
    rec_id: str = None,
    expires_at: date = None,
) -> Optional[str]:
    """Insert an insight. ON CONFLICT DO NOTHING for idempotency. Returns insight_id or None."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                """
                INSERT INTO insights
                    (site_id, cycle_date, insight_type, severity, title, body,
                     data, action_type, confidence, rec_id, expires_at)
                VALUES
                    (:sid, :cd, :itype, :sev, :title, :body,
                     :data, :atype, :conf, :rid, :exp)
                ON CONFLICT (site_id, cycle_date, insight_type, title) DO NOTHING
                RETURNING insight_id
                """
            ),
            {
                "sid": site_id,
                "cd": cycle_date,
                "itype": insight_type,
                "sev": severity,
                "title": title,
                "body": body,
                "data": _json_dumps(data) if data else None,
                "atype": action_type,
                "conf": confidence,
                "rid": rec_id,
                "exp": expires_at,
            },
        )
        row = result.first()
        conn.commit()

    if row:
        logger.info("Stored insight '%s' for %s", title, cycle_date)
        return str(row[0])
    return None


def get_recent_insights(site_id: str, days: int = 14, types: list[str] = None) -> list[dict]:
    """Fetch recent insights for a site, optionally filtered by type."""
    cutoff = date.today() - timedelta(days=days)
    sql = (
        "SELECT insight_id, cycle_date, insight_type, severity, title, body, "
        "data, action_type, confidence, rec_id, status, expires_at, created_at "
        "FROM insights "
        "WHERE site_id = :sid AND cycle_date >= :cutoff AND status = 'active' "
    )
    params: dict = {"sid": site_id, "cutoff": cutoff}
    if types:
        sql += "AND insight_type = ANY(:types) "
        params["types"] = types
    sql += "ORDER BY cycle_date DESC, severity DESC"

    with engine.connect() as conn:
        rows = conn.execute(_text(sql), params).mappings().all()

    results = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("data"), str):
            try:
                d["data"] = json.loads(d["data"])
            except (json.JSONDecodeError, TypeError):
                pass
        results.append(d)
    return results


def update_insight_status(insight_id: str, status: str = None, rec_id: str = None) -> None:
    """Update an insight's status and/or linked rec_id."""
    sets = []
    params: dict = {"iid": insight_id}
    if status:
        sets.append("status = :status")
        params["status"] = status
    if rec_id:
        sets.append("rec_id = :rid")
        params["rid"] = rec_id
    if not sets:
        return

    with engine.connect() as conn:
        conn.execute(
            _text(f"UPDATE insights SET {', '.join(sets)} WHERE insight_id = :iid"),
            params,
        )
        conn.commit()


# ============================================================
# Intelligence Engine — Learned Patterns
# ============================================================


def store_learned_pattern(
    site_id: str,
    pattern_type: str,
    pattern_key: str,
    description: str,
    pattern_data: dict,
    confidence: float = 0.5,
    sample_size: int = 1,
) -> str:
    """Upsert a learned pattern. ON CONFLICT updates description, data, confidence. Returns pattern_id."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                """
                INSERT INTO learned_patterns
                    (site_id, pattern_type, pattern_key, description,
                     pattern_data, confidence, sample_size)
                VALUES
                    (:sid, :ptype, :pkey, :desc, :pdata, :conf, :ss)
                ON CONFLICT (site_id, pattern_type, pattern_key) DO UPDATE SET
                    description = EXCLUDED.description,
                    pattern_data = EXCLUDED.pattern_data,
                    confidence = GREATEST(learned_patterns.confidence, EXCLUDED.confidence),
                    sample_size = learned_patterns.sample_size + 1,
                    updated_at = NOW()
                RETURNING pattern_id
                """
            ),
            {
                "sid": site_id,
                "ptype": pattern_type,
                "pkey": pattern_key,
                "desc": description,
                "pdata": _json_dumps(pattern_data),
                "conf": confidence,
                "ss": sample_size,
            },
        )
        pattern_id = str(result.scalar())
        conn.commit()

    logger.info("Upserted pattern '%s' (%s)", pattern_key, pattern_type)
    return pattern_id


def get_learned_patterns(
    site_id: str, pattern_type: str = None, min_confidence: float = 0.0
) -> list[dict]:
    """Fetch active learned patterns, optionally filtered by type and min confidence."""
    sql = (
        "SELECT pattern_id, pattern_type, pattern_key, description, "
        "pattern_data, confidence, sample_size, total_impact_cents, "
        "last_validated, suppressed, created_at, updated_at "
        "FROM learned_patterns "
        "WHERE site_id = :sid AND confidence >= :mc "
    )
    params: dict = {"sid": site_id, "mc": min_confidence}
    if pattern_type:
        sql += "AND pattern_type = :ptype "
        params["ptype"] = pattern_type
    # Only return non-suppressed unless caller explicitly asks for low confidence
    if min_confidence >= 0.0:
        sql += "AND suppressed = FALSE "
    sql += "ORDER BY confidence DESC"

    with engine.connect() as conn:
        rows = conn.execute(_text(sql), params).mappings().all()

    results = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("pattern_data"), str):
            try:
                d["pattern_data"] = json.loads(d["pattern_data"])
            except (json.JSONDecodeError, TypeError):
                pass
        results.append(d)
    return results


def update_pattern_confidence(
    pattern_id: str,
    new_confidence: float,
    sample_size_delta: int = 0,
    impact_cents_delta: int = 0,
) -> None:
    """Adjust confidence, sample_size, and impact after outcome measurement."""
    with engine.connect() as conn:
        conn.execute(
            _text(
                """
                UPDATE learned_patterns SET
                    confidence = :conf,
                    sample_size = sample_size + :ss_delta,
                    total_impact_cents = total_impact_cents + :impact_delta,
                    last_validated = NOW(),
                    updated_at = NOW()
                WHERE pattern_id = :pid
                """
            ),
            {
                "pid": pattern_id,
                "conf": max(0.05, min(0.95, new_confidence)),
                "ss_delta": sample_size_delta,
                "impact_delta": impact_cents_delta,
            },
        )
        conn.commit()


def suppress_pattern(pattern_id: str) -> None:
    """Mark a pattern as suppressed (consistently negative outcomes)."""
    with engine.connect() as conn:
        conn.execute(
            _text(
                "UPDATE learned_patterns SET suppressed = TRUE, updated_at = NOW() "
                "WHERE pattern_id = :pid"
            ),
            {"pid": pattern_id},
        )
        conn.commit()
    logger.info("Suppressed pattern %s", pattern_id)


def get_intelligence_summary(site_id: str) -> dict:
    """Quick summary: active insights count, top patterns, learning stats."""
    with engine.connect() as conn:
        # Active insights count
        insight_count = (
            conn.execute(
                _text(
                    "SELECT COUNT(*) FROM insights "
                    "WHERE site_id = :sid AND status = 'active' "
                    "AND cycle_date >= (CURRENT_DATE - INTERVAL '14 days')"
                ),
                {"sid": site_id},
            ).scalar()
            or 0
        )

        # Top patterns by confidence
        top_patterns = (
            conn.execute(
                _text(
                    "SELECT pattern_key, description, confidence, "
                    "total_impact_cents, sample_size "
                    "FROM learned_patterns "
                    "WHERE site_id = :sid AND suppressed = FALSE "
                    "AND confidence >= 0.3 "
                    "ORDER BY confidence DESC LIMIT 5"
                ),
                {"sid": site_id},
            )
            .mappings()
            .all()
        )

        # Learning stats
        stats = (
            conn.execute(
                _text(
                    "SELECT "
                    "COUNT(*) AS total_patterns, "
                    "COUNT(*) FILTER (WHERE confidence >= 0.7) AS high_confidence, "
                    "COUNT(*) FILTER (WHERE suppressed = TRUE) AS suppressed, "
                    "SUM(total_impact_cents) AS total_impact "
                    "FROM learned_patterns WHERE site_id = :sid"
                ),
                {"sid": site_id},
            )
            .mappings()
            .first()
        )

    return {
        "active_insights": int(insight_count),
        "top_patterns": [dict(p) for p in top_patterns],
        "learning_stats": {
            "total_patterns": int((stats or {}).get("total_patterns") or 0),
            "high_confidence": int((stats or {}).get("high_confidence") or 0),
            "suppressed": int((stats or {}).get("suppressed") or 0),
            "total_impact_cents": int((stats or {}).get("total_impact") or 0),
        },
    }
