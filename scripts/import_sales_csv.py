#!/usr/bin/env python3
"""
Import Square Sales Summary CSV into daily_sales_history.

Parses the pivot-table CSV exported from Square Dashboard (rows = metrics,
columns = dates) and stores daily revenue totals. Estimates item counts
from revenue using the avg revenue-per-item from the recent API overlap period.

Usage:
    python scripts/import_sales_csv.py "data/imports/sales-summary-2025-01-01-2025-12-31 (3).csv"
    python scripts/import_sales_csv.py --dry-run "data/imports/sales-summary*.csv"
"""

from __future__ import annotations

import argparse
import csv
import glob
import logging
import re
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from config.database import engine
from config.settings import settings
from data.storage import get_site_by_location_id

logger = logging.getLogger("autopilot.import_sales_csv")


# Row labels we care about -> column names in daily_sales_history
ROW_MAP = {
    "Product Sales": "product_sales_cents",
    "Net Sales": "net_sales_cents",
    "Gross Sales": "gross_sales_cents",
    "Tax": "tax_cents",
    "Discounts & Comps": "discounts_cents",
    "Tip": "tips_cents",
    "Total Collected": "total_collected_cents",
}


def _text(sql: str):
    from sqlalchemy import text
    return text(sql)


def parse_dollar(val: str) -> int:
    """Parse '$1,798.39' or '-$114.83' to cents (179839 or -11483)."""
    if not val or val.strip() == "":
        return 0
    cleaned = val.strip().replace('"', '')
    # Handle negative: -$114.83
    negative = cleaned.startswith("-")
    cleaned = cleaned.replace("-", "").replace("$", "").replace(",", "")
    try:
        cents = round(float(cleaned) * 100)
    except ValueError:
        return 0
    return -cents if negative else cents


def parse_date(date_str: str) -> date | None:
    """Parse DD/MM/YYYY to date object."""
    try:
        return datetime.strptime(date_str.strip(), "%d/%m/%Y").date()
    except (ValueError, AttributeError):
        return None


def parse_csv(filepath: str) -> list[dict]:
    """
    Parse a Square Sales Summary CSV pivot table.

    Returns list of dicts, one per date, with keys matching daily_sales_history columns.
    Skips dates where all revenue values are zero (closed days).
    """
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        logger.error("Empty CSV: %s", filepath)
        return []

    # Row 0 is the header: Sales, DD/MM/YYYY, DD/MM/YYYY, ...
    header = rows[0]
    dates = []
    for col_idx in range(1, len(header)):
        d = parse_date(header[col_idx])
        if d:
            dates.append((col_idx, d))
        else:
            logger.debug("Skipping unparseable header column %d: '%s'", col_idx, header[col_idx])

    if not dates:
        logger.error("No valid date columns found in %s", filepath)
        return []

    logger.info("CSV has %d date columns (%s to %s)", len(dates), dates[0][1], dates[-1][1])

    # Build label -> row mapping
    label_rows: dict[str, list[str]] = {}
    for row in rows[1:]:
        if not row:
            continue
        label = row[0].strip()
        if label:
            label_rows[label] = row

    # Extract data per date
    daily_records = []
    for col_idx, sale_date in dates:
        record = {"sale_date": sale_date}
        all_zero = True

        for row_label, col_name in ROW_MAP.items():
            row_data = label_rows.get(row_label)
            if row_data and col_idx < len(row_data):
                cents = parse_dollar(row_data[col_idx])
            else:
                cents = 0
            record[col_name] = cents
            if col_name in ("net_sales_cents", "product_sales_cents") and cents != 0:
                all_zero = False

        if all_zero:
            logger.debug("Skipping %s (closed / zero revenue)", sale_date)
            continue

        daily_records.append(record)

    logger.info("Parsed %d active trading days from %s", len(daily_records), filepath)
    return daily_records


def compute_avg_revenue_per_item(site_id: str) -> float | None:
    """
    Compute average net revenue per item from the API overlap period
    (recent order_items data joined with daily revenue).

    Falls back to computing from order_items alone if daily_sales_history
    doesn't have API rows yet.
    """
    with engine.connect() as conn:
        # Use order_items to compute: total net revenue / total item count
        result = conn.execute(
            _text("""
                SELECT
                    SUM(o.total_money_cents) AS total_revenue,
                    COUNT(DISTINCT o.order_id) AS order_count,
                    COUNT(oi.item_id) AS item_count
                FROM orders_raw o
                JOIN order_items oi ON oi.order_id = o.order_id AND oi.site_id = :sid
                WHERE o.site_id = :sid
            """),
            {"sid": site_id},
        ).mappings().first()

    if not result or not result["item_count"] or result["item_count"] < 50:
        logger.warning("Insufficient order data to compute avg revenue per item")
        return None

    # total_money_cents includes tax; use it as approximate revenue per item
    avg = result["total_revenue"] / result["item_count"]
    logger.info(
        "Avg revenue per item: $%.2f (from %d items across %d orders)",
        avg / 100, result["item_count"], result["order_count"],
    )
    return avg


def store_records(
    site_id: str,
    records: list[dict],
    avg_rev_per_item: float | None,
    dry_run: bool = False,
) -> int:
    """Insert/upsert daily records into daily_sales_history."""
    if dry_run:
        for r in records[:5]:
            net = r["net_sales_cents"]
            est_items = round(net / avg_rev_per_item) if avg_rev_per_item and net > 0 else None
            logger.info(
                "  [DRY RUN] %s: net=$%.2f  gross=$%.2f  est_items=%s",
                r["sale_date"], net / 100, r["gross_sales_cents"] / 100,
                est_items,
            )
        if len(records) > 5:
            logger.info("  ... and %d more rows", len(records) - 5)
        return 0

    stored = 0
    with engine.connect() as conn:
        for r in records:
            net = r["net_sales_cents"]
            est_items = round(net / avg_rev_per_item) if avg_rev_per_item and net > 0 else None

            conn.execute(
                _text("""
                    INSERT INTO daily_sales_history
                        (site_id, sale_date, gross_sales_cents, net_sales_cents,
                         product_sales_cents, tax_cents, discounts_cents, tips_cents,
                         total_collected_cents, items_estimated, source)
                    VALUES
                        (:sid, :sale_date, :gross, :net, :product, :tax,
                         :discounts, :tips, :total_collected, :items_est, 'csv')
                    ON CONFLICT (site_id, sale_date) DO UPDATE SET
                        gross_sales_cents = EXCLUDED.gross_sales_cents,
                        net_sales_cents = EXCLUDED.net_sales_cents,
                        product_sales_cents = EXCLUDED.product_sales_cents,
                        tax_cents = EXCLUDED.tax_cents,
                        discounts_cents = EXCLUDED.discounts_cents,
                        tips_cents = EXCLUDED.tips_cents,
                        total_collected_cents = EXCLUDED.total_collected_cents,
                        items_estimated = EXCLUDED.items_estimated,
                        source = CASE
                            WHEN daily_sales_history.source = 'api' THEN 'api'
                            ELSE EXCLUDED.source
                        END,
                        imported_at = NOW()
                """),
                {
                    "sid": site_id,
                    "sale_date": r["sale_date"],
                    "gross": r["gross_sales_cents"],
                    "net": r["net_sales_cents"],
                    "product": r["product_sales_cents"],
                    "tax": r["tax_cents"],
                    "discounts": r["discounts_cents"],
                    "tips": r["tips_cents"],
                    "total_collected": r["total_collected_cents"],
                    "items_est": est_items,
                },
            )
            stored += 1

        conn.commit()

    logger.info("Stored %d rows to daily_sales_history", stored)
    return stored


def main():
    p = argparse.ArgumentParser(
        description="Import Square Sales Summary CSV into daily_sales_history",
    )
    p.add_argument(
        "csv_paths",
        nargs="+",
        help="Path(s) to CSV file(s). Supports glob patterns.",
    )
    p.add_argument("--dry-run", action="store_true", help="Parse and show data without writing to DB")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Resolve site
    site = get_site_by_location_id(settings.SQUARE_LOCATION_ID)
    if not site:
        logger.error("No site found for SQUARE_LOCATION_ID=%s", settings.SQUARE_LOCATION_ID)
        return 1

    site_id = str(site["site_id"])
    logger.info("Importing for site: %s (%s)", site["name"], site_id)

    # Expand globs
    csv_files = []
    for pattern in args.csv_paths:
        matched = glob.glob(pattern)
        if matched:
            csv_files.extend(matched)
        else:
            logger.warning("No files matched: %s", pattern)

    if not csv_files:
        logger.error("No CSV files found")
        return 1

    # Compute avg revenue per item from existing API data
    avg_rev_per_item = compute_avg_revenue_per_item(site_id)
    if avg_rev_per_item:
        logger.info("Using avg revenue per item: $%.2f", avg_rev_per_item / 100)
    else:
        logger.warning("No avg revenue per item available — items_estimated will be NULL")

    total_stored = 0
    for csv_file in csv_files:
        logger.info("Processing: %s", csv_file)
        records = parse_csv(csv_file)
        if records:
            total_stored += store_records(site_id, records, avg_rev_per_item, args.dry_run)

    # Summary
    print(f"\n{'='*50}")
    print(f"Import complete: {total_stored} rows stored from {len(csv_files)} file(s)")
    if args.dry_run:
        print("[DRY RUN] No changes written to database.")
    print(f"{'='*50}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
