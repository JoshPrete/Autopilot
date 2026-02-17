"""
Clubhouse Autopilot v1.2 - Database Storage
Database operations for all schema tables (Spec Section 6.1)

Handles writing and reading from PostgreSQL using raw SQL via
psycopg2 through SQLAlchemy's engine. All operations are
site-scoped per the multi-site default principle.
"""

import json
import logging
import uuid as _uuid
from datetime import date, datetime, timedelta
from typing import Optional

from config.database import engine
from config.settings import settings

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

    logger.info("Stored %d/%d orders (skipped %d duplicates)",
                inserted, len(parsed_orders), len(parsed_orders) - inserted)
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
        result = conn.execute(
            _text(
                "SELECT workload_units FROM workload_timeline "
                "WHERE site_id = :sid "
                "AND EXTRACT(DOW FROM interval_start) = :dow "
                "AND EXTRACT(HOUR FROM interval_start) = :hr "
                "AND interval_start >= :cutoff "
                "ORDER BY interval_start"
            ),
            {
                "sid": site_id,
                "dow": day_of_week,
                "hr": hour,
                "cutoff": cutoff,
            },
        )
        values = [float(row[0]) for row in result]

    logger.debug(
        "Recent pattern: dow=%d hour=%d -> %d values", day_of_week, hour, len(values)
    )
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
        result = conn.execute(
            _text(
                "SELECT workload_units FROM workload_timeline "
                "WHERE site_id = :sid "
                "AND EXTRACT(WEEK FROM interval_start) = :week "
                "AND EXTRACT(YEAR FROM interval_start) = ANY(:years) "
                "ORDER BY interval_start"
            ),
            {
                "sid": site_id,
                "week": week_number,
                "years": past_years,
            },
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

    day_names = ["Sunday", "Monday", "Tuesday", "Wednesday",
                 "Thursday", "Friday", "Saturday"]

    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "SELECT "
                "EXTRACT(DOW FROM interval_start) as day_of_week, "
                "AVG(workload_units) as avg_workload "
                "FROM workload_timeline "
                "WHERE site_id = :sid "
                "AND interval_start >= :cutoff "
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
        k: v for k, v in prediction.items()
        if k not in ("recent_avg", "yoy_avg", "dow_factor", "event_multiplier",
                      "base_prediction", "confidence")
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


# ============================================================
# Recommendations
# ============================================================


def store_recommendation(
    prediction_id: str,
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
            _text("""
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
            """),
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

    logger.info("Stored daily sales for %s: $%.2f, %d items (source=%s)",
                sale_date, total_revenue / 100 if total_revenue else 0,
                items_count, source)


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
        orders_stored, items_stored, timeline_stored,
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
        result = conn.execute(
            _text("""
                SELECT
                    SUM(wt.workload_units) AS total_wu,
                    SUM(wt.items_count) AS total_items
                FROM workload_timeline wt
                WHERE wt.site_id = :sid
                AND wt.interval_start >= :cutoff
            """),
            {"sid": site_id, "cutoff": cutoff},
        ).mappings().first()

        drink_result = conn.execute(
            _text("""
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
            """),
            {"sid": site_id, "cutoff": cutoff},
        ).mappings().first()

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
            _text("""
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
            """),
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
                "drinks_per_staff": float(row["drinks_per_staff"]) if row["drinks_per_staff"] else None,
            }
            for row in result.mappings()
        ]


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
            _text(
                "SELECT score_key, cost_cents FROM item_costs "
                "WHERE site_id = :sid"
            ),
            {"sid": site_id},
        )
        return {row[0]: int(row[1]) for row in result}


# ============================================================
# Daily Profitability
# ============================================================


def store_daily_profitability(site_id: str, profit_date: date, metrics: dict) -> None:
    """Upsert a daily profitability record."""
    with engine.connect() as conn:
        conn.execute(
            _text("""
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
            """),
            {
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
            },
        )
        conn.commit()

    logger.info(
        "Stored daily profitability for %s: rev=$%.2f, net=$%.2f",
        profit_date,
        metrics["revenue_cents"] / 100,
        (metrics.get("net_profit_cents") or 0) / 100,
    )


def get_daily_profitability(
    site_id: str, start_date: date, end_date: date
) -> list[dict]:
    """Retrieve daily P&L records for a date range."""
    with engine.connect() as conn:
        result = conn.execute(
            _text("""
                SELECT profit_date, revenue_cents, labor_cost_cents,
                       cogs_cents, gross_profit_cents, net_profit_cents,
                       order_count, item_count, drink_count, labor_hours,
                       revenue_per_labor_hour, cost_per_drink, labor_pct
                FROM daily_profitability
                WHERE site_id = :sid AND profit_date BETWEEN :s AND :e
                ORDER BY profit_date
            """),
            {"sid": site_id, "s": start_date, "e": end_date},
        )
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
    """Upsert OAuth2 tokens for a Xero connection."""
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
                "at": access_token,
                "rt": refresh_token,
                "ea": expires_at,
                "sc": scope,
            },
        )
        conn.commit()

    logger.info("Stored Xero tokens for site %s (tenant %s)", site_id, tenant_id)


def get_xero_tokens(site_id: str) -> Optional[dict]:
    """Fetch current Xero OAuth2 tokens for a site."""
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
        return dict(row) if row else None


def update_xero_tokens(
    site_id: str,
    access_token: str,
    refresh_token: str,
    expires_at: datetime,
) -> None:
    """Update tokens after a refresh (keeps tenant_id unchanged)."""
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
                "at": access_token,
                "rt": refresh_token,
                "ea": expires_at,
            },
        )
        conn.commit()

    logger.info("Refreshed Xero tokens for site %s", site_id)


def get_xero_line_mapping(site_id: str, description: str) -> Optional[str]:
    """Check cached mapping: Xero line description → score_key."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "SELECT score_key FROM xero_line_mappings "
                "WHERE site_id = :sid AND xero_description = :desc"
            ),
            {"sid": site_id, "desc": description},
        ).scalar()
        return result


def store_xero_line_mapping(
    site_id: str,
    description: str,
    score_key: str,
    confidence: str = "unconfirmed",
) -> None:
    """Cache an LLM-generated mapping from Xero description to score_key."""
    with engine.connect() as conn:
        conn.execute(
            _text(
                "INSERT INTO xero_line_mappings "
                "(site_id, xero_description, score_key, confidence) "
                "VALUES (:sid, :desc, :sk, :conf) "
                "ON CONFLICT (site_id, xero_description) DO UPDATE SET "
                "score_key = EXCLUDED.score_key, "
                "confidence = EXCLUDED.confidence"
            ),
            {
                "sid": site_id,
                "desc": description,
                "sk": score_key,
                "conf": confidence,
            },
        )
        conn.commit()

    logger.debug("Cached Xero mapping: '%s' → %s", description, score_key)


def get_all_xero_mappings(site_id: str) -> list[dict]:
    """Get all cached Xero line-item mappings for review/display."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "SELECT xero_description, score_key, confidence, created_at "
                "FROM xero_line_mappings "
                "WHERE site_id = :sid "
                "ORDER BY created_at DESC"
            ),
            {"sid": site_id},
        )
        return [dict(row) for row in result.mappings()]


def get_data_freshness(site_id: str) -> Optional[str]:
    """Get the most recent closed_at date from orders_raw."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "SELECT MAX(closed_at)::date AS latest "
                "FROM orders_raw WHERE site_id = :sid"
            ),
            {"sid": site_id},
        ).scalar()
        return str(result) if result else None
