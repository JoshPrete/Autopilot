#!/usr/bin/env python3
"""
Re-score all existing orders with updated item classification and workload scores.

This script:
1. Reads all orders from orders_raw (payload has full Square data)
2. Re-parses and re-processes with current scoring (new ITEM_CATALOG, calibrated times)
3. Clears old order_items
4. Re-inserts with new scores
5. Re-aggregates workload_timeline

Usage:
    python scripts/rescore_orders.py
    python scripts/rescore_orders.py --dry-run   # preview without writing
"""

from __future__ import annotations

import json
import logging
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from config.database import engine
from data.ingestion import parse_order
from data.processing import process_orders_batch
from data.storage import (
    get_site_by_location_id,
    store_order_items,
    store_timeline,
)
from config.settings import settings

logger = logging.getLogger("autopilot.rescore")


def _text(sql: str):
    from sqlalchemy import text
    return text(sql)


def fetch_all_raw_orders(site_id: str) -> list[dict]:
    """Read all raw order payloads from orders_raw."""
    with engine.connect() as conn:
        result = conn.execute(
            _text(
                "SELECT order_id, payload FROM orders_raw "
                "WHERE site_id = :sid ORDER BY created_at"
            ),
            {"sid": site_id},
        )
        rows = result.mappings().all()

    orders = []
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        orders.append(payload)

    return orders


def clear_order_items(site_id: str) -> int:
    """Delete all order_items for a site."""
    with engine.connect() as conn:
        result = conn.execute(
            _text("DELETE FROM order_items WHERE site_id = :sid"),
            {"sid": site_id},
        )
        conn.commit()
        return result.rowcount


def clear_timeline(site_id: str) -> int:
    """Delete all workload_timeline for a site."""
    with engine.connect() as conn:
        result = conn.execute(
            _text("DELETE FROM workload_timeline WHERE site_id = :sid"),
            {"sid": site_id},
        )
        conn.commit()
        return result.rowcount


def main():
    import argparse
    p = argparse.ArgumentParser(description="Re-score all orders with updated workload engine")
    p.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")
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
        logger.error("No site found for location %s", settings.SQUARE_LOCATION_ID)
        return 1

    site_id = str(site["site_id"])
    site_name = site["name"]
    logger.info("Re-scoring orders for %s (%s)", site_name, site_id)

    # 1. Fetch all raw orders
    logger.info("Fetching all raw orders from orders_raw...")
    raw_orders = fetch_all_raw_orders(site_id)
    logger.info("Found %d raw orders", len(raw_orders))

    if not raw_orders:
        logger.warning("No orders to re-score")
        return 0

    # 2. Re-parse (extract line items from payload)
    logger.info("Re-parsing orders...")
    parsed = []
    for raw in raw_orders:
        try:
            parsed.append(parse_order(raw))
        except Exception as e:
            logger.warning("Failed to parse order %s: %s", raw.get("id"), e)

    logger.info("Parsed %d orders", len(parsed))

    # 3. Re-process with new scoring engine
    logger.info("Re-processing with updated scores...")
    pipeline_result = process_orders_batch(parsed)
    summary = pipeline_result["summary"]

    logger.info(
        "Re-scored: %d orders, %d items (drinks=%d, food=%d), %.1f workload units",
        summary["orders_count"],
        summary["items_count"],
        summary.get("drink_count", 0),
        summary.get("food_count", 0),
        summary["total_workload_units"],
    )
    logger.info(
        "  Milk drinks: %d (%.0f%%), Avg units/order: %.2f",
        summary["milk_drink_count"],
        summary["milk_drink_pct"],
        summary["avg_units_per_order"],
    )

    # Show drink_key distribution
    key_counts = defaultdict(int)
    category_counts = defaultdict(int)
    for order in pipeline_result["orders"]:
        for li in order.get("line_items", []):
            w = li.get("workload", {})
            key_counts[w.get("drink_key", "unknown")] += 1
            category_counts[w.get("category", "unknown")] += 1

    logger.info("Item classification breakdown:")
    for cat in ["drink", "food", "retail"]:
        logger.info("  %s: %d items", cat, category_counts.get(cat, 0))
    logger.info("Top score keys:")
    for key, cnt in sorted(key_counts.items(), key=lambda x: -x[1])[:15]:
        logger.info("  %-20s %d", key, cnt)

    if args.dry_run:
        logger.info("DRY RUN — no changes written to DB")
        return 0

    # 4. Clear old data
    logger.info("Clearing old order_items...")
    deleted_items = clear_order_items(site_id)
    logger.info("Deleted %d old order_items", deleted_items)

    logger.info("Clearing old workload_timeline...")
    deleted_timeline = clear_timeline(site_id)
    logger.info("Deleted %d old timeline intervals", deleted_timeline)

    # 5. Re-insert with new scores
    logger.info("Storing re-scored order_items...")
    items_stored = store_order_items(site_id, pipeline_result["orders"])
    logger.info("Stored %d order_items", items_stored)

    logger.info("Storing re-aggregated timeline...")
    timeline_stored = store_timeline(site_id, pipeline_result["timeline"])
    logger.info("Stored %d timeline intervals", timeline_stored)

    logger.info("=== RE-SCORE COMPLETE ===")
    logger.info("  Orders: %d", summary["orders_count"])
    logger.info("  Items re-scored: %d", items_stored)
    logger.info("  Timeline intervals: %d", timeline_stored)
    logger.info("  Total workload: %.1f units", summary["total_workload_units"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
