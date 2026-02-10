"""
Tests for delivery/tomorrow_plan.py - Tomorrow Plan Generator

Validates the printed plan matches the Spec Section 8.1 template.
"""

from datetime import datetime
from unittest.mock import patch

import pytest

from delivery.tomorrow_plan import (
    _format_time,
    generate_tomorrow_plan,
    generate_tomorrow_plan_html,
)
from tests.conftest import TEST_SITE_ID


# ============================================================
# Tomorrow Plan Text
# ============================================================


class TestGenerateTomorrowPlan:
    @patch("delivery.tomorrow_plan.get_yesterday_recap")
    def test_contains_header(self, mock_recap, sample_prediction, staff_names):
        mock_recap.return_value = None

        plan = generate_tomorrow_plan(
            site_name="Clubhouse Nundah",
            site_id=TEST_SITE_ID,
            prediction=sample_prediction,
            staff_names=staff_names,
            generated_at=datetime(2026, 2, 6, 18, 0),
        )

        assert "CLUBHOUSE AUTOPILOT - TOMORROW PLAN" in plan
        assert "Clubhouse Nundah" in plan

    @patch("delivery.tomorrow_plan.get_yesterday_recap")
    def test_contains_forecast_conditions(self, mock_recap, sample_prediction, staff_names):
        mock_recap.return_value = None

        plan = generate_tomorrow_plan(
            site_name="Clubhouse Nundah",
            site_id=TEST_SITE_ID,
            prediction=sample_prediction,
            staff_names=staff_names,
        )

        assert "FORECAST CONDITIONS" in plan
        assert "28" in plan  # temperature
        assert "Friday" in plan

    @patch("delivery.tomorrow_plan.get_yesterday_recap")
    def test_contains_rush_window(self, mock_recap, sample_prediction, staff_names):
        mock_recap.return_value = None

        plan = generate_tomorrow_plan(
            site_name="Clubhouse Nundah",
            site_id=TEST_SITE_ID,
            prediction=sample_prediction,
            staff_names=staff_names,
        )

        assert "RUSH WINDOW #1" in plan
        assert "52 drinks" in plan
        assert "WALLY START" in plan
        assert "SWITCH TO 3P" in plan
        assert "P3 PREP" in plan
        assert "DELIVERY PRIORITY" in plan

    @patch("delivery.tomorrow_plan.get_yesterday_recap")
    def test_contains_staff_names(self, mock_recap, sample_prediction, staff_names):
        mock_recap.return_value = None

        plan = generate_tomorrow_plan(
            site_name="Clubhouse Nundah",
            site_id=TEST_SITE_ID,
            prediction=sample_prediction,
            staff_names=staff_names,
        )

        assert "Sarah" in plan
        assert "Tom" in plan
        assert "Jessica" in plan

    @patch("delivery.tomorrow_plan.get_yesterday_recap")
    def test_contains_checklist(self, mock_recap, sample_prediction, staff_names):
        mock_recap.return_value = None

        plan = generate_tomorrow_plan(
            site_name="Clubhouse Nundah",
            site_id=TEST_SITE_ID,
            prediction=sample_prediction,
            staff_names=staff_names,
        )

        assert "PRE-RUSH CHECKLIST" in plan
        assert "Grind 2kg beans" in plan
        assert "Check Bar 2 steam wand" in plan

    @patch("delivery.tomorrow_plan.get_yesterday_recap")
    def test_contains_targets(self, mock_recap, sample_prediction, staff_names):
        mock_recap.return_value = None

        plan = generate_tomorrow_plan(
            site_name="Clubhouse Nundah",
            site_id=TEST_SITE_ID,
            prediction=sample_prediction,
            staff_names=staff_names,
        )

        assert "TARGETS" in plan
        assert "287" in plan  # total drinks
        assert "33%" in plan  # labour target

    @patch("delivery.tomorrow_plan.get_yesterday_recap")
    def test_contains_feedback(self, mock_recap, sample_prediction, staff_names):
        mock_recap.return_value = None

        plan = generate_tomorrow_plan(
            site_name="Clubhouse Nundah",
            site_id=TEST_SITE_ID,
            prediction=sample_prediction,
            staff_names=staff_names,
        )

        assert "FEEDBACK" in plan
        assert "Rush timing" in plan
        assert "Helpfulness" in plan

    @patch("delivery.tomorrow_plan.get_yesterday_recap")
    def test_contains_footer(self, mock_recap, sample_prediction, staff_names):
        mock_recap.return_value = None

        plan = generate_tomorrow_plan(
            site_name="Clubhouse Nundah",
            site_id=TEST_SITE_ID,
            prediction=sample_prediction,
            staff_names=staff_names,
        )

        assert "Autopilot v1.2" in plan

    @patch("delivery.tomorrow_plan.get_yesterday_recap")
    def test_yesterday_recap_included(self, mock_recap, sample_prediction, staff_names):
        mock_recap.return_value = {
            "date": "2026-02-06",
            "predicted_drinks": 234,
            "actual_accuracy": 94.4,
            "recommendations_total": 5,
            "recommendations_adopted": 4,
            "adoption_rate": 0.80,
        }

        plan = generate_tomorrow_plan(
            site_name="Clubhouse Nundah",
            site_id=TEST_SITE_ID,
            prediction=sample_prediction,
            staff_names=staff_names,
        )

        assert "YESTERDAY RECAP" in plan
        assert "234" in plan
        assert "94.4%" in plan
        assert "4/5" in plan

    @patch("delivery.tomorrow_plan.get_yesterday_recap")
    def test_no_weather(self, mock_recap, sample_prediction, staff_names):
        mock_recap.return_value = None
        sample_prediction["weather"] = None

        plan = generate_tomorrow_plan(
            site_name="Clubhouse Nundah",
            site_id=TEST_SITE_ID,
            prediction=sample_prediction,
            staff_names=staff_names,
        )

        assert "Unavailable" in plan


# ============================================================
# HTML Version
# ============================================================


class TestTomorrowPlanHtml:
    @patch("delivery.tomorrow_plan.get_yesterday_recap")
    def test_generates_valid_html(self, mock_recap, sample_prediction, staff_names):
        mock_recap.return_value = None

        html = generate_tomorrow_plan_html(
            site_name="Clubhouse Nundah",
            site_id=TEST_SITE_ID,
            prediction=sample_prediction,
            staff_names=staff_names,
        )

        assert html.startswith("<!DOCTYPE html>")
        assert "<title>Tomorrow Plan" in html
        assert "Courier" in html  # monospace font
        assert "</html>" in html

    @patch("delivery.tomorrow_plan.get_yesterday_recap")
    def test_html_contains_plan_content(self, mock_recap, sample_prediction, staff_names):
        mock_recap.return_value = None

        html = generate_tomorrow_plan_html(
            site_name="Clubhouse Nundah",
            site_id=TEST_SITE_ID,
            prediction=sample_prediction,
            staff_names=staff_names,
        )

        assert "CLUBHOUSE AUTOPILOT" in html
        assert "RUSH WINDOW" in html


# ============================================================
# Time Formatting
# ============================================================


class TestFormatTime:
    def test_morning(self):
        assert _format_time("2026-02-07T08:47:00") == "8:47am"

    def test_afternoon(self):
        assert _format_time("2026-02-07T14:30:00") == "2:30pm"

    def test_invalid(self):
        assert _format_time("bad") == "bad"
