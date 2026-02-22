#!/usr/bin/env python3
"""
Generate and persist weekly pilot KPI snapshots (JSON + CSV).

Outputs:
  analysis_outputs/pilot_kpi/<site_id>/<week_end>.json
  analysis_outputs/pilot_kpi/<site_id>/summary.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from analysis.reporting import generate_pilot_kpi_snapshot
from config.settings import settings
from data.storage import get_site, get_site_by_location_id

logger = logging.getLogger("autopilot.pilot_kpi")


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="pilot_kpi_snapshot",
        description="Generate and persist weekly pilot KPI snapshot.",
    )
    p.add_argument("--site-id", help="Site UUID. Falls back to SQUARE_LOCATION_ID lookup.")
    p.add_argument("--week-end", help="Week ending date (YYYY-MM-DD).")
    p.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "analysis_outputs" / "pilot_kpi"),
        help="Base output directory for snapshots.",
    )
    p.add_argument("--print-json", action="store_true", help="Print snapshot JSON to stdout.")
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


def write_snapshot(snapshot: dict, output_dir: Path) -> dict:
    site_id = snapshot["site_id"]
    week_end = snapshot["week_end"]

    site_dir = output_dir / site_id
    site_dir.mkdir(parents=True, exist_ok=True)

    json_path = site_dir / f"{week_end}.json"
    json_path.write_text(json.dumps(snapshot, indent=2))

    csv_path = site_dir / "summary.csv"
    row = {
        "generated_at": snapshot.get("generated_at"),
        "site_id": site_id,
        "site_name": snapshot.get("site_name"),
        "week_start": snapshot.get("week_start"),
        "week_end": week_end,
        "labor_pct_avg": snapshot["business_kpis"].get("labor_pct_avg"),
        "revenue_per_labor_hour_avg_cents": snapshot["business_kpis"].get(
            "revenue_per_labor_hour_avg_cents"
        ),
        "weekly_net_profit_cents": snapshot["business_kpis"].get("weekly_net_profit_cents"),
        "weekly_revenue_cents": snapshot["business_kpis"].get("weekly_revenue_cents"),
        "net_profit_wow_delta_cents": snapshot["business_kpis"].get("net_profit_wow_delta_cents"),
        "labor_pct_wow_delta_pp": snapshot["business_kpis"].get("labor_pct_wow_delta_pp"),
        "rev_per_labor_hour_wow_delta_pct": snapshot["business_kpis"].get(
            "rev_per_labor_hour_wow_delta_pct"
        ),
        "avg_prediction_accuracy_pct": snapshot["ops_kpis"].get("avg_prediction_accuracy_pct"),
        "adoption_rate": snapshot["ops_kpis"].get("adoption_rate"),
        "pipeline_success_pct_estimate": snapshot["reliability"].get(
            "pipeline_success_pct_estimate"
        ),
    }
    write_header = not csv_path.exists()
    with csv_path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    return {"json_path": str(json_path), "csv_path": str(csv_path)}


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
    snapshot = generate_pilot_kpi_snapshot(site_id=site_id, site_name=site_name, week_end=week_end)
    paths = write_snapshot(snapshot, Path(args.output_dir))

    logger.info("Pilot KPI snapshot written: %s", paths["json_path"])
    logger.info("Pilot KPI summary updated: %s", paths["csv_path"])

    if args.print_json:
        print(json.dumps(snapshot, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
