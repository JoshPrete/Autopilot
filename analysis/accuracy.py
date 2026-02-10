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
        workload_acc = calculate_volume_accuracy(
            int(predicted_workload), int(actual_workload)
        )

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
        forecast_date, predicted_drinks, actual_drinks,
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
        "meets_target": (
            stats["adoption_rate"] is not None
            and stats["adoption_rate"] >= 0.60
        ),
    }
