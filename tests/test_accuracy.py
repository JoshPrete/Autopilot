"""
Tests for analysis/accuracy.py - Accuracy Tracking

Covers:
- Volume accuracy calculation
- Timing accuracy calculation
- Adoption metrics
"""

import pytest

from analysis.accuracy import (
    calculate_timing_accuracy,
    calculate_volume_accuracy,
)
from datetime import datetime


# ============================================================
# Volume Accuracy
# ============================================================


class TestVolumeAccuracy:
    def test_perfect_prediction(self):
        assert calculate_volume_accuracy(100, 100) == 1.0

    def test_spec_example(self):
        """Predicted 234, Actual 247 → accuracy = 94.7%."""
        acc = calculate_volume_accuracy(234, 247)
        assert 0.94 <= acc <= 0.95

    def test_over_prediction(self):
        """Predicted 120, Actual 100 → 80% accuracy."""
        acc = calculate_volume_accuracy(120, 100)
        assert acc == 0.8

    def test_under_prediction(self):
        """Predicted 80, Actual 100 → 80% accuracy."""
        acc = calculate_volume_accuracy(80, 100)
        assert acc == 0.8

    def test_zero_actual(self):
        assert calculate_volume_accuracy(50, 0) is None

    def test_clamped_to_zero(self):
        """Massive error still clamps to 0."""
        acc = calculate_volume_accuracy(500, 100)
        assert acc == 0.0

    def test_negative_actual(self):
        assert calculate_volume_accuracy(50, -1) is None


# ============================================================
# Timing Accuracy
# ============================================================


class TestTimingAccuracy:
    def test_perfect_timing(self):
        result = calculate_timing_accuracy(
            predicted_start=datetime(2026, 2, 7, 8, 47),
            predicted_end=datetime(2026, 2, 7, 9, 32),
            actual_start=datetime(2026, 2, 7, 8, 47),
            actual_end=datetime(2026, 2, 7, 9, 32),
        )
        assert result["start_error_minutes"] == 0
        assert result["end_error_minutes"] == 0
        assert result["within_target"] is True

    def test_within_20_min_target(self):
        """±20 min is the spec Section 10.1 success criteria."""
        result = calculate_timing_accuracy(
            predicted_start=datetime(2026, 2, 7, 8, 47),
            predicted_end=datetime(2026, 2, 7, 9, 32),
            actual_start=datetime(2026, 2, 7, 9, 0),  # 13 min late
            actual_end=datetime(2026, 2, 7, 9, 45),  # 13 min late
        )
        assert result["within_target"] is True

    def test_outside_target(self):
        result = calculate_timing_accuracy(
            predicted_start=datetime(2026, 2, 7, 8, 47),
            predicted_end=datetime(2026, 2, 7, 9, 32),
            actual_start=datetime(2026, 2, 7, 9, 30),  # 43 min late
            actual_end=datetime(2026, 2, 7, 10, 15),
        )
        assert result["within_target"] is False
        assert result["start_error_minutes"] > 20

    def test_avg_error(self):
        result = calculate_timing_accuracy(
            predicted_start=datetime(2026, 2, 7, 8, 47),
            predicted_end=datetime(2026, 2, 7, 9, 32),
            actual_start=datetime(2026, 2, 7, 8, 57),  # 10 min
            actual_end=datetime(2026, 2, 7, 9, 52),  # 20 min
        )
        assert result["avg_error_minutes"] == 15.0
