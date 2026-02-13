"""
Clubhouse Autopilot - KDS Kitchen Performance CSV Importer

Imports Square Kitchen Performance CSV exports into the kds_tickets table,
then matches tickets to orders_raw via timestamp proximity.

Usage:
    python -m data.kds_import data/imports/MILK_kitchen_report.csv
    python -m data.kds_import data/imports/  # all CSVs in directory
"""

import csv
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config.database import engine
from data.storage import get_site_by_location_id

logger = logging.getLogger("autopilot.kds_import")


# Square KDS CSV column names
CSV_COLUMNS = {
    "device_name": "Device Name",
    "ticket_name": "Ticket Name",
    "order_source": "Order Source",
    "num_items": "Number of Items",
    "items_description": "Items in Ticket",
    "completion_time_sec": "Completion Time (seconds)",
    "time_created": "Time Created",
    "time_completed": "Time Completed",
    "time_due": "Time Due",
    "time_recalled": "Time Recalled",
}

# Timezone for parsing naive timestamps from Square CSV
# Square exports in the location's local time
SITE_TIMEZONE = "Australia/Brisbane"


def _text(sql: str):
    from sqlalchemy import text
    return text(sql)


def parse_kds_csv(filepath: Path) -> list[dict]:
    """
    Parse a Square Kitchen Performance CSV export.

    Returns a list of ticket dicts ready for database insertion.
    """
    tickets = []

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        for row in reader:
            # Parse timestamps (naive — local time from Square)
            time_created = _parse_timestamp(row.get(CSV_COLUMNS["time_created"], ""))
            time_completed = _parse_timestamp(row.get(CSV_COLUMNS["time_completed"], ""))

            if not time_created or not time_completed:
                logger.warning("Skipping row with missing timestamps: %s", row)
                continue

            completion_sec = row.get(CSV_COLUMNS["completion_time_sec"], "0")
            try:
                completion_sec = int(completion_sec)
            except (ValueError, TypeError):
                completion_sec = 0

            num_items = row.get(CSV_COLUMNS["num_items"], "0")
            try:
                num_items = int(num_items)
            except (ValueError, TypeError):
                num_items = 0

            ticket = {
                "device_name": row.get(CSV_COLUMNS["device_name"], "").strip(),
                "ticket_name": row.get(CSV_COLUMNS["ticket_name"], "").strip() or None,
                "order_source": row.get(CSV_COLUMNS["order_source"], "").strip(),
                "num_items": num_items,
                "items_description": row.get(CSV_COLUMNS["items_description"], "").strip(),
                "completion_time_sec": completion_sec,
                "time_created": time_created,
                "time_completed": time_completed,
                "time_due": _parse_timestamp(row.get(CSV_COLUMNS["time_due"], "")),
                "time_recalled": _parse_timestamp(row.get(CSV_COLUMNS["time_recalled"], "")),
            }
            tickets.append(ticket)

    logger.info("Parsed %d tickets from %s", len(tickets), filepath.name)
    return tickets


def _parse_timestamp(value: str) -> Optional[datetime]:
    """Parse a timestamp string from Square CSV. Returns None for empty/invalid."""
    if not value or not value.strip():
        return None
    value = value.strip()
    # Strip trailing Z and milliseconds for ISO formats
    clean = value.rstrip("Z")
    if "." in clean:
        clean = clean.split(".")[0]

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(clean, fmt)
        except ValueError:
            continue
    logger.warning("Could not parse timestamp: '%s'", value)
    return None


def store_kds_tickets(site_id: str, tickets: list[dict]) -> int:
    """
    Insert KDS tickets into kds_tickets table.

    Uses ON CONFLICT to skip duplicates (matched on site_id + time_created + items_description).
    Returns count of newly inserted tickets.
    """
    if not tickets:
        return 0

    inserted = 0
    with engine.connect() as conn:
        for ticket in tickets:
            result = conn.execute(
                _text("""
                    INSERT INTO kds_tickets (
                        site_id, device_name, ticket_name, order_source,
                        num_items, items_description, completion_time_sec,
                        time_created, time_completed, time_due, time_recalled
                    ) VALUES (
                        :site_id, :device_name, :ticket_name, :order_source,
                        :num_items, :items_description, :completion_time_sec,
                        :time_created, :time_completed, :time_due, :time_recalled
                    )
                    ON CONFLICT (site_id, time_created, items_description) DO NOTHING
                """),
                {
                    "site_id": site_id,
                    **ticket,
                },
            )
            inserted += result.rowcount

        conn.commit()

    logger.info("Inserted %d new KDS tickets (of %d parsed)", inserted, len(tickets))
    return inserted


def match_tickets_to_orders(site_id: str, tolerance_sec: int = 15) -> int:
    """
    Match KDS tickets to orders_raw by comparing time_created.

    Square's orders_raw.created_at is UTC, KDS CSV is local (AEST/+10).
    We convert and match within a tolerance window.

    Multiple KDS tickets can match the same order (e.g. ESPRESSO + MILK
    stations both get a ticket for the same order).

    Returns count of newly matched tickets.
    """
    matched = 0

    with engine.connect() as conn:
        # Batch match: use a single SQL update with a correlated subquery
        # This is much faster than row-by-row matching
        result = conn.execute(
            _text("""
                UPDATE kds_tickets k
                SET order_id = match.order_id
                FROM (
                    SELECT DISTINCT ON (k2.ticket_id)
                        k2.ticket_id,
                        o.order_id
                    FROM kds_tickets k2
                    JOIN orders_raw o ON o.site_id = k2.site_id
                        AND o.created_at BETWEEN
                            (k2.time_created - INTERVAL '10 hours' - INTERVAL ':tol seconds')
                            AND (k2.time_created - INTERVAL '10 hours' + INTERVAL ':tol seconds')
                    WHERE k2.site_id = :site_id
                      AND k2.order_id IS NULL
                    ORDER BY k2.ticket_id,
                        ABS(EXTRACT(EPOCH FROM (
                            o.created_at - (k2.time_created - INTERVAL '10 hours')
                        )))
                ) match
                WHERE k.ticket_id = match.ticket_id
            """.replace(":tol", str(tolerance_sec))),
            {"site_id": site_id},
        )
        matched = result.rowcount
        conn.commit()

    # Count remaining unmatched
    with engine.connect() as conn:
        remaining = conn.execute(
            _text("""
                SELECT COUNT(*) FROM kds_tickets
                WHERE site_id = :site_id AND order_id IS NULL
            """),
            {"site_id": site_id},
        ).scalar()

    logger.info(
        "Matched %d tickets to orders (%d still unmatched)",
        matched, remaining,
    )
    return matched


def get_kds_summary(site_id: str, target_date: Optional[datetime] = None) -> dict:
    """
    Get summary statistics from KDS data.

    Returns avg completion time, percentiles, breakdown by item type, etc.
    """
    with engine.connect() as conn:
        date_filter = ""
        params = {"site_id": site_id}

        if target_date:
            date_filter = "AND time_created::date = :target_date"
            params["target_date"] = target_date.date() if isinstance(target_date, datetime) else target_date

        result = conn.execute(
            _text(f"""
                SELECT
                    COUNT(*) AS total_tickets,
                    ROUND(AVG(completion_time_sec)) AS avg_completion_sec,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY completion_time_sec) AS median_sec,
                    PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY completion_time_sec) AS p90_sec,
                    MIN(completion_time_sec) AS min_sec,
                    MAX(completion_time_sec) AS max_sec,
                    SUM(num_items) AS total_items,
                    COUNT(*) FILTER (WHERE order_id IS NOT NULL) AS matched_orders
                FROM kds_tickets
                WHERE site_id = :site_id {date_filter}
            """),
            params,
        ).mappings().first()

        return dict(result) if result else {}


def import_csv_file(filepath: Path, site_id: str) -> dict:
    """
    Full import pipeline for a single CSV file.

    1. Parse CSV
    2. Store tickets
    3. Match to orders
    4. Return summary
    """
    tickets = parse_kds_csv(filepath)
    inserted = store_kds_tickets(site_id, tickets)
    matched = match_tickets_to_orders(site_id)
    summary = get_kds_summary(site_id)

    return {
        "file": filepath.name,
        "parsed": len(tickets),
        "inserted": inserted,
        "matched_to_orders": matched,
        "summary": summary,
    }


def import_directory(dirpath: Path, site_id: str) -> list[dict]:
    """Import all CSV files from a directory."""
    results = []
    csv_files = sorted(dirpath.glob("*.csv"))

    if not csv_files:
        logger.warning("No CSV files found in %s", dirpath)
        return results

    for filepath in csv_files:
        logger.info("Importing %s...", filepath.name)
        result = import_csv_file(filepath, site_id)
        results.append(result)

    return results


# ============================================================
# CLI entry point
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if len(sys.argv) < 2:
        print("Usage: python -m data.kds_import <csv_file_or_directory>")
        sys.exit(1)

    target = Path(sys.argv[1])

    # Look up site
    from config.settings import settings
    site = get_site_by_location_id(settings.SQUARE_LOCATION_ID)
    if not site:
        print(f"ERROR: No site found for location {settings.SQUARE_LOCATION_ID}")
        sys.exit(1)

    site_id = str(site["site_id"])
    print(f"Site: {site['name']} ({site_id})")

    if target.is_dir():
        results = import_directory(target, site_id)
        for r in results:
            print(f"\n{r['file']}:")
            print(f"  Parsed: {r['parsed']} tickets")
            print(f"  Inserted: {r['inserted']} new")
            print(f"  Matched to orders: {r['matched_to_orders']}")
    elif target.is_file():
        result = import_csv_file(target, site_id)
        print(f"\n{result['file']}:")
        print(f"  Parsed: {result['parsed']} tickets")
        print(f"  Inserted: {result['inserted']} new")
        print(f"  Matched to orders: {result['matched_to_orders']}")

        summary = result["summary"]
        if summary:
            print(f"\nKDS Summary:")
            print(f"  Total tickets: {summary.get('total_tickets', 0)}")
            print(f"  Avg completion: {summary.get('avg_completion_sec', 0):.0f}s")
            print(f"  Median: {summary.get('median_sec', 0):.0f}s")
            print(f"  P90: {summary.get('p90_sec', 0):.0f}s")
            print(f"  Matched to orders: {summary.get('matched_orders', 0)}")
    else:
        print(f"ERROR: {target} not found")
        sys.exit(1)
