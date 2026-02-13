#!/usr/bin/env python3
"""
Calibrate special_events historical_impact multipliers from actual sales data.

For each event_type (school_holiday, public_holiday), compares average daily
item counts on event dates vs non-event same-day-of-week dates. Updates the
historical_impact column so the prediction engine's Layer 3 reflects reality.

Usage:
    python scripts/calibrate_events.py              # calibrate and update
    python scripts/calibrate_events.py --dry-run    # show multipliers without updating
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from config.database import engine
from config.settings import settings
from data.storage import get_site_by_location_id

logger = logging.getLogger("autopilot.calibrate_events")


def _text(sql: str):
    from sqlalchemy import text
    return text(sql)


def calibrate(site_id: str, *, dry_run: bool = False) -> dict[str, float]:
    """
    Compute and optionally store multipliers for each event_type.

    Uses two data sources:
      1. order_items (item-level, ~43 days) — original approach
      2. daily_sales_history (revenue, full year) — from CSV import + rolling API

    For each event_type:
      1. Find dates in special_events that have sales data.
      2. Compute avg daily metric on event dates.
      3. Compute avg daily metric on non-event dates with same DOW distribution.
      4. multiplier = event_avg / baseline_avg  (clamped to [0.5, 2.0])

    The revenue-based multiplier is preferred when it has more data points,
    otherwise falls back to the item-count approach.

    Returns dict of {event_type: multiplier}.
    """
    results: dict[str, float] = {}

    with engine.connect() as conn:
        # Get distinct event types we have data for
        event_types = conn.execute(
            _text(
                "SELECT DISTINCT event_type FROM special_events "
                "WHERE site_id = :sid AND event_type IS NOT NULL"
            ),
            {"sid": site_id},
        ).scalars().all()

    if not event_types:
        logger.warning("No event types found in special_events")
        return results

    for etype in event_types:
        logger.info("--- Calibrating: %s ---", etype)

        # ---- Source 1: Item counts from order_items (original) ----
        item_multiplier, item_event_days, item_baseline_days = _calibrate_from_items(site_id, etype)

        # ---- Source 2: Revenue from daily_sales_history ----
        rev_multiplier, rev_event_days, rev_baseline_days = _calibrate_from_revenue(site_id, etype)

        # Choose the source with more data
        if rev_event_days > item_event_days and rev_baseline_days > 0:
            multiplier = rev_multiplier
            source = "revenue"
            logger.info("  Using REVENUE-based multiplier (more data: %d vs %d event days)",
                        rev_event_days, item_event_days)
        elif item_event_days > 0 and item_baseline_days > 0:
            multiplier = item_multiplier
            source = "items"
            logger.info("  Using ITEM-based multiplier (%d event days)", item_event_days)
        elif rev_event_days > 0 and rev_baseline_days > 0:
            multiplier = rev_multiplier
            source = "revenue"
            logger.info("  Using REVENUE-based multiplier (items had no data)")
        else:
            multiplier = 1.0
            source = "default"
            logger.info("  No data from either source — keeping 1.0")

        results[etype] = round(multiplier, 3)

        logger.info(
            "  Final multiplier: %.3f (source=%s, items=%.3f/%dd, revenue=%.3f/%dd)",
            multiplier, source,
            item_multiplier, item_event_days,
            rev_multiplier, rev_event_days,
        )

        # Update the table
        if not dry_run and source != "default":
            with engine.connect() as conn:
                conn.execute(
                    _text(
                        "UPDATE special_events SET historical_impact = :impact "
                        "WHERE site_id = :sid AND event_type = :etype"
                    ),
                    {"impact": multiplier, "sid": site_id, "etype": etype},
                )
                conn.commit()
            logger.info("  Updated historical_impact -> %.3f for all %s rows", multiplier, etype)
        elif dry_run:
            logger.info("  [DRY RUN] Would set historical_impact = %.3f", multiplier)

    return results


def _calibrate_from_items(site_id: str, etype: str) -> tuple[float, int, int]:
    """
    Calibrate from order_items (original approach).
    Returns (multiplier, event_days, baseline_days).
    """
    with engine.connect() as conn:
        event_avg_row = conn.execute(
            _text("""
                WITH event_dates AS (
                    SELECT DISTINCT event_date
                    FROM special_events
                    WHERE site_id = :sid AND event_type = :etype
                ),
                daily_counts AS (
                    SELECT
                        ed.event_date,
                        EXTRACT(DOW FROM ed.event_date) AS dow,
                        COUNT(oi.item_id) AS items
                    FROM event_dates ed
                    JOIN orders_raw o ON
                        (o.closed_at AT TIME ZONE 'UTC' AT TIME ZONE 'Australia/Brisbane')::date = ed.event_date
                        AND o.site_id = :sid
                    JOIN order_items oi ON oi.order_id = o.order_id AND oi.site_id = :sid
                    GROUP BY ed.event_date
                )
                SELECT
                    COUNT(*) AS days_with_data,
                    AVG(items) AS avg_items,
                    STRING_AGG(DISTINCT dow::text, ',' ORDER BY dow::text) AS dows
                FROM daily_counts
            """),
            {"sid": site_id, "etype": etype},
        ).mappings().first()

    event_days = event_avg_row["days_with_data"] or 0
    event_avg = float(event_avg_row["avg_items"]) if event_avg_row["avg_items"] else 0.0
    event_dows = event_avg_row["dows"] or ""

    if event_days == 0:
        return 1.0, 0, 0

    logger.info("  [items] Event dates: %d (avg %.1f items/day, dows=%s)", event_days, event_avg, event_dows)

    # Baseline: non-event dates with same DOW distribution
    with engine.connect() as conn:
        baseline_row = conn.execute(
            _text("""
                WITH event_dates AS (
                    SELECT DISTINCT event_date
                    FROM special_events
                    WHERE site_id = :sid AND event_type = :etype
                ),
                non_event_daily AS (
                    SELECT
                        (o.closed_at AT TIME ZONE 'UTC' AT TIME ZONE 'Australia/Brisbane')::date AS sale_date,
                        COUNT(oi.item_id) AS items
                    FROM orders_raw o
                    JOIN order_items oi ON oi.order_id = o.order_id AND oi.site_id = :sid
                    WHERE o.site_id = :sid
                      AND (o.closed_at AT TIME ZONE 'UTC' AT TIME ZONE 'Australia/Brisbane')::date
                          NOT IN (SELECT event_date FROM event_dates)
                      AND EXTRACT(DOW FROM
                          (o.closed_at AT TIME ZONE 'UTC' AT TIME ZONE 'Australia/Brisbane')::date
                      )::text IN (SELECT unnest(string_to_array(:dows, ',')))
                    GROUP BY sale_date
                )
                SELECT
                    COUNT(*) AS days_with_data,
                    AVG(items) AS avg_items
                FROM non_event_daily
            """),
            {"sid": site_id, "etype": etype, "dows": event_dows},
        ).mappings().first()

    baseline_days = baseline_row["days_with_data"] or 0
    baseline_avg = float(baseline_row["avg_items"]) if baseline_row["avg_items"] else 0.0

    if baseline_days == 0 or baseline_avg == 0:
        return 1.0, event_days, 0

    logger.info("  [items] Baseline: %d days (avg %.1f items/day)", baseline_days, baseline_avg)

    raw = event_avg / baseline_avg
    return max(0.5, min(2.0, raw)), event_days, baseline_days


def _calibrate_from_revenue(site_id: str, etype: str) -> tuple[float, int, int]:
    """
    Calibrate from daily_sales_history (revenue-based, full year).
    Returns (multiplier, event_days, baseline_days).
    """
    with engine.connect() as conn:
        # Check if the table exists and has data
        try:
            has_data = conn.execute(
                _text("SELECT COUNT(*) FROM daily_sales_history WHERE site_id = :sid"),
                {"sid": site_id},
            ).scalar()
        except Exception:
            logger.debug("daily_sales_history table not available")
            return 1.0, 0, 0

    if not has_data:
        return 1.0, 0, 0

    with engine.connect() as conn:
        event_row = conn.execute(
            _text("""
                WITH event_dates AS (
                    SELECT DISTINCT event_date
                    FROM special_events
                    WHERE site_id = :sid AND event_type = :etype
                ),
                event_revenue AS (
                    SELECT
                        dsh.sale_date,
                        EXTRACT(DOW FROM dsh.sale_date) AS dow,
                        dsh.net_sales_cents
                    FROM event_dates ed
                    JOIN daily_sales_history dsh ON dsh.sale_date = ed.event_date
                        AND dsh.site_id = :sid
                    WHERE dsh.net_sales_cents > 0
                )
                SELECT
                    COUNT(*) AS days_with_data,
                    AVG(net_sales_cents) AS avg_revenue,
                    STRING_AGG(DISTINCT dow::text, ',' ORDER BY dow::text) AS dows
                FROM event_revenue
            """),
            {"sid": site_id, "etype": etype},
        ).mappings().first()

    event_days = event_row["days_with_data"] or 0
    event_avg = float(event_row["avg_revenue"]) if event_row["avg_revenue"] else 0.0
    event_dows = event_row["dows"] or ""

    if event_days == 0:
        return 1.0, 0, 0

    logger.info("  [revenue] Event dates: %d (avg $%.2f/day, dows=%s)",
                event_days, event_avg / 100, event_dows)

    # Baseline: non-event dates with same DOW distribution
    with engine.connect() as conn:
        baseline_row = conn.execute(
            _text("""
                WITH event_dates AS (
                    SELECT DISTINCT event_date
                    FROM special_events
                    WHERE site_id = :sid AND event_type = :etype
                ),
                non_event_revenue AS (
                    SELECT
                        dsh.sale_date,
                        dsh.net_sales_cents
                    FROM daily_sales_history dsh
                    WHERE dsh.site_id = :sid
                      AND dsh.net_sales_cents > 0
                      AND dsh.sale_date NOT IN (SELECT event_date FROM event_dates)
                      AND EXTRACT(DOW FROM dsh.sale_date)::text
                          IN (SELECT unnest(string_to_array(:dows, ',')))
                )
                SELECT
                    COUNT(*) AS days_with_data,
                    AVG(net_sales_cents) AS avg_revenue
                FROM non_event_revenue
            """),
            {"sid": site_id, "etype": etype, "dows": event_dows},
        ).mappings().first()

    baseline_days = baseline_row["days_with_data"] or 0
    baseline_avg = float(baseline_row["avg_revenue"]) if baseline_row["avg_revenue"] else 0.0

    if baseline_days == 0 or baseline_avg == 0:
        return 1.0, event_days, 0

    logger.info("  [revenue] Baseline: %d days (avg $%.2f/day)", baseline_days, baseline_avg / 100)

    raw = event_avg / baseline_avg
    return max(0.5, min(2.0, raw)), event_days, baseline_days


def main():
    p = argparse.ArgumentParser(description="Calibrate special_events multipliers from sales data")
    p.add_argument("--dry-run", action="store_true", help="Show multipliers without updating DB")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet down noisy loggers
    logging.getLogger("autopilot.storage").setLevel(logging.WARNING)

    site = get_site_by_location_id(settings.SQUARE_LOCATION_ID)
    if not site:
        logger.error("No site found for SQUARE_LOCATION_ID=%s", settings.SQUARE_LOCATION_ID)
        return 1

    site_id = str(site["site_id"])
    logger.info("Calibrating events for site: %s", site["name"])

    multipliers = calibrate(site_id, dry_run=args.dry_run)

    # Summary
    print("\n" + "=" * 60)
    print("Event Calibration Results")
    print("  (uses revenue from daily_sales_history when available)")
    print("=" * 60)
    for etype, mult in sorted(multipliers.items()):
        direction = "neutral" if abs(mult - 1.0) < 0.01 else (
            f"+{(mult-1)*100:.1f}%" if mult > 1 else f"{(mult-1)*100:.1f}%"
        )
        print(f"  {etype:<20} {mult:.3f}x  ({direction})")
    print("=" * 60)

    if args.dry_run:
        print("\n[DRY RUN] No changes written to database.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
