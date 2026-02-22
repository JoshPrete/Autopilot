#!/usr/bin/env python3
"""
Populate the special_events table with QLD school holidays and public holidays.

Seeds 2025–2026 QLD school holidays and 2026 Brisbane public holidays so the
prediction engine's Layer 3 (special events multiplier) has data to work with.

Usage:
    python scripts/populate_events.py              # populate events
    python scripts/populate_events.py --clear      # clear existing then repopulate
    python scripts/populate_events.py --dry-run    # preview without writing
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from config.database import engine
from config.settings import settings
from data.storage import get_site_by_location_id

logger = logging.getLogger("autopilot.populate_events")


def _text(sql: str):
    from sqlalchemy import text

    return text(sql)


# ---------------------------------------------------------------------------
# QLD School Holidays (one row per date in each range)
# ---------------------------------------------------------------------------
SCHOOL_HOLIDAYS: list[tuple[str, date, date]] = [
    # 2025
    ("QLD School Holidays (Term 1 Break)", date(2025, 4, 5), date(2025, 4, 21)),
    ("QLD School Holidays (Term 2 Break)", date(2025, 6, 28), date(2025, 7, 13)),
    ("QLD School Holidays (Term 3 Break)", date(2025, 9, 20), date(2025, 10, 6)),
    ("QLD School Holidays (Summer)", date(2025, 12, 13), date(2026, 1, 26)),
    # 2026
    ("QLD School Holidays (Term 1 Break)", date(2026, 4, 3), date(2026, 4, 19)),
    ("QLD School Holidays (Term 2 Break)", date(2026, 6, 27), date(2026, 7, 12)),
    ("QLD School Holidays (Term 3 Break)", date(2026, 9, 19), date(2026, 10, 5)),
    ("QLD School Holidays (Summer)", date(2026, 12, 12), date(2027, 1, 26)),
]

# ---------------------------------------------------------------------------
# 2026 QLD Public Holidays (Brisbane)
# ---------------------------------------------------------------------------
PUBLIC_HOLIDAYS: list[tuple[str, date]] = [
    ("New Year's Day", date(2026, 1, 1)),
    ("Australia Day", date(2026, 1, 26)),
    ("Good Friday", date(2026, 4, 3)),
    ("Easter Saturday", date(2026, 4, 4)),
    ("Easter Sunday", date(2026, 4, 5)),
    ("Easter Monday", date(2026, 4, 6)),
    ("Anzac Day", date(2026, 4, 25)),
    ("Labour Day", date(2026, 5, 4)),
    ("Royal Queensland Show (Ekka)", date(2026, 8, 12)),
    ("King's Birthday", date(2026, 10, 5)),
    ("Christmas Day", date(2026, 12, 25)),
    ("Boxing Day", date(2026, 12, 26)),
]


def _date_range(start: date, end: date):
    """Yield each date from start to end inclusive."""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def populate(site_id: str, *, dry_run: bool = False, clear: bool = False) -> int:
    """Insert school holiday and public holiday rows. Returns count inserted."""

    if clear and not dry_run:
        with engine.connect() as conn:
            conn.execute(
                _text("DELETE FROM special_events WHERE site_id = :sid"),
                {"sid": site_id},
            )
            conn.commit()
        logger.info("Cleared existing special_events for site %s", site_id)

    rows: list[dict] = []

    # School holidays — one row per date
    for name, start, end in SCHOOL_HOLIDAYS:
        for d in _date_range(start, end):
            rows.append(
                {
                    "sid": site_id,
                    "name": name,
                    "d": d,
                    "etype": "school_holiday",
                    "recurrence": "annual",
                    "impact": 1.0,
                }
            )

    # Public holidays — one row each
    for name, d in PUBLIC_HOLIDAYS:
        rows.append(
            {
                "sid": site_id,
                "name": name,
                "d": d,
                "etype": "public_holiday",
                "recurrence": "annual",
                "impact": 1.0,
            }
        )

    if dry_run:
        logger.info("[DRY RUN] Would insert %d event rows", len(rows))
        for r in rows[:10]:
            logger.info("  %s | %s | %s", r["d"], r["etype"], r["name"])
        if len(rows) > 10:
            logger.info("  ... and %d more", len(rows) - 10)
        return len(rows)

    insert_sql = _text(
        """
        INSERT INTO special_events
            (site_id, event_name, event_date, event_type, recurrence, historical_impact)
        VALUES
            (:sid, :name, :d, :etype, :recurrence, :impact)
    """
    )

    with engine.connect() as conn:
        for r in rows:
            conn.execute(insert_sql, r)
        conn.commit()

    logger.info("Inserted %d event rows", len(rows))
    return len(rows)


def main():
    p = argparse.ArgumentParser(description="Populate special_events with QLD holidays")
    p.add_argument("--clear", action="store_true", help="Clear existing events before inserting")
    p.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    site = get_site_by_location_id(settings.SQUARE_LOCATION_ID)
    if not site:
        logger.error("No site found for SQUARE_LOCATION_ID=%s", settings.SQUARE_LOCATION_ID)
        return 1

    site_id = str(site["site_id"])
    logger.info("Populating events for site: %s", site["name"])

    count = populate(site_id, dry_run=args.dry_run, clear=args.clear)

    # Verify
    if not args.dry_run:
        with engine.connect() as conn:
            result = (
                conn.execute(
                    _text("SELECT COUNT(*) AS cnt FROM special_events WHERE site_id = :sid"),
                    {"sid": site_id},
                )
                .mappings()
                .first()
            )
        logger.info("Total rows in special_events: %d", result["cnt"])

        # Breakdown by type
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    _text(
                        "SELECT event_type, COUNT(*) AS cnt "
                        "FROM special_events WHERE site_id = :sid "
                        "GROUP BY event_type ORDER BY event_type"
                    ),
                    {"sid": site_id},
                )
                .mappings()
                .all()
            )
        for r in rows:
            logger.info("  %s: %d rows", r["event_type"], r["cnt"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
