#!/usr/bin/env python3
"""
Tomorrow Plan habit CLI.

Commands:
  tomorrow -> generate one markdown report for tomorrow
  verify   -> compare predicted vs actual revenue for a date and append logs/accuracy.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text

# Ensure project root is on sys.path for direct script execution.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from analysis.tomorrow_report import (  # noqa: E402
    build_tomorrow_report_payload,
    normalize_confidence_label,
    render_tomorrow_report_markdown,
)
from config.constants import SUPERANNUATION_RATE  # noqa: E402
from config.database import engine  # noqa: E402
from config.settings import settings  # noqa: E402
from data.storage import (  # noqa: E402
    get_daily_profitability,
    get_data_quality_flags,
    get_day_ingest_diagnostics,
    get_prediction,
    get_rosters_for_date,
    get_site,
    get_site_by_location_id,
)
from models.prediction import generate_prediction  # noqa: E402

ACCURACY_HEADERS = [
    "verified_at",
    "site_id",
    "verify_date",
    "prediction_id",
    "predicted_drinks",
    "predicted_revenue_cents",
    "actual_revenue_cents",
    "error_cents",
    "error_pct",
    "confidence_label",
    "confidence_score",
]


class TomorrowPlanBlockedError(RuntimeError):
    """Raised when preflight checks fail and operator intervention is needed."""


@dataclass
class RevenueBaseline:
    avg_revenue_per_drink_cents: float
    days_count: int


def _safe_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _normalize_prediction_row(row: dict) -> dict:
    forecast_data = _safe_json(row.get("forecast_data", {}))
    if not isinstance(forecast_data, dict):
        forecast_data = {}

    prediction = dict(forecast_data)
    forecast = prediction.get("forecast")
    if not isinstance(forecast, dict):
        forecast = {}
        prediction["forecast"] = forecast

    forecast_date = row.get("forecast_date")
    if forecast_date:
        forecast["forecast_date"] = str(forecast_date)

    rush_windows = prediction.get("rush_windows")
    if rush_windows is None and row.get("rush_windows") is not None:
        rush_windows = _safe_json(row.get("rush_windows"))
    if not isinstance(rush_windows, list):
        rush_windows = []

    prediction["rush_windows"] = rush_windows
    prediction["prediction_id"] = str(row.get("prediction_id", ""))
    prediction["confidence"] = prediction.get("confidence", row.get("confidence_score"))
    prediction["confidence_label"] = prediction.get("confidence_label") or normalize_confidence_label(
        prediction["confidence"]
    )
    prediction["total_predicted_drinks"] = int(
        prediction.get("total_predicted_drinks")
        or forecast.get("total_predicted_drinks")
        or 0
    )
    return prediction


def _resolve_site(site_id: str | None) -> tuple[str, str]:
    if site_id:
        site = get_site(site_id)
        if not site:
            raise TomorrowPlanBlockedError(
                f"Site not found for --site-id {site_id}. Confirm UUID in `sites` table."
            )
        return str(site["site_id"]), str(site["name"])

    location_id = settings.SQUARE_LOCATION_ID
    if location_id:
        site = get_site_by_location_id(location_id)
        if site:
            return str(site["site_id"]), str(site["name"])

    default_site_id = settings.DEFAULT_SITE_ID
    if default_site_id:
        site = get_site(default_site_id)
        if site:
            return str(site["site_id"]), str(site["name"])

    raise TomorrowPlanBlockedError(
        "Could not resolve site. Set SQUARE_LOCATION_ID or pass --site-id <UUID>."
    )


def _build_fix_block(site_id: str, run_date: date, reasons: list[str]) -> str:
    ingest_cmd = (
        f".venv/bin/python scripts/daily_autopilot.py --site-id {site_id} "
        f"--step ingest --date {run_date.isoformat()}"
    )
    predict_cmd = (
        f".venv/bin/python scripts/daily_autopilot.py --site-id {site_id} "
        f"--step predict --date {run_date.isoformat()}"
    )
    retry_cmd = (
        f".venv/bin/python scripts/tomorrow_cli.py tomorrow --site-id {site_id} "
        f"--date {run_date.isoformat()}"
    )
    clear_flag_cmd = (
        f"curl -X DELETE "
        f"\"http://localhost:8000/api/sites/{site_id}/analysis/data-quality/"
        f"flags/partial-ingest?flag_date={run_date.isoformat()}\""
    )

    lines = [
        "Tomorrow Plan blocked: source data is missing or partial.",
        f"- Site: {site_id}",
        f"- Business date: {run_date.isoformat()}",
        "- Reasons:",
    ]
    lines.extend([f"  - {reason}" for reason in reasons])
    lines.extend(
        [
            "- Fix commands:",
            f"  1) {ingest_cmd}",
            f"  2) {predict_cmd}",
            "  If ingest is complete but partial flag remains:",
            f"  3) {clear_flag_cmd}",
            f"  4) {retry_cmd}",
        ]
    )
    return "\n".join(lines)


def ensure_tomorrow_inputs_ready(site_id: str, run_date: date) -> None:
    flags = get_data_quality_flags(
        site_id=site_id,
        start_date=run_date,
        end_date=run_date,
        active_only=True,
        limit=50,
    )
    blocking = [flag for flag in flags if flag.get("flag_type") == "partial_ingest"]
    if blocking:
        reasons = [
            f"partial_ingest[{flag.get('severity', 'unknown')}]: {flag.get('reason')}"
            for flag in blocking
        ]
        raise TomorrowPlanBlockedError(_build_fix_block(site_id, run_date, reasons))

    diagnostics = get_day_ingest_diagnostics(site_id, run_date)
    reasons = []
    if diagnostics.get("suspected_partial"):
        reasons.append(
            "ingest diagnostics suspect partial day: "
            + ", ".join(diagnostics.get("partial_signals", []))
        )
    day_stats = diagnostics.get("day", {})
    if int(day_stats.get("completed_orders") or 0) <= 0:
        reasons.append("no completed orders found for day")
    if int(day_stats.get("active_hours") or 0) < 4:
        reasons.append("active trade hours below expected minimum (4)")
    if reasons:
        raise TomorrowPlanBlockedError(_build_fix_block(site_id, run_date, reasons))


def compute_revenue_baseline(
    site_id: str,
    start_date: date,
    end_date: date,
    min_days: int = 7,
) -> RevenueBaseline:
    rows = get_daily_profitability(site_id, start_date, end_date)
    valid = [
        row
        for row in rows
        if int(row.get("revenue_cents") or 0) > 0 and int(row.get("drink_count") or 0) > 0
    ]
    if len(valid) < min_days:
        raise TomorrowPlanBlockedError(
            "Tomorrow Plan blocked: insufficient profitability history for revenue baseline.\n"
            f"- Needed at least {min_days} days with revenue + drink_count in daily_profitability.\n"
            f"- Found: {len(valid)} days ({start_date} to {end_date}).\n"
            "- Fix commands:\n"
            f"  1) .venv/bin/python scripts/daily_autopilot.py --site-id {site_id} --step profitability --date {end_date.isoformat()}\n"  # noqa: E501
            "  2) Backfill missing dates via ingest + profitability, then rerun `make tomorrow`."
        )

    total_revenue = sum(int(row["revenue_cents"]) for row in valid)
    total_drinks = sum(int(row["drink_count"]) for row in valid)
    if total_drinks <= 0:
        raise TomorrowPlanBlockedError(
            "Tomorrow Plan blocked: profitability rows exist but drink_count totals are zero.\n"
            "Fix data mappings for drink_count in `daily_profitability` then rerun."
        )

    return RevenueBaseline(
        avg_revenue_per_drink_cents=total_revenue / total_drinks,
        days_count=len(valid),
    )


def estimate_scheduled_labor_cents(site_id: str, forecast_date: date, run_date: date) -> int:
    rosters = get_rosters_for_date(site_id, forecast_date)
    if not rosters:
        cmd = (
            f".venv/bin/python scripts/daily_autopilot.py --site-id {site_id} "
            f"--step deputy --date {run_date.isoformat()}"
        )
        raise TomorrowPlanBlockedError(
            "Tomorrow Plan blocked: no Deputy roster found for forecast date.\n"
            f"- Forecast date: {forecast_date.isoformat()}\n"
            "- Fix commands:\n"
            f"  1) {cmd}\n"
            "  2) Confirm tomorrow roster is published in Deputy.\n"
            "  3) Rerun `make tomorrow`."
        )

    total_labor_cents = 0
    for shift in rosters:
        cost_dollars = shift.get("cost_dollars")
        if cost_dollars is None:
            continue
        total_labor_cents += round(float(cost_dollars) * 100 * (1 + SUPERANNUATION_RATE))

    if total_labor_cents <= 0:
        raise TomorrowPlanBlockedError(
            "Tomorrow Plan blocked: roster exists but shift costs are missing/zero.\n"
            "Fix Deputy shift costs and rerun `make tomorrow`."
        )
    return total_labor_cents


def load_or_generate_prediction(site_id: str, forecast_date: date) -> dict:
    row = get_prediction(site_id, forecast_date)
    if row:
        return _normalize_prediction_row(row)
    generated = generate_prediction(site_id=site_id, target_date=forecast_date, save=True)
    return generated


def _ensure_reports_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_report(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _get_actual_revenue_cents(site_id: str, target_date: date) -> int:
    profitability_rows = get_daily_profitability(site_id, target_date, target_date)
    if profitability_rows and int(profitability_rows[0].get("revenue_cents") or 0) > 0:
        return int(profitability_rows[0]["revenue_cents"])

    with engine.connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT
                    COALESCE(xero_revenue_cents, gross_sales_cents, 0) AS revenue_cents
                FROM daily_sales_history
                WHERE site_id = :site_id
                  AND sale_date = :target_date
                """
            ),
            {"site_id": site_id, "target_date": target_date},
        ).mappings().first()
    if result and int(result.get("revenue_cents") or 0) > 0:
        return int(result["revenue_cents"])

    raise TomorrowPlanBlockedError(
        "Verify blocked: actual revenue unavailable for requested date.\n"
        f"- Date: {target_date.isoformat()}\n"
        "- Fix commands:\n"
        f"  1) .venv/bin/python scripts/daily_autopilot.py --site-id {site_id} --step ingest --date {target_date.isoformat()}\n"  # noqa: E501
        f"  2) .venv/bin/python scripts/daily_autopilot.py --site-id {site_id} --step profitability --date {target_date.isoformat()}\n"  # noqa: E501
        "  3) Rerun `make verify DATE=YYYY-MM-DD`."
    )


def run_tomorrow(site_id: str | None, run_date: date, reports_dir: Path) -> Path:
    resolved_site_id, site_name = _resolve_site(site_id)
    ensure_tomorrow_inputs_ready(resolved_site_id, run_date)

    forecast_date = run_date + timedelta(days=1)
    prediction = load_or_generate_prediction(resolved_site_id, forecast_date)
    predicted_drinks = int(prediction.get("total_predicted_drinks") or 0)
    if predicted_drinks <= 0:
        raise TomorrowPlanBlockedError(
            "Tomorrow Plan blocked: prediction contains zero drinks.\n"
            f"- Forecast date: {forecast_date.isoformat()}\n"
            "- Fix: rerun predict step after confirming ingest completeness."
        )

    baseline = compute_revenue_baseline(
        site_id=resolved_site_id,
        start_date=run_date - timedelta(days=28),
        end_date=run_date,
        min_days=7,
    )
    forecast_revenue_cents = round(predicted_drinks * baseline.avg_revenue_per_drink_cents)
    if forecast_revenue_cents <= 0:
        raise TomorrowPlanBlockedError("Tomorrow Plan blocked: forecast revenue calculated as zero.")

    scheduled_labor_cents = estimate_scheduled_labor_cents(
        site_id=resolved_site_id,
        forecast_date=forecast_date,
        run_date=run_date,
    )
    wage_pct = (scheduled_labor_cents / forecast_revenue_cents) * 100

    payload = build_tomorrow_report_payload(
        site_name=site_name,
        site_id=resolved_site_id,
        forecast_date=forecast_date.isoformat(),
        prediction_id=str(prediction.get("prediction_id", "unknown")),
        predicted_drinks=predicted_drinks,
        forecast_revenue_cents=forecast_revenue_cents,
        confidence_score=prediction.get("confidence"),
        confidence_label=prediction.get("confidence_label"),
        rush_windows=prediction.get("rush_windows", []),
        scheduled_labor_cents=scheduled_labor_cents,
        wage_pct=wage_pct,
        baseline_days=baseline.days_count,
        baseline_revenue_per_drink_cents=baseline.avg_revenue_per_drink_cents,
    )

    markdown = render_tomorrow_report_markdown(payload)
    _ensure_reports_dir(reports_dir)
    output_path = reports_dir / f"tomorrow_{forecast_date.isoformat()}.md"
    _write_report(output_path, markdown)
    return output_path


def append_verify_csv(csv_path: Path, row: dict) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ACCURACY_HEADERS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def run_verify(site_id: str | None, verify_date: date, csv_path: Path) -> dict:
    resolved_site_id, _site_name = _resolve_site(site_id)
    prediction_row = get_prediction(resolved_site_id, verify_date)
    if not prediction_row:
        previous_day = verify_date - timedelta(days=1)
        raise TomorrowPlanBlockedError(
            "Verify blocked: no prediction row found for date.\n"
            f"- Date: {verify_date.isoformat()}\n"
            "- Fix commands:\n"
            f"  1) .venv/bin/python scripts/tomorrow_cli.py tomorrow --site-id {resolved_site_id} --date {previous_day.isoformat()}\n"  # noqa: E501
            f"  2) .venv/bin/python scripts/tomorrow_cli.py verify --site-id {resolved_site_id} --date {verify_date.isoformat()}"  # noqa: E501
        )
    prediction = _normalize_prediction_row(prediction_row)

    predicted_drinks = int(prediction.get("total_predicted_drinks") or 0)
    baseline = compute_revenue_baseline(
        site_id=resolved_site_id,
        start_date=verify_date - timedelta(days=28),
        end_date=verify_date - timedelta(days=1),
        min_days=3,
    )
    predicted_revenue_cents = round(predicted_drinks * baseline.avg_revenue_per_drink_cents)
    actual_revenue_cents = _get_actual_revenue_cents(resolved_site_id, verify_date)
    error_cents = actual_revenue_cents - predicted_revenue_cents
    error_pct = (error_cents / predicted_revenue_cents * 100) if predicted_revenue_cents else 0.0

    row = {
        "verified_at": datetime.now().isoformat(timespec="seconds"),
        "site_id": resolved_site_id,
        "verify_date": verify_date.isoformat(),
        "prediction_id": prediction.get("prediction_id", "unknown"),
        "predicted_drinks": predicted_drinks,
        "predicted_revenue_cents": predicted_revenue_cents,
        "actual_revenue_cents": actual_revenue_cents,
        "error_cents": error_cents,
        "error_pct": round(error_pct, 2),
        "confidence_label": prediction.get("confidence_label", "unknown"),
        "confidence_score": prediction.get("confidence"),
    }
    append_verify_csv(csv_path, row)
    return row


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tomorrow_cli",
        description="Generate Tomorrow Plan markdown and verify daily forecast accuracy.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    tomorrow = subparsers.add_parser("tomorrow", help="Generate tomorrow markdown report.")
    tomorrow.add_argument("--site-id", help="Site UUID (optional if SQUARE_LOCATION_ID resolves).")
    tomorrow.add_argument(
        "--date",
        default=str(date.today()),
        help="Business date used as input day (YYYY-MM-DD). Default: today.",
    )
    tomorrow.add_argument(
        "--reports-dir",
        default="reports",
        help="Directory for tomorrow_YYYY-MM-DD.md output.",
    )

    verify = subparsers.add_parser("verify", help="Verify prediction vs actuals for a date.")
    verify.add_argument("--site-id", help="Site UUID (optional if SQUARE_LOCATION_ID resolves).")
    verify.add_argument("--date", required=True, help="Date to verify (YYYY-MM-DD).")
    verify.add_argument(
        "--csv-path",
        default="logs/accuracy.csv",
        help="CSV file to append accuracy rows.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    if not os.getenv("DATABASE_URL"):
        print("DATABASE_URL is required. Set it in .env before running this CLI.", file=sys.stderr)
        return 2

    args = parse_args(argv)
    try:
        if args.command == "tomorrow":
            output_path = run_tomorrow(
                site_id=args.site_id,
                run_date=date.fromisoformat(args.date),
                reports_dir=PROJECT_ROOT / args.reports_dir,
            )
            print(f"Tomorrow report written: {output_path}")
            return 0

        if args.command == "verify":
            row = run_verify(
                site_id=args.site_id,
                verify_date=date.fromisoformat(args.date),
                csv_path=PROJECT_ROOT / args.csv_path,
            )
            print(
                "Verify appended:",
                f"date={row['verify_date']}",
                f"pred=${int(row['predicted_revenue_cents']) / 100:,.2f}",
                f"actual=${int(row['actual_revenue_cents']) / 100:,.2f}",
                f"error_pct={row['error_pct']}%",
            )
            return 0

        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 2

    except TomorrowPlanBlockedError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
