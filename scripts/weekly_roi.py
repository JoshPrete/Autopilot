#!/usr/bin/env python3
"""
Generate a weekly ROI report from daily profitability metrics.

Usage:
    python scripts/weekly_roi.py --site-id SITE_UUID
    python scripts/weekly_roi.py --site-id SITE_UUID --week-end 2026-02-15
    python scripts/weekly_roi.py --site-id SITE_UUID --json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from analysis.reporting import format_weekly_roi_sms, generate_weekly_roi_report
from config.settings import settings
from data.storage import get_site, get_site_by_location_id
from delivery.sender import send_to_manager

logger = logging.getLogger("autopilot.weekly_roi")


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="weekly_roi",
        description="Generate weekly ROI report for a site.",
    )
    p.add_argument("--site-id", help="Site UUID. Falls back to SQUARE_LOCATION_ID lookup.")
    p.add_argument("--week-end", help="Week ending date (YYYY-MM-DD).")
    p.add_argument("--json", action="store_true", help="Output JSON instead of text.")
    p.add_argument(
        "--send-sms", action="store_true", help="Send weekly ROI summary SMS to manager."
    )
    p.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging.")
    return p.parse_args(argv)


def resolve_site(site_id: str = None) -> tuple[str, str]:
    if site_id:
        site = get_site(site_id)
        if site:
            return str(site["site_id"]), site["name"]
        return site_id, "Unknown Site"

    if settings.SQUARE_LOCATION_ID:
        site = get_site_by_location_id(settings.SQUARE_LOCATION_ID)
        if site:
            return str(site["site_id"]), site["name"]

    logger.error("Cannot resolve site. Provide --site-id or set SQUARE_LOCATION_ID.")
    raise SystemExit(1)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not os.getenv("DATABASE_URL"):
        logger.error("DATABASE_URL not set. Create .env from .env.example.")
        return 2

    site_id, site_name = resolve_site(args.site_id)
    week_end = date.fromisoformat(args.week_end) if args.week_end else None

    report = generate_weekly_roi_report(site_id=site_id, site_name=site_name, week_end=week_end)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("\n" + report["report_text"] + "\n")

    if args.send_sms:
        sms_text = format_weekly_roi_sms(report)
        results = send_to_manager(site_id, sms_text)
        delivered = sum(1 for r in results if r.get("delivered"))
        logger.info("Weekly ROI SMS sent: %d/%d delivered", delivered, len(results))

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
