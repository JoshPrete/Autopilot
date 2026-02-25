"""
Clubhouse Autopilot v1.2 - Accuracy Tracking
Prediction vs actual comparison engine (Spec Sections 5.6, 10.4)

Provides tools for measuring how well Autopilot's predictions
match reality. Tracks both volume accuracy (predicted vs actual
drinks) and timing accuracy (rush window start/end).

Used by:
- Daily pipeline (next-day retrospective)
- Weekly review report
- Confidence scoring adjustments
"""

import json
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from config.database import engine
from data.storage import get_prediction, get_weekly_stats

logger = logging.getLogger("autopilot.accuracy")


# ============================================================
# Volume Accuracy (predicted drinks vs actual)
# ============================================================


def calculate_volume_accuracy(predicted: int, actual: int) -> Optional[float]:
    """
    Calculate prediction accuracy for drink volume.

    Formula: accuracy = 1 - abs(predicted - actual) / actual
    Clamped to [0.0, 1.0].

    Spec Section 10.4 target: average volume error ± Y drinks.
    """
    if actual <= 0:
        return None

    error = abs(predicted - actual) / actual
    return max(0.0, min(1.0, 1.0 - error))


def calculate_timing_accuracy(
    predicted_start: datetime,
    predicted_end: datetime,
    actual_start: datetime,
    actual_end: datetime,
) -> dict:
    """
    Calculate rush window timing accuracy.

    Spec Section 10.1 success criteria: Rush timing ± 20 minutes.

    Returns:
        Dict with start_error_min, end_error_min, within_target bool
    """
    start_error = abs((predicted_start - actual_start).total_seconds()) / 60
    end_error = abs((predicted_end - actual_end).total_seconds()) / 60

    return {
        "start_error_minutes": round(start_error, 1),
        "end_error_minutes": round(end_error, 1),
        "avg_error_minutes": round((start_error + end_error) / 2, 1),
        "within_target": start_error <= 20 and end_error <= 20,
    }


# ============================================================
# Daily Accuracy Update
# ============================================================


def update_daily_accuracy(
    site_id: str,
    forecast_date: date,
    actual_drinks: int,
    actual_workload: float = None,
) -> Optional[dict]:
    """
    Compare a day's prediction against actual results and store accuracy.

    Called during the next day's data ingestion when actuals are known.

    Returns accuracy report dict or None if no prediction found.
    """
    pred = get_prediction(site_id, forecast_date)
    if not pred:
        logger.warning("No prediction found for %s on %s", site_id, forecast_date)
        return None

    forecast_data = pred.get("forecast_data")
    if isinstance(forecast_data, str):
        forecast_data = json.loads(forecast_data)

    predicted_drinks = forecast_data.get("total_predicted_drinks", 0)
    predicted_workload = forecast_data.get("total_predicted_workload", 0)

    # Volume accuracy
    volume_acc = calculate_volume_accuracy(predicted_drinks, actual_drinks)

    # Workload accuracy (if actual provided)
    workload_acc = None
    if actual_workload and actual_workload > 0:
        workload_acc = calculate_volume_accuracy(int(predicted_workload), int(actual_workload))

    # Store accuracy in DB
    if volume_acc is not None:
        from sqlalchemy import text

        with engine.connect() as conn:
            conn.execute(
                text(
                    "UPDATE predictions SET actual_accuracy = :acc "
                    "WHERE site_id = :sid AND forecast_date = :fd"
                ),
                {"acc": volume_acc, "sid": site_id, "fd": forecast_date},
            )
            conn.commit()

    report = {
        "forecast_date": forecast_date.isoformat(),
        "predicted_drinks": predicted_drinks,
        "actual_drinks": actual_drinks,
        "volume_accuracy": round(volume_acc * 100, 1) if volume_acc else None,
        "volume_error": actual_drinks - predicted_drinks,
        "volume_error_pct": round(
            (actual_drinks - predicted_drinks) / max(actual_drinks, 1) * 100, 1
        ),
        "predicted_workload": predicted_workload,
        "actual_workload": actual_workload,
        "workload_accuracy": round(workload_acc * 100, 1) if workload_acc else None,
        "confidence_score": pred.get("confidence_score"),
    }

    logger.info(
        "Accuracy for %s: predicted=%d actual=%d accuracy=%.1f%%",
        forecast_date,
        predicted_drinks,
        actual_drinks,
        volume_acc * 100 if volume_acc else 0,
    )

    return report


# ============================================================
# Rolling Accuracy Metrics
# ============================================================


def get_rolling_accuracy(
    site_id: str,
    days_back: int = 7,
    reference_date: date = None,
) -> dict:
    """
    Calculate rolling accuracy metrics over the last N days.

    Spec Section 13.4 monitoring: Prediction accuracy (rolling 7-day).
    Alert if accuracy <75% for 3 days.

    Returns:
        Dict with avg_accuracy, days_measured, trend, alert status
    """
    if reference_date is None:
        reference_date = date.today()

    from sqlalchemy import text

    start_date = reference_date - timedelta(days=days_back)

    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT forecast_date, actual_accuracy, confidence_score "
                "FROM predictions "
                "WHERE site_id = :sid "
                "AND forecast_date BETWEEN :start AND :end "
                "AND actual_accuracy IS NOT NULL "
                "ORDER BY forecast_date"
            ),
            {"sid": site_id, "start": start_date, "end": reference_date},
        )
        rows = list(result.mappings())

    if not rows:
        return {
            "avg_accuracy": None,
            "days_measured": 0,
            "daily_accuracies": [],
            "alert": False,
            "alert_reason": None,
        }

    accuracies = [float(r["actual_accuracy"]) for r in rows]
    avg_accuracy = sum(accuracies) / len(accuracies)

    # Trend: compare first half vs second half
    mid = len(accuracies) // 2
    if mid > 0:
        first_half = sum(accuracies[:mid]) / mid
        second_half = sum(accuracies[mid:]) / len(accuracies[mid:])
        trend = "improving" if second_half > first_half else "declining"
    else:
        trend = "stable"

    # Alert: accuracy <75% for 3+ consecutive days
    consecutive_low = 0
    max_consecutive_low = 0
    for acc in accuracies:
        if acc < 0.75:
            consecutive_low += 1
            max_consecutive_low = max(max_consecutive_low, consecutive_low)
        else:
            consecutive_low = 0

    alert = max_consecutive_low >= 3
    alert_reason = None
    if alert:
        alert_reason = (
            f"Accuracy below 75% for {max_consecutive_low} consecutive days. "
            "Review model inputs and recent data quality."
        )

    daily = [
        {
            "date": str(r["forecast_date"]),
            "accuracy": round(float(r["actual_accuracy"]) * 100, 1),
            "confidence": float(r["confidence_score"]) if r["confidence_score"] else None,
        }
        for r in rows
    ]

    return {
        "avg_accuracy": round(avg_accuracy * 100, 1),
        "days_measured": len(rows),
        "trend": trend,
        "daily_accuracies": daily,
        "alert": alert,
        "alert_reason": alert_reason,
    }


# ============================================================
# Accuracy Dashboard (extended analytics)
# ============================================================

DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
DAY_NAMES_FULL = [
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
]


def get_accuracy_dashboard(
    site_id: str,
    days_back: int = 30,
    reference_date: date = None,
) -> dict:
    """
    Extended accuracy dashboard combining predictions with actual drink counts.

    Returns summary stats, daily trend with rolling average, predicted vs actual
    breakdown, day-of-week analysis, confidence band calibration, and staffing
    mode grouping.
    """
    if reference_date is None:
        reference_date = date.today()

    from sqlalchemy import text

    start_date = reference_date - timedelta(days=days_back)

    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT p.forecast_date, p.actual_accuracy, p.confidence_score, "
                "p.forecast_data->>'total_predicted_drinks' AS predicted_drinks, "
                "p.forecast_data->>'staffing_mode' AS staffing_mode, "
                "dp.drink_count AS actual_drinks, "
                "EXTRACT(DOW FROM p.forecast_date) AS dow_num "
                "FROM predictions p "
                "LEFT JOIN daily_profitability dp "
                "  ON dp.site_id = p.site_id AND dp.profit_date = p.forecast_date "
                "WHERE p.site_id = :sid "
                "  AND p.forecast_date BETWEEN :start AND :end "
                "  AND p.actual_accuracy IS NOT NULL "
                "ORDER BY p.forecast_date"
            ),
            {"sid": site_id, "start": start_date, "end": reference_date},
        )
        rows = list(result.mappings())

    if not rows:
        return {
            "days_back": days_back,
            "days_measured": 0,
            "summary": {
                "avg_accuracy": None,
                "trend": "stable",
                "avg_abs_error": None,
                "avg_signed_error": None,
                "worst_day": None,
                "best_day": None,
                "calibrated": None,
            },
            "alert": False,
            "alert_reason": None,
            "daily_trend": [],
            "predicted_vs_actual": [],
            "by_dow": [],
            "by_confidence": [],
            "by_staffing_mode": [],
        }

    # --- Parse rows ---
    accuracies = [float(r["actual_accuracy"]) for r in rows]

    # --- Trend: first half vs second half (reuse existing logic) ---
    mid = len(accuracies) // 2
    if mid > 0:
        first_half = sum(accuracies[:mid]) / mid
        second_half = sum(accuracies[mid:]) / len(accuracies[mid:])
        if second_half - first_half > 0.01:
            trend = "improving"
        elif first_half - second_half > 0.01:
            trend = "declining"
        else:
            trend = "stable"
    else:
        trend = "stable"

    # --- Alert: <75% for 3+ consecutive days (reuse existing logic) ---
    consecutive_low = 0
    max_consecutive_low = 0
    for acc in accuracies:
        if acc < 0.75:
            consecutive_low += 1
            max_consecutive_low = max(max_consecutive_low, consecutive_low)
        else:
            consecutive_low = 0

    alert = max_consecutive_low >= 3
    alert_reason = None
    if alert:
        alert_reason = (
            f"Accuracy below 75% for {max_consecutive_low} consecutive days. "
            "Review model inputs and recent data quality."
        )

    # --- Build predicted vs actual (only where actual_drinks is known) ---
    predicted_vs_actual = []
    errors = []
    signed_errors = []
    for r in rows:
        predicted_raw = r["predicted_drinks"]
        actual_raw = r["actual_drinks"]
        if predicted_raw is None or actual_raw is None:
            continue
        predicted = int(predicted_raw)
        actual = int(actual_raw)
        if actual <= 0:
            continue
        error = abs(predicted - actual)
        signed = predicted - actual
        error_pct = round(error / actual * 100, 1)
        errors.append(error)
        signed_errors.append(signed)
        predicted_vs_actual.append(
            {
                "date": str(r["forecast_date"]),
                "predicted": predicted,
                "actual": actual,
                "error": error,
                "error_pct": error_pct,
            }
        )

    avg_abs_error = round(sum(errors) / len(errors)) if errors else None
    avg_signed_error = round(sum(signed_errors) / len(signed_errors)) if signed_errors else None

    # --- Daily trend with rolling 7-day average ---
    daily_trend = []
    for i, r in enumerate(rows):
        acc_pct = round(float(r["actual_accuracy"]) * 100, 1)
        window = accuracies[max(0, i - 6) : i + 1]
        rolling = round(sum(window) / len(window) * 100, 1)
        daily_trend.append(
            {
                "date": str(r["forecast_date"]),
                "accuracy": acc_pct,
                "rolling_7d_avg": rolling,
            }
        )

    # --- By day of week ---
    dow_buckets: dict[int, list] = {}
    dow_errors: dict[int, list] = {}
    for r in rows:
        dow = int(r["dow_num"])
        dow_buckets.setdefault(dow, []).append(float(r["actual_accuracy"]))
        # signed error for bias
        predicted_raw = r["predicted_drinks"]
        actual_raw = r["actual_drinks"]
        if predicted_raw is not None and actual_raw is not None and int(actual_raw) > 0:
            dow_errors.setdefault(dow, []).append(int(predicted_raw) - int(actual_raw))

    by_dow = []
    for dow in sorted(dow_buckets.keys()):
        accs = dow_buckets[dow]
        errs = dow_errors.get(dow, [])
        by_dow.append(
            {
                "dow": dow,
                "day_name": DAY_NAMES[dow] if dow < len(DAY_NAMES) else str(dow),
                "avg_accuracy": round(sum(accs) / len(accs) * 100, 1),
                "sample_count": len(accs),
                "avg_signed_error": round(sum(errs) / len(errs)) if errs else None,
            }
        )

    worst_day = None
    best_day = None
    if by_dow:
        worst_entry = min(by_dow, key=lambda d: d["avg_accuracy"])
        best_entry = max(by_dow, key=lambda d: d["avg_accuracy"])
        worst_dow = worst_entry["dow"]
        best_dow = best_entry["dow"]
        worst_day = DAY_NAMES_FULL[worst_dow] if worst_dow < len(DAY_NAMES_FULL) else str(worst_dow)
        best_day = DAY_NAMES_FULL[best_dow] if best_dow < len(DAY_NAMES_FULL) else str(best_dow)

    # --- By confidence band ---
    conf_buckets: dict[str, list] = {"high": [], "medium": [], "low": []}
    for r in rows:
        cs = r["confidence_score"]
        if cs is None:
            continue
        cs_val = float(cs)
        if cs_val >= 0.75:
            band = "high"
        elif cs_val >= 0.50:
            band = "medium"
        else:
            band = "low"
        conf_buckets[band].append(float(r["actual_accuracy"]))

    band_labels = {"high": "75-100%", "medium": "50-74%", "low": "0-49%"}
    by_confidence = []
    for band in ["high", "medium", "low"]:
        accs = conf_buckets[band]
        if accs:
            by_confidence.append(
                {
                    "band": band,
                    "label": band_labels[band],
                    "avg_accuracy": round(sum(accs) / len(accs) * 100, 1),
                    "sample_count": len(accs),
                }
            )

    # Calibrated: high-conf band more accurate than low
    calibrated = None
    high_accs = conf_buckets["high"]
    low_accs = conf_buckets["low"]
    if high_accs and low_accs:
        calibrated = (sum(high_accs) / len(high_accs)) > (sum(low_accs) / len(low_accs))

    # --- By staffing mode ---
    mode_buckets: dict[str, list] = {}
    for r in rows:
        mode = r["staffing_mode"]
        if mode:
            mode_buckets.setdefault(mode, []).append(float(r["actual_accuracy"]))

    by_staffing_mode = []
    for mode in sorted(mode_buckets.keys()):
        accs = mode_buckets[mode]
        by_staffing_mode.append(
            {
                "mode": mode,
                "avg_accuracy": round(sum(accs) / len(accs) * 100, 1),
                "sample_count": len(accs),
            }
        )

    avg_accuracy = round(sum(accuracies) / len(accuracies) * 100, 1)

    return {
        "days_back": days_back,
        "days_measured": len(rows),
        "summary": {
            "avg_accuracy": avg_accuracy,
            "trend": trend,
            "avg_abs_error": avg_abs_error,
            "avg_signed_error": avg_signed_error,
            "worst_day": worst_day,
            "best_day": best_day,
            "calibrated": calibrated,
        },
        "alert": alert,
        "alert_reason": alert_reason,
        "daily_trend": daily_trend,
        "predicted_vs_actual": predicted_vs_actual,
        "by_dow": by_dow,
        "by_confidence": by_confidence,
        "by_staffing_mode": by_staffing_mode,
    }


# ============================================================
# Adoption Tracking
# ============================================================


def get_adoption_metrics(
    site_id: str,
    start_date: date,
    end_date: date,
) -> dict:
    """
    Calculate adoption metrics for a date range.

    Spec Section 1.3 principle: Adoption is King.
    Track whether prompts were followed, not just accuracy.

    Spec Section 10.1 success criteria: Adoption >60%.
    """
    stats = get_weekly_stats(site_id, start_date, end_date)

    from sqlalchemy import text

    # Get rating breakdowns
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT "
                "AVG(rush_timing_rating) as avg_timing, "
                "AVG(helpfulness_rating) as avg_helpful, "
                "COUNT(*) FILTER (WHERE rush_timing_rating IS NOT NULL) as timing_count, "
                "COUNT(*) FILTER (WHERE helpfulness_rating IS NOT NULL) as helpful_count "
                "FROM adoption_logs "
                "WHERE site_id = :sid "
                "AND log_date BETWEEN :start AND :end"
            ),
            {"sid": site_id, "start": start_date, "end": end_date},
        )
        row = result.first()

    avg_timing = round(float(row[0]), 1) if row and row[0] else None
    avg_helpful = round(float(row[1]), 1) if row and row[1] else None

    return {
        "adoption_rate": stats["adoption_rate"],
        "recommendations_total": stats["recommendations_total"],
        "recommendations_adopted": stats["recommendations_adopted"],
        "avg_rush_timing_rating": avg_timing,
        "avg_helpfulness_rating": avg_helpful,
        "ratings_count": int(row[2]) if row else 0,
        "meets_target": (stats["adoption_rate"] is not None and stats["adoption_rate"] >= 0.60),
    }


# ============================================================
# Daily-Loop Health Status
# ============================================================


def get_daily_loop_status(site_id: str) -> dict:
    """
    Health check for the daily pipeline loop.

    Reports on last successful ingest, predict, and SMS delivery,
    rolling 7-day accuracy, whether tomorrow's prediction exists,
    and an overall readiness verdict.
    """
    from sqlalchemy import text

    today = date.today()
    tomorrow = today + timedelta(days=1)

    with engine.connect() as conn:
        # Last pipeline runs by job
        runs = (
            conn.execute(
                text(
                    "SELECT job_name, status, started_at, finished_at, "
                    "duration_ms, error_text, result_json "
                    "FROM pipeline_runs "
                    "WHERE site_id = :sid "
                    "ORDER BY started_at DESC "
                    "LIMIT 50"
                ),
                {"sid": site_id},
            )
            .mappings()
            .all()
        )

        # Tomorrow's prediction
        tomorrow_pred = (
            conn.execute(
                text(
                    "SELECT prediction_id, confidence_score, "
                    "forecast_data->>'total_predicted_drinks' AS predicted_drinks, "
                    "forecast_data->>'staffing_mode' AS staffing_mode, "
                    "generated_at "
                    "FROM predictions "
                    "WHERE site_id = :sid AND forecast_date = :fd"
                ),
                {"sid": site_id, "fd": tomorrow},
            )
            .mappings()
            .first()
        )

    # Find last successful run per key job
    last_runs = {}
    for job in ("ingest", "predict", "daily_loop"):
        for r in runs:
            if r["job_name"] == job and r["status"] == "ok":
                last_runs[job] = {
                    "status": "ok",
                    "at": str(r["started_at"]) if r["started_at"] else None,
                    "duration_ms": r["duration_ms"],
                }
                break
        if job not in last_runs:
            # Check for any run (even failed)
            for r in runs:
                if r["job_name"] == job:
                    last_runs[job] = {
                        "status": r["status"],
                        "at": str(r["started_at"]) if r["started_at"] else None,
                        "error": r["error_text"],
                    }
                    break

    # Last daily_loop report for SMS status
    sms_status = "unknown"
    for r in runs:
        if r["job_name"] == "daily_loop" and r["result_json"]:
            result = r["result_json"]
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except (json.JSONDecodeError, TypeError):
                    result = {}
            trust = result.get("trust", {}) if isinstance(result, dict) else {}
            if trust.get("sms_degraded"):
                sms_status = "degraded"
            elif trust.get("sms_sent", 0) > 0:
                sms_status = "ok"
            elif trust.get("sms_sent") == 0:
                sms_status = "no_recipients"
            break

    # Rolling accuracy
    accuracy = get_rolling_accuracy(site_id, days_back=7)

    # Tomorrow prediction info
    tomorrow_info = None
    if tomorrow_pred:
        tomorrow_info = {
            "prediction_id": str(tomorrow_pred["prediction_id"]),
            "predicted_drinks": (
                int(tomorrow_pred["predicted_drinks"])
                if tomorrow_pred["predicted_drinks"]
                else None
            ),
            "staffing_mode": tomorrow_pred["staffing_mode"],
            "confidence": (
                float(tomorrow_pred["confidence_score"])
                if tomorrow_pred["confidence_score"]
                else None
            ),
            "generated_at": (
                str(tomorrow_pred["generated_at"]) if tomorrow_pred["generated_at"] else None
            ),
        }

    # Overall readiness
    has_ingest = last_runs.get("ingest", {}).get("status") == "ok"
    has_predict = last_runs.get("predict", {}).get("status") == "ok" or tomorrow_info is not None
    has_accuracy = accuracy.get("avg_accuracy") is not None
    accuracy_ok = (accuracy.get("avg_accuracy") or 0) >= 75.0

    if has_ingest and has_predict and accuracy_ok:
        readiness = "green"
    elif has_ingest and has_predict:
        readiness = "yellow"
    else:
        readiness = "red"

    return {
        "site_id": str(site_id),
        "checked_at": datetime.utcnow().isoformat(),
        "readiness": readiness,
        "last_runs": last_runs,
        "sms_status": sms_status,
        "tomorrow": tomorrow_info,
        "accuracy_7d": {
            "avg": accuracy.get("avg_accuracy"),
            "trend": accuracy.get("trend"),
            "alert": accuracy.get("alert", False),
            "alert_reason": accuracy.get("alert_reason"),
            "days_measured": accuracy.get("days_measured", 0),
        },
    }
