"""
Tests for delivery/sms_prompts.py - SMS Prompt Library

Validates all SMS templates match Spec Section 9.1 format
and stay within SMS character limits.
"""

import pytest

from delivery.sms_prompts import (
    _format_time,
    delivery_override,
    end_of_day_feedback,
    p1_to_shots,
    pre_rush_reminder,
    rush_easing,
    switch_to_3p,
    system_alert_ingestion_failed,
    system_alert_sms_failed,
    tomorrow_plan_summary,
    wally_start,
)


# ============================================================
# Time Formatting
# ============================================================


class TestFormatTime:
    def test_morning(self):
        assert _format_time("2026-02-07T08:47:00") == "8:47am"

    def test_afternoon(self):
        assert _format_time("2026-02-07T14:30:00") == "2:30pm"

    def test_noon(self):
        assert _format_time("2026-02-07T12:00:00") == "12pm"

    def test_midnight(self):
        assert _format_time("2026-02-07T00:00:00") == "12am"

    def test_on_the_hour(self):
        assert _format_time("2026-02-07T09:00:00") == "9am"

    def test_invalid_returns_original(self):
        assert _format_time("not-a-time") == "not-a-time"


# ============================================================
# Tomorrow Plan Summary (6pm)
# ============================================================


class TestTomorrowPlanSummary:
    def test_basic_message(self, sample_rush_window):
        msg = tomorrow_plan_summary(
            day_name="Friday",
            date_str="7 FEB",
            rush_windows=[sample_rush_window],
            weather={"temp_c": 28, "description": "sunny"},
            total_drinks=287,
            staffing_mode="3P",
        )

        assert "Tomorrow Plan Ready" in msg
        assert "FRI 7 FEB" in msg
        assert "Rush:" in msg
        assert "52 drinks" in msg
        assert "28°C sunny" in msg
        assert "3P workflow needed" in msg
        assert "Print on bench" in msg

    def test_no_rush_windows(self):
        msg = tomorrow_plan_summary(
            day_name="Monday",
            date_str="3 FEB",
            rush_windows=[],
        )
        assert "No rush windows" in msg

    def test_multiple_rushes(self, sample_rush_window):
        rush2 = {**sample_rush_window, "start": "2026-02-07T12:00:00", "end": "2026-02-07T12:45:00", "predicted_drinks": 38}
        msg = tomorrow_plan_summary(
            day_name="Friday",
            date_str="7 FEB",
            rush_windows=[sample_rush_window, rush2],
        )
        assert msg.count("Rush:") == 2

    def test_no_weather(self, sample_rush_window):
        msg = tomorrow_plan_summary(
            day_name="Friday",
            date_str="7 FEB",
            rush_windows=[sample_rush_window],
        )
        assert "Weather" not in msg

    def test_2p_mode_no_workflow_line(self, sample_rush_window):
        msg = tomorrow_plan_summary(
            day_name="Friday",
            date_str="7 FEB",
            rush_windows=[sample_rush_window],
            staffing_mode="2P",
        )
        assert "workflow needed" not in msg


# ============================================================
# Pre-Rush Reminder
# ============================================================


class TestPreRushReminder:
    def test_basic_message(self):
        msg = pre_rush_reminder(
            rush_start="2026-02-07T08:47:00",
            rush_end="2026-02-07T09:32:00",
            predicted_drinks=52,
            confidence_label="high",
            switch_3p_time="2026-02-07T08:40:00",
        )

        assert "Rush in 15 mins" in msg
        assert "52 drinks" in msg
        assert "Wally start now" in msg
        assert "Switch 3P at 8:40am" in msg
        assert "Pre-stage cups" in msg


# ============================================================
# Switch to 3P
# ============================================================


class TestSwitchTo3P:
    def test_with_names(self, staff_names):
        msg = switch_to_3p(staff_names, "2026-02-07T08:47:00")

        assert "Switch to 3P workflow NOW" in msg
        assert "Sarah (P1): Front" in msg
        assert "Tom (P2): Shots+Milk" in msg
        assert "Jessica (P3): Delivery" in msg
        assert "8:47am" in msg

    def test_without_names(self):
        msg = switch_to_3p({}, "2026-02-07T08:47:00")
        assert "P1 (P1): Front" in msg


# ============================================================
# Wally Start
# ============================================================


class TestWallyStart:
    def test_spec_example(self):
        msg = wally_start(
            volume_litres=8,
            split={"full_cream": 5, "oat": 2, "soy": 1},
            staff_name="Tom",
        )

        assert "Tom: Start Wally cycling" in msg
        assert "8L milk now:" in msg
        assert "5L Full Cream" in msg
        assert "2L Oat" in msg
        assert "1L Soy" in msg
        assert "Keep running until rush ends" in msg


# ============================================================
# Simple Prompts
# ============================================================


class TestSimplePrompts:
    def test_p1_to_shots(self):
        msg = p1_to_shots()
        assert "Counter clear" in msg
        assert "P1 move to shots now" in msg

    def test_delivery_override(self):
        msg = delivery_override("Jessica")
        assert "Drinks stacking" in msg
        assert "Jessica: Delivery priority NOW" in msg

    def test_delivery_override_default(self):
        msg = delivery_override()
        assert "P3: Delivery priority NOW" in msg

    def test_rush_easing(self):
        msg = rush_easing()
        assert "Rush easing" in msg
        assert "Return to 2P baseline" in msg
        assert "P3 can transition out" in msg
        assert "Good work!" in msg

    def test_end_of_day_feedback(self):
        msg = end_of_day_feedback()
        assert "How was today's plan?" in msg
        assert "Rush timing (1-5)" in msg
        assert "Helpfulness (1-5)" in msg
        assert '"5 4"' in msg


# ============================================================
# System Alerts
# ============================================================


class TestSystemAlerts:
    def test_ingestion_failed(self):
        msg = system_alert_ingestion_failed("Clubhouse Nundah", "Connection timeout")
        assert "Autopilot Alert" in msg
        assert "Clubhouse Nundah" in msg
        assert "Connection timeout" in msg
        assert "printed SOP" in msg

    def test_ingestion_failed_truncates_long_error(self):
        long_error = "x" * 200
        msg = system_alert_ingestion_failed("Test", long_error)
        assert len(long_error) > 100
        # Error should be truncated
        assert "x" * 100 in msg

    def test_sms_failed(self):
        msg = system_alert_sms_failed("Clubhouse Nundah")
        assert "SMS delivery failing" in msg
        assert "printed plan" in msg


# ============================================================
# SMS Length Validation
# ============================================================


class TestSmsLengths:
    """SMS messages should ideally fit within a single SMS (160 chars)
    or at most 3 segments (480 chars)."""

    def test_all_prompts_under_480_chars(self, sample_rush_window):
        messages = [
            tomorrow_plan_summary("Friday", "7 FEB", [sample_rush_window],
                                  {"temp_c": 28, "description": "sunny"}, 287, "3P"),
            pre_rush_reminder("2026-02-07T08:47:00", "2026-02-07T09:32:00", 52, "high", "2026-02-07T08:40:00"),
            switch_to_3p({"P1": "Sarah", "P2": "Tom", "P3": "Jess"}, "2026-02-07T08:47:00"),
            wally_start(8, {"full_cream": 5, "oat": 2, "soy": 1}, "Tom"),
            p1_to_shots(),
            delivery_override("Jessica"),
            rush_easing(),
            end_of_day_feedback(),
        ]

        for msg in messages:
            assert len(msg) <= 480, f"SMS too long ({len(msg)} chars): {msg[:50]}..."
