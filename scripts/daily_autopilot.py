#!/usr/bin/env python3
"""
Clubhouse Autopilot v1.2 - Daily Pipeline Runner
Main entry point for the 5pm + 6pm cron jobs (Spec Section 13.2)

Daily schedule:
    5:00pm - Ingest today's data, calculate workload, update accuracy
    6:00pm - Generate tomorrow's prediction, plan, and send SMS

Usage:
    # Full pipeline (ingest + predict + send)
    python scripts/daily_autopilot.py --site-id SITE_UUID

    # Ingest only (5pm job)
    python scripts/daily_autopilot.py --site-id SITE_UUID --step ingest

    # Predict + send only (6pm job)
    python scripts/daily_autopilot.py --site-id SITE_UUID --step predict

    # Dry run (no SMS, no DB writes)
    python scripts/daily_autopilot.py --site-id SITE_UUID --dry-run

    # Specific date
    python scripts/daily_autopilot.py --site-id SITE_UUID --date 2026-02-07
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

# Load .env before any config imports
load_dotenv(PROJECT_ROOT / ".env")

from analysis.accuracy import update_daily_accuracy
from config.settings import settings
from data.ingestion import SquareIngestion, parse_orders
from data.processing import process_orders_batch
from data.storage import (
    get_site,
    get_site_by_location_id,
    store_daily_pipeline,
    store_daily_sales,
    store_deputy_rosters,
)
from delivery.sender import (
    send_feedback_request,
    send_system_alert,
    send_tomorrow_plan_sms,
)
from delivery.tomorrow_plan import generate_tomorrow_plan
from models.prediction import generate_prediction
from models.recommendations import generate_daily_recommendations

logger = logging.getLogger("autopilot.daily")

REQUIRED_ENV = [
    "SQUARE_ACCESS_TOKEN",
    "SQUARE_LOCATION_ID",
    "DATABASE_URL",
]


# ============================================================
# Pipeline Steps
# ============================================================


def step_ingest(site_id: str, run_date: date, dry_run: bool = False) -> dict:
    """
    Step 1: Ingest today's orders from Square and process workload.

    Spec Section 13.2: 5:00pm - Ingest today's data.

    Pipeline:
        1. Fetch completed orders from Square for today
        2. Parse raw orders
        3. Calculate workload scores (Sections 5.1-5.4)
        4. Aggregate into 15-min timeline
        5. Store everything to DB
        6. Update yesterday's prediction accuracy
    """
    logger.info("=== STEP: INGEST (date: %s) ===", run_date)

    # 1. Fetch from Square
    ingestion = SquareIngestion()
    if run_date == date.today():
        raw_orders = ingestion.fetch_todays_orders()
    else:
        raw_orders = ingestion.fetch_date_range(datetime.combine(run_date, datetime.min.time()))

    if not raw_orders:
        logger.warning("No orders fetched for %s", run_date)
        return {"status": "no_data", "orders": 0}

    # 2. Parse
    parsed = parse_orders(raw_orders)
    logger.info("Parsed %d orders", len(parsed))

    # 3. Process workload
    pipeline_result = process_orders_batch(parsed)
    summary = pipeline_result["summary"]
    logger.info(
        "Processed: %d orders, %d items, %.1f workload units",
        summary["orders_count"],
        summary["items_count"],
        summary["total_workload_units"],
    )

    # 4. Store to DB
    if not dry_run:
        storage_result = store_daily_pipeline(site_id, pipeline_result)
        logger.info("Stored: %s", storage_result)
    else:
        storage_result = {"dry_run": True}
        logger.info("DRY RUN: Skipping DB storage")

    # 5. Store daily sales summary for rolling history
    if not dry_run:
        total_revenue_cents = sum(
            o.get("total_money_cents", 0) or 0
            for o in pipeline_result.get("orders", [])
        )
        store_daily_sales(site_id, run_date, {
            "total_revenue_cents": total_revenue_cents,
            "orders_count": summary["orders_count"],
            "items_count": summary["items_count"],
        })

    # 6. Update yesterday's accuracy (if we have actuals now)
    yesterday = run_date - timedelta(days=1)
    actual_drinks = summary["items_count"]
    actual_workload = summary["total_workload_units"]

    if not dry_run and actual_drinks > 0:
        accuracy = update_daily_accuracy(
            site_id=site_id,
            forecast_date=yesterday,
            actual_drinks=actual_drinks,
            actual_workload=actual_workload,
        )
        if accuracy:
            logger.info("Yesterday accuracy: %s", accuracy)

    return {
        "status": "ok",
        "orders": summary["orders_count"],
        "items": summary["items_count"],
        "workload_units": summary["total_workload_units"],
        "storage": storage_result,
    }


def step_deputy(site_id: str, run_date: date, dry_run: bool = False) -> dict:
    """
    Step 1.5: Sync roster data from Deputy.

    Runs after ingest, before predict. Follows fail-quiet pattern —
    skips silently if Deputy credentials are not configured.

    Fetches rosters for today + next 14 days and employee names.
    """
    from data.deputy import DeputyClient, DeputyError, is_deputy_configured

    if not is_deputy_configured():
        logger.info("Deputy not configured — skipping roster sync")
        return {"status": "skipped", "reason": "not_configured"}

    logger.info("=== STEP: DEPUTY (date: %s) ===", run_date)

    try:
        client = DeputyClient()

        # Fetch rosters: today + next 14 days
        end_date = run_date + timedelta(days=14)
        rosters = client.fetch_rosters(run_date, end_date)

        if not rosters:
            logger.info("No rosters returned from Deputy")
            return {"status": "ok", "rosters": 0}

        # Fetch employee names and enrich roster records
        employees = client.fetch_employees()
        for r in rosters:
            emp_id = r.get("employee_id")
            if emp_id and emp_id in employees:
                r["employee_name"] = employees[emp_id]

        # Store to DB
        if not dry_run:
            stored = store_deputy_rosters(site_id, rosters)
            logger.info("Stored %d Deputy rosters", stored)
        else:
            stored = 0
            logger.info("DRY RUN: Would store %d rosters", len(rosters))

        return {"status": "ok", "rosters": len(rosters), "stored": stored}

    except DeputyError as e:
        logger.warning("Deputy sync failed (non-fatal): %s", e)
        return {"status": "error", "error": str(e)}
    except Exception as e:
        logger.warning("Deputy sync unexpected error (non-fatal): %s", e)
        return {"status": "error", "error": str(e)}


def step_predict(
    site_id: str,
    site_name: str,
    run_date: date,
    staff_scheduled: int = None,
    staff_names: dict = None,
    dry_run: bool = False,
) -> dict:
    """
    Step 2: Generate tomorrow's prediction, plan, and send SMS.

    Spec Section 13.2: 6:00pm - Generate Tomorrow Plan.

    Pipeline:
        1. Generate prediction (weather + 4-layer forecast + rush detection)
        2. Generate recommendations (timed actions per rush window)
        3. Generate Tomorrow Plan (printable text)
        4. Send Tomorrow Plan SMS to manager
    """
    tomorrow = run_date + timedelta(days=1)
    logger.info("=== STEP: PREDICT (for: %s) ===", tomorrow)

    if staff_names is None:
        staff_names = {}

    # 1. Generate prediction
    prediction = generate_prediction(
        site_id=site_id,
        target_date=tomorrow,
        staff_scheduled=staff_scheduled,
        save=not dry_run,
    )

    prediction_id = prediction.get("prediction_id", "dry-run")
    logger.info(
        "Prediction: %d drinks, %d rushes, confidence=%s",
        prediction["total_predicted_drinks"],
        prediction["rush_count"],
        prediction.get("confidence_label", "unknown"),
    )

    # 2. Generate recommendations
    if not dry_run:
        recommendations = generate_daily_recommendations(
            site_id=site_id,
            prediction_id=prediction_id,
            prediction=prediction,
            staff_names=staff_names,
        )
        logger.info("Generated %d recommendations", len(recommendations))
    else:
        recommendations = []
        logger.info("DRY RUN: Skipping recommendation storage")

    # 3. Generate Tomorrow Plan
    plan_text = generate_tomorrow_plan(
        site_name=site_name,
        site_id=site_id,
        prediction=prediction,
        staff_names=staff_names,
    )

    # Print plan to stdout (for review / piping to printer)
    print("\n" + plan_text + "\n")

    # 4. Send SMS
    if not dry_run:
        sms_results = send_tomorrow_plan_sms(site_id, prediction, staff_names)
        delivered = sum(1 for r in sms_results if r["delivered"])
        logger.info("SMS sent: %d/%d delivered", delivered, len(sms_results))
    else:
        sms_results = []
        logger.info("DRY RUN: Skipping SMS")

    return {
        "status": "ok",
        "prediction_id": prediction_id,
        "total_drinks": prediction["total_predicted_drinks"],
        "rush_count": prediction["rush_count"],
        "recommendations": len(recommendations),
        "sms_sent": len(sms_results),
        "plan_length": len(plan_text),
    }


# ============================================================
# Main
# ============================================================


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="daily_autopilot",
        description="Clubhouse Autopilot daily pipeline runner.",
    )
    p.add_argument(
        "--site-id",
        help="Site UUID. If not provided, looks up by SQUARE_LOCATION_ID.",
    )
    p.add_argument(
        "--date",
        help="Business date (YYYY-MM-DD). Default: today.",
        default=str(date.today()),
    )
    p.add_argument(
        "--step",
        choices=["ingest", "deputy", "predict", "all"],
        default="all",
        help="Which pipeline step to run. Default: all.",
    )
    p.add_argument(
        "--staff",
        type=int,
        help="Number of staff scheduled for tomorrow.",
    )
    p.add_argument(
        "--staff-names",
        help="Staff names as P1:Name,P2:Name,P3:Name (e.g. P1:Sarah,P2:Tom,P3:Jessica)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without writing to DB or sending SMS.",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging.",
    )
    return p.parse_args(argv)


def resolve_site(site_id: str = None) -> tuple[str, str]:
    """Resolve site_id and site_name from args or environment."""
    if site_id:
        site = get_site(site_id)
        if site:
            return site["site_id"], site["name"]
        return site_id, "Unknown Site"

    # Look up by Square location ID from env
    loc_id = settings.SQUARE_LOCATION_ID
    if loc_id:
        site = get_site_by_location_id(loc_id)
        if site:
            return str(site["site_id"]), site["name"]

    logger.error("Cannot resolve site. Provide --site-id or set SQUARE_LOCATION_ID.")
    sys.exit(1)


def parse_staff_names(staff_str: str) -> dict:
    """Parse 'P1:Sarah,P2:Tom,P3:Jessica' into dict."""
    if not staff_str:
        return {}
    names = {}
    for pair in staff_str.split(","):
        if ":" in pair:
            role, name = pair.split(":", 1)
            names[role.strip().upper()] = name.strip()
    return names


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    # Configure logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Check required env
    missing = [k for k in REQUIRED_ENV if not os.getenv(k)]
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        logger.error("Create .env from .env.example and fill in credentials.")
        return 2

    run_date = date.fromisoformat(args.date)
    site_id, site_name = resolve_site(args.site_id)
    staff_names = parse_staff_names(args.staff_names)

    logger.info(
        "Clubhouse Autopilot - Daily Pipeline\n"
        "  Site: %s (%s)\n"
        "  Date: %s\n"
        "  Step: %s\n"
        "  Dry run: %s",
        site_name, site_id, run_date, args.step, args.dry_run,
    )

    results = {}

    try:
        # Step 1: Ingest
        if args.step in ("ingest", "all"):
            results["ingest"] = step_ingest(site_id, run_date, args.dry_run)

        # Step 1.5: Deputy roster sync (after ingest, before predict)
        if args.step in ("deputy", "all"):
            results["deputy"] = step_deputy(site_id, run_date, args.dry_run)

        # Step 2: Predict
        if args.step in ("predict", "all"):
            results["predict"] = step_predict(
                site_id=site_id,
                site_name=site_name,
                run_date=run_date,
                staff_scheduled=args.staff,
                staff_names=staff_names,
                dry_run=args.dry_run,
            )

        logger.info("Pipeline complete: %s", results)
        return 0

    except Exception:
        logger.exception("Pipeline failed")

        # Fail Quiet: alert manager, then exit
        if not args.dry_run:
            try:
                send_system_alert(
                    site_id,
                    "ingestion_failed",
                    site_name=site_name,
                    error="Daily pipeline failed. Check logs.",
                )
            except Exception:
                logger.exception("Failed to send alert SMS")

        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
