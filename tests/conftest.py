"""
Clubhouse Autopilot v1.2 - Test Configuration
Shared fixtures and mocks for the test suite.
"""

import os
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

# Set test environment before any config imports
os.environ.setdefault("DATABASE_URL", "postgresql://localhost:5432/clubhouse_autopilot_test")
os.environ.setdefault("SQUARE_ACCESS_TOKEN", "test-token")
os.environ.setdefault("SQUARE_LOCATION_ID", "test-location")
os.environ.setdefault("SQUARE_ENVIRONMENT", "sandbox")
os.environ.setdefault("TWILIO_ACCOUNT_SID", "test-sid")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "test-token")
os.environ.setdefault("TWILIO_PHONE_NUMBER", "+61400000000")
os.environ.setdefault("MANAGER_PHONE", "+61412345678")
os.environ.setdefault("WEATHER_API_KEY", "")


# ============================================================
# Common Test Data
# ============================================================

TEST_SITE_ID = "00000000-0000-0000-0000-000000000001"
TEST_SITE_NAME = "Clubhouse Nundah"
TEST_PREDICTION_ID = "00000000-0000-0000-0000-000000000099"


@pytest.fixture
def site_id():
    return TEST_SITE_ID


@pytest.fixture
def site_name():
    return TEST_SITE_NAME


@pytest.fixture
def prediction_id():
    return TEST_PREDICTION_ID


@pytest.fixture
def staff_names():
    return {"P1": "Sarah", "P2": "Tom", "P3": "Jessica"}


@pytest.fixture
def sample_parsed_order():
    """A parsed order matching the spec Section 5.4 example:
    2x Oat Latte + 1x Decaf Cappuccino = 11.38 units."""
    return {
        "order_id": "test-order-001",
        "created_at": "2026-02-07T08:45:00+10:00",
        "closed_at": "2026-02-07T08:48:00+10:00",
        "total_money_cents": 1650,
        "state": "COMPLETED",
        "payload": {},
        "line_items": [
            {
                "item_name": "Latte",
                "quantity": 2,
                "catalog_item_id": "cat-latte",
                "modifiers": [{"name": "Oat Milk"}],
            },
            {
                "item_name": "Cappuccino",
                "quantity": 1,
                "catalog_item_id": "cat-cap",
                "modifiers": [{"name": "Decaf"}],
            },
        ],
    }


@pytest.fixture
def sample_rush_window():
    """A rush window dict matching spec Section 8.1 example."""
    return {
        "start": "2026-02-07T08:47:00",
        "end": "2026-02-07T09:32:00",
        "duration_minutes": 45,
        "predicted_drinks": 52,
        "predicted_workload": 156.0,
        "confidence": 0.87,
        "confidence_label": "high",
        "switch_3p_time": "2026-02-07T08:40:00",
        "alert_time": "2026-02-07T08:37:00",
        "wally_start_time": "2026-02-07T08:35:00",
        "wally_volume_litres": 8,
        "wally_split": {"full_cream": 5, "oat": 2, "soy": 1},
    }


@pytest.fixture
def sample_prediction(sample_rush_window):
    """A complete prediction dict."""
    return {
        "prediction_id": TEST_PREDICTION_ID,
        "recent_avg": 51.3,
        "yoy_avg": 53.7,
        "dow_factor": 1.12,
        "event_multiplier": 1.0,
        "base_prediction": 52.8,
        "confidence": 0.87,
        "confidence_label": "high",
        "forecast": {
            "site_id": TEST_SITE_ID,
            "forecast_date": "2026-02-07",
            "day_name": "Friday",
            "event_multiplier": 1.0,
            "total_predicted_workload": 780.0,
            "total_predicted_drinks": 287,
            "staff_scheduled": 3,
            "staffing_mode": "3P",
            "rush_windows": [sample_rush_window],
            "rush_count": 1,
            "hourly": [],
        },
        "weather": {
            "temp_c": 28.0,
            "feels_like_c": 30.0,
            "humidity": 65,
            "description": "sunny",
            "icon": "01d",
            "rain_3h_mm": 0,
            "rain_probability": 0,
        },
        "total_predicted_drinks": 287,
        "total_predicted_workload": 780.0,
        "rush_count": 1,
        "rush_windows": [sample_rush_window],
        "staff_scheduled": 3,
        "staffing_mode": "3P",
    }


@pytest.fixture
def sample_weather():
    return {
        "temp_c": 28.0,
        "feels_like_c": 30.0,
        "humidity": 65,
        "description": "sunny",
        "icon": "01d",
        "rain_3h_mm": 0,
        "rain_probability": 0,
    }


@pytest.fixture
def mock_db():
    """Mock the database engine for tests that don't need real DB."""
    with (
        patch("config.database.engine") as mock_engine,
        patch("analysis.accuracy.engine") as mock_accuracy_engine,
    ):
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        mock_accuracy_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_accuracy_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        yield mock_engine, mock_conn
