#!/usr/bin/env python3
"""
Clubhouse Autopilot v1.2 - Weekly Review Runner
Monday 8am cron job (Spec Section 13.2)

Generates the Weekly Ops Review report covering the previous
Monday-Sunday period. Sends SMS summary to manager and
optionally refreshes historical patterns.

Usage:
    # Run for last week (auto-detects)
    python scripts/weekly_review.py --site-id SITE_UUID

    # Specific week ending date
    python scripts/weekly_review.py --site-id SITE_UUID --week-end 2026-02-09

    # Dry run
    python scripts/weekly_review.py --site-id SITE_UUID --dry-run

    # Include pattern refresh
    python scripts/weekly_review.py --site-id SITE_UUID --refresh-patterns
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from analysis.reporting import generate_weekly_review, generate_multi_site_comparison
from config.settings import settings
from data.storage import get_site, get_site_by_location_id
from delivery.sender import send_to_manager
from models.prediction import refresh_historical_patterns

logger = logging.getLogger("autopilot.weekly")


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="weekly_review",
        description="Clubhouse Autopilot weekly ops review.",
    )
    p.add_argument(
        "--site-id",
        help="Site UUID. If not provided, looks up by SQUARE_LOCATION_ID.",
    )
    p.add_argument(
        "--week-end",
        help="Week ending date (Sunday, YYYY-MM-DD). Default: last Sunday.",
    )
    p.add_argument(
        "--refresh-patterns",
        action="store_true",
        help="Also refresh DoW historical patterns.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate report but don't send SMS or store.",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging.",
    )
    return p.parse_args(argv)


def resolve_site(site_id: str = None) -> tuple[str, str]:
    """Resolve site_id and site_name."""
    if site_id:
        site = get_site(site_id)
        if site:
            return site["site_id"], site["name"]
        return site_id, "Unknown Site"

    loc_id = settings.SQUARE_LOCATION_ID
    if loc_id:
        site = get_site_by_location_id(loc_id)
        if site:
            return str(site["site_id"]), site["name"]

    logger.error("Cannot resolve site. Provide --site-id or set SQUARE_LOCATION_ID.")
    sys.exit(1)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Check required env
    if not os.getenv("DATABASE_URL"):
        logger.error("DATABASE_URL not set. Create .env from .env.example.")
        return 2

    site_id, site_name = resolve_site(args.site_id)

    # Determine week ending date
    if args.week_end:
        week_end = date.fromisoformat(args.week_end)
    else:
        today = date.today()
        days_since_sunday = (today.weekday() + 1) % 7
        if days_since_sunday == 0:
            days_since_sunday = 7
        week_end = today - timedelta(days=days_since_sunday)

    week_start = week_end - timedelta(days=6)

    logger.info(
        "Clubhouse Autopilot - Weekly Review\n"
        "  Site: %s (%s)\n"
        "  Week: %s to %s\n"
        "  Dry run: %s",
        site_name,
        site_id,
        week_start,
        week_end,
        args.dry_run,
    )

    try:
        # 1. Generate the review
        review = generate_weekly_review(
            site_id=site_id,
            site_name=site_name,
            week_end=week_end,
        )

        # 2. Print the full report
        print("\n" + review["report_text"] + "\n")

        # 3. Print insights
        if review["insights"]:
            logger.info("Insights:")
            for insight in review["insights"]:
                logger.info("  - %s", insight)

        # 4. Send SMS summary
        if not args.dry_run:
            sms_results = send_to_manager(site_id, review["sms_summary"])
            delivered = sum(1 for r in sms_results if r["delivered"])
            logger.info("SMS summary sent: %d/%d delivered", delivered, len(sms_results))
        else:
            logger.info("DRY RUN: Skipping SMS")
            logger.info("SMS would be:\n%s", review["sms_summary"])

        # 5. Refresh historical patterns (optional)
        if args.refresh_patterns and not args.dry_run:
            logger.info("Refreshing historical DoW patterns...")
            pattern_result = refresh_historical_patterns(site_id)
            logger.info("Patterns refreshed: %s", pattern_result)
        elif args.refresh_patterns:
            logger.info("DRY RUN: Skipping pattern refresh")

        # 6. Check for alerts
        accuracy = review.get("accuracy", {})
        if accuracy.get("alert"):
            logger.warning("ALERT: %s", accuracy.get("alert_reason"))
            if not args.dry_run:
                send_to_manager(
                    site_id,
                    f"Autopilot Alert: {accuracy['alert_reason']}",
                )

        logger.info("Weekly review complete (review_id: %s)", review.get("review_id"))
        return 0

    except Exception:
        logger.exception("Weekly review failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
