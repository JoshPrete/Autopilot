"""Tests for the GET /api/sites/{site_id}/tomorrow-plan/json endpoint."""

import json
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import TEST_SITE_ID, TEST_SITE_NAME, TEST_PREDICTION_ID

client = TestClient(app)


def _mock_site(site_id):
    if site_id == TEST_SITE_ID:
        return {"site_id": TEST_SITE_ID, "name": TEST_SITE_NAME}
    return None


def _build_db_row(rush_windows=None, confidence=0.87, forecast_date=None):
    """Build a raw prediction DB row (as returned by get_prediction)."""
    if forecast_date is None:
        forecast_date = date.today() + timedelta(days=1)
    if rush_windows is None:
        rush_windows = [
            {
                "start": f"{forecast_date}T08:47:00",
                "end": f"{forecast_date}T09:32:00",
                "duration_minutes": 45,
                "predicted_drinks": 52,
                "wally_start_time": f"{forecast_date}T08:35:00",
                "wally_volume_litres": 8,
                "wally_split": {"full_cream": 5, "oat": 2, "soy": 1},
                "switch_3p_time": f"{forecast_date}T08:40:00",
                "alert_time": f"{forecast_date}T08:37:00",
            }
        ]
    return {
        "prediction_id": TEST_PREDICTION_ID,
        "forecast_date": forecast_date,
        "generated_at": "2026-02-06T18:00:00",
        "confidence_score": confidence,
        "event_factor": 1.0,
        "actual_accuracy": None,
        "forecast_data": json.dumps(
            {
                "forecast": {
                    "forecast_date": str(forecast_date),
                    "day_name": forecast_date.strftime("%A"),
                    "total_predicted_drinks": 287,
                    "total_predicted_workload": 780.0,
                    "staff_scheduled": 3,
                    "staffing_mode": "3P",
                    "hourly": [
                        {"hour": 8, "predicted_workload": 42.5, "is_rush": False},
                        {"hour": 9, "predicted_workload": 95.0, "is_rush": True},
                    ],
                },
                "weather": {
                    "temp_c": 28.0,
                    "description": "sunny",
                    "rain_probability": 0,
                    "humidity": 65,
                },
                "rush_windows": rush_windows,
            }
        ),
        "rush_windows": None,
    }


@patch("app.routers.tomorrow_plan.get_prediction")
@patch("app.dependencies.get_site")
def test_json_endpoint_returns_structured_response(mock_get_site, mock_get_pred):
    """Endpoint returns all top-level keys: meta, forecast, weather, rush_windows, hourly."""
    mock_get_site.side_effect = _mock_site
    mock_get_pred.return_value = _build_db_row()

    tomorrow = date.today() + timedelta(days=1)
    resp = client.get(
        f"/api/sites/{TEST_SITE_ID}/tomorrow-plan/json?target_date={tomorrow.isoformat()}"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {"meta", "forecast", "weather", "rush_windows", "hourly"}


@patch("app.routers.tomorrow_plan.get_prediction")
@patch("app.dependencies.get_site")
def test_json_meta_fields(mock_get_site, mock_get_pred):
    """Meta section contains expected fields."""
    mock_get_site.side_effect = _mock_site
    mock_get_pred.return_value = _build_db_row()

    tomorrow = date.today() + timedelta(days=1)
    resp = client.get(
        f"/api/sites/{TEST_SITE_ID}/tomorrow-plan/json?target_date={tomorrow.isoformat()}"
    )
    meta = resp.json()["meta"]
    assert meta["prediction_id"] == TEST_PREDICTION_ID
    assert meta["forecast_date"] == str(tomorrow)
    assert meta["confidence"] == 0.87
    assert meta["confidence_label"] == "high"
    assert meta["staffing_mode"] == "3P"
    assert meta["staff_scheduled"] == 3


@patch("app.routers.tomorrow_plan.get_prediction")
@patch("app.dependencies.get_site")
def test_json_rush_windows_have_checklist(mock_get_site, mock_get_pred):
    """Each rush window includes pre_rush_checklist."""
    mock_get_site.side_effect = _mock_site
    mock_get_pred.return_value = _build_db_row()

    tomorrow = date.today() + timedelta(days=1)
    resp = client.get(
        f"/api/sites/{TEST_SITE_ID}/tomorrow-plan/json?target_date={tomorrow.isoformat()}"
    )
    windows = resp.json()["rush_windows"]
    assert len(windows) == 1
    assert windows[0]["window_number"] == 1
    checklist = windows[0]["pre_rush_checklist"]
    assert isinstance(checklist, list)
    assert len(checklist) > 0
    assert "Grind 2kg beans" in checklist


@patch("app.routers.tomorrow_plan.get_prediction")
@patch("app.dependencies.get_site")
def test_json_404_when_no_prediction(mock_get_site, mock_get_pred):
    """Returns 404 when no prediction exists for the date."""
    mock_get_site.side_effect = _mock_site
    mock_get_pred.return_value = None

    resp = client.get(
        f"/api/sites/{TEST_SITE_ID}/tomorrow-plan/json?target_date=2026-01-01"
    )
    assert resp.status_code == 404


@patch("app.routers.tomorrow_plan.get_prediction")
@patch("app.dependencies.get_site")
def test_json_confidence_labels(mock_get_site, mock_get_pred):
    """Confidence score maps to correct label."""
    mock_get_site.side_effect = _mock_site

    for score, expected_label in [(0.9, "high"), (0.7, "medium"), (0.4, "low")]:
        mock_get_pred.return_value = _build_db_row(confidence=score)
        tomorrow = date.today() + timedelta(days=1)
        resp = client.get(
            f"/api/sites/{TEST_SITE_ID}/tomorrow-plan/json?target_date={tomorrow.isoformat()}"
        )
        assert resp.json()["meta"]["confidence_label"] == expected_label


@patch("app.routers.tomorrow_plan.get_prediction")
@patch("app.dependencies.get_site")
def test_json_weather_section(mock_get_site, mock_get_pred):
    """Weather section surfaces key fields."""
    mock_get_site.side_effect = _mock_site
    mock_get_pred.return_value = _build_db_row()

    tomorrow = date.today() + timedelta(days=1)
    resp = client.get(
        f"/api/sites/{TEST_SITE_ID}/tomorrow-plan/json?target_date={tomorrow.isoformat()}"
    )
    weather = resp.json()["weather"]
    assert weather["temp_c"] == 28.0
    assert weather["description"] == "sunny"
    assert weather["rain_probability"] == 0
    assert weather["humidity"] == 65
