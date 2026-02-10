"""
Clubhouse Autopilot v1.2 - Prediction Model
Daily forecast generation orchestrator (Spec Sections 5.5-5.6, 8)

Coordinates between the storage layer (historical data retrieval),
processing layer (4-layer composite prediction), and workload model
(rush window detection) to produce the complete daily forecast
used by the Tomorrow Plan system.
"""

import json
import logging
from datetime import date, datetime, timedelta
from typing import Optional

import requests

from config.settings import settings
from data.processing import predict_workload
from data.storage import (
    check_special_events,
    get_dow_pattern,
    get_prediction,
    get_recent_pattern,
    get_yoy_pattern,
    store_prediction,
)
from models.workload import detect_rush_windows, generate_daily_forecast

logger = logging.getLogger("autopilot.prediction")


# ============================================================
# Weather Integration (Spec Section 7.3)
# ============================================================


def fetch_weather_forecast(target_date: date) -> Optional[dict]:
    """
    Fetch weather forecast from OpenWeatherMap for the target date.

    Returns simplified weather dict or None on failure.
    Per spec principle: Fail Quiet - if weather fails, proceed without it.
    """
    api_key = settings.WEATHER_API_KEY
    if not api_key:
        logger.info("No WEATHER_API_KEY configured, skipping weather")
        return None

    try:
        resp = requests.get(
            "https://api.openweathermap.org/data/2.5/forecast",
            params={
                "lat": settings.WEATHER_LAT,
                "lon": settings.WEATHER_LON,
                "appid": api_key,
                "units": "metric",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.exception("Weather API call failed, proceeding without weather")
        return None

    # Find the forecast entry closest to the target date at noon
    target_noon = datetime(target_date.year, target_date.month, target_date.day, 12)
    best_entry = None
    best_diff = None

    for entry in data.get("list", []):
        entry_dt = datetime.utcfromtimestamp(entry["dt"])
        diff = abs((entry_dt - target_noon).total_seconds())
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_entry = entry

    if not best_entry:
        return None

    main = best_entry.get("main", {})
    weather_list = best_entry.get("weather", [{}])
    weather_desc = weather_list[0] if weather_list else {}
    rain = best_entry.get("rain", {})

    return {
        "temp_c": round(main.get("temp", 0), 1),
        "feels_like_c": round(main.get("feels_like", 0), 1),
        "humidity": main.get("humidity", 0),
        "description": weather_desc.get("description", "unknown"),
        "icon": weather_desc.get("icon", ""),
        "rain_3h_mm": rain.get("3h", 0),
        "rain_probability": best_entry.get("pop", 0),
    }


# ============================================================
# Daily Prediction Pipeline
# ============================================================


def generate_prediction(
    site_id: str,
    target_date: date,
    staff_scheduled: int = None,
    save: bool = True,
) -> dict:
    """
    Generate and optionally store the complete daily prediction.

    This is the main entry point called by the daily 6pm cron job.

    Pipeline:
    1. Fetch weather forecast
    2. Check special events (Layer 3)
    3. Generate full daily forecast (all 4 layers + rush detection)
    4. Attach weather data
    5. Store prediction to database

    Args:
        site_id: Site UUID
        target_date: Date to predict for (usually tomorrow)
        staff_scheduled: Number of staff scheduled (from Deputy or manual)
        save: Whether to persist to predictions table

    Returns:
        Complete prediction dict with forecast, rush windows, weather
    """
    logger.info("Generating prediction for site %s, date %s", site_id, target_date)

    # Step 1: Weather
    weather = fetch_weather_forecast(target_date)

    # Step 2: Full daily forecast (handles all 4 layers internally)
    forecast = generate_daily_forecast(
        site_id=site_id,
        target_date=target_date,
        staff_scheduled=staff_scheduled,
    )

    # Step 3: Build the prediction summary for the Tomorrow Plan
    day_name = target_date.strftime("%A")
    event_multiplier = forecast["event_multiplier"]

    # Get the pattern breakdown for storage (using first operating hour as reference)
    py_dow = target_date.weekday()
    pg_dow = (py_dow + 1) % 7
    recent_values = get_recent_pattern(site_id, pg_dow, 8)  # 8am reference hour
    yoy_values = get_yoy_pattern(site_id, target_date)

    summary_prediction = predict_workload(
        recent_values=recent_values,
        yoy_values=yoy_values,
        dow_name=day_name,
        event_multiplier=event_multiplier,
    )

    prediction = {
        # Pattern components (for storage)
        "recent_avg": summary_prediction["recent_avg"],
        "yoy_avg": summary_prediction["yoy_avg"],
        "dow_factor": summary_prediction["dow_factor"],
        "event_multiplier": summary_prediction["event_multiplier"],
        "base_prediction": summary_prediction["base_prediction"],
        "confidence": summary_prediction["confidence"],
        "confidence_label": summary_prediction["confidence_label"],
        # Full forecast
        "forecast": forecast,
        # Weather
        "weather": weather,
        # Key metrics for Tomorrow Plan
        "total_predicted_drinks": forecast["total_predicted_drinks"],
        "total_predicted_workload": forecast["total_predicted_workload"],
        "rush_count": forecast["rush_count"],
        "rush_windows": forecast["rush_windows"],
        "staff_scheduled": staff_scheduled,
        "staffing_mode": forecast["staffing_mode"],
    }

    # Step 4: Store
    if save:
        prediction_id = store_prediction(site_id, target_date, prediction)
        prediction["prediction_id"] = prediction_id

    logger.info(
        "Prediction complete: %d drinks, %d rushes, confidence=%s, weather=%s",
        forecast["total_predicted_drinks"],
        forecast["rush_count"],
        summary_prediction["confidence_label"],
        weather["description"] if weather else "unavailable",
    )

    return prediction


# ============================================================
# Prediction Accuracy Tracking
# ============================================================


def update_prediction_accuracy(
    site_id: str,
    forecast_date: date,
    actual_drinks: int,
    actual_workload: float,
) -> Optional[float]:
    """
    Compare a prediction against actual results and store accuracy.

    Accuracy = 1 - abs(predicted - actual) / actual
    Clamped to [0.0, 1.0].

    Called during the next day's data ingestion when actuals are known.
    """
    pred = get_prediction(site_id, forecast_date)
    if not pred:
        logger.warning("No prediction found for %s on %s", site_id, forecast_date)
        return None

    forecast_data = pred.get("forecast_data")
    if isinstance(forecast_data, str):
        forecast_data = json.loads(forecast_data)

    predicted_drinks = forecast_data.get("total_predicted_drinks", 0)

    if actual_drinks <= 0:
        logger.warning("Actual drinks is 0, cannot compute accuracy")
        return None

    error = abs(predicted_drinks - actual_drinks) / actual_drinks
    accuracy = max(0.0, min(1.0, 1.0 - error))

    # Update in database
    from config.database import engine
    from sqlalchemy import text

    with engine.connect() as conn:
        conn.execute(
            text(
                "UPDATE predictions SET actual_accuracy = :acc "
                "WHERE site_id = :sid AND forecast_date = :fd"
            ),
            {"acc": accuracy, "sid": site_id, "fd": forecast_date},
        )
        conn.commit()

    logger.info(
        "Accuracy for %s: predicted=%d actual=%d accuracy=%.1f%%",
        forecast_date, predicted_drinks, actual_drinks, accuracy * 100,
    )

    return accuracy


# ============================================================
# Historical Pattern Refresh
# ============================================================


def refresh_historical_patterns(site_id: str) -> dict:
    """
    Recalculate and store historical pattern summaries.

    Updates the historical_patterns table with current:
    - Day-of-week patterns (last 12 weeks)
    - Used by the prediction engine for DoW normalization

    Should be run weekly (e.g. Monday morning cron).
    """
    from config.database import engine
    from sqlalchemy import text

    dow_pattern = get_dow_pattern(site_id, weeks_back=12)

    stored = 0
    with engine.connect() as conn:
        for day_name, factor in dow_pattern.items():
            conn.execute(
                text(
                    "INSERT INTO historical_patterns "
                    "(site_id, pattern_type, pattern_key, avg_workload, "
                    "sample_size, confidence) "
                    "VALUES (:sid, 'dow', :key, :avg, 12, 0.85) "
                    "ON CONFLICT (site_id, pattern_type, pattern_key) DO UPDATE SET "
                    "avg_workload = EXCLUDED.avg_workload, "
                    "last_updated = NOW()"
                ),
                {"sid": site_id, "key": day_name.lower(), "avg": factor},
            )
            stored += 1
        conn.commit()

    logger.info("Refreshed %d DoW patterns for site %s", stored, site_id)
    return {"patterns_updated": stored, "dow_pattern": dow_pattern}


# ============================================================
# Convenience: Yesterday Recap
# ============================================================


def get_yesterday_recap(site_id: str, today: date = None) -> Optional[dict]:
    """
    Get yesterday's prediction vs actual for the Tomorrow Plan recap.

    Returns dict with predicted/actual drinks, accuracy %, and
    adoption stats. Used by Tomorrow Plan template (Section 8.1).
    """
    if today is None:
        today = date.today()
    yesterday = today - timedelta(days=1)

    pred = get_prediction(site_id, yesterday)
    if not pred:
        return None

    forecast_data = pred.get("forecast_data")
    if isinstance(forecast_data, str):
        forecast_data = json.loads(forecast_data)

    predicted = forecast_data.get("total_predicted_drinks", 0)
    accuracy = pred.get("actual_accuracy")

    # Get adoption stats for yesterday
    from data.storage import get_weekly_stats
    adoption = get_weekly_stats(site_id, yesterday, yesterday)

    recap = {
        "date": yesterday.isoformat(),
        "predicted_drinks": predicted,
        "actual_accuracy": round(accuracy * 100, 1) if accuracy else None,
        "recommendations_total": adoption["recommendations_total"],
        "recommendations_adopted": adoption["recommendations_adopted"],
        "adoption_rate": adoption["adoption_rate"],
    }

    return recap
