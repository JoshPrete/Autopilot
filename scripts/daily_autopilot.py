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
import json
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
    apply_partial_ingest_guard,
    get_data_quality_flags,
    get_prediction,
    get_prediction_by_id,
    get_site,
    get_site_by_location_id,
    store_daily_pipeline,
    store_daily_sales,
    store_deputy_rosters,
    store_prediction_plan_snapshot,
)
from delivery.sender import (
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


def _safe_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _normalize_prediction_record(record: dict) -> dict:
    """
    Convert a predictions table row into the in-memory prediction shape expected
    by Tomorrow Plan and SMS generators.
    """
    forecast_data = _safe_json(record.get("forecast_data", {}))
    if not isinstance(forecast_data, dict):
        forecast_data = {}

    prediction = dict(forecast_data)

    if "forecast" not in prediction:
        prediction["forecast"] = {}
    if not isinstance(prediction.get("forecast"), dict):
        prediction["forecast"] = {}

    forecast = prediction["forecast"]
    if record.get("forecast_date"):
        forecast["forecast_date"] = str(record["forecast_date"])
    if "day_name" not in forecast and forecast.get("forecast_date"):
        try:
            forecast["day_name"] = date.fromisoformat(str(forecast["forecast_date"])).strftime("%A")
        except ValueError:
            pass

    rush_windows = prediction.get("rush_windows")
    if rush_windows is None and record.get("rush_windows") is not None:
        rush_windows = _safe_json(record.get("rush_windows"))
    if not isinstance(rush_windows, list):
        rush_windows = []

    prediction["rush_windows"] = rush_windows
    prediction["rush_count"] = prediction.get("rush_count", len(rush_windows))
    prediction["prediction_id"] = str(record.get("prediction_id", ""))
    prediction["event_multiplier"] = record.get("event_factor")
    prediction["confidence"] = record.get("confidence_score")
    prediction["actual_accuracy"] = record.get("actual_accuracy")
    return prediction


def _build_blocked_predict_message(
    site_id: str,
    run_date: date,
    blocking_flags: list[dict],
) -> tuple[str, list[str], str]:
    reasons = []
    for flag in blocking_flags:
        flag_type = flag.get("flag_type", "unknown")
        severity = flag.get("severity", "unknown")
        reason = flag.get("reason") or "no reason provided"
        reasons.append(f"{flag_type}[{severity}] {reason}")

    ingest_cmd = (
        f".venv/bin/python scripts/daily_autopilot.py --site-id {site_id} "
        f"--step ingest --date {run_date.isoformat()}"
    )
    predict_cmd = (
        f".venv/bin/python scripts/daily_autopilot.py --site-id {site_id} "
        f"--step predict --date {run_date.isoformat()}"
    )
    clear_flag_cmd = (
        f"curl -X DELETE "
        f'"http://localhost:8000/api/sites/{site_id}/analysis/data-quality/flags/partial-ingest'
        f'?flag_date={run_date.isoformat()}"'
    )

    message_lines = [
        "PREDICTION BLOCKED (partial ingest guard)",
        f"- Site: {site_id}",
        f"- Date: {run_date.isoformat()}",
        "- Reasons:",
    ]
    message_lines.extend([f"  - {r}" for r in reasons])
    message_lines.extend(
        [
            "- Downstream skipped: tomorrow plan generation, tomorrow plan SMS, intelligence step.",
            "- Rerun commands:",
            f"  1) {ingest_cmd}",
            f"  2) {predict_cmd}",
            "  If data is complete and flag persists, clear it then rerun predict:",
            f"  3) {clear_flag_cmd}",
        ]
    )

    return "\n".join(message_lines), reasons, predict_cmd


def _print_summary(
    site_id: str,
    site_name: str,
    run_date: date,
    step: str,
    dry_run: bool,
    results: dict,
) -> None:
    """Print operator-friendly pipeline summary to stdout."""
    site_id_text = str(site_id)
    print()
    print("=" * 60)
    print("DAILY AUTOPILOT SUMMARY")
    print("=" * 60)
    print(f"  Date:     {run_date.isoformat()}")
    print(f"  Site:     {site_name} ({site_id_text[:8]}...)")
    print(f"  Step:     {step}")
    if dry_run:
        print("  Mode:     DRY RUN (no DB writes, no SMS)")
    print("-" * 60)

    for step_name, step_result in results.items():
        if isinstance(step_result, dict):
            status = step_result.get("status", "ok")
        else:
            status = "ok"

        if status in ("ok", "clear", "dry_run"):
            icon = " OK "
        elif status == "skipped":
            icon = "SKIP"
        else:
            icon = " ERR"

        print(f"  [{icon}] {step_name}")

        if status == "skipped" and isinstance(step_result, dict):
            reason = step_result.get("reason", "")
            print(f"         Reason: {reason}")
            if reason == "data_quality_flag":
                print(
                    f"         Rerun:  .venv/bin/python scripts/daily_autopilot.py"
                    f" --site-id {site_id_text} --step ingest --date {run_date.isoformat()}"
                )
                print(
                    f"         Then:   .venv/bin/python scripts/daily_autopilot.py"
                    f" --site-id {site_id_text} --step predict --date {run_date.isoformat()}"
                )
            skipped_downstream = step_result.get("downstream_skipped", [])
            if skipped_downstream:
                print(f"         Skipped downstream: {', '.join(skipped_downstream)}")
        elif status not in ("ok", "clear", "dry_run") and isinstance(step_result, dict):
            error = step_result.get("error", "")
            if error:
                print(f"         Error: {error}")

    print("=" * 60)


def build_run_report(
    site_id: str,
    site_name: str,
    run_date: date,
    step: str,
    dry_run: bool,
    results: dict,
    started_at: datetime,
) -> dict:
    """
    Build a structured JSON report from the pipeline results.

    Includes per-step status, trust metrics (accuracy, confidence,
    data quality), and degraded service tracking. Suitable for
    storage in pipeline_runs and consumption by the daily-loop
    health endpoint.
    """
    finished_at = datetime.utcnow()
    duration_ms = max(0, round((finished_at - started_at).total_seconds() * 1000))

    # Classify each step
    steps_report = {}
    degraded = []
    errors = []
    for step_name, step_result in results.items():
        if not isinstance(step_result, dict):
            steps_report[step_name] = {"status": "ok"}
            continue
        status = step_result.get("status", "ok")
        steps_report[step_name] = {"status": status}
        if status == "error":
            errors.append(step_name)
            steps_report[step_name]["error"] = step_result.get("error", "")
        elif status == "skipped":
            reason = step_result.get("reason", "")
            steps_report[step_name]["reason"] = reason
            # Non-critical skips (deputy not configured, dry_run) are not degraded
            if reason not in ("not_configured", "dry_run", "prediction_id_regeneration_mode"):
                degraded.append(step_name)

    # Trust metrics from predict result
    predict_result = results.get("predict", {})
    trust = {}
    if isinstance(predict_result, dict) and predict_result.get("status") == "ok":
        trust["prediction_id"] = predict_result.get("prediction_id")
        trust["total_drinks"] = predict_result.get("total_drinks")
        trust["rush_count"] = predict_result.get("rush_count")
        trust["sms_sent"] = predict_result.get("sms_sent", 0)
        trust["sms_degraded"] = predict_result.get("sms_degraded", False)
        trust["plan_length"] = predict_result.get("plan_length", 0)

    # Ingest metrics
    ingest_result = results.get("ingest", {})
    if isinstance(ingest_result, dict) and ingest_result.get("status") == "ok":
        trust["orders_ingested"] = ingest_result.get("orders", 0)
        trust["items_ingested"] = ingest_result.get("items", 0)
        quality_guard = ingest_result.get("quality_guard", {})
        if isinstance(quality_guard, dict):
            trust["data_quality_status"] = quality_guard.get("status", "unknown")

    # Overall status
    if errors:
        overall = "error"
    elif degraded:
        overall = "degraded"
    else:
        overall = "ok"

    return {
        "site_id": str(site_id),
        "site_name": site_name,
        "run_date": run_date.isoformat(),
        "step": step,
        "dry_run": dry_run,
        "overall_status": overall,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_ms": duration_ms,
        "steps": steps_report,
        "degraded_services": degraded,
        "errors": errors,
        "trust": trust,
    }


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
            o.get("total_money_cents", 0) or 0 for o in pipeline_result.get("orders", [])
        )
        store_daily_sales(
            site_id,
            run_date,
            {
                "total_revenue_cents": total_revenue_cents,
                "orders_count": summary["orders_count"],
                "items_count": summary["items_count"],
            },
        )

        # Strict data-quality guard: detect and flag likely partial ingestion days.
        quality_guard = apply_partial_ingest_guard(site_id, run_date)
    else:
        quality_guard = {"status": "dry_run"}

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
        "quality_guard": quality_guard,
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


def step_profitability(site_id: str, run_date: date, dry_run: bool = False) -> dict:
    """
    Step 1.75: Compute daily P&L and item margins.

    Runs after deputy (needs roster data for labor costs), before predict.
    Follows fail-quiet pattern — logs warning and continues on error.
    """
    logger.info("=== STEP: PROFITABILITY (date: %s) ===", run_date)

    try:
        from analysis.profitability import compute_daily_profitability

        if dry_run:
            logger.info("DRY RUN: Would compute profitability for %s", run_date)
            return {"status": "dry_run"}

        metrics = compute_daily_profitability(site_id, run_date)
        if metrics:
            logger.info(
                "Profitability computed: rev=$%.2f, net=$%.2f, labor%%=%.1f%%",
                metrics["revenue_cents"] / 100,
                metrics["net_profit_cents"] / 100,
                metrics["labor_pct"],
            )
            return {"status": "ok", **metrics}
        else:
            logger.info("No data for profitability on %s", run_date)
            return {"status": "no_data"}

    except Exception as e:
        logger.warning("Profitability computation failed (non-fatal): %s", e)
        return {"status": "error", "error": str(e)}


def step_xero(site_id: str, run_date: date, dry_run: bool = False) -> dict:
    """
    Step 1.8: Sync supplier bills from Xero into item_costs.

    Runs after deputy, before profitability. Follows fail-quiet pattern —
    skips silently if Xero is not connected for this site.
    """
    from data.xero import XeroAuthError, XeroError, is_xero_configured, sync_xero_bills

    if not is_xero_configured(site_id):
        logger.info("Xero not configured — skipping bill sync")
        return {"status": "skipped", "reason": "not_configured"}

    logger.info("=== STEP: XERO (date: %s) ===", run_date)

    if dry_run:
        logger.info("DRY RUN: Would sync Xero bills")
        return {"status": "dry_run"}

    try:
        result = sync_xero_bills(site_id, days_back=7)
        review_by_reason = result.get("review_queue_by_reason") or {}
        review_parts = [f"{k}:{v}" for k, v in sorted(review_by_reason.items())]
        logger.info(
            "Xero summary: bills=%s lines=%s approved=%s proposed=%s auto_applied=%s updated=%s review=%s%s",
            result.get("bills_fetched", 0),
            result.get("lines_processed", 0),
            result.get("mappings_approved_used", 0),
            result.get("mappings_proposed", 0),
            result.get("mappings_auto_applied", 0),
            result.get("items_updated", result.get("costs_updated", 0)),
            result.get("review_queue_added", 0),
            f" ({', '.join(review_parts)})" if review_parts else "",
        )
        return {"status": "ok", **result}

    except XeroAuthError as e:
        logger.warning(
            "Xero sync auth failed (non-fatal): %s. Next action: reauthorize Xero at /xero/setup and retry sync.",
            e,
        )
        return {
            "status": "error",
            "error": str(e),
            "next_action": "Reauthorize Xero at /xero/setup and retry sync",
        }
    except XeroError as e:
        logger.warning("Xero sync failed (non-fatal): %s", e)
        return {"status": "error", "error": str(e)}
    except Exception as e:
        logger.warning("Xero sync unexpected error (non-fatal): %s", e)
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

    # Strict fail-closed: if today's ingest is flagged partial, skip prediction.
    active_flags = get_data_quality_flags(
        site_id=site_id,
        start_date=run_date,
        end_date=run_date,
        active_only=True,
        limit=20,
    )
    blocking_flags = [f for f in active_flags if f.get("flag_type") in ("partial_ingest",)]
    if blocking_flags:
        operator_message, reasons, rerun_predict_cmd = _build_blocked_predict_message(
            site_id=site_id,
            run_date=run_date,
            blocking_flags=blocking_flags,
        )
        logger.error("%s", operator_message)
        if not dry_run:
            send_system_alert(
                site_id,
                "prediction_blocked_data_quality",
                site_name=site_name,
                run_date=run_date.isoformat(),
                reasons=reasons,
            )
        return {
            "status": "skipped",
            "reason": "data_quality_flag",
            "flags": blocking_flags,
            "operator_message": operator_message,
            "rerun_predict_cmd": rerun_predict_cmd,
            "downstream_skipped": ["tomorrow_plan", "tomorrow_plan_sms", "intelligence"],
        }

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

    # 2. Generate recommendations (degrade-not-die: failure must not block plan)
    if not dry_run:
        try:
            recommendations = generate_daily_recommendations(
                site_id=site_id,
                prediction_id=prediction_id,
                prediction=prediction,
                staff_names=staff_names,
            )
            logger.info("Generated %d recommendations", len(recommendations))
        except Exception:
            logger.exception("Recommendation generation failed (non-fatal)")
            recommendations = []
    else:
        recommendations = []
        logger.info("DRY RUN: Skipping recommendation storage")

    # 3. Generate Tomorrow Plan from stored prediction record when available.
    plan_prediction = prediction
    plan_generated_at = None
    if not dry_run:
        stored_row = get_prediction(site_id, tomorrow)
        if stored_row:
            plan_prediction = _normalize_prediction_record(stored_row)
            plan_generated_at = stored_row.get("generated_at")

    plan_text = generate_tomorrow_plan(
        site_name=site_name,
        site_id=site_id,
        prediction=plan_prediction,
        staff_names=staff_names,
        generated_at=plan_generated_at,
    )

    # Persist exact plan text for deterministic regeneration by prediction_id.
    if not dry_run and prediction_id and prediction_id != "dry-run":
        try:
            store_prediction_plan_snapshot(site_id, str(prediction_id), plan_text)
        except Exception:
            logger.exception(
                "Failed to persist plan snapshot for prediction %s (non-fatal)",
                prediction_id,
            )

    # Print plan to stdout (for review / piping to printer)
    print("\n" + plan_text + "\n")

    # 4. Send SMS (degrade-not-die: SMS failure must not crash pipeline)
    sms_degraded = False
    if not dry_run:
        try:
            sms_results = send_tomorrow_plan_sms(site_id, plan_prediction, staff_names)
            delivered = sum(1 for r in sms_results if r["delivered"])
            logger.info("SMS sent: %d/%d delivered", delivered, len(sms_results))
        except Exception:
            logger.exception("SMS dispatch failed (non-fatal, plan still generated)")
            sms_results = []
            sms_degraded = True
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
        "sms_degraded": sms_degraded,
        "plan_length": len(plan_text),
    }


def step_predict_from_prediction_id(
    site_id: str,
    site_name: str,
    prediction_id: str,
    staff_names: dict = None,
    dry_run: bool = False,
) -> dict:
    """
    Deterministic tomorrow plan regeneration.

    Loads a stored prediction and renders the plan without re-running model or
    recommendation generation.
    """
    if staff_names is None:
        staff_names = {}

    logger.info(
        "=== STEP: PREDICT (regenerate from prediction_id=%s) ===",
        prediction_id,
    )

    row = get_prediction_by_id(site_id, prediction_id)
    if not row:
        return {
            "status": "error",
            "reason": "prediction_not_found",
            "prediction_id": prediction_id,
        }

    plan_prediction = _normalize_prediction_record(row)
    plan_generated_at = row.get("generated_at")
    snapshot_text = plan_prediction.get("plan_snapshot_text")
    if isinstance(snapshot_text, str) and snapshot_text.strip():
        plan_text = snapshot_text
    else:
        plan_text = generate_tomorrow_plan(
            site_name=site_name,
            site_id=site_id,
            prediction=plan_prediction,
            staff_names=staff_names,
            generated_at=plan_generated_at,
        )
    print("\n" + plan_text + "\n")

    logger.info(
        "Regenerated tomorrow plan from stored prediction %s without recalculation.",
        prediction_id,
    )

    return {
        "status": "ok",
        "mode": "regenerated",
        "prediction_id": prediction_id,
        "forecast_date": str(row.get("forecast_date")),
        "sms_sent": 0,
        "plan_length": len(plan_text),
        "dry_run": dry_run,
    }


def step_replan(
    site_id: str,
    site_name: str,
    run_date: date,
    staff_names: dict = None,
    dry_run: bool = False,
) -> dict:
    """
    Regenerate tomorrow plan from the latest stored prediction for a date.

    Unlike --prediction-id (which requires a UUID), this looks up the most
    recent prediction for (run_date + 1 day) and regenerates the plan.
    No forecast recomputation occurs.

    Usage:
        python scripts/daily_autopilot.py --step replan --date 2026-02-20
        python scripts/daily_autopilot.py --step replan --date 2026-02-20 --staff-names "P1:Sarah,P2:Tom"
    """
    if staff_names is None:
        staff_names = {}

    tomorrow = run_date + timedelta(days=1)
    logger.info("=== STEP: REPLAN (for: %s, from stored prediction) ===", tomorrow)

    stored = get_prediction(site_id, tomorrow)
    if not stored:
        logger.error("No stored prediction found for %s", tomorrow)
        return {
            "status": "error",
            "reason": f"No stored prediction for {tomorrow}. Run --step predict first.",
        }

    prediction_id = str(stored.get("prediction_id", "unknown"))
    plan_prediction = _normalize_prediction_record(stored)
    plan_generated_at = stored.get("generated_at")
    snapshot_text = plan_prediction.get("plan_snapshot_text")
    if isinstance(snapshot_text, str) and snapshot_text.strip():
        plan_text = snapshot_text
    else:
        plan_text = generate_tomorrow_plan(
            site_name=site_name,
            site_id=site_id,
            prediction=plan_prediction,
            staff_names=staff_names,
            generated_at=plan_generated_at,
        )
    print("\n" + plan_text + "\n")

    logger.info("Replanned from prediction %s (no recomputation)", prediction_id)

    return {
        "status": "ok",
        "mode": "replan",
        "prediction_id": prediction_id,
        "forecast_date": str(tomorrow),
        "plan_length": len(plan_text),
    }


def step_intelligence(
    site_id: str,
    site_name: str,
    run_date: date,
    dry_run: bool = False,
) -> dict:
    """
    Step 3: Run intelligence cycle — analyze patterns, generate insights, learn.

    Runs after predict. Follows fail-quiet pattern.
    """
    logger.info("=== STEP: INTELLIGENCE (date: %s) ===", run_date)

    if dry_run:
        return {"status": "skipped", "reason": "dry_run"}

    try:
        from analysis.intelligence import run_intelligence_cycle

        result = run_intelligence_cycle(site_id, site_name, run_date)
        return {"status": "ok", **result}
    except Exception as e:
        logger.exception("Intelligence cycle failed (non-fatal)")
        return {"status": "error", "error": str(e)}


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
        choices=[
            "ingest",
            "deputy",
            "xero",
            "profitability",
            "predict",
            "replan",
            "intelligence",
            "all",
        ],
        default="all",
        help="Which pipeline step to run. 'replan' regenerates tomorrow plan from stored prediction. Default: all.",
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
        "--prediction-id",
        help=("Render tomorrow plan from stored prediction_id only " "(no recalculation)."),
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging.",
    )
    return p.parse_args(argv)


def resolve_site(site_id: str = None) -> tuple[str, str]:
    """Resolve site_id and site_name from args, DEFAULT_SITE_ID, or SQUARE_LOCATION_ID."""
    if site_id:
        site = get_site(site_id)
        if site:
            return site["site_id"], site["name"]
        return site_id, "Unknown Site"

    # Fallback: DEFAULT_SITE_ID from env
    default_id = settings.DEFAULT_SITE_ID
    if default_id:
        site = get_site(default_id)
        if site:
            return site["site_id"], site["name"]
        return default_id, "Unknown Site"

    # Fallback: look up by Square location ID from env
    loc_id = settings.SQUARE_LOCATION_ID
    if loc_id:
        site = get_site_by_location_id(loc_id)
        if site:
            return str(site["site_id"]), site["name"]

    logger.error(
        "Cannot resolve site. Provide --site-id, set DEFAULT_SITE_ID, or set SQUARE_LOCATION_ID."
    )
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
    if args.prediction_id and args.step == "all":
        args.step = "predict"

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
        site_name,
        site_id,
        run_date,
        args.step,
        args.dry_run,
    )

    results = {}
    started_at = datetime.utcnow()

    try:
        # Step 1: Ingest
        if args.step in ("ingest", "all"):
            results["ingest"] = step_ingest(site_id, run_date, args.dry_run)

        # Step 1.5: Deputy roster sync (after ingest, before predict)
        if args.step in ("deputy", "all"):
            results["deputy"] = step_deputy(site_id, run_date, args.dry_run)

        # Step 1.8: Xero bill sync (after deputy, before profitability)
        if args.step in ("xero", "all"):
            results["xero"] = step_xero(site_id, run_date, args.dry_run)

        # Step 1.75: Profitability (after deputy, before predict)
        if args.step in ("profitability", "all"):
            results["profitability"] = step_profitability(site_id, run_date, args.dry_run)

        # Step 2: Predict
        if args.step in ("predict", "all"):
            if args.prediction_id:
                results["predict"] = step_predict_from_prediction_id(
                    site_id=site_id,
                    site_name=site_name,
                    prediction_id=args.prediction_id,
                    staff_names=staff_names,
                    dry_run=args.dry_run,
                )
            else:
                results["predict"] = step_predict(
                    site_id=site_id,
                    site_name=site_name,
                    run_date=run_date,
                    staff_scheduled=args.staff,
                    staff_names=staff_names,
                    dry_run=args.dry_run,
                )

        # Step 2.5: Replan (regenerate plan from stored prediction)
        if args.step == "replan":
            results["replan"] = step_replan(
                site_id=site_id,
                site_name=site_name,
                run_date=run_date,
                staff_names=staff_names,
                dry_run=args.dry_run,
            )

        # Step 3: Intelligence (after predict)
        if args.step in ("intelligence", "all"):
            if args.step == "all" and args.prediction_id:
                logger.warning("Skipping intelligence: --prediction-id mode renders plan only.")
                results["intelligence"] = {
                    "status": "skipped",
                    "reason": "prediction_id_regeneration_mode",
                }
            elif (
                args.step == "all"
                and results.get("predict", {}).get("status") == "skipped"
                and results.get("predict", {}).get("reason") == "data_quality_flag"
            ):
                logger.warning(
                    "Skipping intelligence because prediction was blocked by data-quality guard."
                )
                results["intelligence"] = {
                    "status": "skipped",
                    "reason": "blocked_by_data_quality_flag",
                }
            else:
                results["intelligence"] = step_intelligence(
                    site_id=site_id,
                    site_name=site_name,
                    run_date=run_date,
                    dry_run=args.dry_run,
                )

        logger.info("Pipeline complete: %s", results)

        # Operator-friendly summary
        _print_summary(site_id, site_name, run_date, args.step, args.dry_run, results)

        # Structured run report with trust metrics
        report = build_run_report(
            site_id=site_id,
            site_name=site_name,
            run_date=run_date,
            step=args.step,
            dry_run=args.dry_run,
            results=results,
            started_at=started_at,
        )
        if not args.dry_run and args.step == "all":
            try:
                from data.storage import store_pipeline_run

                store_pipeline_run(
                    site_id=site_id,
                    job_name="daily_loop",
                    status=report["overall_status"],
                    started_at=started_at,
                    finished_at=datetime.utcnow(),
                    result=report,
                )
            except Exception:
                logger.exception("Failed to store run report (non-fatal)")

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
