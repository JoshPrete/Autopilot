"""
Tests for models/workload.py - Workload Model

Covers:
- RushWindow class and properties
- Rush window detection logic
- Queue signal evaluation
- State transition rules (Spec Section 3.3)
"""

from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

from models.workload import (
    QueueSignals,
    RushWindow,
    _merge_rush_hours,
    _resolve_staffing_mode,
    evaluate_transition_signals,
)


# ============================================================
# RushWindow Class
# ============================================================


class TestRushWindow:
    def test_duration_minutes(self):
        rw = RushWindow(
            start=datetime(2026, 2, 7, 8, 47),
            end=datetime(2026, 2, 7, 9, 32),
            predicted_drinks=52,
            predicted_workload=156.0,
            confidence=0.87,
            confidence_label="high",
        )
        assert rw.duration_minutes == 45

    def test_switch_3p_time(self):
        """Switch to 3P should be T-7 minutes before rush start."""
        rw = RushWindow(
            start=datetime(2026, 2, 7, 8, 47),
            end=datetime(2026, 2, 7, 9, 32),
            predicted_drinks=52,
            predicted_workload=156.0,
            confidence=0.87,
            confidence_label="high",
        )
        expected = datetime(2026, 2, 7, 8, 40)
        assert rw.switch_3p_time == expected

    def test_wally_start_time(self):
        """Wally starts T-12 minutes before rush."""
        rw = RushWindow(
            start=datetime(2026, 2, 7, 8, 47),
            end=datetime(2026, 2, 7, 9, 32),
            predicted_drinks=52,
            predicted_workload=156.0,
            confidence=0.87,
            confidence_label="high",
        )
        expected = datetime(2026, 2, 7, 8, 35)
        assert rw.wally_start_time == expected

    def test_alert_time(self):
        """Alert at T-10 minutes before rush."""
        rw = RushWindow(
            start=datetime(2026, 2, 7, 8, 47),
            end=datetime(2026, 2, 7, 9, 32),
            predicted_drinks=52,
            predicted_workload=156.0,
            confidence=0.87,
            confidence_label="high",
        )
        expected = datetime(2026, 2, 7, 8, 37)
        assert rw.alert_time == expected

    def test_to_dict(self):
        rw = RushWindow(
            start=datetime(2026, 2, 7, 8, 47),
            end=datetime(2026, 2, 7, 9, 32),
            predicted_drinks=52,
            predicted_workload=156.0,
            confidence=0.87,
            confidence_label="high",
        )
        d = rw.to_dict()

        assert d["start"] == "2026-02-07T08:47:00"
        assert d["end"] == "2026-02-07T09:32:00"
        assert d["duration_minutes"] == 45
        assert d["predicted_drinks"] == 52
        assert d["confidence_label"] == "high"
        assert "wally_volume_litres" in d
        assert "wally_split" in d

    def test_wally_volume(self):
        """52 drinks → 8L wally volume (spec example)."""
        rw = RushWindow(
            start=datetime(2026, 2, 7, 8, 47),
            end=datetime(2026, 2, 7, 9, 32),
            predicted_drinks=52,
            predicted_workload=156.0,
            confidence=0.87,
            confidence_label="high",
        )
        wally = rw.wally_volume
        assert wally["total_litres"] == 8


# ============================================================
# Merge Rush Hours
# ============================================================


class TestMergeRushHours:
    def test_single_hour(self):
        hours = [
            {
                "hour": 8,
                "prediction": {
                    "final_prediction": 50.0,
                    "confidence": 0.85,
                },
            }
        ]
        rw = _merge_rush_hours(date(2026, 2, 7), hours)

        assert rw.start == datetime(2026, 2, 7, 8, 0)
        assert rw.end == datetime(2026, 2, 7, 9, 0)
        assert rw.duration_minutes == 60

    def test_consecutive_hours(self):
        hours = [
            {"hour": 8, "prediction": {"final_prediction": 50.0, "confidence": 0.85}},
            {"hour": 9, "prediction": {"final_prediction": 45.0, "confidence": 0.80}},
        ]
        rw = _merge_rush_hours(date(2026, 2, 7), hours)

        assert rw.start == datetime(2026, 2, 7, 8, 0)
        assert rw.end == datetime(2026, 2, 7, 10, 0)
        assert rw.duration_minutes == 120
        # Total workload summed
        assert rw.predicted_workload == 95.0

    def test_confidence_uses_minimum(self):
        hours = [
            {"hour": 8, "prediction": {"final_prediction": 50.0, "confidence": 0.90}},
            {"hour": 9, "prediction": {"final_prediction": 50.0, "confidence": 0.60}},
        ]
        rw = _merge_rush_hours(date(2026, 2, 7), hours)

        # Label based on min confidence (0.60 < 0.70 → "low")
        assert rw.confidence_label == "low"


# ============================================================
# Queue Signals
# ============================================================


class TestQueueSignals:
    def test_workload_ratio(self):
        signals = QueueSignals(
            workload_units_per_15min=75.0,
            baseline_workload=50.0,
        )
        assert signals.workload_ratio == 1.5

    def test_workload_ratio_zero_baseline(self):
        signals = QueueSignals(baseline_workload=0.0)
        assert signals.workload_ratio == 0.0

    def test_drinks_in_progress_from_kds(self):
        signals = QueueSignals(items_in_progress=12)
        assert signals.drinks_in_progress == 12

    def test_drinks_in_progress_estimated(self):
        signals = QueueSignals(orders_per_5min=5)
        assert signals.drinks_in_progress == 10  # 5 * 2

    def test_orders_last_2min(self):
        signals = QueueSignals(orders_per_5min=10)
        assert signals.orders_last_2min == 4  # 10 * 0.4


# ============================================================
# State Transition Rules (Spec Section 3.3)
# ============================================================


class TestEvaluateTransitionSignals:
    def test_switch_to_3p_all_conditions(self):
        """ALL conditions must be true for 2P→3P switch."""
        signals = QueueSignals(
            workload_units_per_15min=80.0,
            baseline_workload=50.0,  # ratio = 1.6 > 1.5
            orders_per_5min=7,  # > 6
            items_in_progress=10,  # > 8
            staff_on_floor=2,
        )
        triggered = evaluate_transition_signals(signals)
        types = [t["transition"] for t in triggered]
        assert "SWITCH_TO_3P" in types

    def test_switch_to_3p_not_triggered_low_workload(self):
        """Missing workload condition → no 3P switch."""
        signals = QueueSignals(
            workload_units_per_15min=50.0,
            baseline_workload=50.0,  # ratio = 1.0 < 1.5
            orders_per_5min=7,
            items_in_progress=10,
            staff_on_floor=2,
        )
        triggered = evaluate_transition_signals(signals)
        types = [t["transition"] for t in triggered]
        assert "SWITCH_TO_3P" not in types

    def test_switch_to_3p_not_triggered_already_3p(self):
        """Already 3 staff → no 3P switch."""
        signals = QueueSignals(
            workload_units_per_15min=80.0,
            baseline_workload=50.0,
            orders_per_5min=7,
            items_in_progress=10,
            staff_on_floor=3,  # Already 3P
        )
        triggered = evaluate_transition_signals(signals)
        types = [t["transition"] for t in triggered]
        assert "SWITCH_TO_3P" not in types

    def test_wally_activate_milk_queue(self):
        """ANY condition triggers Wally."""
        signals = QueueSignals(milk_drinks_queued=3)  # >= 3
        triggered = evaluate_transition_signals(signals)
        types = [t["transition"] for t in triggered]
        assert "WALLY_START" in types

    def test_wally_activate_workload(self):
        signals = QueueSignals(
            workload_units_per_15min=70.0,
            baseline_workload=50.0,  # ratio = 1.4 > 1.3
        )
        triggered = evaluate_transition_signals(signals)
        types = [t["transition"] for t in triggered]
        assert "WALLY_START" in types

    def test_wally_activate_manual_signal(self):
        signals = QueueSignals(milk_queue_high=True)
        triggered = evaluate_transition_signals(signals)
        types = [t["transition"] for t in triggered]
        assert "WALLY_START" in types

    def test_p1_to_shots(self):
        """P1 moves to shots when counter clear and drinks in progress."""
        signals = QueueSignals(
            orders_per_5min=0,  # last_2min = 0
            items_in_progress=3,  # > 0
        )
        triggered = evaluate_transition_signals(signals)
        types = [t["transition"] for t in triggered]
        assert "P1_TO_SHOTS" in types

    def test_delivery_override(self):
        """3+ drinks ready and <=1 orders waiting."""
        signals = QueueSignals(
            items_completed=4,  # >= 3
            orders_waiting=0,  # <= 1
        )
        triggered = evaluate_transition_signals(signals)
        types = [t["transition"] for t in triggered]
        assert "DELIVERY_OVERRIDE" in types

    def test_rush_end(self):
        """10 min below baseline → rush end."""
        signals = QueueSignals(minutes_below_baseline=10)
        triggered = evaluate_transition_signals(signals)
        types = [t["transition"] for t in triggered]
        assert "RUSH_END" in types

    def test_rush_end_not_triggered(self):
        """Only 5 min below baseline → not yet."""
        signals = QueueSignals(minutes_below_baseline=5)
        triggered = evaluate_transition_signals(signals)
        types = [t["transition"] for t in triggered]
        assert "RUSH_END" not in types

    def test_no_transitions_quiet_period(self):
        """Normal quiet period → only P1_TO_SHOTS (counter clear with drinks in progress)."""
        signals = QueueSignals(
            orders_per_5min=2,
            workload_units_per_15min=20.0,
            baseline_workload=50.0,
            staff_on_floor=2,
        )
        triggered = evaluate_transition_signals(signals)
        # Low orders triggers P1_TO_SHOTS (orders_last_2min=0, drinks_in_progress estimated=4)
        types = [t["transition"] for t in triggered]
        assert "SWITCH_TO_3P" not in types
        assert "RUSH_END" not in types


# ============================================================
# Staffing Mode Resolution
# ============================================================


class TestResolveStaffingMode:
    def test_1p(self):
        assert _resolve_staffing_mode(1) == "1P"

    def test_2p(self):
        assert _resolve_staffing_mode(2) == "2P"

    def test_3p(self):
        assert _resolve_staffing_mode(3) == "3P"

    def test_4p(self):
        assert _resolve_staffing_mode(4) == "4P"
        assert _resolve_staffing_mode(5) == "4P"
