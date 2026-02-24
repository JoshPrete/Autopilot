"""
Tests for analysis/accuracy.py - get_accuracy_dashboard()

Covers:
- Empty results → graceful empty response
- Full week → 7 DOW entries
- Confidence bucketing into 3 bands
- Staffing mode grouping
- Trend direction calculation
- Null drink counts excluded from predicted_vs_actual
"""

import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from analysis.accuracy import get_accuracy_dashboard


def _make_row(
    forecast_date,
    actual_accuracy,
    confidence_score,
    predicted_drinks,
    staffing_mode,
    actual_drinks,
    dow_num,
):
    """Build a dict that behaves like a SQLAlchemy RowMapping."""
    return {
        "forecast_date": forecast_date,
        "actual_accuracy": actual_accuracy,
        "confidence_score": confidence_score,
        "predicted_drinks": str(predicted_drinks) if predicted_drinks is not None else None,
        "staffing_mode": staffing_mode,
        "actual_drinks": actual_drinks,
        "dow_num": dow_num,
    }


def _mock_execute(rows):
    """Return a mock that makes engine.connect() yield controlled rows."""
    mock_result = MagicMock()
    mock_result.mappings.return_value = rows
    mock_conn = MagicMock()
    mock_conn.execute.return_value = mock_result
    mock_engine = MagicMock()
    mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    return mock_engine


class TestEmptyResults:
    @patch("analysis.accuracy.engine")
    def test_empty_returns_graceful_response(self, mock_engine):
        mock_engine.__class__ = type(mock_engine)
        engine_mock = _mock_execute([])
        mock_engine.connect = engine_mock.connect

        result = get_accuracy_dashboard("site-1", days_back=30, reference_date=date(2026, 2, 20))

        assert result["days_back"] == 30
        assert result["days_measured"] == 0
        assert result["summary"]["avg_accuracy"] is None
        assert result["summary"]["trend"] == "stable"
        assert result["alert"] is False
        assert result["daily_trend"] == []
        assert result["predicted_vs_actual"] == []
        assert result["by_dow"] == []
        assert result["by_confidence"] == []
        assert result["by_staffing_mode"] == []


class TestFullWeek:
    @patch("analysis.accuracy.engine")
    def test_seven_dow_entries(self, mock_engine):
        """A full week of data should produce 7 DOW entries."""
        ref = date(2026, 2, 22)  # Sunday
        rows = []
        for i in range(7):
            d = ref - timedelta(days=6 - i)
            dow = d.weekday()
            # Python weekday: Mon=0..Sun=6 → SQL DOW: Sun=0..Sat=6
            sql_dow = (dow + 1) % 7
            rows.append(_make_row(
                forecast_date=d,
                actual_accuracy=0.85 + i * 0.01,
                confidence_score=0.80,
                predicted_drinks=250 + i * 5,
                staffing_mode="2P",
                actual_drinks=240 + i * 3,
                dow_num=sql_dow,
            ))

        engine_mock = _mock_execute(rows)
        mock_engine.connect = engine_mock.connect

        result = get_accuracy_dashboard("site-1", days_back=7, reference_date=ref)

        assert result["days_measured"] == 7
        assert len(result["by_dow"]) == 7
        # Each DOW should have sample_count=1
        for entry in result["by_dow"]:
            assert entry["sample_count"] == 1
            assert entry["day_name"] in ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


class TestConfidenceBucketing:
    @patch("analysis.accuracy.engine")
    def test_three_bands(self, mock_engine):
        """Rows with varying confidence should bucket into high/medium/low."""
        ref = date(2026, 2, 20)
        rows = [
            # High confidence (>= 0.75)
            _make_row(ref - timedelta(days=2), 0.95, 0.90, 300, "3P", 290, 2),
            _make_row(ref - timedelta(days=1), 0.92, 0.80, 280, "3P", 270, 3),
            # Medium confidence (0.50 - 0.74)
            _make_row(ref - timedelta(days=4), 0.82, 0.60, 250, "2P", 230, 0),
            _make_row(ref - timedelta(days=3), 0.78, 0.55, 240, "2P", 220, 1),
            # Low confidence (< 0.50)
            _make_row(ref - timedelta(days=5), 0.70, 0.30, 200, "2P", 180, 6),
        ]

        engine_mock = _mock_execute(rows)
        mock_engine.connect = engine_mock.connect

        result = get_accuracy_dashboard("site-1", days_back=7, reference_date=ref)

        bands = {b["band"]: b for b in result["by_confidence"]}
        assert "high" in bands
        assert "medium" in bands
        assert "low" in bands
        assert bands["high"]["sample_count"] == 2
        assert bands["medium"]["sample_count"] == 2
        assert bands["low"]["sample_count"] == 1
        # High should be more accurate than low
        assert bands["high"]["avg_accuracy"] > bands["low"]["avg_accuracy"]
        assert result["summary"]["calibrated"] is True


class TestStaffingModeGrouping:
    @patch("analysis.accuracy.engine")
    def test_groups_by_mode(self, mock_engine):
        ref = date(2026, 2, 20)
        rows = [
            _make_row(ref - timedelta(days=3), 0.90, 0.80, 300, "3P", 290, 1),
            _make_row(ref - timedelta(days=2), 0.88, 0.80, 280, "3P", 270, 2),
            _make_row(ref - timedelta(days=1), 0.85, 0.70, 220, "2P", 210, 3),
        ]

        engine_mock = _mock_execute(rows)
        mock_engine.connect = engine_mock.connect

        result = get_accuracy_dashboard("site-1", days_back=7, reference_date=ref)

        modes = {m["mode"]: m for m in result["by_staffing_mode"]}
        assert "3P" in modes
        assert "2P" in modes
        assert modes["3P"]["sample_count"] == 2
        assert modes["2P"]["sample_count"] == 1


class TestTrendDirection:
    @patch("analysis.accuracy.engine")
    def test_improving_trend(self, mock_engine):
        """Second half better than first half → improving."""
        ref = date(2026, 2, 20)
        rows = [
            _make_row(ref - timedelta(days=3), 0.70, 0.80, 250, "2P", 240, 1),
            _make_row(ref - timedelta(days=2), 0.72, 0.80, 250, "2P", 240, 2),
            _make_row(ref - timedelta(days=1), 0.90, 0.80, 250, "2P", 240, 3),
            _make_row(ref, 0.95, 0.80, 250, "2P", 240, 4),
        ]

        engine_mock = _mock_execute(rows)
        mock_engine.connect = engine_mock.connect

        result = get_accuracy_dashboard("site-1", days_back=7, reference_date=ref)
        assert result["summary"]["trend"] == "improving"

    @patch("analysis.accuracy.engine")
    def test_declining_trend(self, mock_engine):
        """First half better than second half → declining."""
        ref = date(2026, 2, 20)
        rows = [
            _make_row(ref - timedelta(days=3), 0.95, 0.80, 250, "2P", 240, 1),
            _make_row(ref - timedelta(days=2), 0.92, 0.80, 250, "2P", 240, 2),
            _make_row(ref - timedelta(days=1), 0.70, 0.80, 250, "2P", 240, 3),
            _make_row(ref, 0.68, 0.80, 250, "2P", 240, 4),
        ]

        engine_mock = _mock_execute(rows)
        mock_engine.connect = engine_mock.connect

        result = get_accuracy_dashboard("site-1", days_back=7, reference_date=ref)
        assert result["summary"]["trend"] == "declining"

    @patch("analysis.accuracy.engine")
    def test_stable_trend_single_row(self, mock_engine):
        """Single row → stable."""
        ref = date(2026, 2, 20)
        rows = [
            _make_row(ref, 0.85, 0.80, 250, "2P", 240, 4),
        ]

        engine_mock = _mock_execute(rows)
        mock_engine.connect = engine_mock.connect

        result = get_accuracy_dashboard("site-1", days_back=7, reference_date=ref)
        assert result["summary"]["trend"] == "stable"


class TestNullDrinkCounts:
    @patch("analysis.accuracy.engine")
    def test_null_drinks_excluded_from_predicted_vs_actual(self, mock_engine):
        """Rows with null actual_drinks should not appear in predicted_vs_actual."""
        ref = date(2026, 2, 20)
        rows = [
            _make_row(ref - timedelta(days=1), 0.85, 0.80, 250, "2P", None, 3),
            _make_row(ref, 0.90, 0.80, 280, "2P", 270, 4),
        ]

        engine_mock = _mock_execute(rows)
        mock_engine.connect = engine_mock.connect

        result = get_accuracy_dashboard("site-1", days_back=7, reference_date=ref)

        # Both rows counted for accuracy (they have actual_accuracy)
        assert result["days_measured"] == 2
        # Only the row with actual_drinks shows up in predicted_vs_actual
        assert len(result["predicted_vs_actual"]) == 1
        assert result["predicted_vs_actual"][0]["actual"] == 270

    @patch("analysis.accuracy.engine")
    def test_null_predicted_drinks_excluded(self, mock_engine):
        """Rows with null predicted_drinks should not appear in predicted_vs_actual."""
        ref = date(2026, 2, 20)
        rows = [
            _make_row(ref, 0.90, 0.80, None, "2P", 270, 4),
        ]

        engine_mock = _mock_execute(rows)
        mock_engine.connect = engine_mock.connect

        result = get_accuracy_dashboard("site-1", days_back=7, reference_date=ref)

        assert result["days_measured"] == 1
        assert len(result["predicted_vs_actual"]) == 0
        assert result["summary"]["avg_abs_error"] is None


class TestAlertLogic:
    @patch("analysis.accuracy.engine")
    def test_alert_triggered_three_consecutive_low(self, mock_engine):
        """Three consecutive days below 75% triggers alert."""
        ref = date(2026, 2, 20)
        rows = [
            _make_row(ref - timedelta(days=3), 0.90, 0.80, 250, "2P", 240, 1),
            _make_row(ref - timedelta(days=2), 0.60, 0.80, 250, "2P", 240, 2),
            _make_row(ref - timedelta(days=1), 0.65, 0.80, 250, "2P", 240, 3),
            _make_row(ref, 0.70, 0.80, 250, "2P", 240, 4),
        ]

        engine_mock = _mock_execute(rows)
        mock_engine.connect = engine_mock.connect

        result = get_accuracy_dashboard("site-1", days_back=7, reference_date=ref)
        assert result["alert"] is True
        assert "3 consecutive days" in result["alert_reason"]

    @patch("analysis.accuracy.engine")
    def test_no_alert_when_not_consecutive(self, mock_engine):
        """Non-consecutive low days should not trigger alert."""
        ref = date(2026, 2, 20)
        rows = [
            _make_row(ref - timedelta(days=3), 0.60, 0.80, 250, "2P", 240, 1),
            _make_row(ref - timedelta(days=2), 0.90, 0.80, 250, "2P", 240, 2),
            _make_row(ref - timedelta(days=1), 0.60, 0.80, 250, "2P", 240, 3),
            _make_row(ref, 0.90, 0.80, 250, "2P", 240, 4),
        ]

        engine_mock = _mock_execute(rows)
        mock_engine.connect = engine_mock.connect

        result = get_accuracy_dashboard("site-1", days_back=7, reference_date=ref)
        assert result["alert"] is False


class TestDailyTrendRolling:
    @patch("analysis.accuracy.engine")
    def test_rolling_7d_avg_computed(self, mock_engine):
        """Rolling 7-day average should be computed for each day."""
        ref = date(2026, 2, 20)
        rows = []
        for i in range(10):
            d = ref - timedelta(days=9 - i)
            rows.append(_make_row(d, 0.80 + i * 0.01, 0.80, 250, "2P", 240, i % 7))

        engine_mock = _mock_execute(rows)
        mock_engine.connect = engine_mock.connect

        result = get_accuracy_dashboard("site-1", days_back=14, reference_date=ref)

        assert len(result["daily_trend"]) == 10
        # Each entry should have date, accuracy, rolling_7d_avg
        for entry in result["daily_trend"]:
            assert "date" in entry
            assert "accuracy" in entry
            assert "rolling_7d_avg" in entry


class TestCalibration:
    @patch("analysis.accuracy.engine")
    def test_miscalibrated_when_low_conf_better(self, mock_engine):
        """If low-confidence rows are more accurate than high, calibrated=False."""
        ref = date(2026, 2, 20)
        rows = [
            _make_row(ref - timedelta(days=1), 0.70, 0.90, 250, "2P", 240, 3),  # high conf, low acc
            _make_row(ref, 0.95, 0.30, 250, "2P", 240, 4),  # low conf, high acc
        ]

        engine_mock = _mock_execute(rows)
        mock_engine.connect = engine_mock.connect

        result = get_accuracy_dashboard("site-1", days_back=7, reference_date=ref)
        assert result["summary"]["calibrated"] is False

    @patch("analysis.accuracy.engine")
    def test_calibrated_none_when_missing_band(self, mock_engine):
        """If only one band present, calibrated=None."""
        ref = date(2026, 2, 20)
        rows = [
            _make_row(ref, 0.90, 0.80, 250, "2P", 240, 4),
        ]

        engine_mock = _mock_execute(rows)
        mock_engine.connect = engine_mock.connect

        result = get_accuracy_dashboard("site-1", days_back=7, reference_date=ref)
        assert result["summary"]["calibrated"] is None


class TestBestWorstDay:
    @patch("analysis.accuracy.engine")
    def test_best_and_worst_day_identified(self, mock_engine):
        ref = date(2026, 2, 20)
        rows = [
            _make_row(ref - timedelta(days=1), 0.60, 0.80, 250, "2P", 240, 6),  # Saturday (dow=6)
            _make_row(ref, 0.95, 0.80, 250, "2P", 240, 2),  # Tuesday (dow=2)
        ]

        engine_mock = _mock_execute(rows)
        mock_engine.connect = engine_mock.connect

        result = get_accuracy_dashboard("site-1", days_back=7, reference_date=ref)
        assert result["summary"]["worst_day"] == "Saturday"
        assert result["summary"]["best_day"] == "Tuesday"
