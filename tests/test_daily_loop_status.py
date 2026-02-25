"""
Tests for the daily-loop health status endpoint.
"""

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

from analysis.accuracy import get_daily_loop_status


class TestDailyLoopStatusEmpty:
    def test_no_data_returns_red(self, mock_db):
        """With no pipeline runs or predictions, readiness should be red."""
        _, mock_conn = mock_db

        # pipeline_runs query returns empty
        mock_result_runs = MagicMock()
        mock_result_runs.mappings.return_value.all.return_value = []

        # predictions query returns empty
        mock_result_pred = MagicMock()
        mock_result_pred.mappings.return_value.first.return_value = None

        mock_conn.execute.side_effect = [mock_result_runs, mock_result_pred]

        with patch("analysis.accuracy.get_rolling_accuracy") as mock_accuracy:
            mock_accuracy.return_value = {
                "avg_accuracy": None,
                "days_measured": 0,
                "trend": "stable",
                "alert": False,
                "alert_reason": None,
            }

            result = get_daily_loop_status("site-1")

        assert result["readiness"] == "red"
        assert result["tomorrow"] is None
        assert result["accuracy_7d"]["avg"] is None


class TestDailyLoopStatusHealthy:
    def test_green_when_all_ok(self, mock_db):
        """Readiness should be green when ingest+predict ran ok and accuracy is above 75%."""
        _, mock_conn = mock_db

        now = datetime.utcnow()
        runs = [
            {
                "job_name": "predict",
                "status": "ok",
                "started_at": now - timedelta(hours=1),
                "finished_at": now,
                "duration_ms": 5000,
                "error_text": None,
                "result_json": None,
            },
            {
                "job_name": "ingest",
                "status": "ok",
                "started_at": now - timedelta(hours=2),
                "finished_at": now - timedelta(hours=1, minutes=55),
                "duration_ms": 3000,
                "error_text": None,
                "result_json": None,
            },
            {
                "job_name": "daily_loop",
                "status": "ok",
                "started_at": now - timedelta(hours=2),
                "finished_at": now,
                "duration_ms": 8000,
                "error_text": None,
                "result_json": {
                    "trust": {"sms_sent": 1, "sms_degraded": False},
                },
            },
        ]

        tomorrow = date.today() + timedelta(days=1)
        pred_row = {
            "prediction_id": "pred-1",
            "confidence_score": 0.85,
            "predicted_drinks": "280",
            "staffing_mode": "3P",
            "generated_at": now,
        }

        mock_result_runs = MagicMock()
        mock_result_runs.mappings.return_value.all.return_value = runs

        mock_result_pred = MagicMock()
        mock_result_pred.mappings.return_value.first.return_value = pred_row

        mock_conn.execute.side_effect = [mock_result_runs, mock_result_pred]

        with patch("analysis.accuracy.get_rolling_accuracy") as mock_accuracy:
            mock_accuracy.return_value = {
                "avg_accuracy": 88.4,
                "days_measured": 7,
                "trend": "improving",
                "alert": False,
                "alert_reason": None,
            }

            result = get_daily_loop_status("site-1")

        assert result["readiness"] == "green"
        assert result["tomorrow"]["predicted_drinks"] == 280
        assert result["tomorrow"]["staffing_mode"] == "3P"
        assert result["sms_status"] == "ok"
        assert result["accuracy_7d"]["avg"] == 88.4
        assert result["accuracy_7d"]["trend"] == "improving"


class TestDailyLoopStatusDegraded:
    def test_yellow_when_accuracy_low(self, mock_db):
        """Readiness should be yellow when ingest+predict ok but accuracy below 75%."""
        _, mock_conn = mock_db

        now = datetime.utcnow()
        runs = [
            {
                "job_name": "predict",
                "status": "ok",
                "started_at": now - timedelta(hours=1),
                "finished_at": now,
                "duration_ms": 5000,
                "error_text": None,
                "result_json": None,
            },
            {
                "job_name": "ingest",
                "status": "ok",
                "started_at": now - timedelta(hours=2),
                "finished_at": now - timedelta(hours=1, minutes=55),
                "duration_ms": 3000,
                "error_text": None,
                "result_json": None,
            },
        ]

        mock_result_runs = MagicMock()
        mock_result_runs.mappings.return_value.all.return_value = runs

        mock_result_pred = MagicMock()
        mock_result_pred.mappings.return_value.first.return_value = None

        mock_conn.execute.side_effect = [mock_result_runs, mock_result_pred]

        with patch("analysis.accuracy.get_rolling_accuracy") as mock_accuracy:
            mock_accuracy.return_value = {
                "avg_accuracy": 68.2,
                "days_measured": 7,
                "trend": "declining",
                "alert": True,
                "alert_reason": "Accuracy below 75% for 3 consecutive days.",
            }

            result = get_daily_loop_status("site-1")

        assert result["readiness"] == "yellow"
        assert result["accuracy_7d"]["alert"] is True
