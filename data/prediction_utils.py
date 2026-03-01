"""
Shared prediction normalization utilities.

Extracted from scripts/daily_autopilot.py so both the pipeline runner
and the API can convert raw DB rows into the in-memory prediction shape.
"""

from __future__ import annotations

import json
from datetime import date


def _safe_json(value):
    """Parse a JSON string or return the value as-is."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def normalize_prediction_record(record: dict) -> dict:
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
